"""The link between this Hermes and the Coma phone app, through a Coma relay.

This Hermes dials out to the relay and keeps a WebSocket open. A phone sends its requests to the
relay, which passes them down that socket; `Link` answers each one from Hermes' own API server on
this machine and streams the reply back. The Hermes API key never leaves this machine.

A phone joins by scanning the QR of `Link.status()["link"]`, or, on the same Wi-Fi, by picking this
machine in the app and typing the six-digit code. Either way it ends up with a one-time pairing
token for the relay.

The relay is https://coma-relay.zyphite.com unless you point somewhere else: set COMA_RELAY_URL, or
run `hermes coma --relay https://your.relay`. The relay is open source (sorsama/coma-relay).

The terminal command, the /coma chat command and the Desktop page all drive this module.
"""
from __future__ import annotations

import asyncio
import codecs
import http.server
import json
import os
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_RELAY = "https://coma-relay.zyphite.com"
DEFAULT_PORT = 8642
# Coma broadcasts SEARCH to this UDP port and reads the answers; pairing then happens over TCP.
DISCOVERY_PORT = 48642
SEARCH = b"COMA?"
MAX_WRONG_CODES = 5
PAIRING_REFRESH_SECONDS = 480
PING_SECONDS = 25


class LinkError(Exception):
    """A failure with a sentence fit to show the person pairing."""


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        pass
    candidates = [os.environ.get("HERMES_HOME"), Path.home() / ".hermes"]
    if os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "hermes")
    for candidate in candidates:
        if candidate and (Path(candidate) / "config.yaml").exists():
            return Path(candidate)
    raise LinkError("Couldn't find Hermes' home folder. Set HERMES_HOME to it.")


def _read_env(path: Path) -> dict:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
            if match:
                values[match.group(1)] = match.group(2).strip("\"'")
    return values


def ensure_api_server(env_path: Path) -> tuple:
    """Returns (key, address, changed). Appends only the settings that are missing from Hermes' .env."""
    values = _read_env(env_path)
    additions = []
    if values.get("API_SERVER_ENABLED", "").lower() not in ("1", "true", "yes", "on"):
        additions.append("API_SERVER_ENABLED=true")
    key = values.get("API_SERVER_KEY", "")
    if not key:
        key = secrets.token_urlsafe(32)
        additions.append(f"API_SERVER_KEY={key}")
    if additions:
        with env_path.open("a", encoding="utf-8") as handle:
            handle.write("\n# Added by the coma plugin\n" + "\n".join(additions) + "\n")
    # Hermes can be told to listen on one address only (a VPN address, say); reach it there.
    host = values.get("API_SERVER_HOST", "").strip()
    if host in ("", "0.0.0.0", "::", "localhost"):
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    return key, f"http://{host}:{int(values.get('API_SERVER_PORT') or DEFAULT_PORT)}", bool(additions)


def api_server_up(address: str) -> bool:
    try:
        with urllib.request.urlopen(f"{address}/health", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def clean_relay(address: str) -> str:
    address = address.strip().rstrip("/")
    if not re.match(r"^https?://[^\s/]+", address):
        raise LinkError("A relay address looks like https://relay.example.com")
    return address


class Relay:
    """The relay's HTTP control surface for one host."""

    def __init__(self, address: str, host_id: str = "", secret: str = ""):
        self.address, self.host_id, self.secret = address, host_id, secret

    def _call(self, method: str, path: str, body=None, authed: bool = True) -> dict:
        request = urllib.request.Request(
            self.address + path, method=method, data=None if body is None else json.dumps(body).encode(),
            # Cloudflare turns away urllib's default agent, so the plugin names itself.
            headers={"Content-Type": "application/json", "User-Agent": "coma-hermes"},
        )
        if authed:
            request.add_header("Authorization", f"Bearer {self.secret}")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as failure:
            try:
                said = json.loads(failure.read()).get("error", {}).get("message", "")
            except Exception:
                said = ""
            error = LinkError(said or f"The relay answered with an error ({failure.code}).")
            error.status = failure.code
            raise error from None
        except (urllib.error.URLError, OSError, ValueError):
            raise LinkError(f"Can't reach the relay at {self.address}.") from None

    def register(self) -> None:
        answer = self._call("POST", "/v1/hosts", authed=False)
        self.host_id, self.secret = answer["host_id"], answer["host_secret"]

    def describe(self) -> dict:
        return self._call("GET", f"/v1/hosts/{self.host_id}")

    def new_pairing(self) -> str:
        return self._call("POST", f"/v1/hosts/{self.host_id}/pairing")["token"]

    def remove_device(self, device_id: str) -> dict:
        return self._call("DELETE", f"/v1/hosts/{self.host_id}/devices/{urllib.parse.quote(device_id, safe='')}")

    @property
    def socket_address(self) -> str:
        return re.sub(r"^http", "ws", self.address) + f"/v1/hosts/{self.host_id}/socket"


class LanPairing:
    """Lets a phone on the same network find this machine and pair with a six-digit code.

    The search answer carries only this machine's name and the pairing port. What a phone needs to
    join ([offer]) is returned for the right code alone; after a few wrong codes a new one is drawn.
    """

    def __init__(self, name: str, offer):
        self.name, self.offer = name, offer
        self.lock = threading.Lock()
        self.wrong = 0
        self.code = self._draw()
        pairing = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 4096)) or b"{}")
                except (ValueError, TypeError):
                    body = {}
                code = str(body.get("code", "")) if isinstance(body, dict) else ""
                answer = pairing.redeem(code) if self.path == "/pair" else None
                raw = json.dumps(answer or {"error": "wrong code"}).encode()
                self.send_response(200 if answer else 403)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("0.0.0.0", DISCOVERY_PORT))
        self.http = http.server.ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        threading.Thread(target=self._answer_searches, daemon=True).start()

    @staticmethod
    def _draw() -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    def redeem(self, code: str):
        with self.lock:
            if secrets.compare_digest(code.encode(), self.code.encode()):
                self.wrong = 0
                return self.offer()
            self.wrong += 1
            if self.wrong >= MAX_WRONG_CODES:
                self.wrong, self.code = 0, self._draw()
            return None

    def close(self) -> None:
        self.udp.close()
        self.http.shutdown()
        self.http.server_close()

    def _answer_searches(self) -> None:
        reply = json.dumps({"coma": 1, "name": self.name, "port": self.http.server_address[1]}).encode()
        while True:
            try:
                data, sender = self.udp.recvfrom(64)
                if data.strip() == SEARCH:
                    self.udp.sendto(reply, sender)
            except OSError:
                return


def qr_rows(text: str):
    """The QR of [text] as rows of '1' (dark) and '0', or None when the qrcode package is missing."""
    try:
        import qrcode
    except ImportError:
        return None
    code = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_L)
    code.add_data(text)
    code.make(fit=True)
    return ["".join("1" if cell else "0" for cell in row) for row in code.get_matrix()]


def qr_text(text: str):
    """The same QR drawn with half-block characters for a terminal or a code block, or None."""
    rows = qr_rows(text)
    if rows is None:
        return None
    width = len(rows[0]) + 4
    blank = "0" * width
    grid = [blank, blank] + ["00" + row + "00" for row in rows] + [blank, blank]
    if len(grid) % 2:
        grid.append(blank)
    # Light modules are drawn as ink, so the code reads dark-on-light on a dark terminal.
    glyph = {("0", "0"): "█", ("0", "1"): "▀", ("1", "0"): "▄", ("1", "1"): " "}
    return "\n".join("".join(glyph[pair] for pair in zip(grid[i], grid[i + 1])) for i in range(0, len(grid), 2))


class Link:
    """This Hermes' place on the relay. Safe to call from any thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = "off"  # off, starting, waiting, on, error
        self._error = ""
        self._generation = 0
        self._changed_env = False
        self._relay = None
        # True while this process holds the socket; False when another Hermes process on this
        # machine already does and this one only hands out pairing codes.
        self._serving = False
        self._token = ""
        self._token_at = 0.0
        self._info = {}
        self._info_at = 0.0
        self._lan = None
        self._lan_error = ""
        self._name = socket.gethostname().split(".")[0].lower()

    # -- settings ------------------------------------------------------------------------------

    def _settings_path(self) -> Path:
        return hermes_home() / "coma-relay.json"

    def _load_settings(self) -> dict:
        try:
            return json.loads(self._settings_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def is_set_up(self) -> bool:
        """True once this Hermes has a place on a relay, so it can reconnect by itself."""
        try:
            return bool(self._load_settings().get("host_id"))
        except LinkError:
            return False

    def relay_address(self) -> str:
        return clean_relay(os.environ.get("COMA_RELAY_URL") or self._load_settings().get("relay") or DEFAULT_RELAY)

    def use_relay(self, address: str) -> dict:
        """Moves this Hermes to another relay. Phones paired through the old one have to pair again."""
        address = clean_relay(address)
        self.stop()
        path = self._settings_path()
        path.write_text(json.dumps({"relay": address}), encoding="utf-8")
        return self.start()

    # -- lifecycle -----------------------------------------------------------------------------

    def start(self) -> dict:
        """Connects in the background and returns the status so far. A live link is left alone."""
        with self._lock:
            if self._state in ("starting", "waiting", "on"):
                return self._status()
            self._generation += 1
            self._state, self._error = "starting", ""
            threading.Thread(target=self._run, args=(self._generation,), daemon=True).start()
            return self._status()

    def stop(self) -> dict:
        with self._lock:
            self._generation += 1
            lan, self._lan = self._lan, None
            self._state, self._error, self._serving, self._token = "off", "", False, ""
        if lan is not None:
            lan.close()
        return self.status()

    def remove_device(self, device_id: str) -> dict:
        relay = self._relay
        if relay is not None:
            try:
                info = relay.remove_device(device_id)
                with self._lock:
                    self._info, self._info_at = info, time.time()
            except LinkError:
                pass
        return self.status()

    def status(self) -> dict:
        self._refresh()
        with self._lock:
            return self._status()

    def _refresh(self) -> None:
        """Keeps the pairing token fresh and the device list recent while the link is on."""
        with self._lock:
            relay, on = self._relay, self._state == "on"
            stale_token = time.time() - self._token_at > PAIRING_REFRESH_SECONDS
            stale_info = time.time() - self._info_at > 5
        if not on or relay is None:
            return
        try:
            token = relay.new_pairing() if stale_token else None
            info = relay.describe() if stale_info else None
        except LinkError:
            return
        with self._lock:
            if token:
                self._token, self._token_at = token, time.time()
            if info is not None:
                self._info, self._info_at = info, time.time()

    def _offer(self) -> dict:
        relay = self._relay
        return {"relay": relay.address, "host": relay.host_id, "token": self._token, "name": self._name}

    def _status(self) -> dict:
        on = self._state == "on" and self._relay is not None and bool(self._token)
        link = "coma://pair?" + urllib.parse.urlencode(self._offer()) if on else ""
        return {
            "state": self._state,
            "error": self._error,
            # True when this run switched the API server on: Hermes has to restart before it answers.
            "needs_restart": self._state == "waiting" and self._changed_env,
            "name": self._name,
            "relay": self._relay.address if self._relay is not None else "",
            "link": link,
            "qr": qr_rows(link) if on else None,
            "code": self._lan.code if on and self._lan is not None else "",
            "lan_error": self._lan_error if on else "",
            "plan": self._info.get("plan", "free"),
            "device_limit": self._info.get("device_limit", 1),
            "devices": self._info.get("devices", []),
        }

    def _fail(self, generation: int, message: str) -> None:
        with self._lock:
            if generation == self._generation:
                self._state, self._error, self._serving = "error", message, False

    def _run(self, generation: int) -> None:
        def current() -> bool:
            return generation == self._generation

        try:
            key, local, changed = ensure_api_server(hermes_home() / ".env")
            with self._lock:
                self._changed_env = changed or self._changed_env
            if not api_server_up(local):
                with self._lock:
                    if current():
                        self._state = "waiting"
                while not api_server_up(local):
                    if not current():
                        return
                    time.sleep(2)
                with self._lock:
                    if current():
                        self._state = "starting"

            address = self.relay_address()
            saved = self._load_settings()
            relay = Relay(address, saved.get("host_id", ""), saved.get("host_secret", ""))
            if saved.get("relay") != address or not relay.host_id:
                relay.register()
                self._settings_path().write_text(
                    json.dumps({"relay": address, "host_id": relay.host_id, "host_secret": relay.secret}), encoding="utf-8")
            try:
                info = relay.describe()
            except LinkError as failure:
                if getattr(failure, "status", 0) not in (401, 404):
                    raise
                # The relay has forgotten this host (a wiped or replaced relay): take a new place.
                relay.register()
                self._settings_path().write_text(
                    json.dumps({"relay": address, "host_id": relay.host_id, "host_secret": relay.secret}), encoding="utf-8")
                info = relay.describe()

            serving = not info.get("online")
            token = relay.new_pairing()
            lan, lan_error = None, ""
            try:
                lan = LanPairing(self._name, self._offer)
            except OSError as failure:
                lan_error = f"Phones on this Wi-Fi can't find this machine by themselves ({failure}). The QR still works."
            with self._lock:
                if not current():
                    if lan is not None:
                        lan.close()
                    return
                self._relay, self._info, self._info_at = relay, info, time.time()
                self._token, self._token_at = token, time.time()
                self._lan, self._lan_error, self._serving = lan, lan_error, serving
                self._state = "on"
            if serving:
                asyncio.run(self._serve(generation, relay, local, key))
        except LinkError as failure:
            self._fail(generation, str(failure))
        except Exception as failure:  # nothing here may take the host process down
            self._fail(generation, f"Couldn't link to the relay: {failure}")

    # -- the socket ----------------------------------------------------------------------------

    async def _serve(self, generation: int, relay: Relay, local: str, key: str) -> None:
        import aiohttp

        wait = 1
        async with aiohttp.ClientSession(headers={"User-Agent": "coma-hermes"}) as session:
            while generation == self._generation:
                try:
                    async with session.ws_connect(
                        relay.socket_address, headers={"Authorization": f"Bearer {relay.secret}"}, heartbeat=None,
                    ) as ws:
                        wait = 1
                        if await self._converse(generation, ws, session, local, key):
                            return
                except aiohttp.WSServerHandshakeError as refusal:
                    if refusal.status == 403:
                        return self._fail(generation, "Another Hermes on this network is already using the free relay "
                                                      "plan. Plus covers any number of hosts.")
                    if refusal.status in (401, 404):
                        return self._fail(generation, "The relay no longer knows this Hermes. Start pairing again.")
                except (aiohttp.ClientError, OSError, asyncio.TimeoutError):
                    pass
                # The relay or the network dropped: keep trying, a little slower each time.
                for _ in range(wait * 2):
                    if generation != self._generation:
                        return
                    await asyncio.sleep(0.5)
                wait = min(wait * 2, 30)

    async def _converse(self, generation: int, ws, session, local: str, key: str) -> bool:
        """Serves one connection. Returns True when the link should not be dialled again."""
        import aiohttp

        tasks = {}
        send_lock = asyncio.Lock()

        async def send(message: dict) -> None:
            async with send_lock:
                await ws.send_str(json.dumps(message))

        async def answer(request: dict) -> None:
            request_id = request["id"]
            path = str(request.get("path", "/"))
            try:
                if not path.startswith("/"):
                    raise ValueError("bad path")
                headers = {name: value for name, value in (request.get("headers") or {}).items()
                           if isinstance(value, str)}
                headers["Authorization"] = f"Bearer {key}"
                async with session.request(
                    request.get("method", "GET"), local + path, headers=headers,
                    data=(request.get("body") or "").encode() or None,
                    timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=120),
                ) as response:
                    await send({"t": "head", "id": request_id, "status": response.status,
                                "headers": {name.lower(): value for name, value in response.headers.items()}})
                    decoder = codecs.getincrementaldecoder("utf-8")("replace")
                    async for piece in response.content.iter_any():
                        text = decoder.decode(piece)
                        if text:
                            await send({"t": "chunk", "id": request_id, "data": text})
                    await send({"t": "end", "id": request_id})
            except asyncio.CancelledError:
                raise
            except Exception:
                await send({"t": "err", "id": request_id, "message": "Hermes on the host did not answer."})
            finally:
                tasks.pop(request_id, None)

        async def keep_alive() -> None:
            while True:
                for _ in range(PING_SECONDS):
                    await asyncio.sleep(1)
                    if generation != self._generation:  # stopped: hang up now, not at the next ping
                        await ws.close()
                        return
                await send({"t": "ping"})

        pinger = asyncio.ensure_future(keep_alive())
        try:
            async for message in ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(message.data)
                except ValueError:
                    continue
                kind = data.get("t")
                if kind == "req":
                    tasks[data["id"]] = asyncio.ensure_future(answer(data))
                elif kind == "cancel" and data.get("id") in tasks:
                    tasks[data["id"]].cancel()
                elif kind == "paired":
                    # A code pairs one phone, so draw the next one right away.
                    with self._lock:
                        self._token_at = self._info_at = 0.0
        finally:
            pinger.cancel()
            for task in list(tasks.values()):
                task.cancel()
        # 4000: another process on this machine took the link over; leave it to that one.
        if ws.close_code == 4000:
            with self._lock:
                if generation == self._generation:
                    self._serving = False
            return True
        return generation != self._generation


#: The one link of this process.
LINK = Link()


def describe(status: dict, *, qr: bool) -> str:
    """The status as text for a terminal or a chat reply."""
    state = status["state"]
    if state == "error":
        return status["error"]
    if state == "off":
        return "The link to the relay is off."
    if state == "waiting":
        if status["needs_restart"]:
            return ("Turned on the Hermes API server. Restart Hermes (hermes gateway restart, or quit and "
                    "reopen the app) so it starts, then run this again.")
        return "Waiting for the Hermes gateway to start. Start it with: hermes gateway"
    if state == "starting" or not status["link"]:
        return "Connecting to the relay..."
    lines = []
    art = qr_text(status["link"]) if qr else None
    if art:
        lines += [art, "", "Scan this in Coma: Connect > Scan pairing code."]
    else:
        lines += ["Open this link on the phone that has Coma:", f"  {status['link']}"]
    if status["code"]:
        code = status["code"]
        lines += ["", f"On the same Wi-Fi, Coma lists this machine as \"{status['name']}\". Pairing code:  {code[:3]} {code[3:]}"]
    elif status["lan_error"]:
        lines += ["", status["lan_error"]]
    limit = status["device_limit"]
    paired = len(status["devices"])
    lines += ["", f"Relay: {status['relay']}",
              f"Phones paired: {paired}" + (f" of {limit} ({status['plan']} plan)" if limit else f" ({status['plan']} plan)")]
    lines += ["", "A code pairs one phone and stops working after ten minutes. Don't share it."]
    return "\n".join(lines)
