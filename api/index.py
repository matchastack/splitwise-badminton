"""Vercel entry point: every /api/* request is rewritten here (see vercel.json)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from badminton.web import app

__all__ = ["app"]
