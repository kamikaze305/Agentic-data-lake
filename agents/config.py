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


# --------------------------------------------------------------------------------------
# Part 2 — the simulated SU mailbox and the CG outbox
# --------------------------------------------------------------------------------------

# The watched folder that stands in for the CG team's shared mailbox. An SU
# "email" is a small .json envelope (from / subject / body / attachments); a bare
# PDF or image dropped here is treated as an email with no covering note.
DEFAULT_SU_INBOX = "su_inbox"

# Where the replies CG sends end up, one text file per send — the tangible
# artifact of "CG sent it", and the demo's proof the agent never sends anything.
DEFAULT_CG_OUTBOX = "cg_outbox"


def _resolved_dir(env_var: str, default: str) -> Path:
    raw = (os.getenv(env_var) or default).strip().strip('"').strip("'")
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def su_inbox() -> Path:
    """Absolute path to the simulated SU mailbox folder, created if missing."""
    return _resolved_dir("SU_INBOX", DEFAULT_SU_INBOX)


def cg_outbox() -> Path:
    """Absolute path to the folder CG's sent replies are written into."""
    return _resolved_dir("CG_OUTBOX", DEFAULT_CG_OUTBOX)
