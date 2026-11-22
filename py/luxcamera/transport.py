"""Standard-library transport for the Python runtime (and SSDP helpers)."""
from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


@dataclass
class Response:
    status: int
    headers: dict = field(default_factory=dict)  # lowercase names
    body: str = ""


class UrllibTransport:
    def _do(self, req, timeout) -> Response:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return Response(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            return Response(e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read().decode("utf-8", "replace"))

    def get(self, url: str, timeout: float, headers: dict) -> Response:
        return self._do(urllib.request.Request(url, headers=headers), timeout)

    def post(self, url: str, body: str, timeout: float, headers: dict) -> Response:
        return self._do(urllib.request.Request(url, body.encode("utf-8"), headers=headers, method="POST"), timeout)

    def open(self, url: str, timeout: float, headers: dict):
        return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)

    def connect(self, host: str, port: int, timeout: float) -> None:
        socket.create_connection((host, port), timeout=timeout).close()


def ssdp_search(request: str, to: tuple = ("239.255.255.250", 1900), timeout: float = 1.5) -> list[tuple[str, str]]:
    """Send one M-SEARCH (multicast, or unicast to (ip, port)); returns [(sender ip, reply text)].
    Docker NAT drops multicast replies, so in a container only unicast answers."""
    out: list[tuple[str, str]] = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(0.3)
        s.sendto(request.replace("{ip}", to[0]).encode(), to)
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = s.recvfrom(8192)
            except socket.timeout:
                continue
            out.append((addr[0], data.decode("utf-8", "replace")))
    except OSError:
        pass
    finally:
        s.close()
    return out
