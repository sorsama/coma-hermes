"""Backend for the Coma page in Hermes Desktop. Mounted at /api/plugins/coma/."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()


def _pairing():
    """The process-wide pairing from ../pairing.py, the same one /coma drives."""
    name = "coma_pairing_core"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent.parent / "pairing.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module.PAIRING


@router.get("/status")
def status() -> dict:
    return _pairing().status()


@router.post("/start")
def start() -> dict:
    return _pairing().start()


@router.post("/stop")
def stop() -> dict:
    return _pairing().stop()
