"""User-configurable paths and settings.

One place to resolve the document inbox folder — the folder the app reads
documents from. It is configurable so a new user does not have to touch code:
set DOC_INBOX in .env to any absolute or relative path. Relative paths are
resolved against the project root, so the default works on any machine.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# The folder the "Load from folder" picker reads. Change DOC_INBOX in .env to
# point at wherever you drop trade documents — no code change needed.
DEFAULT_INBOX = "Testdocs"

SUPPORTED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


def doc_inbox() -> Path:
    """Absolute path to the configured document inbox, created if missing."""
    raw = (os.getenv("DOC_INBOX") or DEFAULT_INBOX).strip().strip('"').strip("'")
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_inbox_documents() -> list[Path]:
    """Every supported document in the inbox, newest first."""
    folder = doc_inbox()
    files = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    ]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
