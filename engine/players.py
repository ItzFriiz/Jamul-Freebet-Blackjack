"""Computer players: who they are, and how their personality drives decisions.

Rules reference: RULES.md R2.16. The cast itself lives in data/players.toml --
nothing here knows any names.

About the play styles
---------------------
These are *placeholders*, not strategy. The point of this project is to work out
what the right play actually is, and that work has not been done yet. Until it
has, each style is a small hand-written rule set whose only job is to make the
other seats behave plausibly and consume cards. When the real strategy exists,
`decide` is the one function to replace.

One thing every style does agree on: a free double or a free split costs the
player nothing on a loss (R4.2 / R4.4), so declining one is hard to justify.
The styles differ in what they do with their own money.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .config import DOLLAR
from .content import players as load_players
from .hand import Hand
from .state import Action

# How often each setting actually puts the bet out
SIDE_BET_CHANCE = {"none": 0.0, "rare": 0.15, "sometimes": 0.45, "always": 1.0}
TIP_CHANCE = {"never": 0.0, "rare": 0.05, "sometimes": 0.15, "often": 0.35}


@dataclass(frozen=True)
class Character:
    name: str
    bio: str
    bet_units: int
    spread: int
    side_bets: str
    tips: str
    toke_bets: bool
    play: str
    agreeable: float
    buy_in: int

    @classmethod
    def from_data(cls, entry: dict) -> "Character":
        return cls(**{f: entry[f] for f in cls.__dataclass_fields__})


def roster() -> list[Character]:
    return [Character.from_data(e) for e in load_players()]


def by_name(name: str) -> Character:
    """Look a character up, so a seat can be filled with a chosen person."""
    wanted = name.strip().casefold()
    for ch in roster():
        if ch.name.casefold() == wanted:
            return ch
    known = ", ".join(c.name for c in roster())
    raise KeyError(f"no character called {name!r}. Known: {known}")


def search(query: str, among: list[Character] | None = None) -> list[Character]:
    """Characters whose name contains `query`, exact match winning outright.

    Typing the whole name is tedious when picking somebody out of a list, so a
    fragment is enough as long as it only fits one person.
    """
    pool = roster() if among is None else list(among)
    wanted = query.strip().casefold()
    if not wanted:
        return pool
    exact = [c for c in pool if c.name.casefold() == wanted]
    if exact:
        return exact
    return [c for c in pool if wanted in c.name.casefold()]


def traits(ch: Character) -> str:
    """A one-line read on how somebody plays, for the character list."""
    bet = "flat" if ch.spread == 0 else "spreads"
    side = {"none": "no side bets", "rare": "rare side bets",
            "sometimes": "some side bets", "always": "always side bets"}[ch.side_bets]
    tip = {"never": "never tips", "rare": "rarely tips",
           "sometimes": "tips sometimes", "often": "tips often"}[ch.tips]
    mood = ("easy to ask" if ch.agreeable >= 0.7 else
            "hard to ask" if ch.agreeable <= 0.3 else "reasonable")
    return f"{ch.play} - {bet} {ch.bet_units}x - {side} - {tip} - {mood}"


class Bot:
    """One seated character, and the decisions their personality leads to."""

    def __init__(self, character: Character, rng: random.Random | None = None):
        self.character = character
        self.rng = rng or random.Random()
        self.started_with = 0        # set when they buy in, for the leaving rule

    @property
    def name(self) -> str:
        return self.character.name

    # --- Money -------------------------------------------------------------
    def base_bet(self, rules) -> int:
        """A multiple of the table minimum, wobbled by their spread."""
        c = self.character
        units = c.bet_units
        if c.spread:
            units += self.rng.randint(0, c.spread)
        return min(units * rules.min_bet, rules.max_bet)

    def side_bet(self, rules) -> int:
        if self.rng.random() >= SIDE_BET_CHANCE[self.character.side_bets]:
            return 0
        return min(5 * DOLLAR, rules.max_side_bet)

    def wants_to_tip(self) -> int:
        """R2.10 an outright tip, now and then."""
        if self.rng.random() < TIP_CHANCE[self.character.tips]:
            return 5 * DOLLAR
        return 0

    def toke_bet(self) -> int:
        """R2.11 backing the dealer, for the characters who do that."""
        if not self.character.toke_bets or self.rng.random() > 0.25:
            return 0
        return 5 * DOLLAR

    # --- Leaving (R2.16) ---------------------------------------------------
    def wants_to_leave(self, bankroll: int, rules) -> bool:
        """Broke, or up enough to be satisfied. Quitting while ahead is a trait."""
        if bankroll < rules.min_bet:
            return True                       # cannot cover a bet any more
        if self.character.play == "basic" and self.character.spread == 0:
            # The steady ones bank a win and go home
            return bankroll >= self.started_with + 200 * DOLLAR
        return False

    # --- Playing -----------------------------------------------------------
    def wants_insurance(self, hand: Hand) -> bool:
        """R5.4. Only the superstitious and the reckless take it."""
        return self.character.play in ("superstitious", "aggressive")

    def decide(self, hand: Hand, upcard_value: int, rules,
               neighbour: Action | None = None) -> Action:
        """Pick an action. `neighbour` is what the player to their right just did."""
        legal_split = hand.can_split(rules)
        legal_double = hand.can_double(rules)
        free_double = legal_double and hand.double_is_free(rules)
        style = self.character.play

        # Free money first: nobody in the cast turns down a free bet (R4.2/R4.4).
        if free_double:
            return Action.DOUBLE
        if legal_split and self._will_split(hand, rules):
            return Action.SPLIT

        if style == "mimic" and neighbour in (Action.HIT, Action.STAND):
            # Copies the neighbour, but not into a bust
            if neighbour is Action.HIT and hand.total <= 18 and hand.can_hit(rules):
                return Action.HIT
            if neighbour is Action.STAND and hand.total >= 12:
                return Action.STAND

        if legal_double and style == "aggressive" and hand.total in (9, 10, 11):
            return Action.DOUBLE          # pays for it out of their own pocket

        return self._hit_or_stand(hand, upcard_value, rules)

    def _will_split(self, hand: Hand, rules) -> bool:
        """Every split is free (R4.4), so the only question is temperament."""
        style = self.character.play
        if style == "cautious" and hand.total == 20:
            return False                  # will not break up a twenty, free or not
        return True

    def _hit_or_stand(self, hand: Hand, upcard_value: int, rules) -> Action:
        style = self.character.play
        total, soft = hand.total, hand.is_soft
        if not hand.can_hit(rules):
            return Action.STAND

        if soft:
            limit = 18 if style != "aggressive" else 19
            return Action.HIT if total <= limit else Action.STAND

        if style == "aggressive":
            return Action.HIT if total < 17 else Action.STAND
        if style == "cautious":
            return Action.HIT if total < 15 else Action.STAND
        if style == "superstitious":
            # Plays the dealer's upcard by feel rather than by any table
            limit = 17 if upcard_value >= 7 else 13
            return Action.HIT if total < limit else Action.STAND
        # "basic" and "mimic" fall back to the same plain rule
        return Action.HIT if total < 17 else Action.STAND
