"""Stacked shoes: deal a chosen sequence of cards so a situation can be tried by hand.

Reaching four hands of free splits with free doubles on top of them by shuffling
and hoping is not practical, so these decks arrange it. Once the script runs out
the shoe goes back to dealing at random, and play continues normally.

The order to write a script in follows the order the engine actually asks for
cards, which is *not* the order they land on the felt:

    player's first card, dealer's upcard, player's second card,
    then every card the player draws,
    then the dealer's hole card, then the dealer's draws

The hole card comes late because the engine defers it until the dealer needs it
(see state.py). The burn card is not part of the script -- it is drawn at random
before the script starts, exactly as it would be at a table.

One trap when writing a script: if the dealer's upcard is an ace or a ten the
dealer peeks *before* the players act, so the hole card is pulled off the script
right then, not at the end. Give the dealer a 2..9 upcard unless the peek is the
point of the scenario.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .cards import Card, Shoe, make_card


def cards(spec: str) -> list[Card]:
    """'8S 6S 8H' -> card ids. Rank then suit, suits are S H D C."""
    out = []
    for token in spec.split():
        out.append(make_card(token[:-1], token[-1]))
    return out


class ScriptedShoe(Shoe):
    """A shoe that deals a fixed sequence first, then behaves normally."""

    def __init__(self, rules, script, rng: random.Random | None = None):
        self.script: list[Card] = []
        super().__init__(rules, rng)          # shuffles and burns a random card
        self.script = list(script)

    def _draw_raw(self) -> Card:
        while self.script:
            card = self.script.pop(0)
            if self.counts[card] > 0:
                self.counts[card] -= 1
                return card
            # The script asked for a card the shoe has run out of; skip it rather
            # than deal something the scenario did not intend.
        return super()._draw_raw()


@dataclass
class Scenario:
    key: str
    title: str
    hint: str                       # what to press to reach the situation
    script: str
    bets: dict = field(default_factory=lambda: {"base": 2500})
    actions: list[str] = field(default_factory=list)   # for the automated check
    expect_to_player: int | None = None                # settled total, in cents


SCENARIOS: dict[str, Scenario] = {
    "split-double": Scenario(
        key="split-double",
        title="Free split, then a free double on both halves",
        hint="p (split), d (double), d (double)",
        #     p1 up  p2 | h1 draw, double | h2 draw, double | hole, dealer draw
        script="8S 6S 8H  3D 10C  3H 9D  10D 9H",
        actions=["SPLIT", "DOUBLE", "DOUBLE"],
        # hand 1: $25 own + $25 free -> 25 + 50 = 75
        # hand 2: $0 own + $50 free  ->  0 + 50 = 50
        expect_to_player=12500,
    ),
    "resplit-chain": Scenario(
        key="resplit-chain",
        title="Splitting into four hands, three of them free-doubled",
        hint="p, p, d, p, d, s, d   (split whenever offered, double on 9/10/11)",
        script="8S 5S 8H  8D  3C 10H  8C  2S 9S  10S  3S KH  10D 9C",
        actions=["SPLIT", "SPLIT", "DOUBLE", "SPLIT", "DOUBLE", "STAND", "DOUBLE"],
        # 75 + 50 + 25 + 50 on a $25 bet
        expect_to_player=20000,
    ),
    "resplit-aces": Scenario(
        key="resplit-aces",
        title="Splitting aces, drawing another ace, splitting again",
        hint="p (split aces), p (split again), then nothing -- each gets one card",
        script="AS 9S AH  AD  9C  10S  7H  8D",
        actions=["SPLIT", "SPLIT"],
        # 20, 21 and soft 18 all beat the dealer's 17: 50 + 25 + 25
        expect_to_player=10000,
    ),
    "dealer-22": Scenario(
        key="dealer-22",
        title="Two free-doubled hands, and the dealer lands on 22",
        hint="p, d, d   -- then watch everything push and the free bets come off",
        script="8S 6S 8H  3D 10C  3H 9D  6D 4S 6H",
        bets={"base": 2500, "push22": 500},
        actions=["SPLIT", "DOUBLE", "DOUBLE"],
        # both hands push: only the player's own $25 comes back.
        # PUSH 22 on 6S 6D 4S 6H is mixed colour, so 8 to 1: 5 + 40 = 45
        expect_to_player=7000,
    ),
    "ten-split": Scenario(
        key="ten-split",
        title="Splitting 10 and K, and why the resulting A+10 is only 21",
        hint="p (split), then s, s",
        #     p1  up  p2 | h1 draw | h2 draw | hole
        script="10S 9S KH  AD  9C  10D",
        actions=["SPLIT", "STAND", "STAND"],
        # dealer 19. hand 1 is A+10 = 21, which after a split is only 21 and not a
        # blackjack (R4.8), so it pays even money: 25 + 25 = 50.
        # hand 2 is 19 and pushes; it was the free hand, so nothing comes back.
        expect_to_player=5000,
    ),
    "buster-jackpot": Scenario(
        key="buster-jackpot",
        title="Player blackjack while the dealer busts with seven cards",
        hint="nothing to press -- blackjack settles itself, then watch the dealer",
        script="AS 2S KH  2H 2D 2C 3S 3H 9C",
        bets={"base": 2500, "push22": 500, "buster": 500},
        actions=[],
        # blackjack 25 + 37.50 = 62.50; PUSH 22 loses (dealer has 23);
        # BUSTER on a seven-card bust is 50 to 1 plus the $1,000 bonus
        expect_to_player=6250 + 500 + 25000 + 100000,
    ),
}


def get(key: str) -> Scenario:
    if key not in SCENARIOS:
        raise KeyError(f"no such scenario: {key}. Try one of: {', '.join(SCENARIOS)}")
    return SCENARIOS[key]
