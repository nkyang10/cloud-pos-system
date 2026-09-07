"""Entry point for the tiny POS web app.

Starts the shared cloud station server on 0.0.0.0:8000 by default so any
station on the shop network can reach it. Override the port with the ``PORT``
environment variable::

    PORT=9000 python run.py
"""

import os
import socket
from http.server import ThreadingHTTPServer

from pos.server import POSHandler

HOST = "0.0.0.0"
DEFAULT_PORT = 8000


def main():
    port = _parse_port(os.environ.get("PORT"))
    server = ThreadingHTTPServer((HOST, port), POSHandler)
    print(
        "Serving POS for the shop network at http://{}:{}  (Ctrl+C to stop)".format(
            _reachable_host(), port
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


def _reachable_host():
    """Pick an address other stations can use to reach this server.

    ``0.0.0.0`` is the bind address, not a reachable host. A throwaway UDP
    ``connect`` learns the machine's LAN IP without sending any packets;
    fall back to the bind host if no route exists (offline box).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return HOST
    finally:
        sock.close()


def _parse_port(raw):
    """Parse the PORT env var into an int; blank/missing falls back to 8000."""
    if raw is None or not raw.strip():
        return DEFAULT_PORT
    try:
        port = int(raw.strip())
    except ValueError:
        raise SystemExit("Invalid PORT {!r}: must be an integer.".format(raw)) from None
    if not 1 <= port <= 65535:
        raise SystemExit("Invalid PORT {!r}: must be between 1 and 65535.".format(raw))
    return port


if __name__ == "__main__":
    main()
