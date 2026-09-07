"""Settlement: the main bet, PUSH 22 and BUSTER, each resolved independently.

Rules reference: RULES.md R5.4 (insurance), R6.2 and R6.4-R6.8 (main bet),
R7 (PUSH 22), R8 (BUSTER), R2.6 (rounding direction).

Everything is reported as money moving off the layout, which is what actually
happens at a table:
  to_player       chips the dealer pushes back to the player (stake plus winnings)
  to_dealer       chips a winning toke bet hands to the dealer (R2.11)
  left_on_table   a pushed toke bet, which stays out there for the next round
A player's profit on a hand is `to_player` minus what they committed to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .cards import Card, is_red, suit_of
from .chips import round_down_payout
from .config import DOLLAR
from .hand import Hand


class Outcome(Enum):
    BLACKJACK = "blackjack"
    WIN = "win"
    PUSH = "push"
    LOSE = "lose"


# R8.3 BUSTER pays on the number of cards the dealer busted with.
BUSTER_ODDS = ((4, 2), (5, 4), (6, 15), (7, 50))   # anything above uses 250 to 1
BUSTER_TOP_ODDS = 250
# R8.4 flat bonus, only for a player who holds a blackjack AND backed BUSTER
BUSTER_BONUS = {7: 1000 * DOLLAR, 8: 8000 * DOLLAR}


@dataclass
class HandResult:
    hand: Hand
    outcome: Outcome
    to_player: int = 0
    to_dealer: int = 0
    left_on_table: int = 0


@dataclass
class SideResult:
    name: str
    bet: int
    odds: int | None = None       # None means the bet lost
    bonus: int = 0
    to_player: int = 0
    to_dealer: int = 0

    @property
    def won(self) -> bool:
        return self.odds is not None


@dataclass
class SeatResult:
    hands: list[HandResult] = field(default_factory=list)
    insurance_bet: int = 0
    insurance_to_player: int = 0
    push22: SideResult | None = None
    buster: SideResult | None = None

    @property
    def to_player(self) -> int:
        return (
            sum(h.to_player for h in self.hands)
            + self.insurance_to_player
            + (self.push22.to_player if self.push22 else 0)
            + (self.buster.to_player if self.buster else 0)
        )

    @property
    def to_dealer(self) -> int:
        return (
            sum(h.to_dealer for h in self.hands)
            + (self.push22.to_dealer if self.push22 else 0)
            + (self.buster.to_dealer if self.buster else 0)
        )

    @property
    def left_on_table(self) -> int:
        return sum(h.left_on_table for h in self.hands)


# --- Side bets -------------------------------------------------------------
def push22_odds(dealer_cards: list[Card], dealer_total: int) -> int | None:
    """R7.1 / R7.3 pays only on a dealer 22, priced by the cards that made it.

    The colour and suit test covers *every* card in the dealer's hand, not just
    the first two, so a 6+6+10 of three different suits is the plain 8 to 1.
    """
    if dealer_total != 22:
        return None
    if len({suit_of(c) for c in dealer_cards}) == 1:
        return 50
    if len({is_red(c) for c in dealer_cards}) == 1:
        return 20
    return 8


def buster_odds(num_cards: int) -> int:
    """R8.3 priced by how many cards the dealer needed to bust."""
    for limit, odds in BUSTER_ODDS:
        if num_cards <= limit:
            return odds
    return BUSTER_TOP_ODDS


def buster_bonus(num_cards: int) -> int:
    """R8.4 the flat bonus, which is a fixed amount and does not scale with the bet."""
    if num_cards >= 8:
        return BUSTER_BONUS[8]
    return BUSTER_BONUS.get(num_cards, 0)


# --- Main bet --------------------------------------------------------------
def _blackjack_win(stake: int) -> int:
    """R5.1 pays 3 to 2. R2.6 the half that cannot be made from chips rounds down."""
    return round_down_payout(stake * 3 // 2)


def settle_hand(hand: Hand, dealer: Hand, dealer_blackjack: bool, rules) -> HandResult:
    outcome = _hand_outcome(hand, dealer, dealer_blackjack, rules)

    if outcome is Outcome.BLACKJACK:
        return HandResult(
            hand,
            outcome,
            to_player=hand.player_stake + _blackjack_win(hand.player_stake),
            to_dealer=hand.toke_stake + _blackjack_win(hand.toke_stake),
        )

    if outcome is Outcome.WIN:
        # R6.7 / R6.8 the winnings equal both stakes; the free bet pays as if the
        # player had put the money up, but only the player's own stake comes back.
        return HandResult(
            hand,
            outcome,
            to_player=hand.player_stake + hand.player_stake + hand.house_stake,
            to_dealer=(
                hand.toke_stake + hand.toke_stake + hand.toke_house_stake
                if hand.toke_stake or hand.toke_house_stake
                else 0
            ),
        )

    if outcome is Outcome.PUSH:
        # R6.4 the player's own money comes back in full, including a paid double.
        # The free bet button is simply lifted. R2.11 a pushed toke stays on the felt.
        return HandResult(
            hand,
            outcome,
            to_player=hand.player_stake,
            left_on_table=hand.toke_stake,
        )

    return HandResult(hand, Outcome.LOSE)


def _hand_outcome(hand: Hand, dealer: Hand, dealer_blackjack: bool, rules) -> Outcome:
    if dealer_blackjack:
        # R6.2 a blackjack pushes against a blackjack; everything else loses.
        return Outcome.PUSH if hand.is_blackjack else Outcome.LOSE

    if hand.is_bust:
        return Outcome.LOSE                       # R6.6 a dealer 22 does not rescue it

    if hand.is_blackjack:
        return Outcome.BLACKJACK                  # R5.2 / R5.3 / R5.6

    if dealer.total == rules.dealer_push_total:
        return Outcome.PUSH                       # R6.4 the dealer 22 push

    if dealer.is_bust:
        return Outcome.WIN                        # R6.5 anything above 22

    if hand.total > dealer.total:
        return Outcome.WIN
    if hand.total < dealer.total:
        return Outcome.LOSE
    return Outcome.PUSH


# --- Whole round -----------------------------------------------------------
def settle_round(state) -> list[SeatResult]:
    """Settle every seat. Call this once the round has reached Phase.SETTLE."""
    dealer = state.dealer
    dealer_bj = state.dealer_blackjack
    results = []

    for seat in state.seats:
        res = SeatResult()
        for hand in seat.hands:
            res.hands.append(settle_hand(hand, dealer, dealer_bj, state.rules))

        # R5.4 insurance pays 2 to 1, so a winner collects three times the premium
        res.insurance_bet = seat.insurance
        if seat.insurance and dealer_bj:
            res.insurance_to_player = seat.insurance * 3

        # R7.4 / R8.6 a dealer blackjack stops the hand at 21, so neither side bet
        # can ever come in. That falls out of the totals; no special case needed.
        res.push22 = _settle_side(
            "PUSH 22",
            seat.bets.push22,
            seat.bets.toke_push22,
            push22_odds(dealer.cards, dealer.total),
        )

        b_odds = buster_odds(len(dealer.cards)) if dealer.is_bust else None
        bonus = 0
        if (b_odds is not None and seat.bets.buster > 0
                and len(seat.hands) == 1 and seat.hands[0].is_blackjack):
            # R8.4 both conditions: this player holds a blackjack, and backed
            # BUSTER themselves. The dealer's spot never collects it.
            bonus = buster_bonus(len(dealer.cards))
        res.buster = _settle_side(
            "BUSTER", seat.bets.buster, seat.bets.toke_buster, b_odds, bonus
        )

        results.append(res)

    return results


def _settle_side(name, bet, toke_bet, odds, bonus=0) -> SideResult:
    res = SideResult(name=name, bet=bet, odds=odds, bonus=bonus)
    if odds is None:
        return res
    if bet:
        res.to_player = bet + bet * odds + bonus
    if toke_bet:
        # R8.4 the dealer's spot settles at odds only -- the blackjack bonus is
        # the player's alone.
        res.to_dealer = toke_bet + toke_bet * odds
    return res
