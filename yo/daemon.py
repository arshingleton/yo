from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import typer
from rich.console import Console

from .util import mk_envelope, read_messages, send, now_ms
from .discovery import advertise_async, unadvertise_async

app = typer.Typer(add_completion=False, help="yo daemon (yod)")
console = Console()

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 7331
DEFAULT_NODE_ID = "yo.hub"


@dataclass
class Binding:
    expr: str                 # "<stream-pattern>:<key>==<value>"
    target: str               # "<node_id>:<action>(args)"
    enabled: bool = True
    min_interval_ms: int = 1000
    _last_fired_ms: int = 0   # runtime only


@dataclass
class Rule:
    subject: str
    verb: str
    path_pat: str


class _GPIOLed:
    def __init__(self, pin: int):
        self.pin = pin
        self._impl = None
        self._kind = None

        try:
            from gpiozero import LED  # type: ignore
            self._impl = LED(pin)
            self._kind = "gpiozero"
            return
        except Exception:
            pass

        try:
            import RPi.GPIO as GPIO  # type: ignore
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(pin, GPIO.OUT)
            self._impl = GPIO
            self._kind = "rpigpio"
            return
        except Exception:
            pass

        raise RuntimeError("No GPIO backend found. Install gpiozero or run on Raspberry Pi with RPi.GPIO.")

    def set(self, on: bool) -> None:
        if self._kind == "gpiozero":
            if on:
                self._impl.on()
            else:
                self._impl.off()
        elif self._kind == "rpigpio":
            self._impl.output(self.pin, bool(on))


class Hub:
    def __init__(
        self,
        node_id: str,
        auth_enabled: bool,
        state_path: Path,
        gpio_pin: Optional[int] = None,
        gpio_node: str = "devices/led.living_room",
    ):
        self.node_id = node_id
        self.auth_enabled = auth_enabled
        self.state_path = state_path

        self.subs: Dict[asyncio.StreamWriter, List[str]] = {}

        self.bindings: List[Binding] = []
        self.rules: List[Rule] = []
        self.admin_token: Optional[str] = None

        self.devices: Dict[str, Dict[str, Any]] = {
            "devices/light.kitchen": {"power": "off", "brightness": 50},
            "devices/motion.living_room": {"motion": False},
        }
        self.actions: Dict[str, List[str]] = {
            "devices/light.kitchen": ["set_power", "set_brightness"],
            "devices/motion.living_room": [],
        }
        self.streams: Dict[str, List[str]] = {
            "devices/light.kitchen": ["devices/light.kitchen/state"],
            "devices/motion.living_room": ["devices/motion.living_room/events"],
        }

        self._gpio_led: Optional[_GPIOLed] = None
        self._gpio_node: Optional[str] = None
        if gpio_pin is not None:
            try:
                self._gpio_led = _GPIOLed(gpio_pin)
                self._gpio_node = gpio_node
                self.devices[gpio_node] = {"power": "off"}
                self.actions[gpio_node] = ["set_power"]
                self.streams[gpio_node] = [f"{gpio_node}/state"]
                console.print(f"[green]GPIO LED enabled[/green] pin={gpio_pin} node={gpio_node}")
            except Exception as e:
                console.print(f"[yellow]GPIO requested but unavailable:[/yellow] {e}")

        # Chat + Web (filesystem-native resources)
        self.chat_rooms: Dict[str, List[Dict[str, Any]]] = {"general": []}
        self.web_pages: Dict[str, str] = {"status": "# yo\n\nlocal-first page.\n"}

        if self.auth_enabled:
            import secrets
            self.admin_token = secrets.token_urlsafe(32)
            self.rules.append(Rule(subject="__token_admin__", verb="admin", path_pat="*"))

        self._load_state()

    def _load_state(self) -> None:
        try:
            if not self.state_path.exists():
                return
            data = json.loads(self.state_path.read_text())

            self.bindings = []
            for b in data.get("bindings", []):
                self.bindings.append(
                    Binding(
                        expr=b["expr"],
                        target=b["target"],
                        enabled=bool(b.get("enabled", True)),
                        min_interval_ms=int(b.get("min_interval_ms", 1000)),
                    )
                )

            for r in data.get("rules", []):
                if isinstance(r, dict) and {"subject", "verb", "path_pat"} <= set(r.keys()):
                    self.rules.append(Rule(subject=r["subject"], verb=r["verb"], path_pat=r["path_pat"]))

            cr = data.get("chat_rooms")
            if isinstance(cr, dict):
                for room, msgs in cr.items():
                    if isinstance(room, str) and isinstance(msgs, list):
                        cleaned: List[Dict[str, Any]] = []
                        for m in msgs[-500:]:
                            if isinstance(m, dict):
                                cleaned.append(m)
                        self.chat_rooms[room] = cleaned

            wp = data.get("web_pages")
            if isinstance(wp, dict):
                for name, content in wp.items():
                    if isinstance(name, str) and isinstance(content, str):
                        self.web_pages[name] = content

        except Exception:
            return

    def _save_state(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "v": "0.1.0",
                "bindings": [
                    {"expr": b.expr, "target": b.target, "enabled": b.enabled, "min_interval_ms": b.min_interval_ms}
                    for b in self.bindings
                ],
                "rules": [asdict(r) for r in self.rules],
                "chat_rooms": self.chat_rooms,
                "web_pages": self.web_pages,
            }
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2))
            tmp.replace(self.state_path)
        except Exception:
            return

    def _glob_match(self, pat: str, s: str) -> bool:
        pat_re = "^" + re.escape(pat).replace(r"\*", ".*") + "$"
        return re.match(pat_re, s) is not None

    def _authz(self, msg: Dict[str, Any], verb: str, path: str) -> Tuple[bool, str]:
        if not self.auth_enabled:
            return True, "auth_disabled"
        if verb == "read":
            return True, "read_open"
        token = msg.get("token")
        if token and self.admin_token and token == self.admin_token:
            return True, "admin_token"
        return False, "denied"

    # ---------- filesystem ----------

    def ls(self, path: str) -> List[str]:
        p = (path or "").strip("/")
        if p == "":
            return ["devices", "bindings", "auth", "chat", "web"]

        if p == "devices":
            return sorted(self.devices.keys())
        if p.startswith("devices/"):
            nid = p
            if nid in self.devices:
                leaves = ["state", "actions", "streams"]
                st = self.devices.get(nid, {})
                if "power" in st:
                    leaves.append("power")
                if "brightness" in st:
                    leaves.append("brightness")
                return leaves

        if p == "bindings":
            return [str(i) for i in range(len(self.bindings))]
        if p.startswith("bindings/"):
            try:
                i = int(p.split("/", 1)[1].split("/", 1)[0])
                if 0 <= i < len(self.bindings):
                    return ["expr", "target", "enabled", "min_interval_ms"]
            except Exception:
                pass

        if p == "auth":
            return ["enabled"]

        if p == "chat":
            return ["rooms"]
        if p == "chat/rooms":
            return sorted(self.chat_rooms.keys())
        if p.startswith("chat/rooms/"):
            room = p.split("/", 2)[2]
            if room in self.chat_rooms:
                return ["history", "post", "events"]

        if p == "web":
            return ["pages"]
        if p == "web/pages":
            return sorted(self.web_pages.keys())
        if p.startswith("web/pages/"):
            name = p.split("/", 2)[2]
            if name in self.web_pages:
                return ["content", "events"]

        return []

    def cat(self, path: str) -> Any:
        p = (path or "").strip("/")

        if p == "auth/enabled":
            return {"enabled": self.auth_enabled}

        if p.endswith("/state"):
            nid = p[:-len("/state")]
            return self.devices.get(nid)
        if p.endswith("/actions"):
            nid = p[:-len("/actions")]
            return self.actions.get(nid, [])
        if p.endswith("/streams"):
            nid = p[:-len("/streams")]
            return self.streams.get(nid, [])
        if p.endswith("/power"):
            nid = p[:-len("/power")]
            return {"power": (self.devices.get(nid, {}) or {}).get("power")}
        if p.endswith("/brightness"):
            nid = p[:-len("/brightness")]
            return {"brightness": (self.devices.get(nid, {}) or {}).get("brightness")}

        if p.startswith("bindings/"):
            parts = p.split("/")
            try:
                i = int(parts[1])
                if 0 <= i < len(self.bindings):
                    b = self.bindings[i]
                    if len(parts) == 2:
                        return {"index": i, "expr": b.expr, "target": b.target, "enabled": b.enabled, "min_interval_ms": b.min_interval_ms}
                    if parts[2] == "enabled":
                        return {"enabled": b.enabled}
                    if parts[2] == "expr":
                        return {"expr": b.expr}
                    if parts[2] == "target":
                        return {"target": b.target}
                    if parts[2] == "min_interval_ms":
                        return {"min_interval_ms": b.min_interval_ms}
            except Exception:
                pass

        if p.startswith("chat/rooms/") and p.endswith("/history"):
            room = p.split("/")[2]
            msgs = self.chat_rooms.get(room, [])
            return {"room": room, "messages": msgs[-50:]}

        if p.startswith("web/pages/") and (p.endswith("/content") or p.count("/") == 2):
            name = p.split("/")[2]
            return {"page": name, "content": self.web_pages.get(name, "")}

        return None

    async def write_path(self, path: str, value: Any, dry_run: bool = False, from_id: str = "person.local") -> Dict[str, Any]:
        p = (path or "").strip("/")

        if p.startswith("bindings/") and p.endswith("/enabled"):
            try:
                i = int(p.split("/")[1])
                if not (0 <= i < len(self.bindings)):
                    return {"ok": False, "error": "no_such_binding"}
                want = bool(value) if isinstance(value, bool) else str(value).lower() in ("1", "true", "on", "yes")
                if dry_run:
                    return {"ok": True, "dry_run": True, "would_set": {"binding": i, "enabled": want}}
                self.bindings[i].enabled = want
                self._save_state()
                return {"ok": True, "binding": i, "enabled": want}
            except Exception:
                return {"ok": False, "error": "bad_path"}

        if p.startswith("devices/") and p.endswith("/power"):
            nid = p[:-len("/power")]
            return await self.run_action(nid, "set_power", {"power": value}, dry_run=dry_run)

        if p.startswith("devices/") and p.endswith("/brightness"):
            nid = p[:-len("/brightness")]
            return await self.run_action(nid, "set_brightness", {"brightness": value}, dry_run=dry_run)

        if p.startswith("chat/rooms/") and p.endswith("/post"):
            room = p.split("/")[2]
            if dry_run:
                return {"ok": True, "dry_run": True, "would_post": {"room": room, "value": value}}
            payload = value if isinstance(value, dict) else {"text": str(value)}
            msg = {
                "id": f"msg_{now_ms()}",
                "ts": now_ms(),
                "from": payload.get("from", from_id),
                "text": payload.get("text", ""),
                "meta": {k: v for k, v in payload.items() if k not in ("from", "text")},
            }
            self.chat_rooms.setdefault(room, []).append(msg)
            self.chat_rooms[room] = self.chat_rooms[room][-500:]
            self._save_state()
            await self.publish(f"chat/rooms/{room}/events", {"message": msg})
            return {"ok": True, "room": room, "posted": msg}

        if p.startswith("web/pages/") and p.endswith("/content"):
            name = p.split("/")[2]
            content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            if dry_run:
                return {"ok": True, "dry_run": True, "would_set": {"page": name, "bytes": len(content)}}
            self.web_pages[name] = content
            self._save_state()
            await self.publish(f"web/pages/{name}/events", {"page": name, "updated": True, "bytes": len(content)})
            return {"ok": True, "page": name, "bytes": len(content)}

        return {"ok": False, "error": "not_writable"}

    async def publish(self, stream: str, data: Dict[str, Any]) -> None:
        msg = mk_envelope("event", self.node_id, stream=stream, data=data)

        for w, pats in list(self.subs.items()):
            if any(self._glob_match(p, stream) for p in pats):
                try:
                    await send(w, msg)
                except Exception:
                    self.subs.pop(w, None)

        await self._eval_bindings(stream, data)

    async def _eval_bindings(self, stream: str, data: Dict[str, Any]) -> None:
        now = now_ms()
        for b in list(self.bindings):
            if not b.enabled:
                continue
            if now - int(b._last_fired_ms) < int(b.min_interval_ms):
                continue
            try:
                stream_pat, cond = b.expr.split(":", 1)
                if not self._glob_match(stream_pat, stream):
                    continue

                key, rhs = cond.split("==", 1)
                key = key.strip()
                rhs_raw = rhs.strip()

                val = data.get(key)
                want: Any = rhs_raw
                if rhs_raw.lower() in ("true", "false"):
                    want = rhs_raw.lower() == "true"
                else:
                    try:
                        want = int(rhs_raw)
                    except Exception:
                        try:
                            want = float(rhs_raw)
                        except Exception:
                            want = rhs_raw.strip('"').strip("'")

                if val == want:
                    await self._execute_target(b.target)
                    b._last_fired_ms = now
            except Exception:
                continue

    async def _execute_target(self, target: str) -> None:
        try:
            node_part, rest = target.split(":", 1)
            action, args_part = rest.split("(", 1)
            args_part = args_part.rsplit(")", 1)[0].strip()

            args: Dict[str, Any] = {}
            if args_part:
                if "=" not in args_part and "," not in args_part:
                    args = {"power": args_part}
                else:
                    for piece in args_part.split(","):
                        k, v = piece.split("=", 1)
                        args[k.strip()] = v.strip().strip('"').strip("'")

            await self.run_action(node_part.strip(), action.strip(), args)
        except Exception:
            return

    async def run_action(self, node_id: str, action: str, args: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
        if node_id not in self.devices:
            return {"ok": False, "error": "unknown_node"}

        def _as_bool(x: Any) -> bool:
            if isinstance(x, bool):
                return x
            return str(x).lower() in ("1", "true", "on", "yes")

        if node_id == "devices/light.kitchen":
            if action == "set_power":
                newv = str(args.get("power", "off")).lower()
                if dry_run:
                    return {"ok": True, "dry_run": True, "would_set": {"node": node_id, "power": newv}}
                self.devices[node_id]["power"] = newv
                await self.publish("devices/light.kitchen/state", dict(self.devices[node_id]))
                return {"ok": True, "state": dict(self.devices[node_id])}

            if action == "set_brightness":
                try:
                    b = int(args.get("brightness", args.get("value", 50)))
                    b = max(0, min(100, b))
                except Exception:
                    b = int(self.devices[node_id].get("brightness", 50))
                if dry_run:
                    return {"ok": True, "dry_run": True, "would_set": {"node": node_id, "brightness": b}}
                self.devices[node_id]["brightness"] = b
                await self.publish("devices/light.kitchen/state", dict(self.devices[node_id]))
                return {"ok": True, "state": dict(self.devices[node_id])}

            return {"ok": False, "error": "unknown_action"}

        if self._gpio_node and node_id == self._gpio_node:
            if action == "set_power":
                on = _as_bool(args.get("power", "off"))
                if dry_run:
                    return {"ok": True, "dry_run": True, "would_set": {"node": node_id, "power": "on" if on else "off"}}
                if self._gpio_led:
                    self._gpio_led.set(on)
                self.devices[node_id]["power"] = "on" if on else "off"
                await self.publish(f"{node_id}/state", {"power": self.devices[node_id]["power"]})
                return {"ok": True, "state": dict(self.devices[node_id])}
            return {"ok": False, "error": "unknown_action"}

        return {"ok": False, "error": "unknown_action"}

    def add_rule(self, subject: str, verb: str, path_pat: str) -> None:
        self.rules.append(Rule(subject=subject, verb=verb, path_pat=path_pat))
        self._save_state()


async def _sim_motion(hub: Hub):
    while True:
        await asyncio.sleep(random.uniform(7, 14))
        nid = "devices/motion.living_room"
        hub.devices[nid]["motion"] = not bool(hub.devices[nid]["motion"])
        await hub.publish("devices/motion.living_room/events", {"motion": hub.devices[nid]["motion"]})


async def handle(hub: Hub, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    await send(writer, mk_envelope("hello", hub.node_id, node_id=hub.node_id, auth=hub.auth_enabled))
    hub.subs.setdefault(writer, [])

    try:
        async for msg in read_messages(reader):
            t = msg.get("t")

            if t == "sub":
                hub.subs[writer].append(str(msg.get("stream", "")))
                await send(writer, mk_envelope("sub", hub.node_id, ok=True, stream=msg.get("stream", "")))
                continue

            if t == "ls":
                path = str(msg.get("path", ""))
                ok, why = hub._authz(msg, "read", path)
                items = hub.ls(path) if ok else []
                await send(writer, mk_envelope("ls", hub.node_id, ok=ok, why=why, path=path, items=items))
                continue

            if t == "cat":
                path = str(msg.get("path", ""))
                ok, why = hub._authz(msg, "read", path)
                data = hub.cat(path) if ok else None
                await send(writer, mk_envelope("cat", hub.node_id, ok=ok, why=why, path=path, data=data))
                continue

            if t == "write":
                path = str(msg.get("path", ""))
                ok, why = hub._authz(msg, "write", path)
                if not ok:
                    await send(writer, mk_envelope("write", hub.node_id, ok=False, why=why, error="denied"))
                    continue
                out = await hub.write_path(path, msg.get("value"), dry_run=bool(msg.get("dry_run", False)), from_id=str(msg.get("from", "person.local")))
                await send(writer, mk_envelope("write", hub.node_id, ok=bool(out.get("ok", False)), data=out))
                continue

            if t == "command":
                to = str(msg.get("to", ""))
                ok, why = hub._authz(msg, "write", to)
                if not ok:
                    await send(writer, mk_envelope("result", hub.node_id, ok=False, why=why, error="denied"))
                    continue
                out = await hub.run_action(to, str(msg.get("action", "")), msg.get("args", {}) or {}, dry_run=bool(msg.get("dry_run", False)))
                await send(writer, mk_envelope("result", hub.node_id, ok=bool(out.get("ok", False)), data=out))
                continue

            if t == "bind":
                ok, why = hub._authz(msg, "bind", "bindings/*")
                if not ok:
                    await send(writer, mk_envelope("bind", hub.node_id, ok=False, why=why, error="denied"))
                    continue
                expr = str(msg.get("expr", ""))
                target = str(msg.get("target", ""))
                if bool(msg.get("dry_run", False)):
                    await send(writer, mk_envelope("bind", hub.node_id, ok=True, dry_run=True, would_add={"expr": expr, "target": target}))
                    continue
                hub.bindings.append(Binding(expr=expr, target=target))
                hub._save_state()
                await send(writer, mk_envelope("bind", hub.node_id, ok=True, index=len(hub.bindings) - 1, expr=expr, target=target))
                continue

            if t == "unbind":
                ok, why = hub._authz(msg, "bind", "bindings/*")
                if not ok:
                    await send(writer, mk_envelope("unbind", hub.node_id, ok=False, why=why, error="denied"))
                    continue
                idx = msg.get("index")
                ok2 = False
                if isinstance(idx, int) and 0 <= idx < len(hub.bindings):
                    hub.bindings.pop(idx)
                    hub._save_state()
                    ok2 = True
                await send(writer, mk_envelope("unbind", hub.node_id, ok=ok2, index=idx))
                continue

            if t == "grant":
                ok, why = hub._authz(msg, "admin", "auth/rules")
                if not ok:
                    await send(writer, mk_envelope("grant", hub.node_id, ok=False, why=why, error="denied"))
                    continue
                subject = str(msg.get("subject", "person.unknown"))
                verb = str(msg.get("verb", "read"))
                path_pat = str(msg.get("path_pat", "*"))
                if bool(msg.get("dry_run", False)):
                    await send(writer, mk_envelope("grant", hub.node_id, ok=True, dry_run=True, would_add={"subject": subject, "verb": verb, "path_pat": path_pat}))
                    continue
                hub.add_rule(subject, verb, path_pat)
                await send(writer, mk_envelope("grant", hub.node_id, ok=True, rule={"subject": subject, "verb": verb, "path_pat": path_pat}))
                continue

            await send(writer, mk_envelope("error", hub.node_id, error="unknown_type"))

    finally:
        hub.subs.pop(writer, None)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    host: str = typer.Option(DEFAULT_HOST, help="Bind host"),
    port: int = typer.Option(DEFAULT_PORT, help="TCP port"),
    node_id: str = typer.Option(DEFAULT_NODE_ID, help="Daemon node id"),
    auth: bool = typer.Option(False, help="Enable token auth (prints admin token at startup)"),
    state_path: Optional[Path] = typer.Option(None, help="Path to hub state JSON (bindings/rules/chat/web). Default: ~/.yo/hub/<node_id>/state.json"),
    gpio_led_pin: Optional[int] = typer.Option(None, help="Enable a real GPIO LED device on this host (BCM pin)"),
    gpio_led_node: str = typer.Option("devices/led.living_room", help="Node id for the GPIO LED device"),
):
    if ctx.invoked_subcommand is None:
        run(host=host, port=port, node_id=node_id, auth=auth, state_path=state_path, gpio_led_pin=gpio_led_pin, gpio_led_node=gpio_led_node)


@app.command()
def run(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    node_id: str = DEFAULT_NODE_ID,
    auth: bool = False,
    state_path: Optional[Path] = None,
    gpio_led_pin: Optional[int] = None,
    gpio_led_node: str = "devices/led.living_room",
):
    async def main():
        sp = state_path or (Path.home() / ".yo" / "hub" / node_id / "state.json")
        hub = Hub(node_id=node_id, auth_enabled=auth, state_path=sp, gpio_pin=gpio_led_pin, gpio_node=gpio_led_node)

        azc, info = await advertise_async(hub.node_id, port)

        server = await asyncio.start_server(lambda r, w: handle(hub, r, w), host=host, port=port)
        addrs = ", ".join(str(s.getsockname()) for s in (server.sockets or []))
        console.print(f"[bold green]yod[/] listening on {addrs} as [bold]{hub.node_id}[/]")
        console.print("mDNS: advertising _yo._tcp.local")
        console.print(f"[dim]state:[/dim] {hub.state_path}")
        if hub.auth_enabled:
            console.print(f"[bold yellow]AUTH ENABLED[/] admin token: [bold]{hub.admin_token}[/]")

        sim_task = asyncio.create_task(_sim_motion(hub))
        try:
            async with server:
                await server.serve_forever()
        finally:
            sim_task.cancel()
            try:
                await sim_task
            except Exception:
                pass
            await unadvertise_async(azc, info)

    asyncio.run(main())
