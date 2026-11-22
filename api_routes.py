"""Extra API server routes the Coma app reads, under /api/coma/.

Hermes' API server is the only surface a phone reaches (directly or through the relay), but the
settings, plugins and MCP connectors Hermes Desktop shows live behind the dashboard instead. These
routes give the phone the same view, behind the API server's own key check:

    GET  /api/coma/skills          every installed skill, with its on/off state
    POST /api/coma/skills/toggle   {"name", "enabled"}: switches one skill for every platform
    GET  /api/coma/plugins         every discovered plugin
    GET  /api/coma/connectors      the MCP servers in config.yaml
    GET  /api/coma/config/schema   Hermes Desktop's settings schema
    GET  /api/coma/config          the settings, defaults filled in
    PUT  /api/coma/config          {"config": {...}}: merged over config.yaml, as the dashboard does
    GET  /api/coma/context         {"context_length"} for ?model=&provider=

They are added by wrapping APIServerAdapter._http_route_table, so they exist from the next time
the gateway starts.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

logger = logging.getLogger("coma.api")
_LOCK = threading.Lock()
_SECRET_HINTS = ("key", "token", "secret", "password")


def _json(data: Any, status: int = 200):
    from aiohttp import web
    return web.json_response(data, status=status)


def _error(message: str, status: int = 500):
    return _json({"error": {"message": message, "type": "server_error"}}, status)


def _redact(value: Any, key: str = "") -> Any:
    """Secrets never cross to the phone: any string under a key that names one is masked."""
    if isinstance(value, dict):
        return {k: _redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, key) for v in value]
    if isinstance(value, str) and value and any(h in key.lower() for h in _SECRET_HINTS):
        return "••••" + value[-4:] if len(value) > 8 else "••••"
    return value


def _disabled_names() -> set:
    """Skills switched off in config.yaml, globally or for the API server."""
    try:
        from hermes_cli.config import load_config
        skills = load_config().get("skills") or {}
        names = set(skills.get("disabled") or [])
        names |= set((skills.get("platform_disabled") or {}).get("api_server") or [])
        return names
    except Exception:
        return set()


def _catalog() -> list:
    """(name, description, category) of every installed skill. Hermes has renamed and re-signed its
    skill listing helpers more than once, so each known shape is tried, then the skills folder itself."""
    attempts = []
    try:
        from tools import skills_tool
    except Exception as exc:
        skills_tool = None
        attempts.append(exc)
    if skills_tool is not None:
        for call in (
            lambda: [(s.get("load_name"), s) for s in skills_tool._skill_catalog(skip_disabled=False)],
            lambda: [(s.get("load_name"), s) for s in skills_tool._skill_catalog()],
            lambda: [(s.get("name"), s) for s in skills_tool.find_all_skills(skip_disabled=False)],
            lambda: [(s.get("name"), s) for s in skills_tool.find_all_skills()],
        ):
            try:
                return [(name, s.get("description") or "", s.get("category") or "") for name, s in call() if name]
            except Exception as exc:  # a signature this Hermes doesn't have: try the next
                attempts.append(exc)
    rows = _scan_skill_folders()
    if rows or not attempts:
        return rows
    raise attempts[-1]


def _scan_skill_folders() -> list:
    """Reads SKILL.md front matter under Hermes' skills folder: the listing of last resort."""
    import os
    import re
    from pathlib import Path
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    root = home / "skills"
    rows = []
    if not root.is_dir():
        return rows
    # os.walk skips folders it can't open (a dangling link, say) instead of failing the whole list.
    found = [Path(top) / "SKILL.md" for top, _dirs, files in os.walk(root, onerror=lambda _e: None) if "SKILL.md" in files]
    for skill_md in found:
        try:
            text = skill_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        head = text.split("---", 2)[1] if text.startswith("---") and text.count("---") >= 2 else ""
        field = lambda key: (re.search(rf"(?m)^{key}:\s*[\"']?(.*?)[\"']?\s*$", head) or [None, ""])[1]
        relative = skill_md.parent.relative_to(root)
        name = field("name") or skill_md.parent.name
        category = field("category") or (relative.parts[0] if len(relative.parts) > 1 else "")
        rows.append((name, field("description"), category))
    return rows


def _skills() -> dict:
    off = _disabled_names()
    rows = [
        {"name": name, "description": about, "category": category, "enabled": name not in off, "source": "local"}
        for name, about, category in _catalog()
    ]
    try:
        from hermes_cli.plugins import discover_plugins, get_plugin_manager
        discover_plugins()
        for skill in get_plugin_manager().list_plugin_skill_metadata():
            rows.append({
                "name": skill.get("name", ""), "description": skill.get("description") or "",
                "category": skill.get("category") or "plugin", "enabled": True, "source": "plugin",
            })
    except Exception:
        logger.debug("plugin skills unavailable", exc_info=True)
    rows.sort(key=lambda s: (s["category"], s["name"]))
    return {"object": "list", "data": rows}


def _toggle_skill(name: str, enabled: bool) -> dict:
    from hermes_cli.config import require_readable_config_before_write, save_config
    with _LOCK:
        config = require_readable_config_before_write()
        skills = config.setdefault("skills", {})
        disabled = [n for n in (skills.get("disabled") or []) if n != name]
        if not enabled:
            disabled.append(name)
        skills["disabled"] = disabled
        save_config(config)
    return {"ok": True, "name": name, "enabled": enabled}


def _plugins() -> dict:
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    discover_plugins()
    return {"object": "list", "data": get_plugin_manager().list_plugins()}


def _connectors() -> dict:
    from hermes_cli.config import load_config
    servers = load_config().get("mcp_servers") or {}
    rows = []
    for name, entry in servers.items():
        entry = entry if isinstance(entry, dict) else {}
        transport = "http" if entry.get("url") else "stdio"
        target = entry.get("url") or " ".join([str(entry.get("command", ""))] + [str(a) for a in entry.get("args") or []])
        rows.append({
            "name": name, "transport": transport, "target": target.strip(),
            "enabled": entry.get("enabled", True) is not False,
            "tools": entry.get("tools") if isinstance(entry.get("tools"), dict) else {},
        })
    return {"object": "list", "data": rows}


def _schema() -> dict:
    from hermes_cli.web_server_config import _schema_with_dynamic_provider_options
    return {"fields": _schema_with_dynamic_provider_options()}


def _config() -> dict:
    from hermes_cli.config import load_config
    from hermes_cli.web_server_config import _normalize_config_for_web
    config = _normalize_config_for_web(load_config())
    return _redact({k: v for k, v in config.items() if not k.startswith("_")})


def _save_config(incoming: dict) -> dict:
    from hermes_cli.config import _deep_merge, require_readable_config_before_write, save_config
    from hermes_cli.web_server_config import _denormalize_config_from_web
    with _LOCK:
        existing = require_readable_config_before_write()
        save_config(_deep_merge(existing, _denormalize_config_from_web(incoming)))
    return {"ok": True}


def _context(model: str, provider: str) -> dict:
    from agent.model_metadata import get_model_context_length
    from hermes_cli.config import load_config
    config = load_config()
    configured = config.get("model")
    if isinstance(configured, dict):
        provider = provider or str(configured.get("provider") or "").replace("auto", "")
        configured = configured.get("default")
    model = model or str(configured or "")
    override = config.get("model_context_length")
    length = get_model_context_length(
        model, provider=provider, config_context_length=override if isinstance(override, int) else None,
    )
    return {"model": model, "provider": provider, "context_length": int(length or 0)}


def _routes(adapter) -> list[tuple]:
    async def run(request, work):
        failure = adapter._check_auth(request)
        if failure:
            return failure
        try:
            return _json(await asyncio.to_thread(work))
        except Exception as exc:
            logger.exception("coma route %s failed", request.path)
            return _error(str(exc) or "That didn't work on the host.")

    async def skills(request):
        return await run(request, _skills)

    async def toggle(request):
        body = await request.json()
        return await run(request, lambda: _toggle_skill(str(body.get("name", "")), bool(body.get("enabled", True))))

    async def plugins(request):
        return await run(request, _plugins)

    async def connectors(request):
        return await run(request, _connectors)

    async def schema(request):
        return await run(request, _schema)

    async def config(request):
        return await run(request, _config)

    async def save(request):
        body = await request.json()
        incoming = body.get("config") if isinstance(body, dict) else None
        if not isinstance(incoming, dict):
            return _error("Send {\"config\": {...}}.", 400)
        return await run(request, lambda: _save_config(incoming))

    async def context(request):
        query = request.query
        return await run(request, lambda: _context(query.get("model", ""), query.get("provider", "")))

    return [
        ("GET", "/api/coma/skills", skills),
        ("POST", "/api/coma/skills/toggle", toggle),
        ("GET", "/api/coma/plugins", plugins),
        ("GET", "/api/coma/connectors", connectors),
        ("GET", "/api/coma/config/schema", schema),
        ("GET", "/api/coma/config", config),
        ("PUT", "/api/coma/config", save),
        ("GET", "/api/coma/context", context),
    ]


def install() -> bool:
    """Adds the routes to every API server this process starts. Safe to call more than once."""
    try:
        from gateway.platforms.api_server import APIServerAdapter
    except Exception:
        return False
    original = APIServerAdapter._http_route_table
    if getattr(original, "_coma", False):
        return True

    def table(self):
        return list(original(self)) + _routes(self)

    table._coma = True
    APIServerAdapter._http_route_table = table
    return True
