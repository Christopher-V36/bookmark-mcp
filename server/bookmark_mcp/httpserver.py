"""HTTP server base that really owns its port.

Python's HTTPServer sets SO_REUSEADDR, which on Windows lets a second process bind
the same port silently. bookmark-mcp relies on "port busy" to know that another
instance is already receiving captures, so on Windows we ask for exclusive use.
"""

from __future__ import annotations

import os
import socket
from http.server import ThreadingHTTPServer


class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()
