"""Backend for the Coma page in Hermes Desktop. Mounted at /api/plugins/coma/."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()


def _core():
    """../pairing.py, the same module /coma and the gateway drive."""
    name = "coma_pairing_core"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent.parent / "pairing.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


@router.get("/status")
def status() -> dict:
    return _core().LINK.status()


@router.post("/start")
def start() -> dict:
    return _core().LINK.start()


@router.post("/stop")
def stop() -> dict:
    return _core().LINK.stop()


@router.post("/relay")
def relay(body: dict) -> dict:
    """Moves this Hermes to another relay: {"address": "https://…"}."""
    core = _core()
    try:
        return core.LINK.use_relay(str(body.get("address", "")))
    except core.LinkError as failure:
        return {**core.LINK.status(), "state": "error", "error": str(failure)}


@router.post("/devices/remove")
def remove_device(body: dict) -> dict:
    """Unpairs one phone: {"id": "…"}."""
    return _core().LINK.remove_device(str(body.get("id", "")))
