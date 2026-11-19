"""Pairing between this Hermes and the Coma phone app.

One Pairing object per process does the whole job: it makes sure Hermes' API server is on and has
a key, opens a free Cloudflare quick tunnel to it with `cloudflared`, and answers Coma's search on
the local network. A phone pairs by scanning the QR of [Pairing.status]'s link, or, on the same
Wi-Fi, by picking this machine and typing the six-digit code.

The terminal command, the /coma chat command and the Desktop page all drive this module, so they
show the same code while they share a process.
"""
from __future__ import annotations

import atexit
import http.server
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_PORT = 8642
# Coma broadcasts SEARCH to this UDP port and reads the answers; pairing then happens over TCP.
DISCOVERY_PORT = 48642
SEARCH = b"COMA?"
MAX_WRONG_CODES = 5
CLOUDFLARED_HELP = "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"


class PairingError(Exception):
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
    raise PairingError("Couldn't find Hermes' home folder. Set HERMES_HOME to it.")


def _read_env(path: Path) -> dict:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
            if match:
                values[match.group(1)] = match.group(2).strip("\"'")
    return values


def ensure_api_server(env_path: Path) -> tuple:
    """Returns (key, port, changed). Appends only the settings that are missing from Hermes' .env."""
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
    return key, int(values.get("API_SERVER_PORT") or DEFAULT_PORT), bool(additions)


def api_server_up(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def open_tunnel(port: int) -> tuple:
    """Starts cloudflared and returns (process, public https address)."""
    if not shutil.which("cloudflared"):
        raise PairingError(f"cloudflared isn't installed on this machine. Get it from {CLOUDFLARED_HELP}")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        ["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", creationflags=flags,
    )
    said = []
    deadline = time.time() + 60
    while time.time() < deadline:
        line = process.stderr.readline()
        if not line:
            if process.poll() is not None:
                if any("status 429" in words for words in said):
                    raise PairingError("Cloudflare is limiting new tunnels from this network. Wait a few minutes and try again.")
                raise PairingError("cloudflared stopped before it opened a tunnel. " + " ".join(said[-3:]).strip())
            continue
        said.append(line)
        match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if match:
            # Keep reading so cloudflared's log pipe never fills.
            threading.Thread(target=lambda: [None for _ in process.stderr], daemon=True).start()
            return process, match.group(0)
    process.terminate()
    raise PairingError("cloudflared didn't open a tunnel within a minute.")


class LanPairing:
    """Lets a phone on the same network find this machine and pair with a six-digit code.

    The search answer carries only this machine's name and the pairing port. The tunnel address and
    the API key are returned for the right code alone; after a few wrong codes a new one is drawn.
    """

    def __init__(self, name: str, address: str, key: str):
        self.name, self.address, self.key = name, address, key
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
                return {"url": self.address, "key": self.key, "name": self.name}
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


class Pairing:
    """The tunnel and the LAN answerer, started and stopped together. Safe to call from any thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = "off"  # off, starting, waiting, on, error
        self._error = ""
        self._process = None
        self._lan = None
        self._lan_error = ""
        self._address = ""
        self._key = ""
        self._changed_env = False
        self._name = socket.gethostname().split(".")[0].lower()
        self._generation = 0
        atexit.register(self.stop)

    def start(self) -> dict:
        """Begins pairing in the background and returns the status so far. A running pairing is left alone."""
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
            process, lan = self._process, self._lan
            self._process = self._lan = None
            self._state, self._error, self._address = "off", "", ""
        if process is not None and process.poll() is None:
            process.terminate()
        if lan is not None:
            lan.close()
        return self.status()

    def status(self) -> dict:
        with self._lock:
            return self._status()

    def _status(self) -> dict:
        if self._state == "on" and self._process is not None and self._process.poll() is not None:
            self._state, self._error = "error", "The Cloudflare tunnel closed. Start pairing again."
        on = self._state == "on"
        link = "coma://connect?" + urllib.parse.urlencode(
            {"url": self._address, "key": self._key, "name": self._name}) if on else ""
        return {
            "state": self._state,
            "error": self._error,
            # True when this run switched the API server on: Hermes has to restart before it answers.
            "needs_restart": self._state == "waiting" and self._changed_env,
            "name": self._name,
            "address": self._address if on else "",
            "key": self._key if on else "",
            "link": link,
            "qr": qr_rows(link) if on else None,
            "code": self._lan.code if on and self._lan is not None else "",
            "lan_error": self._lan_error if on else "",
        }

    def _run(self, generation: int) -> None:
        def current() -> bool:
            return generation == self._generation

        try:
            key, port, changed = ensure_api_server(hermes_home() / ".env")
            with self._lock:
                if not current():
                    return
                self._key, self._changed_env = key, changed or self._changed_env
            if not api_server_up(port):
                with self._lock:
                    if current():
                        self._state = "waiting"
                while not api_server_up(port):
                    if not current():
                        return
                    time.sleep(2)
                with self._lock:
                    if current():
                        self._state = "starting"
            process, address = open_tunnel(port)
            lan, lan_error = None, ""
            try:
                lan = LanPairing(self._name, address, key)
            except OSError as failure:
                lan_error = f"Phones on this Wi-Fi can't find this machine by themselves ({failure}). The QR still works."
            with self._lock:
                if current():
                    self._process, self._lan, self._lan_error = process, lan, lan_error
                    self._address, self._state = address, "on"
                    return
            process.terminate()
            if lan is not None:
                lan.close()
        except PairingError as failure:
            with self._lock:
                if current():
                    self._state, self._error = "error", str(failure)
        except Exception as failure:  # nothing here may take the host process down
            with self._lock:
                if current():
                    self._state, self._error = "error", f"Pairing failed: {failure}"


#: The one pairing of this process.
PAIRING = Pairing()


def describe(status: dict, *, qr: bool) -> str:
    """The status as text for a terminal or a chat reply."""
    state = status["state"]
    if state == "error":
        return status["error"]
    if state == "off":
        return "Pairing is off."
    if state == "waiting":
        if status["needs_restart"]:
            return ("Turned on the Hermes API server. Restart Hermes (hermes gateway restart, or quit and "
                    "reopen the app) so it starts, then run this again.")
        return "Waiting for the Hermes gateway to start. Start it with: hermes gateway"
    if state == "starting":
        return "Opening a Cloudflare tunnel..."
    lines = []
    art = qr_text(status["link"]) if qr else None
    if art:
        lines += [art, "", "Scan this in Coma: Connect > Scan pairing code."]
    else:
        lines += ["Enter these in Coma (Connect > Enter an address by hand):"]
    lines += [f"  address  {status['address']}", f"  key      {status['key']}"]
    if status["code"]:
        code = status["code"]
        lines += ["", f"On the same Wi-Fi, Coma lists this machine as \"{status['name']}\". Pairing code:  {code[:3]} {code[3:]}"]
    elif status["lan_error"]:
        lines += ["", status["lan_error"]]
    lines += ["", "Anyone with these codes can run commands on this machine, so don't share them."]
    return "\n".join(lines)
