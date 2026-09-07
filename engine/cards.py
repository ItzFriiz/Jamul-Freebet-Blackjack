"""Cards and the shoe. Rules reference: RULES.md section R1.

A card is an int in 0..51: card = rank_index * 4 + suit_index.
Ints rather than objects because the shoe is a "how many of each card are left"
count table, and the int doubles as the index into it.
"""

from __future__ import annotations

import random

RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
SUITS = ("S", "H", "D", "C")
SUIT_SYMBOLS = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
RED_SUITS = frozenset({"H", "D"})      # used by the PUSH 22 "same color" payout (R7.3)

NUM_RANKS = len(RANKS)
NUM_SUITS = len(SUITS)
DECK_SIZE = NUM_RANKS * NUM_SUITS      # 52

Card = int


def make_card(rank: str, suit: str) -> Card:
    return RANKS.index(rank) * NUM_SUITS + SUITS.index(suit)


def rank_of(card: Card) -> str:
    return RANKS[card // NUM_SUITS]


def suit_of(card: Card) -> str:
    return SUITS[card % NUM_SUITS]


def is_red(card: Card) -> bool:
    return suit_of(card) in RED_SUITS


def is_ace(card: Card) -> bool:
    return card // NUM_SUITS == 0


def is_ten_rank(card: Card) -> bool:
    """10 / J / Q / K -- all worth ten points."""
    return card // NUM_SUITS >= 9


def value_of(card: Card) -> int:
    """An ace counts 11 here; Hand decides whether to drop it to 1 (see hand.py)."""
    idx = card // NUM_SUITS
    if idx == 0:
        return 11
    if idx >= 9:
        return 10
    return idx + 1


def card_name(card: Card) -> str:
    return f"{rank_of(card)}{SUIT_SYMBOLS[suit_of(card)]}"


def hand_name(cards) -> str:
    return " ".join(card_name(c) for c in cards)


class ShoeExhausted(RuntimeError):
    """Shoe and discard tray are both empty -- should never happen in a real round."""


class Shoe:
    """The shoe: a count table of undealt cards, plus a discard tray and a cut card.

    Deliberately NOT a shuffled list of cards. A count table answers "what is the
    probability the next card is a ten" directly, which is what the exact solver
    needs; a shuffled list could only be sampled from. For a human player the two
    are equivalent, since drawing in proportion to the counts is the same process.
    """

    def __init__(self, rules, rng: random.Random | None = None):
        self.rules = rules
        self.rng = rng or random.Random()
        self.counts = [0] * DECK_SIZE        # undealt cards
        self.discard = [0] * DECK_SIZE       # discard tray
        self.cut_remaining = 0               # cards sitting behind the cut card (R1.3)
        self.cut_card_seen = False           # R1.4 reshuffle once this round ends
        self.midround_reshuffles = 0         # R1.5 how often the fallback fired
        self.burned = 0                      # R1.2 cards burned into this shoe
        self.shuffles = 0
        self.shuffle()

    # --- Shuffling and the cut card ---------------------------------------
    def shuffle(self) -> None:
        """Reshuffle everything, place the cut card, burn a card. R1.2 + R1.3."""
        self.counts = [self.rules.num_decks] * DECK_SIZE
        self.discard = [0] * DECK_SIZE
        self.cut_remaining = self._sample_cut_position()
        self.cut_card_seen = False
        self.burned = 0
        self.shuffles += 1
        for _ in range(self.rules.burn_cards_per_shuffle):
            self.discard_cards([self._draw_raw()])
            self.burned += 1

    def _sample_cut_position(self) -> int:
        """R1.3 cards left behind the cut card: normal, clamped to the configured range."""
        n = self.rng.gauss(self.rules.cut_remaining_mean, self.rules.cut_remaining_sd)
        n = round(n)
        return max(self.rules.cut_remaining_min, min(self.rules.cut_remaining_max, n))

    def reshuffle_discards(self) -> None:
        """R1.5 fallback: the shoe ran dry mid-round, so wash the discard tray back in.

        Cards currently on the table are not in the discard tray, so they are not
        swept up. The shoe still gets a full reshuffle when the round ends, which is
        why cut_card_seen stays true.
        """
        for i in range(DECK_SIZE):
            self.counts[i] += self.discard[i]
            self.discard[i] = 0
        self.cut_card_seen = True
        self.midround_reshuffles += 1

    # --- Queries -----------------------------------------------------------
    @property
    def remaining(self) -> int:
        return sum(self.counts)

    @property
    def discarded(self) -> int:
        return sum(self.discard)

    def outcomes(self) -> list[tuple[Card, float]]:
        """[(card, probability), ...] -- what the exact solver expands a chance node into."""
        total = self.remaining
        if total == 0:
            return []
        return [(c, n / total) for c, n in enumerate(self.counts) if n]

    def probability_of(self, card: Card) -> float:
        total = self.remaining
        return self.counts[card] / total if total else 0.0

    # --- Drawing -----------------------------------------------------------
    def _draw_raw(self) -> Card:
        """Draw without checking the cut card. Only burning uses this."""
        total = self.remaining
        if total == 0:
            if sum(self.discard) == 0:
                raise ShoeExhausted("shoe and discard tray are both empty")
            self.reshuffle_discards()
            total = self.remaining
        pick = self.rng.randrange(total)
        for card, n in enumerate(self.counts):
            if pick < n:
                self.counts[card] -= 1
                return card
            pick -= n
        raise AssertionError("count table disagrees with its own total")

    def draw(self) -> Card:
        card = self._draw_raw()
        self._check_cut_card()
        return card

    def take(self, card: Card) -> Card:
        """Draw one specific card -- used by the solver expanding a branch, and by replay."""
        if self.counts[card] <= 0:
            raise ValueError(f"no {card_name(card)} left in the shoe")
        self.counts[card] -= 1
        self._check_cut_card()
        return card

    def _check_cut_card(self) -> None:
        """R1.4 the cut card surfaces when the remaining count reaches its position."""
        if self.remaining <= self.cut_remaining:
            self.cut_card_seen = True

    # --- Discarding --------------------------------------------------------
    def discard_cards(self, cards) -> None:
        for c in cards:
            self.discard[c] += 1

    # --- Self-check --------------------------------------------------------
    def accounted_total(self, cards_in_play: int = 0) -> int:
        """Shoe + discard tray + table. Must equal deck_size at every instant."""
        return self.remaining + sum(self.discard) + cards_in_play
