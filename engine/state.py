"""One round of play, as an explicit state machine.

Rules reference: RULES.md R3 (dealing), R4 (player actions), R5 (blackjack and
insurance), R6.1-R6.3 (dealer play).  Settlement lives in payout.py.

Two kinds of node
-----------------
A round alternates between nodes where somebody must decide something and nodes
where a card must come off the shoe.  They are kept apart on purpose: a solver
takes a maximum over the branches of a decision node, but a probability-weighted
average over the branches of a chance node.  Fusing them makes the recursion
impossible to write.

The hole card
-------------
The dealer's hole card is not drawn when the hand is dealt.  It is drawn the
moment it is first needed -- at the peek, or when the dealer begins to play.
The distribution is identical either way, because the unseen cards are
exchangeable, and deferring it keeps the card genuinely unknown: it never leaves
the shoe's count table while the players are still acting, so a card counter and
a solver both see exactly what a real player at the table would see.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum, auto

from .cards import Card, Shoe, is_ace
from .chips import round_up_payment
from .hand import Hand


class Phase(Enum):
    DEAL = auto()        # chance: the opening cards
    INSURANCE = auto()   # decision: insurance, offered only on a dealer ace (R3.3)
    PEEK = auto()        # chance: the hole card, checked for blackjack (R3.3/R3.4)
    PLAYER = auto()      # decision + chance: the players act
    DEALER = auto()      # chance: the dealer draws to R6.1
    SETTLE = auto()      # the round is over; payout.py takes it from here


class Action(Enum):
    HIT = auto()
    STAND = auto()
    DOUBLE = auto()      # free or paid; the engine decides which (R4.2/R4.3)
    SPLIT = auto()       # always free (R4.4)
    TAKE_INSURANCE = auto()
    DECLINE_INSURANCE = auto()


@dataclass
class SeatBets:
    """What one seat put up before the cards came out."""

    base: int = 0
    push22: int = 0
    buster: int = 0
    # R2.11 bets placed on the dealer's behalf
    toke_base: int = 0
    toke_push22: int = 0
    toke_buster: int = 0


@dataclass
class Seat:
    bets: SeatBets
    hands: list[Hand] = field(default_factory=list)
    insurance: int = 0            # R5.4 amount actually paid, 0 if declined


class RoundState:
    """The complete state of one round. Holds no opinion about how it is displayed."""

    def __init__(self, rules, shoe: Shoe, bets: list[SeatBets]):
        self.rules = rules
        self.shoe = shoe
        self.seats = [
            Seat(
                bets=b,
                hands=[
                    Hand(
                        base_unit=b.base,
                        player_stake=b.base,
                        toke_unit=b.toke_base,
                        toke_stake=b.toke_base,
                    )
                ],
            )
            for b in bets
        ]
        self.dealer = Hand()
        self.swept = False          # true once settlement moved these cards away
        self.hole_drawn = False
        self.hole_revealed = False
        self.dealer_blackjack = False

        # Chips the player owes for the action just taken -- a paid double or a
        # paid split. The round knows the rules, the table owns the chips, so
        # the amount is handed over here and collected there.
        self.player_owes = 0

        self.phase = Phase.DEAL
        self.current_seat = 0
        self.current_hand = 0
        self.insurance_seat = 0
        self._pending_card = False

        # R3.2 one card to each seat, then the dealer's upcard, then a second to
        # each seat. The hole card is deferred -- see the module docstring.
        self._deal_queue: list[int | None] = (
            list(range(len(self.seats))) + [None] + list(range(len(self.seats)))
        )

    # --- Views -------------------------------------------------------------
    @property
    def upcard(self) -> Card | None:
        return self.dealer.cards[0] if self.dealer.cards else None

    # --- What each viewer has seen (R3.7) ----------------------------------
    def dealer_public(self) -> list[Card]:
        """The dealer's cards the table has seen -- the hole card only once turned."""
        if self.hole_revealed:
            return list(self.dealer.cards)
        return self.dealer.cards[:1]

    def seen_by(self, seats: Iterable[int] = ()) -> list[Card]:
        """Every card this viewer has watched come out this round (R3.7).

        `seats` are the round positions the viewer is holding: a player picks
        their own cards up and sees all of them, and sees only what is face up
        in front of everybody else. Pass nothing for somebody at the rail.

        This is the honest information set -- what a person sitting there could
        actually count. Counting off the full deal would be counting cards
        nobody has been shown.
        """
        own = set(seats)
        cards = self.dealer_public()
        for i, seat in enumerate(self.seats):
            for hand in seat.hands:
                cards.extend(hand.cards if i in own else hand.public_cards())
        return cards

    @property
    def face_down_on_felt(self) -> int:
        """Cards lying face down in front of the players -- unseen, not dealt away."""
        return sum(h.face_down for s in self.seats for h in s.hands)

    @property
    def cards_in_play(self) -> int:
        return len(self.dealer.cards) + sum(
            len(h.cards) for s in self.seats for h in s.hands
        )

    def all_cards_on_table(self) -> list[Card]:
        cards = list(self.dealer.cards)
        for s in self.seats:
            for h in s.hands:
                cards.extend(h.cards)
        return cards

    def current(self) -> Hand | None:
        if self.phase is not Phase.PLAYER or self.current_seat >= len(self.seats):
            return None
        seat = self.seats[self.current_seat]
        if self.current_hand >= len(seat.hands):
            return None
        return seat.hands[self.current_hand]

    @property
    def is_over(self) -> bool:
        return self.phase is Phase.SETTLE

    # --- Chance nodes ------------------------------------------------------
    def is_chance_node(self) -> bool:
        if self.phase is Phase.DEAL:
            return bool(self._deal_queue)
        if self.phase is Phase.PEEK:
            return not self.hole_drawn
        if self.phase is Phase.PLAYER:
            hand = self.current()
            if hand is None:
                return False
            return self._pending_card or len(hand.cards) < 2
        if self.phase is Phase.DEALER:
            if not self.hole_drawn:
                return True             # R6.3 the hole card is always turned over
            return self._dealer_must_play_out() and self._dealer_should_hit()
        return False

    def chance_outcomes(self) -> list[tuple[Card, float]]:
        """Every card that could come next, with its probability."""
        return self.shoe.outcomes() if self.is_chance_node() else []

    def deal_next(self) -> Card:
        """Draw the next card at random and apply it."""
        card = self.shoe.draw()
        self.apply_chance(card)
        return card

    def apply_chance(self, card: Card) -> None:
        """Place a specific card. The caller has already removed it from the shoe."""
        if not self.is_chance_node():
            raise RuntimeError(f"no card is due in phase {self.phase.name}")

        if self.phase is Phase.DEAL:
            target = self._deal_queue.pop(0)
            if target is None:
                self.dealer.cards.append(card)
            else:
                self.seats[target].hands[0].cards.append(card)
            if not self._deal_queue:
                self._after_deal()

        elif self.phase is Phase.PEEK:
            self.dealer.cards.append(card)
            self.hole_drawn = True
            self._after_peek()

        elif self.phase is Phase.PLAYER:
            hand = self.current()
            hand.cards.append(card)
            self._expose_if_shown(hand)       # R3.7 busting throws the cards in
            self._pending_card = False
            self._settle_position()

        elif self.phase is Phase.DEALER:
            self.dealer.cards.append(card)
            if not self.hole_drawn:
                self.hole_drawn = True
                self.hole_revealed = True
            if not self._dealer_must_play_out() or not self._dealer_should_hit():
                self.phase = Phase.SETTLE

    # --- Decision nodes ----------------------------------------------------
    def legal_actions(self) -> list[Action]:
        if self.is_chance_node():
            return []
        if self.phase is Phase.INSURANCE:
            return [Action.TAKE_INSURANCE, Action.DECLINE_INSURANCE]
        if self.phase is Phase.PLAYER:
            hand = self.current()
            if hand is None:
                return []
            acts: list[Action] = []
            if hand.can_hit(self.rules):
                acts.append(Action.HIT)
                acts.append(Action.STAND)
            if hand.can_double(self.rules):
                acts.append(Action.DOUBLE)
            if hand.can_split(self.rules, len(self.seats[self.current_seat].hands)):
                acts.append(Action.SPLIT)
            return acts
        return []

    def action_cost(self, action: Action) -> int:
        """Chips the player must put up for this action right now, 0 if it is free."""
        if self.phase is Phase.INSURANCE:
            if action is Action.TAKE_INSURANCE:
                return round_up_payment(self.seats[self.insurance_seat].bets.base // 2)
            return 0
        if self.phase is not Phase.PLAYER:
            return 0
        hand = self.current()
        if hand is None:
            return 0
        if action is Action.DOUBLE and not hand.double_is_free(self.rules):
            return hand.base_unit          # R4.3
        if action is Action.SPLIT and not hand.split_is_free(self.rules):
            # R4.4 the new box, and R2.11 the dealer's bet that goes with it
            return hand.base_unit + hand.toke_unit
        return 0

    def apply_action(self, action: Action) -> None:
        if action not in self.legal_actions():
            raise ValueError(f"{action.name} is not legal in phase {self.phase.name}")

        if self.phase is Phase.INSURANCE:
            self._apply_insurance(action)
            return

        self.player_owes = 0
        hand = self.current()
        if action is Action.STAND:
            hand.stood = True
        elif action is Action.HIT:
            self._pending_card = True
        elif action is Action.DOUBLE:
            self._apply_double(hand)
        elif action is Action.SPLIT:
            self._apply_split(hand)
        self._settle_position()

    # --- Action mechanics --------------------------------------------------
    def _apply_insurance(self, action: Action) -> None:
        seat = self.seats[self.insurance_seat]
        if action is Action.TAKE_INSURANCE:
            # R5.4 exactly half the base bet, rounded up when the half cannot be
            # made from chips (R2.6: the player pays, so round up).
            seat.insurance = round_up_payment(seat.bets.base // 2)
        self.insurance_seat += 1
        if self.insurance_seat >= len(self.seats):
            self.phase = Phase.PEEK

    def _expose_if_shown(self, hand: Hand) -> None:
        """R3.7 a hand goes face up as soon as the table has to see it.

        Busting means throwing the cards in, and a blackjack is shown straight
        away so it can be paid. Doubling and splitting turn the hand over too,
        but those are done where the action is applied.
        """
        if hand.is_bust or hand.is_blackjack:
            hand.exposed = True

    def reveal_all(self) -> None:
        """R3.7 settlement: every hand still face down is turned over."""
        for seat in self.seats:
            for hand in seat.hands:
                hand.exposed = True

    def reveal_seat(self, index: int) -> bool:
        """Turn one seat's hands over, and say whether anything was face down.

        R3.7 the dealer works down the table at settlement; the interface uses
        the return value to know which seats are worth pausing on.
        """
        turned = False
        for hand in self.seats[index].hands:
            if not hand.exposed and hand.cards:
                turned = True
            hand.exposed = True
        return turned

    def _apply_double(self, hand: Hand) -> None:
        """R4.2 free on 9/10/11, otherwise the player pays (R4.3). One card only.

        R3.7 asking to double means putting the hand face up on the felt.
        """
        if hand.double_is_free(self.rules):
            hand.house_stake += hand.base_unit
            hand.doubled_free = True
            # R2.11 a free double extends the dealer's bet too, on the casino's money
            hand.toke_house_stake += hand.toke_unit
        else:
            hand.player_stake += hand.base_unit
            self.player_owes += hand.base_unit
        hand.doubled = True
        hand.exposed = True
        self._pending_card = True

    def _apply_split(self, hand: Hand) -> None:
        """R4.4 the casino backs the new hand -- unless the pair is two tens.

        A ten-ten split is funded by the player (R6.8b), and so is the dealer's
        bet that goes on the new box: the casino only puts up a free bet where
        it is already paying, so the player covers that side too (R2.11).
        """
        seat = self.seats[self.current_seat]
        first, second = hand.cards
        aces = is_ace(first)
        free = hand.split_is_free(self.rules)

        # R3.7 a split is shown to the table -- both halves stay face up
        hand.exposed = True
        hand.cards = [first]
        hand.from_split = True
        hand.from_split_aces = aces

        seat.hands.insert(
            self.current_hand + 1,
            Hand(
                cards=[second],
                base_unit=hand.base_unit,
                player_stake=0 if free else hand.base_unit,
                house_stake=hand.base_unit if free else 0,
                toke_unit=hand.toke_unit,
                toke_stake=0 if free else hand.toke_unit,
                toke_house_stake=hand.toke_unit if free else 0,
                from_split=True,
                from_split_aces=aces,
                exposed=True,
            ),
        )
        if not free:
            self.player_owes += hand.base_unit + hand.toke_unit
        # Both halves now hold one card; _settle_position turns that into a chance node.

    # --- Phase transitions -------------------------------------------------
    def _after_deal(self) -> None:
        """R3.3 / R3.4 / R3.5 decide what happens once the opening cards are out."""
        # R3.7 a blackjack is turned over at once, whoever is due to act next
        for seat in self.seats:
            for hand in seat.hands:
                self._expose_if_shown(hand)
        up = self.upcard
        if is_ace(up):
            self.phase = Phase.INSURANCE      # insurance first, then peek (R3.3)
            self.insurance_seat = 0
        elif self.dealer.cards and _is_ten(up):
            self.phase = Phase.PEEK           # R3.4 peek with no insurance offered
        else:
            self._start_player_phase()        # R3.5 no peek at all

    def _after_peek(self) -> None:
        if self.dealer.total == 21:
            # R3.6 / R6.2 the round ends here; the dealer draws no further cards,
            # so both side bets lose (R7.4, R8.6).
            self.dealer_blackjack = True
            self.hole_revealed = True
            self.phase = Phase.SETTLE
        else:
            self._start_player_phase()

    def _start_player_phase(self) -> None:
        self.phase = Phase.PLAYER
        self.current_seat = 0
        self.current_hand = 0
        self._settle_position()

    def _settle_position(self) -> None:
        """Walk the seat/hand cursor forward until a real decision or card is due."""
        while self.phase is Phase.PLAYER:
            if self.current_seat >= len(self.seats):
                self._start_dealer_phase()
                return
            seat = self.seats[self.current_seat]
            if self.current_hand >= len(seat.hands):
                self.current_seat += 1
                self.current_hand = 0
                continue
            hand = seat.hands[self.current_hand]
            if self._pending_card or len(hand.cards) < 2:
                return                              # a card is due
            if self.legal_actions():
                return                              # the player must choose
            self.current_hand += 1                  # nothing left to do with this hand

    def _start_dealer_phase(self) -> None:
        """R6.3 the dealer plays only while something on the layout still needs it.

        The hole card always comes up -- the table gets to see it either way --
        but once every bet out there is already decided, the dealer stops and
        clears instead of drawing to 17.
        """
        self.phase = Phase.DEALER
        self.hole_revealed = True
        if not self.is_chance_node():
            self.phase = Phase.SETTLE

    def _dealer_must_play_out(self) -> bool:
        """R6.3 is any bet still waiting on the dealer's final total?

        A busted hand has lost and a blackjack has won (the peek already ruled
        out a dealer blackjack), so neither cares what the dealer draws. The two
        side bets always do -- including the ones placed for the dealer (R2.11).
        """
        for seat in self.seats:
            b = seat.bets
            if b.push22 or b.buster or b.toke_push22 or b.toke_buster:
                return True
            for hand in seat.hands:
                if not hand.is_bust and not hand.is_blackjack:
                    return True
        return False

    def _dealer_should_hit(self) -> bool:
        """R6.1 hit to 17, and hit soft 17."""
        if len(self.dealer.cards) < 2:
            return True
        total = self.dealer.total
        if total < 17:
            return True
        if total == 17 and self.dealer.is_soft and self.rules.dealer_hits_soft_17:
            return True
        return False


def _is_ten(card: Card) -> bool:
    from .cards import is_ten_rank

    return is_ten_rank(card)
