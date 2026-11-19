"""Coma: pair the Coma phone app with this Hermes.

    hermes coma          in a terminal: shows the QR and pairing code, runs until Ctrl+C
    /coma                in any Hermes chat: starts pairing and replies with the codes
    /coma stop           closes the tunnel

Hermes Desktop also gets a Coma page (desktop/plugin.js) that shows the same QR.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path


def core():
    """pairing.py, loaded once under one name so every surface in this process shares a pairing."""
    name = "coma_pairing_core"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / "pairing.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


def _slash(raw_args: str) -> str:
    pairing = core().PAIRING
    if raw_args.strip().lower() in ("stop", "off"):
        pairing.stop()
        return "Pairing stopped and the tunnel is closed."
    status = pairing.start()
    # A tunnel opens in a few seconds; wait for it so the reply can carry the codes.
    deadline = time.time() + 45
    while status["state"] == "starting" and time.time() < deadline:
        time.sleep(1)
        status = pairing.status()
    text = core().describe(status, qr=True)
    return f"```\n{text}\n```" if status["state"] == "on" else text


def _setup_cli(parser) -> None:
    parser.add_argument("action", nargs="?", default="pair", choices=["pair"],
                        help="pair (default): show the QR and pairing code until you press Ctrl+C")


def _run_cli(args) -> None:
    module = core()
    pairing = module.PAIRING
    shown = None
    try:
        status = pairing.start()
        while True:
            if status["state"] != shown:
                shown = status["state"]
                print(module.describe(status, qr=True), flush=True)
                if shown == "on":
                    print("\nLeave this running. Press Ctrl+C to close the tunnel.", flush=True)
                if shown == "error" or (shown == "waiting" and status["needs_restart"]):
                    return
            time.sleep(1)
            status = pairing.status()
    except KeyboardInterrupt:
        pass
    finally:
        pairing.stop()
        if shown == "on":
            print("\nTunnel closed.")


def register(ctx) -> None:
    ctx.register_cli_command(
        name="coma", help="Pair the Coma phone app with this Hermes",
        setup_fn=_setup_cli, handler_fn=_run_cli,
        description="Opens a Cloudflare tunnel to this Hermes and shows the QR and code the Coma app pairs with.",
    )
    ctx.register_command(
        "coma", handler=_slash, args_hint="[stop]",
        description="Show the QR and code that pair the Coma phone app with this Hermes.",
    )
