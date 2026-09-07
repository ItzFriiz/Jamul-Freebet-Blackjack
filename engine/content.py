"""Loading the editable content files in data/.

Names, dealer lines and computer characters live in TOML rather than in Python,
so adding a character or rewriting a line does not mean touching code. The
project targets 3.11, so tomllib is in the standard library and this costs no
dependency.
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    """Read data/<name>.toml. Cached, so a file is parsed once per session."""
    path = DATA_DIR / f"{name}.toml"
    if not path.exists():
        raise FileNotFoundError(f"missing content file: {path}")
    with path.open("rb") as fh:
        return tomllib.load(fh)


def dealer_names() -> list[str]:
    names = load("dealers").get("names", [])
    if not names:
        raise ValueError("data/dealers.toml lists no names")
    return list(names)


def chatter() -> dict[str, list[str]]:
    return {k: list(v) for k, v in load("chatter").items()}


def table_talk() -> dict[str, list[str]]:
    """What the seated characters say to each other (R2.19)."""
    return {k: list(v) for k, v in load("table_talk").items()}


def players() -> list[dict]:
    """The computer characters (R2.16). Not used until the multi-seat table."""
    return list(load("players").get("player", []))
