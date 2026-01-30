from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import typer
from rich.console import Console
from rich.json import JSON
from rich.table import Table

from .util import mk_envelope, read_messages, send
from .discovery import discover

app = typer.Typer(add_completion=False, help="yo - shell-first LAN client")
console = Console()

CONFIG_DIR = Path.home() / ".yo"
CONFIG_PATH = CONFIG_DIR / "config.json"


def _load_config() -> Dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def _save_config(cfg: Dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def _get_token() -> Optional[str]:
    if os.getenv("YO_TOKEN"):
        return os.getenv("YO_TOKEN")
    return _load_config().get("token")


def _cache_first(items) -> None:
    if not items:
        return
    cfg = _load_config()
    d0 = items[0]
    cfg.update({"host": d0.host, "port": d0.port, "node_id": d0.props.get("node_id", "yo.hub")})
    _save_config(cfg)


def _pick_hub(timeout_s: float = 2.0) -> Optional[Tuple[str, int, str]]:
    cfg = _load_config()
    if cfg.get("host") and cfg.get("port"):
        return (cfg["host"], int(cfg["port"]), cfg.get("node_id", "yo.hub"))

    items = discover(timeout_s=timeout_s)
    if not items:
        return None

    _cache_first(items)
    d = items[0]
    return (d.host, int(d.port), d.props.get("node_id", "yo.hub"))


async def _connect(timeout_s: float = 2.0, host: Optional[str] = None, port: Optional[int] = None):
    if host is None or port is None:
        picked = _pick_hub(timeout_s=timeout_s)
        if not picked:
            raise RuntimeError("No hub found. Start `yod run` or run `yo discover`.")
        host, port, _ = picked

    reader, writer = await asyncio.open_connection(host, int(port))
    _ = await reader.readline()  # hello
    return reader, writer


def _envelope(t: str, from_id: str, **fields: Any) -> Dict[str, Any]:
    msg = mk_envelope(t, from_id, **fields)
    tok = _get_token()
    if tok:
        msg["token"] = tok
    return msg


def _parse_value(raw: str) -> Any:
    s = raw.strip()
    if s == "":
        return ""
    if s.lower() in ("on", "true", "yes"):
        return True
    if s.lower() in ("off", "false", "no"):
        return False
    # JSON-ish
    if s[0] in "{[\"" or s.lower() in ("true", "false", "null") or s[0].isdigit() or s[0] == "-":
        try:
            return json.loads(s)
        except Exception:
            pass
    try:
        return int(s)
    except Exception:
        pass
    try:
        return float(s)
    except Exception:
        pass
    return s


def _stdin_value_if_any() -> Optional[str]:
    try:
        if sys.stdin is not None and not sys.stdin.isatty():
            data = sys.stdin.read()
            if data is None:
                return None
            return data.strip()
    except Exception:
        return None
    return None


@app.command("discover")
def discover_cmd(timeout_s: float = 2.0, cache_first: bool = True):
    items = discover(timeout_s=timeout_s)
    if not items:
        console.print("[yellow]No yo services found.[/yellow]")
        return
    table = Table(title="Discovered yo services")
    table.add_column("node_id")
    table.add_column("host")
    table.add_column("port")
    table.add_column("proto")
    for d in items:
        table.add_row(d.props.get("node_id","?"), d.host, str(d.port), d.props.get("proto","?"))
    console.print(table)
    if cache_first:
        _cache_first(items)


@app.command("use")
def use_cmd(node_id: str, timeout_s: float = 2.0):
    items = discover(timeout_s=timeout_s)
    matches = [d for d in items if d.props.get("node_id") == node_id]
    if not matches:
        console.print(f"[red]No discovered hub with node_id={node_id}[/red]")
        return
    d = matches[0]
    cfg = _load_config()
    cfg.update({"host": d.host, "port": d.port, "node_id": node_id})
    _save_config(cfg)
    console.print(f"[green]Using hub[/green] {node_id} at {d.host}:{d.port}")


@app.command("ls")
def ls_cmd(path: str = typer.Argument("/"), host: Optional[str] = None, port: Optional[int] = None, from_id: str = "person.local"):
    async def _run():
        r, w = await _connect(host=host, port=port)
        await send(w, _envelope("ls", from_id, path=path))
        msg = json.loads((await r.readline()).decode("utf-8"))
        for it in (msg.get("items") or []):
            console.print(it)
        w.close(); await w.wait_closed()
    asyncio.run(_run())


@app.command("cat")
def cat_cmd(path: str = typer.Argument(...), host: Optional[str] = None, port: Optional[int] = None, from_id: str = "person.local"):
    async def _run():
        r, w = await _connect(host=host, port=port)
        await send(w, _envelope("cat", from_id, path=path))
        msg = json.loads((await r.readline()).decode("utf-8"))
        console.print(JSON.from_data(msg))
        w.close(); await w.wait_closed()
    asyncio.run(_run())


@app.command("write")
def write_cmd(
    path: str = typer.Argument(...),
    value: Optional[str] = typer.Argument(None),
    dry_run: bool = typer.Option(False, "--dry-run"),
    host: Optional[str] = None,
    port: Optional[int] = None,
    from_id: str = "person.local",
):
    raw = value
    if raw is None:
        raw = _stdin_value_if_any()
        if raw is None:
            raise typer.BadParameter("Missing VALUE. Provide a value argument or pipe from stdin.")
    parsed = _parse_value(raw)

    async def _run():
        r, w = await _connect(host=host, port=port)
        await send(w, _envelope("write", from_id, path=path, value=parsed, dry_run=dry_run))
        msg = json.loads((await r.readline()).decode("utf-8"))
        console.print(JSON.from_data(msg))
        w.close(); await w.wait_closed()
    asyncio.run(_run())


@app.command("enable")
def enable_cmd(binding: str = typer.Argument(...), host: Optional[str] = None, port: Optional[int] = None, from_id: str = "person.local"):
    b = binding.strip()
    if b.isdigit():
        path = f"bindings/{b}/enabled"
    else:
        path = b.rstrip("/") + "/enabled" if not b.endswith("/enabled") else b

    async def _run():
        r, w = await _connect(host=host, port=port)
        await send(w, _envelope("write", from_id, path=path, value=True))
        msg = json.loads((await r.readline()).decode("utf-8"))
        console.print(JSON.from_data(msg))
        w.close(); await w.wait_closed()
    asyncio.run(_run())


@app.command("disable")
def disable_cmd(binding: str = typer.Argument(...), host: Optional[str] = None, port: Optional[int] = None, from_id: str = "person.local"):
    b = binding.strip()
    if b.isdigit():
        path = f"bindings/{b}/enabled"
    else:
        path = b.rstrip("/") + "/enabled" if not b.endswith("/enabled") else b

    async def _run():
        r, w = await _connect(host=host, port=port)
        await send(w, _envelope("write", from_id, path=path, value=False))
        msg = json.loads((await r.readline()).decode("utf-8"))
        console.print(JSON.from_data(msg))
        w.close(); await w.wait_closed()
    asyncio.run(_run())


@app.command("do")
def do_cmd(
    node_id: str = typer.Argument(...),
    action: str = typer.Argument(...),
    args_kv: List[str] = typer.Argument([]),
    dry_run: bool = typer.Option(False, "--dry-run"),
    host: Optional[str] = None,
    port: Optional[int] = None,
    from_id: str = "person.local",
):
    parsed: Dict[str, Any] = {}
    if len(args_kv) == 1 and "=" not in args_kv[0]:
        if action == "set_power":
            parsed = {"power": args_kv[0]}
        else:
            parsed = {"value": args_kv[0]}
    else:
        for kv in args_kv:
            if "=" in kv:
                k, v = kv.split("=", 1)
                parsed[k] = v

    async def _run():
        r, w = await _connect(host=host, port=port)
        await send(w, _envelope("command", from_id, to=node_id, action=action, args=parsed, dry_run=dry_run))
        msg = json.loads((await r.readline()).decode("utf-8"))
        console.print(JSON.from_data(msg))
        w.close(); await w.wait_closed()
    asyncio.run(_run())


@app.command("sub")
def sub_cmd(stream: str = typer.Argument(...), host: Optional[str] = None, port: Optional[int] = None, from_id: str = "person.local"):
    async def _run():
        r, w = await _connect(host=host, port=port)
        await send(w, _envelope("sub", from_id, stream=stream))
        ack = await r.readline()
        if ack:
            console.print(f"[dim]{ack.decode('utf-8').strip()}[/dim]")
        try:
            async for msg in read_messages(r):
                console.print(JSON.from_data(msg))
        finally:
            w.close(); await w.wait_closed()
    asyncio.run(_run())


@app.command("bind")
def bind_cmd(
    expr: str = typer.Argument(...),
    target: str = typer.Argument(...),
    dry_run: bool = typer.Option(False, "--dry-run"),
    host: Optional[str] = None,
    port: Optional[int] = None,
    from_id: str = "person.local",
):
    async def _run():
        r, w = await _connect(host=host, port=port)
        await send(w, _envelope("bind", from_id, expr=expr, target=target, dry_run=dry_run))
        msg = json.loads((await r.readline()).decode("utf-8"))
        console.print(JSON.from_data(msg))
        w.close(); await w.wait_closed()
    asyncio.run(_run())


@app.command("unbind")
def unbind_cmd(index: int = typer.Argument(...), host: Optional[str] = None, port: Optional[int] = None, from_id: str = "person.local"):
    async def _run():
        r, w = await _connect(host=host, port=port)
        await send(w, _envelope("unbind", from_id, index=index))
        msg = json.loads((await r.readline()).decode("utf-8"))
        console.print(JSON.from_data(msg))
        w.close(); await w.wait_closed()
    asyncio.run(_run())


@app.command("grant")
def grant_cmd(
    subject: str = typer.Argument(...),
    verb: str = typer.Argument(...),
    path_pat: str = typer.Argument(...),
    dry_run: bool = typer.Option(False, "--dry-run"),
    host: Optional[str] = None,
    port: Optional[int] = None,
    from_id: str = "person.admin",
):
    async def _run():
        r, w = await _connect(host=host, port=port)
        await send(w, _envelope("grant", from_id, subject=subject, verb=verb, path_pat=path_pat, dry_run=dry_run))
        msg = json.loads((await r.readline()).decode("utf-8"))
        console.print(JSON.from_data(msg))
        w.close(); await w.wait_closed()
    asyncio.run(_run())
