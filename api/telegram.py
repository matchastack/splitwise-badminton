"""Vercel function for POST /api/telegram (Vercel serves api/<name>.py at /api/<name>).

Don't route here with a vercel.json rewrite: a rewritten request reaches the app under its
destination path (e.g. /api/index), so none of the app's /api/... routes would match.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from badminton.web import app

__all__ = ["app"]
