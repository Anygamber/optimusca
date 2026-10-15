"""
Project wrapper around the shared completion notifier.

Canonical module:
  C:\\Users\\user\\Desktop\\Custom\\notify_done.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SHARED = Path(r"C:\Users\user\Desktop\Custom\notify_done.py")


def _load_shared():
    if not _SHARED.is_file():
        raise FileNotFoundError(f"Shared notifier not found: {_SHARED}")
    spec = importlib.util.spec_from_file_location("notify_done_shared", _SHARED)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {_SHARED}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_shared()
notify_done = _mod.notify_done
DEFAULT_SOUND = _mod.DEFAULT_SOUND

__all__ = ["notify_done", "DEFAULT_SOUND"]


if __name__ == "__main__":
    sys.exit(0 if notify_done() else 1)
