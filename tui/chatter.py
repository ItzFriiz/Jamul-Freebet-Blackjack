"""Things the dealer says.

The lines themselves live in data/chatter.toml so they can be rewritten without
touching code. This module only decides which group fires and picks one at
random, so a long session does not sound like a recording.
"""

from __future__ import annotations

import random
import string

from engine.content import chatter, table_talk


class _Forgiving(dict):
    """Leaves an unknown {placeholder} as written instead of raising."""

    def __missing__(self, key):
        return "{" + key + "}"

# Groups the code knows how to trigger. check_content.py holds the data file to
# this list, so a typo in either place is caught rather than silently going mute.
EVENTS = (
    "arrive", "leave",
    "tip", "toke_placed", "toke_win", "toke_lose",
    "win_nice", "win_big", "win_huge", "win_jackpot", "win_hands",
    "dealer_21", "dealer_blackjack",
)

# R8.3 / R7.3 how long the odds were, and what the dealer makes of it.
# Anything from 4 to 1 upward is worth a word (Junze, 2026-09-06).
WIN_TIERS = ((250, "win_jackpot"), (50, "win_huge"), (15, "win_big"), (4, "win_nice"))

# A round can also be huge without a side bet: split, split again, free-double
# the halves. Measured in base bets returned, since there are no odds to quote.
BIG_HAND_MULTIPLE = 4


def say(event: str, rng: random.Random | None = None, **context) -> str | None:
    """Pick a line for this moment. Never raises, whatever the file says.

    The lines are meant to be written freely in data/chatter.toml, so a stray
    brace or an unknown placeholder renders as literal text rather than crashing
    the table mid-hand.
    """
    pool = chatter().get(event)
    if not pool:
        return None
    line = (rng or random).choice(pool)
    try:
        return string.Formatter().vformat(line, (), _Forgiving(context))
    except (ValueError, IndexError, TypeError):
        return line        # unbalanced braces and the like: show it as written


def win_tier(odds: int | None, bonus: int = 0) -> str | None:
    """Which group of congratulations a side bet win deserves, if any."""
    if bonus:
        return "win_jackpot"
    if odds is None:
        return None
    for threshold, event in WIN_TIERS:
        if odds >= threshold:
            return event
    return None


def big_hand(net: int, base_bet: int) -> bool:
    """Did the hands themselves pay out several times the base bet?

    Only consulted when no side bet came in, so the same pile of chips does not
    get congratulated twice.
    """
    return bool(base_bet) and net >= BIG_HAND_MULTIPLE * base_bet


def talk(event: str, rng: random.Random | None = None, **context) -> str | None:
    """A line from the players' own chatter (R2.19). Forgiving, like say()."""
    pool = table_talk().get(event)
    if not pool:
        return None
    line = (rng or random).choice(pool)
    try:
        return string.Formatter().vformat(line, (), _Forgiving(context))
    except (ValueError, IndexError, TypeError):
        return line


def will_move(agreeable: float, goodwill: float, rng: random.Random) -> bool:
    """R2.19 whether a character gives up their seat when asked.

    Goodwill is what compliments and insults move, so being rude to somebody and
    then asking them for a favour goes about as well as it would in person.
    """
    return rng.random() < max(0.0, min(1.0, agreeable + goodwill))
