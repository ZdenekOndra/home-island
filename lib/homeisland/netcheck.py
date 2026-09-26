"""Network probes with short, hard timeouts.

Nothing here may block for long: the collector and the dashboard must stay
responsive when the WAN is down or DNS upstreams are unreachable.
"""

from __future__ import annotations

import http.client
import random
import shutil
import socket
import struct
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple

from .util import try_output


def tcp_probe(host: str, port: int, timeout: float = 2.0) -> Optional[float]:
    """Return connect latency in ms, or None if unreachable."""
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (time.monotonic() - start) * 1000
    except OSError:
        return None


def parse_targets(spec: str) -> List[Tuple[str, int]]:
    targets = []
    for item in spec.replace(",", " ").split():
        host, _, port = item.rpartition(":")
        if not host:
            host, port = item, "443"
        try:
            targets.append((host, int(port)))
        except ValueError:
            continue
    return targets


def wan_check(targets: List[Tuple[str, int]], timeout: float = 2.5) -> dict:
    """Probe several independent Internet hosts in parallel.

    The WAN counts as online if any target answers, so no single third-party
    service decides the result. Only a TCP handshake is made; no data is sent.
    """
    if not targets:
        return {"state": "unknown", "reachable": 0, "total": 0, "latency_ms": None, "checked_at": int(time.time())}
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        results = list(pool.map(lambda t: tcp_probe(t[0], t[1], timeout), targets))
    ok = [r for r in results if r is not None]
    return {
        "state": "online" if ok else "offline",
        "reachable": len(ok),
        "total": len(targets),
        "latency_ms": round(min(ok), 1) if ok else None,
        "checked_at": int(time.time()),
    }


def default_gateway() -> Optional[str]:
    """Read the IPv4 default gateway from /proc/net/route."""
    try:
        with open("/proc/net/route", "r") as fh:
            next(fh)
            for line in fh:
                fields = line.split()
                if len(fields) >= 3 and fields[1] == "00000000" and int(fields[3], 16) & 2:
                    return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    except (OSError, StopIteration, ValueError):
        pass
    return None


def ping(host: str, timeout: int = 1) -> Optional[bool]:
    """Return True/False, or None if ping is not available."""
    if shutil.which("ping") is None:
        return None
    try:
        res = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return res.returncode == 0


def local_ipv4_addresses() -> List[str]:
    out = try_output(["ip", "-4", "-o", "addr", "show", "scope", "global"], timeout=3) or ""
    addrs = []
    for line in out.splitlines():
        parts = line.split()
        if "inet" in parts:
            addrs.append(parts[parts.index("inet") + 1].split("/")[0])
    return addrs


# -- minimal DNS client (A records only) ---------------------------------------


def _encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode("idna") if label else b""
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def _skip_name(msg: bytes, offset: int) -> int:
    while True:
        length = msg[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:
            return offset + 2
        offset += length + 1


def dns_query(server: str, name: str, port: int = 53, timeout: float = 2.0) -> Tuple[str, List[str]]:
    """Query an A record over UDP.

    Returns (rcode, addresses) where rcode is "NOERROR", "NXDOMAIN", "SERVFAIL",
    "REFUSED", "TIMEOUT" or "ERROR".
    """
    qid = random.randint(0, 0xFFFF)
    packet = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + _encode_name(name) + struct.pack(">HH", 1, 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, (server, port))
        deadline = time.monotonic() + timeout
        while True:
            sock.settimeout(max(0.05, deadline - time.monotonic()))
            data, _ = sock.recvfrom(4096)
            if len(data) >= 12 and struct.unpack(">H", data[:2])[0] == qid:
                break
    except socket.timeout:
        return "TIMEOUT", []
    except OSError:
        return "ERROR", []
    finally:
        sock.close()
    flags, qd, an = struct.unpack(">HHH", data[2:8])
    rcode = {0: "NOERROR", 2: "SERVFAIL", 3: "NXDOMAIN", 5: "REFUSED"}.get(flags & 0xF, "ERROR")
    offset = 12
    try:
        for _ in range(qd):
            offset = _skip_name(data, offset) + 4
        addrs = []
        for _ in range(an):
            offset = _skip_name(data, offset)
            rtype, _rclass, _ttl, rdlen = struct.unpack(">HHIH", data[offset:offset + 10])
            offset += 10
            if rtype == 1 and rdlen == 4:
                addrs.append(socket.inet_ntoa(data[offset:offset + 4]))
            offset += rdlen
    except (IndexError, struct.error):
        return "ERROR", []
    return rcode, addrs


# -- HTTP ------------------------------------------------------------------------


def http_probe(
    ip: str, port: int, host_header: Optional[str], path: str = "/", timeout: float = 3.0, read_limit: int = 65536
) -> Tuple[Optional[int], str, Optional[float]]:
    """GET a URL on a specific IP. Returns (status, body_prefix, latency_ms)."""
    start = time.monotonic()
    conn = http.client.HTTPConnection(ip, port, timeout=timeout)
    try:
        headers = {"User-Agent": "homeisland-check", "Connection": "close"}
        if host_header:
            headers["Host"] = host_header
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = resp.read(read_limit).decode("utf-8", errors="replace")
        return resp.status, body, (time.monotonic() - start) * 1000
    except (OSError, http.client.HTTPException):
        return None, "", None
    finally:
        conn.close()
