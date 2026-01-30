from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from zeroconf import IPVersion, ServiceInfo, Zeroconf, ServiceBrowser
from zeroconf.asyncio import AsyncZeroconf

from .util import PROTO_VERSION

SERVICE_TYPE = "_yo._tcp.local."

@dataclass
class Discovered:
    name: str
    host: str
    port: int
    props: Dict[str, str]

def _best_effort_ip() -> str:
    # Prefer a LAN-routable address if possible
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return socket.gethostbyname(socket.gethostname())

async def advertise_async(node_id: str, port: int) -> Tuple[AsyncZeroconf, ServiceInfo]:
    """Advertise the daemon via mDNS. Must be called from a running asyncio loop."""
    azc = AsyncZeroconf(ip_version=IPVersion.V4Only)
    hostname = socket.gethostname()
    ip = _best_effort_ip()
    props = {"node_id": node_id, "proto": PROTO_VERSION}
    info = ServiceInfo(
        SERVICE_TYPE,
        f"{node_id}.{SERVICE_TYPE}",
        addresses=[socket.inet_aton(ip)],
        port=port,
        properties={k: v.encode("utf-8") for k, v in props.items()},
        server=f"{hostname}.local.",
    )
    await azc.async_register_service(info)
    return azc, info

async def unadvertise_async(azc: AsyncZeroconf, info: ServiceInfo) -> None:
    try:
        await azc.async_unregister_service(info)
    finally:
        await azc.async_close()

class _Listener:
    def __init__(self):
        self.found: Dict[str, Discovered] = {}

    def add_service(self, zc: Zeroconf, type_: str, name: str):
        info = zc.get_service_info(type_, name)
        if not info or not info.addresses:
            return
        host = socket.inet_ntoa(info.addresses[0])
        props = {k.decode("utf-8"): v.decode("utf-8") for k, v in (info.properties or {}).items()}
        self.found[name] = Discovered(name=name, host=host, port=info.port, props=props)

    def remove_service(self, zc: Zeroconf, type_: str, name: str):
        self.found.pop(name, None)

    def update_service(self, zc, type_, name):
        # zeroconf may call this when a service's TXT records change
        # v0 doesn't care, but we implement it to satisfy future versions
        self.add_service(zc, type_, name)


def discover(timeout_s: float = 2.0) -> List[Discovered]:
    """Synchronous discovery for CLI use."""
    zc = Zeroconf(ip_version=IPVersion.V4Only)
    listener = _Listener()
    ServiceBrowser(zc, SERVICE_TYPE, listener)
    time.sleep(timeout_s)
    zc.close()
    return list(listener.found.values())
