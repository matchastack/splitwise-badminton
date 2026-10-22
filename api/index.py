"""Vercel entry point: Vercel detects the FastAPI app here and serves every path to it.

Don't add a vercel.json rewrite to this file: a rewritten request reaches the app under its
destination path (/api/index), so none of the app's /api/... routes would match.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from badminton.web import app

__all__ = ["app"]
