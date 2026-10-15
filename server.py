"""
Local entry: uvicorn server:app

Re-exports FastAPI from app/server.py (canonical module for cPanel).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent / "app"
SERVER_FILE = APP_DIR / "server.py"

if not SERVER_FILE.is_file():
    raise ModuleNotFoundError("Expected %s" % SERVER_FILE)

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

spec = importlib.util.spec_from_file_location("app_server", SERVER_FILE)
if spec is None or spec.loader is None:
    raise ImportError("Cannot load %s" % SERVER_FILE)

_mod = importlib.util.module_from_spec(spec)
sys.modules["app_server"] = _mod
spec.loader.exec_module(_mod)

app = _mod.app
init_app = _mod.init_app

__all__ = ["app", "init_app"]
