"""The table layer: chips, bet validation, tipping, and the round loop.

Rules reference: RULES.md R2 (table and betting, chips, tipping) plus R1.4 for
when the shoe gets reshuffled.

This is the layer that owns money. RoundState (state.py) only knows stakes as
numbers; Table is what moves actual chips in and out of a player's rack, which is
where R2.8's "can these chips even make that bet" friction lives.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from .cards import Shoe
from .dealers import DealerShift, roster
from .chips import ChipStack, default_buy_in, format_money, payout_chips, round_up_payment
from .config import DEFAULT_RULES
from .payout import SeatResult, settle_round
from .state import Action, Phase, RoundState, SeatBets


class BetError(ValueError):
    """A bet the table will not accept, or one the player's chips cannot make."""


@dataclass
class Occupant:
    """A person at the table, seated or not.

    Chips belong to the person, not to the chair. That matters for R2.18: someone
    who stands up to watch keeps their rack, and sits back down with it.
    """

    name: str
    bot: object | None = None
    # R2.19 shifted by compliments and insults; added to a bot's `agreeable`
    goodwill: float = 0.0
    chips: ChipStack = field(default_factory=ChipStack)
    bought_in: int = 0

    @property
    def is_bot(self) -> bool:
        return self.bot is not None

    @property
    def bankroll(self) -> int:
        return self.chips.total


_NO_CHIPS = ChipStack()


@dataclass
class TableSeat:
    occupant: Occupant | None = None
    sitting_out: bool = False        # R2.17 seated but not betting this round
    # R2.13 sat down part way through a shoe: they hold the seat but cannot bet
    # until the shoe is finished and a new one starts.
    waiting_for_shoe: bool = False
    pending: SeatBets | None = None  # bets placed, waiting for the deal
    resting_before: int = 0          # R2.11 toke already on the felt when betting
    # R2.11 a toke bet that pushed stays on the layout and rides the next round
    # until the player picks it up.
    toke_on_table: int = 0
    # What each spot was paid with, so R2.12 can pay back in matching denominations.
    bet_chips: dict[str, dict[int, int]] = field(default_factory=dict)
    committed: int = 0          # everything this seat put up this round

    # The seat reads and writes the chips of whoever is sitting in it, so all
    # the existing seat-facing code keeps working.
    @property
    def chips(self) -> ChipStack:
        return self.occupant.chips if self.occupant else _NO_CHIPS

    @chips.setter
    def chips(self, stack: ChipStack) -> None:
        if self.occupant:
            self.occupant.chips = stack

    @property
    def bought_in(self) -> int:
        return self.occupant.bought_in if self.occupant else 0

    @bought_in.setter
    def bought_in(self, amount: int) -> None:
        if self.occupant:
            self.occupant.bought_in = amount

    @property
    def bankroll(self) -> int:
        return self.chips.total

    @property
    def occupied(self) -> bool:
        return self.occupant is not None

    @property
    def label(self) -> str:
        return self.occupant.name if self.occupant else "(empty)"


class Table:
    def __init__(self, rules=DEFAULT_RULES, rng=None, shoe=None, clock=time.monotonic):
        self.rules = rules
        # `shoe` lets a stacked deck be dropped in (see engine/scripted.py)
        self.shoe = shoe if shoe is not None else Shoe(rules, rng)
        self.seats = [TableSeat() for _ in range(rules.num_seats)]
        self.state: RoundState | None = None
        self.rounds_played = 0
        # R3.1 which table seat each seat of the current round belongs to
        self._round_seats: list[int] = []
        # Everybody who has been at this table tonight, still here or not, so the
        # session can be totted up for people who left or were sent away.
        self.known: list[Occupant] = []
        # R2.18 everyone at the table without a seat. People and characters alike
        # wait here; the only difference is that at a new shoe a character takes
        # an open seat by themselves while a person chooses one (R2.13).
        self.standing: list[Occupant] = []
        # True in the gap after a shoe ends -- the only moment anyone may join
        self.new_shoe = True

        # R2.14 dealers work a shift and then hand the table over
        self.clock = clock
        self.rng = rng or random.Random()
        self._names = roster()
        self.rng.shuffle(self._names)
        self._next_name = 0
        self.past_dealers: list[DealerShift] = []
        self.dealer = DealerShift(self._take_name(), self.clock())

    def remember(self, occupant: Occupant) -> Occupant:
        """Note that this person was here, so they still count at the end."""
        if all(o is not occupant for o in self.known):
            self.known.append(occupant)
        return occupant

    @property
    def bought_in(self) -> int:
        """Everything everyone has bought in tonight, including people who left."""
        return sum(o.bought_in for o in self.known)

    @property
    def waiting(self) -> list[Occupant]:
        """Characters at the rail, who will seat themselves at the next shoe."""
        return [o for o in self.standing if o.is_bot]

    def everyone(self) -> list[Occupant]:
        """Each person once, however many boxes they are playing (R2.15)."""
        out, seen = [], set()
        for occ in [s.occupant for s in self.seats if s.occupied] + list(self.standing):
            if id(occ) in seen:
                continue
            seen.add(id(occ))
            out.append(occ)
        return out

    # --- Who is sitting where (R2.1) ---------------------------------------
    def seat_occupant(self, index: int, occupant: Occupant, buy_in: int) -> None:
        """Put someone in a seat and give them chips.

        R2.13 polices *when* this may happen; the caller decides that, because
        the rule differs for a new arrival and for someone already at the table.
        """
        seat = self.seats[index]
        if seat.occupied:
            raise BetError(f"seat {index + 1} is taken by {seat.label}")
        seat.occupant = self.remember(occupant)
        seat.sitting_out = False
        self.buy_in(index, buy_in)

    def vacate(self, index: int) -> Occupant | None:
        """R2.13 stand up. Only between rounds -- a bet already out must play."""
        if self.state is not None:
            raise BetError("a seat cannot be given up partway through a hand")
        seat = self.seats[index]
        who, seat.occupant = seat.occupant, None
        seat.sitting_out = False
        seat.waiting_for_shoe = False
        seat.pending = None
        return who

    # --- Waiting to sit down (R2.16) ---------------------------------------
    def queue_arrival(self, occupant: Occupant, buy_in: int) -> None:
        """R2.13 somebody who wants in. They stand at the rail until a new shoe."""
        clash = ((lambda o: o.is_bot and o.name == occupant.name) if occupant.is_bot
                 else (lambda o: not o.is_bot
                       and o.name.casefold() == occupant.name.casefold()))
        if any(clash(o) for o in self.everyone()):
            raise BetError(f"{occupant.name} is already at the table")
        for denom, n in default_buy_in(buy_in).counts.items():
            occupant.chips.counts[denom] += n
        occupant.bought_in = buy_in
        self.standing.append(self.remember(occupant))

    def admit_waiting(self, rng=None) -> list[tuple[int, Occupant, str]]:
        """Seat the characters standing at the rail.

        They take a free seat as soon as one is open -- picked at random, the way
        somebody joining a table would -- and wait out the rest of the shoe
        before betting (R2.13). People are left where they are: they pick their
        own seat with sit_down.
        """
        if self.state is not None:
            return []
        rng = rng or self.rng
        seated = []
        while self.waiting and self.empty_seats():
            occ = self.waiting[0]
            # Somebody walking up to a table picks a chair; they do not queue for
            # the lowest-numbered one.
            index = rng.choice(self.empty_seats())
            self.sit_down(occ, index)
            seated.append((index, occ, "waiting"))
        return seated

    def bot_names(self) -> set[str]:
        """Characters currently at the table. A person who happens to share a
        character's name does not stop that character from turning up."""
        return {o.name for o in self.everyone() if o.is_bot}

    def human_names(self) -> set[str]:
        return {o.name.casefold() for o in self.everyone() if not o.is_bot}

    def available_characters(self) -> list:
        """R2.16 who could take a seat: any character not already here or waiting.

        Deliberately the whole cast, not only the people who have played before
        -- a new face is as likely as an old one at a real table.
        """
        from .players import roster
        taken = self.bot_names()
        return [c for c in roster() if c.name not in taken]

    def move_seat(self, frm: int, to: int) -> None:
        """R2.13 an occupant moving to an empty seat, chips and all."""
        if self.state is not None:
            raise BetError("seats cannot change partway through a hand")
        src, dst = self.seats[frm], self.seats[to]
        if not src.occupied:
            raise BetError(f"seat {frm + 1} is empty")
        if dst.occupied:
            raise BetError(f"seat {to + 1} is taken by {dst.label}")
        # The chips belong to the occupant, so they travel without being moved.
        # Everything else about the box has to go too: a bet already on the
        # layout belongs to the player, not to the spot they were sitting in.
        # Leaving it behind strands a wager with nobody attached to it.
        dst.occupant, src.occupant = src.occupant, None
        dst.sitting_out, src.sitting_out = src.sitting_out, False
        # R2.13 the wait belongs to the person, not the chair. Left behind it
        # would both let them bet early and stop whoever takes the old seat.
        dst.waiting_for_shoe, src.waiting_for_shoe = src.waiting_for_shoe, False
        dst.pending, src.pending = src.pending, None
        dst.bet_chips, src.bet_chips = src.bet_chips, {}
        dst.committed, src.committed = src.committed, 0
        dst.toke_on_table, src.toke_on_table = src.toke_on_table, 0

    def take_extra_seat(self, frm: int, to: int) -> None:
        """R2.15 open a second box next door.

        The same person sits in both, sharing one rack -- a player has a single
        pile of chips in front of them and bets each box out of it. Nothing is
        set aside for a box in advance.

        Adjacent only, and only for somebody already at the table, which is why
        this is not held to R2.13's joining window.
        """
        if self.state is not None:
            raise BetError("seats cannot be taken partway through a hand")
        if abs(to - frm) != 1:
            raise BetError("an extra hand has to be in the seat next to yours")
        src, dst = self.seats[frm], self.seats[to]
        if not src.occupied:
            raise BetError(f"seat {frm + 1} is empty")
        if dst.occupied:
            raise BetError(f"seat {to + 1} is taken by {dst.label}")
        dst.occupant = src.occupant          # literally the same person
        dst.sitting_out = False
        # R2.15 an extra box for somebody already seated is not a new arrival, so
        # it plays exactly when their existing box does.
        dst.waiting_for_shoe = src.waiting_for_shoe

    def release_seat(self, seat: int) -> None:
        """R2.15 give up one of your extra boxes, keeping the others.

        Refuses on somebody's only seat -- that is standing up (R2.18), which is
        a different thing, and leaving nobody in any seat by accident would be a
        surprising way to lose your place at the table.
        """
        if self.state is not None:
            raise BetError("a box cannot be given up partway through a hand")
        s = self.seats[seat]
        if not s.occupied:
            raise BetError(f"seat {seat + 1} is empty")
        if len(self.seats_of(s.occupant.name)) < 2:
            raise BetError("that is your only box -- use stand to leave the table")
        self.cancel_bets(seat)          # anything already out comes back first
        self.vacate(seat)

    def seats_of(self, name: str) -> list[int]:
        """Every seat this person is playing (R2.15)."""
        return [i for i, s in enumerate(self.seats)
                if s.occupied and s.occupant.name == name]

    # --- Standing at the table (R2.18) -------------------------------------
    def stand_up(self, seat: int) -> Occupant:
        """R2.18 leave the table but stay to watch, chips and all.

        Every box that person is playing comes down, not just the one they were
        looking at -- you cannot stand up from one chair and stay in another.

        R2.13 says a bet already out has to play, so this is only legal between
        rounds. Sitting back down means waiting for a new shoe like anyone else.
        """
        if self.state is not None:
            raise BetError("you have a bet out -- this hand has to finish first")
        if not self.seats[seat].occupied:
            raise BetError(f"seat {seat + 1} is empty")
        who = self.seats[seat].occupant
        for i in self.seats_of(who.name):
            self.vacate(i)
        self.standing.append(who)
        return who

    def sit_down(self, occupant: Occupant, seat: int) -> None:
        """R2.13 take an open seat.

        The seat can be taken as soon as one is free, but a shoe that is already
        under way has to finish before the new arrival may bet -- that is what
        "no mid-shoe entry" actually means at a table. `waiting_for_shoe` marks
        the gap.
        """
        if self.state is not None:
            raise BetError("wait for this hand to finish")
        if occupant not in self.standing:
            raise BetError(f"{occupant.name} is not standing at this table")
        if self.seats[seat].occupied:
            raise BetError(f"seat {seat + 1} is taken by {self.seats[seat].label}")
        self.standing.remove(occupant)
        self.seats[seat].occupant = occupant
        self.seats[seat].sitting_out = False
        self.seats[seat].waiting_for_shoe = not self.new_shoe

    def remove(self, seat: int) -> Occupant | None:
        """Send whoever is in this seat away from the table for good.

        Different from stand_up (R2.18), which keeps them at the rail. Any bet
        that has not been dealt to comes back to them first.
        """
        if self.state is not None:
            raise BetError("wait for this hand to finish")
        seat_obj = self.seats[seat]
        if not seat_obj.occupied:
            raise BetError(f"seat {seat + 1} is empty")
        who = seat_obj.occupant
        for i in self.seats_of(who.name):
            self.cancel_bets(i)
            self.vacate(i)
        return who

    def arrive_standing(self, name: str, buy_in: int) -> Occupant:
        """R2.18 somebody walks up to the table and buys chips.

        Arriving is not joining: they stand at the rail with their chips and take
        a seat when a new shoe starts (R2.13). That keeps the no-mid-shoe-entry
        rule intact while still letting a person turn up at any time.
        """
        # Only people have to have distinct names -- sharing one with a character
        # is fine, and the display marks which is which.
        if name.casefold() in self.human_names():
            raise BetError(f"somebody called {name} is already at this table")
        occupant = Occupant(name)
        # R2.8 the same mix a seated player is given: 20 x $5, the rest in $25
        for denom, n in default_buy_in(buy_in).counts.items():
            occupant.chips.counts[denom] += n
        occupant.bought_in = buy_in
        self.standing.append(self.remember(occupant))
        return occupant

    def standing_by_name(self, name: str) -> Occupant | None:
        for occ in self.standing:
            if occ.name.casefold() == name.casefold():
                return occ
        return None

    def occupied_seats(self) -> list[int]:
        return [i for i, s in enumerate(self.seats) if s.occupied]

    def empty_seats(self) -> list[int]:
        return [i for i, s in enumerate(self.seats) if not s.occupied]

    def _take_name(self) -> str:
        name = self._names[self._next_name % len(self._names)]
        self._next_name += 1
        return name

    @property
    def dealer_tokes(self) -> int:
        """Everything every dealer has collected this session."""
        return self.dealer.tokes + sum(d.tokes for d in self.past_dealers)

    def shift_due(self) -> bool:
        minutes = self.rules.dealer_shift_minutes
        if minutes <= 0 or self.state is not None:
            return False
        return self.dealer.minutes(self.clock()) >= minutes

    def rotate_dealer(self) -> tuple[DealerShift, DealerShift]:
        """Swap in the next dealer. Only legal between rounds.

        R1.6 changing dealers partway through a shoe costs a burn card. If the
        shoe was just shuffled its own burn already covers it, so no second card
        comes off.
        """
        if self.state is not None:
            raise BetError("the dealer cannot change partway through a hand")
        untouched = self.shoe.remaining == (
            self.rules.deck_size - self.rules.burn_cards_per_shuffle)
        if not untouched:
            self.shoe.discard_cards([self.shoe.draw()])
            self.shoe.burned += 1
        outgoing = self.dealer
        self.past_dealers.append(outgoing)
        self.dealer = DealerShift(self._take_name(), self.clock())
        return outgoing, self.dealer

    # --- Chips outside the round ------------------------------------------
    def buy_in(self, seat: int, amount: int) -> None:
        """R2.8 take chips: 20 x $5 and the rest in $25.

        R2.13 a player already in their seat may buy more at any time between
        hands; only *joining* the table is held to the start of a shoe.
        """
        if self.state is not None:
            raise BetError("chips can only be bought between hands")
        if not self.seats[seat].occupied:
            raise BetError(f"seat {seat + 1} is empty, so there is nobody to buy in")
        try:
            stack = default_buy_in(amount)
        except ValueError as exc:
            raise BetError(str(exc)) from exc
        for denom, n in stack.counts.items():
            self.seats[seat].chips.counts[denom] += n
        self.seats[seat].occupant.bought_in += amount

    def exchange(self, seat: int, denom_from: int, count: int, denom_to: int) -> None:
        """R2.9 change denominations with the dealer. Legal at any time."""
        self.seats[seat].chips.exchange(denom_from, count, denom_to)

    def tip(self, seat: int, amount: int) -> None:
        """R2.10 hand the dealer chips outright. The money leaves play immediately."""
        self.seats[seat].chips.pay_with_change(amount)
        self.dealer.tokes += amount

    # --- Betting -----------------------------------------------------------
    def place_bets(
        self,
        seat: int,
        base: int,
        push22: int = 0,
        buster: int = 0,
        toke_base: int = 0,
        toke_push22: int = 0,
        toke_buster: int = 0,
    ) -> SeatBets:
        """Validate the bets (R2.2 - R2.4, R2.11) and take the chips off the rack.

        `toke_base` is the *total* the dealer ends up with riding on the base
        spot, not an amount to add. A toke that pushed is already sitting out
        there (R2.11), so asking for the same total moves no chips, asking for
        more tops it up, and asking for less -- `toke_base=0` in particular --
        takes the difference back.
        """
        r = self.rules
        if not r.min_bet <= base <= r.max_bet:
            raise BetError(
                f"base bet must be between {format_money(r.min_bet)} "
                f"and {format_money(r.max_bet)}"
            )
        for name, amount in (("PUSH 22", push22), ("BUSTER", buster)):
            if not 0 <= amount <= r.max_side_bet:
                raise BetError(f"{name} must be between $0 and {format_money(r.max_side_bet)}")
        # R2.4 a side bet needs a base bet behind it. The base bet is mandatory
        # anyway, so this only bites if that ever becomes optional.
        if (push22 or buster) and base <= 0:
            raise BetError("a side bet requires a base bet")
        # R2.11 a toke bet is not held to the table minimum, but the player must
        # be on that spot themselves before they can back the dealer on it.
        for name, amount, backing in (("PUSH 22", toke_push22, push22),
                                      ("BUSTER", toke_buster, buster)):
            if amount and not backing:
                raise BetError(
                    f"you have no {name} bet of your own, so you cannot back the "
                    f"dealer on {name}"
                )
        for name, amount in (("toke base", toke_base), ("toke PUSH 22", toke_push22),
                             ("toke BUSTER", toke_buster)):
            if not 0 <= amount <= r.max_bet:
                raise BetError(f"{name} is out of range")

        s = self.seats[seat]
        resting = s.toke_on_table
        s.resting_before = resting       # so the bet can be taken back untouched
        top_up = toke_base - resting     # only the difference changes hands

        spots = {
            "base": base,
            "push22": push22,
            "buster": buster,
            "toke_base": max(top_up, 0),
            "toke_push22": toke_push22,
            "toke_buster": toke_buster,
        }
        snapshot = s.chips.copy()
        s.bet_chips = {}
        try:
            for spot, amount in spots.items():
                if amount:
                    handed, _ = s.chips.pay_with_change(amount)
                    s.bet_chips[spot] = handed
        except ValueError as exc:
            s.chips = snapshot
            s.bet_chips = {}
            raise BetError(str(exc)) from exc

        if top_up < 0:
            s.chips.receive(-top_up)     # the player pulled some of it back

        s.committed = sum(spots.values())
        s.toke_on_table = 0
        s.pending = SeatBets(
            base=base,
            push22=push22,
            buster=buster,
            toke_base=toke_base,
            toke_push22=toke_push22,
            toke_buster=toke_buster,
        )
        return s.pending

    # --- Running a round ---------------------------------------------------
    def start_round(self, bets: list[SeatBets] | None = None) -> RoundState:
        """Deal to whoever has money out.

        With no argument, every seat holding a pending bet joins the round, in
        seat order (R3.1). Seats that are empty or sitting out (R2.17) are
        skipped, and `_round_seats` remembers the mapping so settlement can find
        its way back to the right rack.
        """
        if bets is None:
            self._round_seats = [i for i, s in enumerate(self.seats) if s.pending]
            bets = [self.seats[i].pending for i in self._round_seats]
            if not bets:
                raise BetError("nobody has a bet out")
        else:
            self._round_seats = list(range(len(bets)))
        self.state = RoundState(self.rules, self.shoe, bets)
        return self.state

    def cancel_bets(self, seat: int | None = None) -> int:
        """Take back bets that have not been dealt to, and say how much came back.

        With no argument every seat is cleared, which is what happens when the
        session ends before a card comes out. With a seat, only that box is
        taken back -- used when a player goes back to re-do one of their bets.

        The session can end after bets are down but before a card comes out --
        somebody quits at the betting screen. Those chips never had a chance to
        win or lose, so leaving them on the felt would quietly take money off
        everyone at the table, the computer players included.
        """
        if self.state is not None:
            raise BetError("the hand is already in progress")
        returned = 0
        wanted = self.seats if seat is None else [self.seats[seat]]
        for seat in wanted:
            if not seat.pending:
                continue
            if seat.committed:
                seat.chips.receive(seat.committed)
                returned += seat.committed
            # A toke that was already resting stays resting; only what came off
            # the rack this time goes back to it (R2.11).
            seat.toke_on_table = min(seat.resting_before, seat.pending.toke_base)
            seat.pending = None
            seat.bet_chips = {}
            seat.committed = 0
            seat.resting_before = 0
        return returned

    def table_seat_of(self, round_index: int) -> int:
        return self._round_seats[round_index]

    def round_index_of(self, seat: int) -> int | None:
        """Where this table seat sits in the current round, if it is playing."""
        try:
            return self._round_seats.index(seat)
        except ValueError:
            return None

    def round_seat(self, seat: int):
        """The RoundState seat for this table seat, or None if it is sitting out."""
        idx = self.round_index_of(seat)
        if self.state is None or idx is None:
            return None
        return self.state.seats[idx]

    def current_seat(self) -> int | None:
        """Which table seat has to act right now, if any."""
        st = self.state
        if st is None or st.is_over or st.is_chance_node():
            return None
        if st.phase is Phase.INSURANCE:
            return self.table_seat_of(st.insurance_seat)
        if st.phase is Phase.PLAYER and st.current() is not None:
            return self.table_seat_of(st.current_seat)
        return None

    def deal_next(self):
        return self.state.deal_next()

    def legal_actions(self) -> list[Action]:
        """The round's legal moves, minus anything this seat cannot pay for.

        A paid double or a paid ten-split needs real chips on the felt, so a
        short rack simply is not offered the choice.
        """
        if self.state is None:
            return []
        acts = self.state.legal_actions()
        if self.state.phase is Phase.INSURANCE:
            round_index = self.state.insurance_seat
        elif self.state.phase is Phase.PLAYER:
            round_index = self.state.current_seat
        else:
            return acts
        s = self.seats[self.table_seat_of(round_index)]
        return [a for a in acts if s.chips.total >= self.state.action_cost(a)]

    def act(self, action: Action) -> None:
        """Apply a player action, taking the chips it costs as it goes."""
        st = self.state
        if st.phase is Phase.INSURANCE and action is Action.TAKE_INSURANCE:
            # R5.4 the premium is half the base bet, rounded up (R2.6).
            s = self.seats[self.table_seat_of(st.insurance_seat)]
            premium = round_up_payment(st.seats[st.insurance_seat].bets.base // 2)
            handed, _ = s.chips.pay_with_change(premium)
            s.bet_chips["insurance"] = handed
            s.committed += premium
            st.apply_action(action)
            return

        acting = st.current_seat if st.phase is Phase.PLAYER else None
        st.apply_action(action)
        # R4.3 / R4.4 a paid double or a paid split adds the player's own money
        # to the box; it rides next to the base bet.
        if acting is not None and st.player_owes:
            s = self.seats[self.table_seat_of(acting)]
            handed, _ = s.chips.pay_with_change(st.player_owes)
            base = s.bet_chips.setdefault("base", {})
            for denom, n in handed.items():
                base[denom] = base.get(denom, 0) + n
            s.committed += st.player_owes
            st.player_owes = 0

    def finish_round(self) -> list[SeatResult]:
        """Settle, move the chips, sweep the cards, and reshuffle if the cut card is out."""
        if self.state is None or not self.state.is_over:
            raise RuntimeError("the round is not finished")

        results = settle_round(self.state)
        for round_index, res in enumerate(results):
            s = self.seats[self.table_seat_of(round_index)]
            base_chips = s.bet_chips.get("base")
            for hr in res.hands:
                if hr.to_player:
                    self._pay(s, hr.to_player, base_chips)
                self.dealer.tokes += hr.to_dealer
                s.toke_on_table += hr.left_on_table
            if res.insurance_to_player:
                self._pay(s, res.insurance_to_player, s.bet_chips.get("insurance"))
            for side, spot in ((res.push22, "push22"), (res.buster, "buster")):
                if side and side.to_player:
                    self._pay(s, side.to_player, s.bet_chips.get(spot))
                if side:
                    self.dealer.tokes += side.to_dealer
            s.bet_chips = {}
            s.committed = 0
            s.pending = None

        self.new_shoe = False
        self.shoe.discard_cards(self.state.all_cards_on_table())
        # The hands stay readable for the settlement screen, but the cards
        # themselves are now in the tray -- anything counting them must stop.
        self.state.swept = True
        if self.shoe.cut_card_seen:
            self.shoe.shuffle()     # R1.4 the yellow card ends the shoe
            self.new_shoe = True    # R2.13 the window for anyone to join
            for seat in self.seats:
                seat.waiting_for_shoe = False   # they have waited it out
        self.state = None
        self.rounds_played += 1
        self.dealer.rounds += 1
        return results

    @staticmethod
    def _pay(seat: TableSeat, amount: int, bet_chips: dict[int, int] | None) -> None:
        """R2.12 pay in the denominations the player bet with, colouring up as needed."""
        for denom, n in payout_chips(amount, bet_chips).items():
            seat.chips.counts[denom] += n

    # --- Convenience -------------------------------------------------------
    def play_round_auto(self, bets: list[SeatBets], policy) -> list[SeatResult]:
        """Run a whole round, asking `policy(state)` whenever a decision is due."""
        self.start_round(bets)
        while not self.state.is_over:
            if self.state.is_chance_node():
                self.deal_next()
            else:
                self.act(policy(self.state))
        return self.finish_round()
