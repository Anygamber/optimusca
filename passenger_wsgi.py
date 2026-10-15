"""
Root Passenger entry when Application root = document root.
Delegates to app/server.py via the same ASGI→WSGI bridge.
"""

from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, "app")

for path in (APP_DIR, BASE_DIR):
    if os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)

from server import app as asgi_app  # noqa: E402
from asgi_wsgi_bridge import wrap_asgi  # noqa: E402

application = wrap_asgi(asgi_app)
