"""Dealers and their shifts.

Rules reference: RULES.md R1.6 (a dealer change mid-shoe costs a burn card) and
R2.14 (how often the change happens).

A shift is worth tracking for more than flavour: swapping dealers burns a card,
which removes one unknown card from the shoe, and that is real information loss
for anyone counting.
"""

from __future__ import annotations

from dataclasses import dataclass

from .content import dealer_names


def roster() -> list[str]:
    """Who is on shift tonight. Edit data/dealers.toml to change the list."""
    return dealer_names()


@dataclass
class DealerShift:
    """One dealer's turn at the table."""

    name: str
    started_at: float
    tokes: int = 0          # everything this dealer collected, tips and toke bets
    rounds: int = 0

    def minutes(self, now: float) -> float:
        return (now - self.started_at) / 60.0
