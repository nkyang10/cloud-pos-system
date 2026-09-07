"""Entry point for the tiny POS web app.

Starts the stock-items server on http://127.0.0.1:8000 by default.
Override the port with the ``PORT`` environment variable::

    PORT=9000 python run.py
"""

import os
from http.server import ThreadingHTTPServer

from pos.server import POSHandler

HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def main():
    port = _parse_port(os.environ.get("PORT"))
    server = ThreadingHTTPServer((HOST, port), POSHandler)
    print("Serving POS stock items at http://{}:{}  (Ctrl+C to stop)".format(HOST, port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


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
