"""Run AI Marketplace Monitor from source without package installation."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if __name__ == "__main__":
    runpy.run_module("ai_marketplace_monitor.cli", run_name="__main__")
