"""
Phusion Passenger (cPanel) entry — WSGI callable wrapping FastAPI (ASGI).

cPanel:
  Application root:         …/app
  Application startup file: passenger_wsgi.py
  Application Entry point:  application

Prefers a2wsgi if installed in the venv; otherwise uses built-in
asgi_wsgi_bridge (no pip required).
"""

from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

SITE_ROOT = os.path.dirname(BASE_DIR)
if SITE_ROOT and SITE_ROOT not in sys.path:
    sys.path.append(SITE_ROOT)

from server import app as asgi_app  # noqa: E402
from asgi_wsgi_bridge import wrap_asgi  # noqa: E402

application = wrap_asgi(asgi_app)
