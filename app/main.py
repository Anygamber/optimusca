"""
Local / alternate entry: re-exports FastAPI app from server.py.

cPanel should use passenger_wsgi.py → `application` (WSGI via a2wsgi).
Local: uvicorn main:app  OR  uvicorn server:app
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from server import app, init_app  # noqa: E402

__all__ = ["app", "init_app"]
