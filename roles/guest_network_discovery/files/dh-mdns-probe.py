#!/usr/bin/env python3
"""Verify A response for a guest from independent same-LAN mDNS, no DNS fallback."""
import argparse
import ipaddress
import socket
import struct
import sys
import time

GROUP = "224.0.0.251"
PORT = 5353


def name_at(packet, offset, visited=None):
    if visited is None:
        visited = set()
    parts = []
    end = None
    while True:
        if offset >= len(packet) or offset in visited:
            raise ValueError("INVALID_DNS_NAME")
        visited.add(offset)
        size = packet[offset]
        if size & 0xC0 == 0xC0:
            if offset + 1 >= len(packet):
                raise ValueError("INVALID_DNS_POINTER")
            pos = ((size & 0x3F) << 8) | packet[offset + 1]
            if end is None:
                end = offset + 2
            offset = pos
            continue
        if size & 0xC0 or size > 63:
            raise ValueError("INVALID_DNS_LABEL")
        offset += 1
        if size == 0:
            return ".".join(parts).lower(), (offset if end is None else end)
        if offset + size > len(packet):
            raise ValueError("TRUNCATED_DNS_LABEL")
        parts.append(packet[offset:offset + size].decode("ascii"))
        offset += size


def records(packet):
    if len(packet) < 12:
        raise ValueError("DNS_HEADER_TRUNCATED")
    _, flags, questions, answers, authority, additional = struct.unpack_from(
        "!HHHHHH", packet, 0)
    if flags & 0x8000 == 0 or flags & 0x000F:
        return []
    pos = 12
    for _ in range(questions):
        _, pos = name_at(packet, pos)
        if pos + 4 > len(packet):
            raise ValueError("TRUNCATED_DNS_QUESTION")
        pos += 4
    output = []
    for _ in range(answers + authority + additional):
        label, pos = name_at(packet, pos)
        if pos + 10 > len(packet):
            raise ValueError("TRUNCATED_DNS_RECORD")
        kind, cls, _, length = struct.unpack_from("!HHIH", packet, pos)
        pos += 10
        if pos + length > len(packet):
            raise ValueError("TRUNCATED_DNS_DATA")
        if kind == 1 and (cls & 0x7FFF) == 1 and length == 4:
            output.append((label, str(ipaddress.IPv4Address(packet[pos:pos + 4]))))
        pos += length
    return output


def probe(host, target, cidr):
    net = ipaddress.IPv4Network(cidr, strict=False)
    ip = ipaddress.IPv4Address(target)
    if ip not in net or not host.endswith(".local") or host.count(".") != 1:
        raise ValueError("GUEST_TARGET_SCOPE_INVALID")
    labels = host.split(".")
    qname = b"".join(bytes([len(label)]) + label.encode("ascii") for label in labels) + b"\0"
    # QU bit requests a unicast answer to our ephemeral source port (RFC 6762).
    message = struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0) + qname + struct.pack(
        "!HH", 1, 0x8001)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.connect((target, 9))
        local = sock.getsockname()[0]
    if ipaddress.IPv4Address(local) not in net or local == target:
        raise ValueError("INDEPENDENT_LAN_PROBE_REQUIRED")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.bind((local, 0))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local))
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            sock.sendto(message, (GROUP, PORT))
            finish = min(deadline, time.monotonic() + 1.2)
            while time.monotonic() < finish:
                sock.settimeout(max(.1, finish - time.monotonic()))
                try:
                    data, source = sock.recvfrom(9000)
                except socket.timeout:
                    break
                if source[0] != target:
                    continue
                try:
                    if (host, target) in records(data):
                        return True
                except (ValueError, UnicodeError):
                    continue
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--ip", required=True)
    parser.add_argument("--cidr", required=True)
    args = parser.parse_args()
    if not probe(args.name, args.ip, args.cidr):
        raise ValueError("NO_GUEST_MDNS_ANSWER")
    print("GUEST_MDNS=VERIFIED")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, UnicodeError):
        print("GUEST_MDNS=BLOCKED", file=sys.stderr)
        sys.exit(2)
