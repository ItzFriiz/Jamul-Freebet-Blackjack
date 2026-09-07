"""One hand: totals, soft/hard, what it may do, and the split of the two stakes.

Rules reference: RULES.md R4 (player actions), R5.1 (blackjack), R6.7/R6.8 (money flow).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .cards import Card, card_name, is_ace, is_ten_rank, rank_of, value_of


@dataclass
class Hand:
    """A single hand.

    The two stakes are tracked separately because they settle differently
    (R6.7 / R6.8):
      player_stake  the player's own money -- genuinely lost on a loss
      house_stake   the casino's free bet -- on a loss the button is simply
                    removed and the player pays nothing
      toke_*        the same two channels for a bet placed for the dealer (R2.11)
    """

    cards: list[Card] = field(default_factory=list)
    base_unit: int = 0              # the seat's original base bet; doubles and splits are sized off it
    player_stake: int = 0
    house_stake: int = 0

    # R2.11 a bet the player placed on the dealer's behalf, riding on this hand.
    # It follows the same free-bet mechanics: a free double or free split extends
    # it with the casino's money too.
    toke_unit: int = 0
    toke_stake: int = 0
    toke_house_stake: int = 0

    from_split: bool = False        # R4.8 a split hand's A+10 is 21, not blackjack
    from_split_aces: bool = False   # R4.6 split aces receive exactly one card
    doubled: bool = False           # doubled already, so one card and done
    doubled_free: bool = False      # that double was on the casino's money (R4.2)
    stood: bool = False

    # --- Totals ------------------------------------------------------------
    @property
    def hard_total(self) -> int:
        """Total with every ace counted as 1."""
        return sum(1 if is_ace(c) else value_of(c) for c in self.cards)

    @property
    def num_aces(self) -> int:
        return sum(1 for c in self.cards if is_ace(c))

    @property
    def total(self) -> int:
        """Best total: promote one ace to 11 when that does not bust."""
        t = self.hard_total
        if self.num_aces and t + 10 <= 21:
            return t + 10
        return t

    @property
    def is_soft(self) -> bool:
        """Soft means an ace is currently being counted as 11."""
        return bool(self.num_aces) and self.hard_total + 10 <= 21

    @property
    def possible_totals(self) -> set[int]:
        """Every total reachable by counting aces as 1 or 11 -- drives R4.2 eligibility."""
        totals = {self.hard_total}
        if self.num_aces:
            totals.add(self.hard_total + 10)
        return totals

    # --- Status ------------------------------------------------------------
    @property
    def is_bust(self) -> bool:
        return self.hard_total > 21

    @property
    def is_blackjack(self) -> bool:
        """R5.1 two-card A+10. R4.8 a split hand reaching 21 is only 21."""
        return len(self.cards) == 2 and not self.from_split and self.total == 21

    @property
    def is_finished(self) -> bool:
        return self.stood or self.is_bust or self.is_blackjack or self.doubled

    # --- Legal actions -----------------------------------------------------
    def is_pair(self, rules) -> bool:
        """R4.10 same rank is a pair, and every 10-point rank pairs with the others."""
        if len(self.cards) != 2:
            return False
        a, b = self.cards
        if rank_of(a) == rank_of(b):
            return True
        return rules.ten_ranks_are_pairs and is_ten_rank(a) and is_ten_rank(b)

    def can_hit(self, rules) -> bool:
        if self.is_finished:
            return False
        # R4.6 a split-ace hand takes one card and is then done
        if self.from_split_aces and len(self.cards) >= 2:
            return False
        return True

    def can_split(self, rules, num_hands: int = 1) -> bool:
        """R4.5 unlimited splits. R4.6 a split ace that draws another ace may split again."""
        if self.is_finished or not self.is_pair(rules):
            return False
        if rules.max_splits is not None and num_hands > rules.max_splits:
            return False
        return True

    def can_double(self, rules) -> bool:
        """Only on the first two cards; never on a split-ace hand (R4.6, one card only)."""
        if self.is_finished or len(self.cards) != 2:
            return False
        if self.from_split_aces:
            return False
        return True

    def double_is_free(self, rules) -> bool:
        """R4.2 the casino pays when the two cards make 9/10/11, soft hands included.

        An ace may count 1 or 11, so A+8 qualifies as 9, A+9 as 10, and a split
        hand's A+10 as 11. An unsplit A+10 is blackjack and can_double already
        rejects it via is_finished.
        """
        return bool(self.possible_totals & rules.free_double_totals)

    # --- Display -----------------------------------------------------------
    def describe(self, rules=None) -> str:
        cards = " ".join(card_name(c) for c in self.cards)
        if self.is_bust:
            tag = f"bust({self.hard_total})"
        elif self.is_blackjack:
            tag = "BJ"
        else:
            tag = f"{'soft ' if self.is_soft else ''}{self.total}"
        return f"{cards} = {tag}"
