"""Content version for the passive board, including uncommitted UI changes."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

_PRESENTATION = Path(__file__).resolve().parent.parent


def board_asset_version() -> str:
    digest = sha256()
    for name in ("templates/board.html", "static/board.css", "static/board.js"):
        digest.update((_PRESENTATION / name).read_bytes())
    return digest.hexdigest()[:16]
