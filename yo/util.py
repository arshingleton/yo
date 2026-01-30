from __future__ import annotations
import asyncio, json, uuid, time
from dataclasses import dataclass
from typing import Any, Dict, AsyncIterator

PROTO_VERSION = "0.1"

def now_ms() -> int:
    return int(time.time() * 1000)

def new_msg_id() -> str:
    return str(uuid.uuid4())

def dumps(msg: Dict[str, Any]) -> bytes:
    return (json.dumps(msg, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")

def loads(line: bytes) -> Dict[str, Any]:
    return json.loads(line.decode("utf-8"))

async def read_messages(reader: asyncio.StreamReader) -> AsyncIterator[Dict[str, Any]]:
    while True:
        line = await reader.readline()
        if not line:
            return
        try:
            yield loads(line)
        except Exception:
            continue

async def send(writer: asyncio.StreamWriter, msg: Dict[str, Any]) -> None:
    writer.write(dumps(msg))
    await writer.drain()

def mk_envelope(t: str, from_id: str, **fields: Any) -> Dict[str, Any]:
    msg = {"t": t, "v": PROTO_VERSION, "id": new_msg_id(), "ts": now_ms(), "from": from_id}
    msg.update(fields)
    return msg
