"""Discover MiIO devices via UDP broadcast and print raw response fields.

Sends the standard MiIO hello packet, then uses python-miio's Message.parse()
to decode each response. Prints IP, DID, token, and timestamp for every
responding device.

The hello response's checksum field IS the device token in plaintext —
no app or cloud access needed to retrieve it.

Usage:
    python tests/miio/test_discover.py <broadcast> [timeout]

Arguments:
    broadcast   Broadcast address (e.g. 192.168.1.255)
    timeout     Seconds to wait for responses (default: 5)

Example:
    python tests/miio/test_discover.py 192.168.1.255
    python tests/miio/test_discover.py 192.168.1.255 10
"""

import binascii
import codecs
import socket
import sys

from miio.protocol import Message

_MIIO_PORT = 54321
_HELLO = bytes.fromhex("21310020" + "ff" * 28)


def discover(broadcast: str, timeout: int = 5) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)

        for _ in range(3):
            sock.sendto(_HELLO, (broadcast, _MIIO_PORT))
        print(f"Sent hello to {broadcast}:{_MIIO_PORT}, waiting {timeout}s...\n")

        seen: set[str] = set()
        while True:
            try:
                data, addr = sock.recvfrom(1024)
                ip = addr[0]
                if ip in seen:
                    continue
                seen.add(ip)

                print(f"{'='*50}")
                print(f"  IP:         {ip}")
                print(f"  raw bytes:  {data.hex()}")

                try:
                    m = Message.parse(data)
                    assert m is not None
                    header = m.header.value

                    did_bytes: bytes = header.device_id
                    did_hex = binascii.hexlify(did_bytes).decode()
                    did_dec = int.from_bytes(did_bytes, byteorder="big")
                    token = codecs.encode(m.checksum, "hex").decode()

                    print(f"  DID (hex):  {did_hex}")
                    print(f"  DID (dec):  {did_dec}   ← use as miio_id")
                    print(f"  token:      {token}   ← use as token")
                    print(f"  timestamp:  {header.ts}")
                    print(f"  unknown:    {header.unknown:#010x}")
                except Exception as e:
                    print(f"  (parse error: {e})")
                print()

            except socket.timeout:
                break
    finally:
        sock.close()

    if not seen:
        print("No MiIO devices responded.")
        print("Check broadcast address and that devices are on the same network.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    broadcast_addr = sys.argv[1]
    wait = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    discover(broadcast_addr, wait)
