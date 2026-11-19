"""Coma: reach this Hermes from the Coma phone app, through a Coma relay.

    hermes coma                    in a terminal: shows the pairing QR, runs until Ctrl+C
    hermes coma --relay <address>  use your own relay instead of the default one
    /coma                          in any Hermes chat: replies with the pairing QR
    /coma stop                     drops the link to the relay

Hermes Desktop also gets a Coma page (desktop/plugin.js) that shows the same QR. Once a phone has
been paired, the gateway reconnects to the relay by itself every time Hermes starts.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path


def core():
    """pairing.py, loaded once under one name so every surface in this process shares a link."""
    name = "coma_pairing_core"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / "pairing.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


def _settled(link, status: dict, seconds: int) -> dict:
    """Waits up to [seconds] for a starting link to connect, so a reply can carry the code."""
    deadline = time.time() + seconds
    while (status["state"] == "starting" or (status["state"] == "on" and not status["link"])) and time.time() < deadline:
        time.sleep(1)
        status = link.status()
    return status


def _slash(raw_args: str) -> str:
    link = core().LINK
    if raw_args.strip().lower() in ("stop", "off"):
        link.stop()
        return "The link to the relay is off. Phones can't reach this Hermes until you run /coma again."
    status = _settled(link, link.start(), 30)
    text = core().describe(status, qr=True)
    return f"```\n{text}\n```" if status["state"] == "on" else text


def _setup_cli(parser) -> None:
    parser.add_argument("--relay", metavar="ADDRESS",
                        help="use this relay from now on (your own, for example) instead of the default one")


def _run_cli(args) -> None:
    module = core()
    link = module.LINK
    shown = None
    try:
        status = link.use_relay(args.relay) if getattr(args, "relay", None) else link.start()
        while True:
            status = _settled(link, status, 30)
            key = (status["state"], status["link"], len(status["devices"]))
            if key != shown:
                shown = key
                print(module.describe(status, qr=True), flush=True)
                if status["state"] == "on":
                    print("\nLeave this running. Press Ctrl+C to stop.", flush=True)
                if status["state"] == "error" or (status["state"] == "waiting" and status["needs_restart"]):
                    return
            time.sleep(2)
            status = link.status()
    except module.LinkError as failure:
        print(failure)
    except KeyboardInterrupt:
        pass
    finally:
        link.stop()


def register(ctx) -> None:
    ctx.register_cli_command(
        name="coma", help="Pair the Coma phone app with this Hermes",
        setup_fn=_setup_cli, handler_fn=_run_cli,
        description="Links this Hermes to a Coma relay and shows the QR and code the Coma app pairs with.",
    )
    ctx.register_command(
        "coma", handler=_slash, args_hint="[stop]",
        description="Show the QR and code that pair the Coma phone app with this Hermes.",
    )
    # The gateway is the process that runs Hermes' API server, so it is the one that keeps the
    # link up. It only does so once pairing has been set up, never on a fresh install.
    if "gateway" in sys.argv[1:]:
        try:
            link = core().LINK
            if link.is_set_up():
                link.start(lan=False)
        except Exception:
            pass
