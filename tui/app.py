"""The interactive table. Reads keys, calls the engine, draws the result."""

from __future__ import annotations

import random
import time

from rich.console import Console
from rich.panel import Panel
from rich.padding import Padding
from rich.table import Table as RichTable
from rich.text import Text

from engine.cards import value_of
from engine.chips import format_money, round_up_payment
from engine.config import DEFAULT_RULES, DOLLAR, Rules
from engine import scripted
from engine.payout import Outcome
from engine.players import Bot, by_name, roster, search
from engine.state import Action, Phase
from engine.table import BetError, ChipsError, Occupant, Table
from .chatter import big_hand, say, talk, will_move, win_tier
from .format import parse_money
from .render import (render_character_list, render_dealer_says,
                     render_payouts, render_results, render_screen,
                     render_session, render_shift_change)

# R9.6 seconds between the dealer's cards. Long enough to actually read the card
# that just landed; a fast flip makes a five-card draw impossible to follow.
DEALER_PACE = 1.0

# R9.6 how long a computer player's decision sits on screen before it is applied.
# Without this the other seats resolve faster than anyone can read them.
REVEAL_PACE = 0.8      # R3.7 turning one seat's hand over at settlement
SCENARIO_BUY_IN = 2000 * DOLLAR   # enough for any stacked deck's bets
BOT_PACE = 1.2

ACTION_KEYS = {
    "h": Action.HIT,
    "s": Action.STAND,
    "d": Action.DOUBLE,
    "p": Action.SPLIT,
}


class App:
    def __init__(self, console: Console | None = None, seed: int | None = None,
                 scenario: str | None = None, shift_minutes: float | None = None,
                 clock=None):
        self.console = console or Console()
        self.rules = DEFAULT_RULES
        self.table: Table | None = None
        self.rng = random.Random(seed)
        self.scenario = scripted.get(scenario) if scenario else None
        self.shift_minutes = shift_minutes
        self.clock = clock
        # R2.1 seat -> ("human", name) or ("bot", character name) or None
        self.seat_plan: list[tuple[str, str] | None] = [None] * 5
        self.num_seats = 1
        # R2.11 each human seat remembers its own last bet
        self.seat_bets: dict[int, dict] = {}
        # Which seat the person at the keyboard is working from. The betting
        # screen sets it, but the table commands are also reachable from the
        # rail (R9.3), where that screen has never run.
        self._seat_index = 0
        self.watcher_name: str | None = None      # R2.18 starts at the rail
        # A panel that takes over the whole screen for one redraw, so a long
        # character list is not fighting the table for space.
        self._overlay = None
        # Every spot starts empty. The bet is the player's to make -- putting a
        # base bet up for them means they can deal a round they never chose the
        # size of (Junze, 2026-09-08). After a round, this holds what they last
        # played, so a repeat is still just one keypress.
        self.last_bets = {"base": 0, "push22": 0, "buster": 0,
                          "toke_base": 0, "toke_push22": 0, "toke_buster": 0}

    # --- Setup -------------------------------------------------------------
    def setup(self) -> None:
        c = self.console
        c.clear()
        c.print(Panel("[bold]Free Bet Blackjack[/bold] - Jamul Casino\n"
                      "[dim]two decks - dealer hits soft 17 - dealer 22 pushes[/dim]",
                      border_style="green4", padding=(1, 4)))
        c.print(render_payouts())

        while True:
            raw = c.input("\n  Table minimum - [bold]25[/bold] or 50? [$25] ").strip()
            if raw in ("", "25", "$25"):
                minimum = 25 * DOLLAR
                break
            if raw in ("50", "$50"):
                minimum = 50 * DOLLAR
                break
            c.print("  [red]Pick 25 or 50.[/red]")

        # R2.14 how long each dealer works. Asked here rather than on the command
        # line so it can be changed without leaving the game.
        if self.shift_minutes is None:
            while True:
                raw = c.input("  Change dealers every how many minutes? "
                              "\\[10] [dim](0 = same dealer all night)[/dim] ").strip()
                try:
                    self.shift_minutes = 10.0 if raw == "" else float(raw)
                    if self.shift_minutes < 0:
                        raise ValueError("that has to be zero or more")
                    break
                except ValueError as exc:
                    c.print(f"  [red]{exc}[/red]")

        if not any(self.seat_plan):
            self._plan_seats()
        if self.scenario:
            self.seat_plan = self._solo_for_scenario(self.seat_plan)
        # The single-seat table only exists for one person sitting in seat 1.
        # Counting heads is not enough: one person who chose seat 3 still needs
        # a five-seat table, or _seat_everyone walks past them and the round
        # opens with nobody playing (Junze, 2026-09-09).
        taken = [i for i, s in enumerate(self.seat_plan) if s]
        self.num_seats = 1 if taken in ([], [0]) else 5

        overrides = {"min_bet": minimum, "dealer_shift_minutes": self.shift_minutes,
                     "num_seats": self.num_seats}
        self.rules = Rules(**overrides)
        shoe = None
        if self.scenario:
            shoe = scripted.ScriptedShoe(
                self.rules, scripted.cards(self.scenario.script), self.rng)
        kwargs = {"shoe": shoe}
        if self.clock is not None:
            kwargs["clock"] = self.clock
        self.table = Table(self.rules, self.rng, **kwargs)
        if self.scenario:
            # The stacked deck was built around a particular set of bets, and
            # they have to be here before _seat_everyone copies last_bets into
            # each seat -- otherwise the seat keeps the empty spots it was born
            # with and the scenario opens with nothing on the layout.
            for spot, value in self.scenario.bets.items():
                self.last_bets[spot] = value
        self._seat_everyone()
        if self.watcher_name:
            self.table.arrive_standing(self.watcher_name, 0)   # buys in with `i`
        c.print()
        c.print(render_dealer_says(self.table.dealer.name,
                                   say("arrive", self.rng, name=self.table.dealer.name)))
        c.input("\n  [dim]enter to sit down[/dim] ")

    def _plan_seats(self) -> None:
        """R2.1 who sits where: a person, a named character, or nobody.

        Walk the five seats, then show the whole thing back and let any of them
        be changed. Getting to seat 4 and realising seat 1 is wrong should not
        mean starting over.
        """
        c = self.console
        c.print("\n  [bold]Seats[/bold]  [dim](1 is on the dealer's left, "
                "cards go 1 to 5)[/dim]")
        c.print("  [dim]enter a name for a person, a character's name for a "
                "computer player,\n  or just enter to leave the seat empty. "
                "type [/dim][bold]-[/bold][dim] to go back a seat[/dim]")
        c.print("  [dim]type part of a character's name to find them, or "
                "[/dim][bold]?[/bold][dim] to see them all with their bios[/dim]")
        c.print(render_character_list(roster()))

        i = 0
        while i < 5:
            answer = self._ask_seat(i)
            if answer == "back":
                i = max(0, i - 1)
                continue
            self.seat_plan[i] = answer
            i += 1

        while not self._confirm_seats():
            pass

        if not any(p and p[0] == "human" for p in self.seat_plan):
            # R2.18 a full table is no reason not to be here
            raw = c.input("\n  No seat for you. Your name, to watch from the rail: "
                          ).strip()
            self.watcher_name = raw or "You"
            if not any(self.seat_plan):
                c.print("  [yellow]An empty table with nobody to play. "
                        "Putting you in seat 1.[/yellow]")
                self.seat_plan[0] = ("human", self.watcher_name)
                self.watcher_name = None

    def pick_character(self, query: str, among=None, taken=()):
        """Turn what somebody typed into one character.

        Returns (character, what to show). The caller decides where to put the
        panel, because printing it here got it wiped by the next redraw.

        Bios are shown wherever a character is chosen, because the names on their
        own say nothing about how any of them play (Junze, 2026-09-06).
        """
        pool = list(among) if among is not None else roster()
        if query.strip() in ("?", "list"):
            return None, render_character_list(pool, taken=taken, compact=True,
                                               title="Characters")
        found = search(query, pool)
        if not found:
            return None, Text(f"Nobody here called {query!r}. "
                              f"Type ? to see everybody.", style="yellow")
        if len(found) > 1:
            return None, render_character_list(
                found, taken=taken, compact=True,
                title=f"{len(found)} match {query!r} -- type more of the name")
        ch = found[0]
        return ch, render_character_list([ch], taken=taken, title=ch.name)

    def _ask_seat(self, index: int):
        """One seat. Returns the plan entry, or the string "back"."""
        c = self.console
        current = self.seat_plan[index]
        shown = f" [dim](now: {self._describe_plan(current)})[/dim]" if current else ""
        while True:
            raw = c.input(f"    seat {index + 1}:{shown} ").strip()
            if raw in ("-", "back"):
                return "back"
            if not raw:
                return None
            if raw in ("?", "list"):
                seated = {p[1] for j, p in enumerate(self.seat_plan)
                          if p and p[0] == "bot" and j != index}
                c.print(render_character_list(roster(), taken=seated))
                continue

            # An exact name only. The whole cast is printed above this prompt,
            # so there is nothing to search for, and matching fragments would
            # turn a typo into a person nobody meant to create.
            match = {ch.name.casefold(): ch for ch in roster()}.get(raw.casefold())
            if match:
                seated = any(p == ("bot", match.name)
                             for j, p in enumerate(self.seat_plan) if j != index)
                if seated:
                    kind = c.input(
                        f"      [dim]the character {match.name} is already at "
                        f"this table.[/dim] a [bold]p[/bold]erson called "
                        f"{raw}? [p]/n ").strip().lower()
                    if kind in ("n", "no"):
                        continue
                else:
                    kind = c.input(
                        f"      [dim]{match.name} is one of the characters.[/dim] "
                        f"[bold]c[/bold]haracter or [bold]p[/bold]erson? [c] "
                    ).strip().lower()
                    if kind in ("", "c", "character"):
                        return ("bot", match.name)

            # R2.1 only people have to have distinct names
            if any(p and p[0] == "human" and p[1].casefold() == raw.casefold()
                   for j, p in enumerate(self.seat_plan) if j != index):
                c.print(f"    [red]somebody called {raw} is already "
                        f"at this table.[/red]")
                continue
            return ("human", raw)

    @staticmethod
    def _describe_plan(plan) -> str:
        if plan is None:
            return "empty"
        kind, name = plan
        return f"{name} (bot)" if kind == "bot" else name

    def _confirm_seats(self) -> bool:
        """Show the table back and let any seat be changed before dealing."""
        c = self.console
        grid = RichTable.grid(padding=(0, 2))
        grid.add_column(justify="right", style="dim", width=6)
        grid.add_column(width=4)
        grid.add_column()
        for i, plan in enumerate(self.seat_plan):
            if plan is None:
                grid.add_row(f"seat {i + 1}", "", Text("empty", style="dim"))
            else:
                kind, name = plan
                grid.add_row(f"seat {i + 1}",
                             Text("bot", style="dim cyan") if kind == "bot" else "",
                             Text(name, style="bold"))
        c.print()
        c.print(Panel(grid, title="The table", border_style="grey37", padding=(0, 1)))
        raw = c.input("  [bold]enter[/bold] to deal, or a seat number to change it "
                      "[dim](1-5)[/dim] ").strip()
        if not raw:
            return True
        if raw in "12345" and len(raw) == 1:
            index = int(raw) - 1
            answer = self._ask_seat(index)
            if answer != "back":
                self.seat_plan[index] = answer
        else:
            c.print(f"  [yellow]Type a seat number to change it, or just enter "
                    f"to start. ({raw!r} is neither.)[/yellow]")
        return False

    def _seat_everyone(self) -> None:
        """Sit everybody down. People start with no chips and buy in with `i`."""
        for i, plan in enumerate(self.seat_plan[:self.num_seats]):
            if plan is None:
                continue
            kind, name = plan
            if kind == "human":
                # R2.8 people arrive with nothing and buy in with `i`. A scenario
                # is a demonstration rather than a session: its bets are already
                # on the layout when the screen opens, so the chips have to be
                # there to cover them or the spots are cleared before it starts.
                self.table.seat_occupant(
                    i, Occupant(name), SCENARIO_BUY_IN if self.scenario else 0)
                self.seat_bets[i] = dict(self.last_bets)
            else:
                ch = by_name(name)
                bot = Bot(ch, self.rng)
                self.table.seat_occupant(i, Occupant(ch.name, bot),
                                         ch.buy_in * DOLLAR)
                bot.started_with = self.table.seats[i].bankroll

    def human_seats(self) -> list[int]:
        return [i for i, s in enumerate(self.table.seats)
                if s.occupied and not s.occupant.is_bot]

    # --- The dealer (R2.14) ------------------------------------------------
    def _maybe_change_dealer(self) -> None:
        if not self.table.shift_due():
            return
        outgoing, incoming = self.table.rotate_dealer()
        c = self.console
        c.clear()
        c.print(render_screen(None, self.table))
        c.print(render_shift_change(
            outgoing, incoming,
            say("leave", self.rng, name=outgoing.name),
            say("arrive", self.rng, name=incoming.name)))
        c.input("\n  [dim]enter to carry on[/dim] ")

    def _settlement_chatter(self, state, results, staked=None) -> list:
        """What the dealer has to say about the round. At most two lines."""
        events = [self._win_event(state, results, staked),
                  self._toke_event(state, results)]
        if state.dealer_blackjack:
            events.append("dealer_blackjack")
        elif not state.dealer.is_bust and state.dealer.total == 21:
            events.append("dealer_21")

        name = self.table.dealer.name
        out = []
        for event in [e for e in events if e][:2]:
            line = say(event, self.rng, name=name)
            if line:
                out.append(render_dealer_says(name, line))
        return out

    @staticmethod
    def _best_side_win(results):
        """The longest odds the *player* collected on this round, and any bonus.

        A toke bet coming in is the dealer's win, not the player's, so it does
        not count towards the congratulations.
        """
        odds, bonus = None, 0
        for res in results:
            for side in (res.push22, res.buster):
                if side and side.won and side.bet and side.to_player:
                    odds = max(odds or 0, side.odds or 0)
                    bonus = max(bonus, side.bonus)
        return odds, bonus

    def _win_event(self, state, results, staked=None):
        """A side bet coming in, or failing that, a seat that simply paid a lot.

        The second case is what catches a hand that split three times and free
        doubled the halves: no long odds anywhere, but a large pile of chips.
        Judged per seat, so one person's big round is not diluted by the table.
        """
        event = win_tier(*self._best_side_win(results))
        if event:
            return event
        for i, res in enumerate(results):
            out = staked[i] if staked else self.table._committed_this_round
            if big_hand(res.to_player - out, state.seats[i].bets.base):
                return "win_hands"
        return None

    @staticmethod
    def _toke_event(state, results):
        """R2.11 the dealer reacts to a bet placed on their behalf, win or lose."""
        placed = any(s.bets.toke_base or s.bets.toke_push22 or s.bets.toke_buster
                     for s in state.seats)
        if not placed:
            return None
        if any(r.to_dealer for r in results):
            return "toke_win"
        for i, res in enumerate(results):
            bets = state.seats[i].bets
            lost = any(hr.hand.toke_stake and hr.outcome is Outcome.LOSE
                       for hr in res.hands)
            lost = lost or (bets.toke_push22 and not res.push22.won)
            lost = lost or (bets.toke_buster and not res.buster.won)
            if lost:
                return "toke_lose"
        # A pushed toke is neither, so nothing is said about it.
        return None

    def _solo_for_scenario(self, plan):
        """A stacked deck is dealt to one seat, so clear the rest of the table.

        The scripts in engine/scripted.py are written in the order the engine
        asks for cards with a single player: p1, upcard, p2. Seat anybody else
        and every card after the first lands somewhere it was not meant to --
        the upcard becomes a player's second card, and the hand the scenario
        exists to demonstrate never happens at all.
        """
        people = [s for s in plan if s]
        if len(people) <= 1:
            return plan
        keep = next((s for s in people if s[0] == "human"), people[0])
        self.console.print(
            f"  [yellow]A stacked deck is dealt for one seat, so only "
            f"{keep[1]} takes a chair for this one.[/yellow]")
        return [s if s is keep else None for s in plan]

    def _scenario_banner(self):
        """Stays up while the stacked deck still has cards left to deal."""
        sc = self.scenario
        if not sc or not getattr(self.table.shoe, "script", None):
            return None
        body = Text()
        body.append(sc.title + "\n", style="bold")
        body.append("what to press:  ", style="dim")
        body.append(sc.hint, style="bold yellow")
        body.append(f"\n{len(self.table.shoe.script)} scripted cards left; after "
                    f"that the shoe deals at random again.", style="dim")
        return Panel(body, title=f"Scenario: {sc.key}", border_style="yellow",
                     padding=(0, 1))

    # --- Betting -----------------------------------------------------------
    def spectator_screen(self, occupant, seat_index: int | None = None) -> str:
        """R9.3 a person who cannot bet this round but is still at the table.

        Two ways to end up here: standing at the rail (R2.18), or sitting in a
        seat that is waiting out the shoe (R2.13). Either way they need somewhere
        to act from -- without this they could not even quit until the shoe ran
        out.
        """
        c, t = self.console, self.table
        note = None
        while True:
            overlay, self._overlay = self._overlay, None
            c.clear()
            if overlay is not None:
                c.print(overlay)
                c.print("  [dim]enter to go back to the table[/dim]")
                c.input("\n  watching > ")
                continue
            seated = seat_index is not None
            c.print(render_screen(None, t, note=note,
                                  rack_seat=seat_index if seated else 0,
                                  standing=None if seated else occupant))
            free = t.empty_seats()
            lines = Text("  ")
            if seated:
                lines.append(f"{occupant.name} has seat {seat_index + 1}, ",
                             style="bold")
                lines.append("and is waiting for this shoe to finish before "
                             "betting.\n", style="dim")
            else:
                lines.append(f"{occupant.name} is standing at the rail.\n",
                             style="bold")
                if free:
                    lines.append(f"  seats open: "
                                 f"{', '.join(str(i + 1) for i in free)}\n",
                                 style="dim")
                else:
                    lines.append("  the table is full -- wait for somebody to "
                                 "leave\n", style="dim")
            c.print(lines)
            options = [("enter", None, "watch the next round")]
            if not seated:
                options.append(("sit", "4", "take an open seat"))
            options += [("add", "Ernie", "invite somebody"),
                        ("q", None, "quit")]
            help_line = Text()
            for word, arg, what in options:
                help_line.append("  ")
                help_line.append(word, style="bold")
                if arg:
                    help_line.append(f" {arg}", style="cyan")
                help_line.append(f" {what}", style="dim")
            c.print(help_line)

            raw = c.input("\n  watching > ").strip()
            note = None
            if raw == "":
                if not t.occupied_seats():
                    note = Text("Nobody is playing. Sit down, invite somebody, "
                                "or quit.", style="yellow")
                    continue
                return "watch"
            cmd, *args = raw.split()
            cmd = cmd.lower()
            if cmd == "q":
                return "quit"
            try:
                if cmd == "sit":
                    if seated:
                        note = Text("You already have a seat.", style="yellow")
                        continue
                    seat = self._seat_arg(args)
                    t.sit_down(occupant, seat)
                    self.seat_bets[seat] = dict(self.last_bets)
                    return "sat"
                if cmd == "add":
                    note = self._table_command(cmd, args)
                    continue
            except (BetError, ValueError, KeyError) as exc:
                note = Text(str(exc), style="red")
                continue
            note = Text(f"Unknown command: {cmd}", style="yellow")

    def _anyone_playing(self) -> bool:
        """Is there a person at this table at all -- seated or at the rail?"""
        seated = any(s.occupied and not s.occupant.is_bot for s in self.table.seats)
        return seated or any(not o.is_bot for o in self.table.standing)

    def _watch_screen(self) -> str:
        """Nobody is playing. Watch the characters, or put somebody back in."""
        c, t = self.console, self.table
        note = None
        while True:
            overlay, self._overlay = self._overlay, None
            c.clear()
            if overlay is not None:
                c.print(overlay)
                c.print("  [dim]enter to go back to the table[/dim]")
                c.input("\n  watching > ")
                continue
            c.print(render_screen(None, t, note=note))
            c.print(Text("  Nobody is playing. You are watching.", style="bold"))
            line = Text()
            for word, arg, what in (("enter", None, "watch the next round"),
                                    ("add", "Mia", "put somebody at the table"),
                                    ("q", None, "quit")):
                line.append("  ")
                line.append(word, style="bold")
                if arg:
                    line.append(f" {arg}", style="cyan")
                line.append(f" {what}", style="dim")
            c.print(line)

            raw = c.input("\n  watching > ").strip()
            note = None
            if raw == "":
                if not t.occupied_seats():
                    note = Text("The table is empty. Put somebody in a seat, "
                                "or quit.", style="yellow")
                    continue
                return "watch"
            cmd, *args = raw.split()
            if cmd.lower() == "q":
                return "quit"
            if cmd.lower() == "add":
                try:
                    note = self._table_command("add", args)
                except (BetError, ValueError, KeyError) as exc:
                    note = Text(str(exc), style="red")
                continue
            note = Text(f"Unknown command: {cmd}", style="yellow")

    def betting_phase(self) -> str:
        """Take everyone's bets, in seat order.

        Returns "play" to deal, "skip" if nobody staked anything, or "quit".
        """
        self._maybe_change_dealer()
        # R2.16 a character who was invited takes a chair straight away rather
        # than loitering at the rail; R2.13 still makes them sit the shoe out.
        self.table.admit_waiting(self.rng)
        if not self._anyone_playing():
            # Every person has left or been removed. Somebody still has to be
            # able to stop the table, so keep a plain watching screen.
            what = self._watch_screen()
            if what == "quit":
                return "quit"
        for occupant in list(self.table.standing):
            if occupant.is_bot:
                continue
            if self.spectator_screen(occupant) == "quit":
                return "quit"
        # R2.13 a seat that is waiting out the shoe cannot bet, but the person in
        # it is still here and needs somewhere to act from.
        for i, seat in enumerate(self.table.seats):
            if (seat.occupied and not seat.occupant.is_bot
                    and seat.waiting_for_shoe):
                if self.spectator_screen(seat.occupant, seat_index=i) == "quit":
                    return "quit"
        asked: set[int] = set()
        preferred = None
        while True:
            if self._collect_bets(asked, preferred) == "quit":
                return "quit"
            preferred = None
            # Everyone is in. Show the whole layout before a card comes out --
            # the other seats bet after you pressed enter, so without this you
            # never got to see what you were playing against.
            if not self._human_bettors():
                break
            ready = self._confirm_deal()
            if ready == "quit":
                return "quit"
            if ready == "deal":
                break
            preferred = ready                # they went back to change a bet
            asked.discard(ready)

        return "play" if any(s.pending for s in self.table.seats) else "skip"

    def _collect_bets(self, asked: set, preferred) -> str | None:
        """Ask every seat that still owes a bet. "quit" ends the session.

        The list is recomputed each time round, because a betting screen can
        change the table underneath us: `move` puts the player in a different
        seat, `join` opens a second box, `stand` empties one, `remove` takes
        somebody away (R2.13 / R2.15 / R2.18).
        """
        while True:
            todo = [i for i, s in enumerate(self.table.seats)
                    if s.occupied and not s.pending and not s.sitting_out
                    and not s.waiting_for_shoe and i not in asked]
            if preferred is not None:
                i, preferred = preferred, None       # they asked to go there
            elif todo:
                i = todo[0]
            else:
                return None

            seat = self.table.seats[i]
            if seat.occupant.is_bot:
                asked.add(i)
                self._bot_bets(i)
                continue

            pending = self.betting_screen(i)
            if pending is None:
                return "quit"
            if pending == "switch":
                # They jumped to another of their own boxes. This one still owes
                # a bet, so leave it in the queue -- and the box they jumped to
                # keeps whatever is already down until they type a new amount.
                preferred = self._seat_index
                asked.discard(preferred)
                continue
            asked.add(i)
            if pending == "stood up":           # R2.18 they left the seat
                continue
            target = self._seat_index           # where they actually ended up
            asked.add(target)
            if pending == "sit out":            # R2.17
                # A bet typed here is already out on the layout, so sitting the
                # round out has to take it back off.
                self.table.cancel_bets(target)
                self.seat_bets.pop(target, None)
                self.table.seats[target].sitting_out = True
                continue
            self.table.seats[target].sitting_out = False
            try:
                if self.table.seats[target].pending:
                    # Re-doing a bet that was already down: take the old one back
                    # first, so the chips are not charged twice.
                    self.table.cancel_bets(target)
                self.table.place_bets(target, base=pending["base"],
                                      push22=pending["push22"],
                                      buster=pending["buster"],
                                      toke_base=pending["toke_base"],
                                      toke_push22=pending["toke_push22"],
                                      toke_buster=pending["toke_buster"])
            except BetError as exc:
                self.console.print(f"  [red]{exc}[/red]")
                time.sleep(1.5)

    def _human_bettors(self) -> list[int]:
        """Seats held by a person who is actually betting this round."""
        return [i for i, s in enumerate(self.table.seats)
                if s.occupied and not s.occupant.is_bot
                and not s.waiting_for_shoe and (s.pending or s.sitting_out)]

    def _confirm_deal(self) -> str | int:
        """The last look before the cards come out.

        Returns "deal", "quit", or the seat whose bet should be revisited.
        """
        c = self.console
        note = None
        while True:
            overlay, self._overlay = self._overlay, None
            c.clear()
            if overlay is not None:
                c.print(overlay)
                c.print("  [dim]enter to go back to the table[/dim]")
                c.input("\n  ready > ")
                continue
            c.print(render_screen(None, self.table, note=note,
                                  rack_seat=self._first_human()))
            mine = self._human_bettors()
            line = Text("  Everyone is in.")
            for word, arg, what in (("enter", None, "deal"),
                                    ("go", str(mine[0] + 1) if mine else "1",
                                     "change a bet"),
                                    ("q", None, "quit")):
                line.append("   ")
                line.append(word, style="bold")
                if arg:
                    line.append(f" {arg}", style="cyan")
                line.append(f" {what}", style="dim")
            c.print(line)

            raw = c.input("\n  ready > ").strip()
            note = None
            if raw == "":
                return "deal"
            cmd, *args = raw.split()
            cmd = cmd.lower()
            if cmd == "q":
                return "quit"
            if cmd == "go":
                seats = [i for i, s in enumerate(self.table.seats)
                         if s.occupied and not s.occupant.is_bot
                         and not s.waiting_for_shoe]
                try:
                    target = self._seat_arg(args) if args else (
                        seats[0] if seats else None)
                except ValueError as exc:
                    note = Text(str(exc), style="red")
                    continue
                if target not in seats:
                    note = Text("That is not a seat you are betting from.",
                                style="yellow")
                    continue
                self._seat_index = target
                return target
            note = Text(f"Unknown command: {cmd}", style="yellow")

    def _bot_bets(self, seat_index: int) -> None:
        """R2.16 a character stakes what their personality says, or gets up."""
        seat = self.table.seats[seat_index]
        bot = seat.occupant.bot
        if bot.wants_to_leave(seat.bankroll, self.rules):
            self.table.vacate(seat_index)
            return
        base = min(bot.base_bet(self.rules), seat.bankroll)
        spare = seat.bankroll - base
        side = bot.side_bet(self.rules) if spare >= 5 * DOLLAR else 0
        toke = bot.toke_bet() if spare >= side + 5 * DOLLAR else 0
        # R2.10 a tip is handed over there and then. It is gone whether or not a
        # card is ever dealt, which is the point of a tip.
        tip = bot.wants_to_tip()
        if tip and seat.bankroll > base + tip:
            self.table.tip(seat_index, tip)
        try:
            self.table.place_bets(seat_index, base=base, push22=side, buster=0,
                                  toke_base=toke)
        except BetError:
            self.table.vacate(seat_index)       # cannot cover it after all

    def betting_screen(self, seat_index: int = 0):
        """One person's bets. None means quit, "sit out" means skip this round."""
        c = self.console
        self._seat_index = seat_index
        pending = dict(self.seat_bets.get(seat_index, self.last_bets))
        # R2.11 a toke that pushed is already on the layout, and `k` sets the
        # dealer's total, so the default has to cover what is already out there.
        pending["toke_base"] = max(pending["toke_base"],
                                   self.table.seats[seat_index].toke_on_table)
        note = None
        # R9.15 a bet carried over from last round is a bet, so it goes on the
        # layout now rather than sitting in the screen's head looking placed.
        # Anything that will not go down -- too big for the rack after a bad
        # round, over the limit after a rules change -- clears the spots instead.
        if self._place(pending) is not None:
            resting = self.table.seats[seat_index].toke_on_table
            pending = {k: 0 for k in pending}
            pending["toke_base"] = resting
            self._place(pending)
            note = Text("Your last bet is more than you have left, so the spots "
                        "are clear. Put up a new one.", style="yellow")
        while True:
            # A `move` or a `join` can put this player in a different seat part
            # way through their own betting screen, so re-read it every redraw
            # rather than trusting the seat we were called with.
            seat_index = self._seat_index
            overlay, self._overlay = self._overlay, None
            c.clear()
            if overlay is not None:
                # Something long to read. Give it the screen to itself rather
                # than letting the table push it off the top.
                c.print(overlay)
                c.print("  [dim]enter to go back to the table[/dim]")
            else:
                stack, on_layout = self._rack_preview(seat_index, pending)
                c.print(render_screen(None, self.table, note=note,
                                      banner=self._scenario_banner(),
                                      rack_seat=seat_index,
                                      rack_stack=stack, on_layout=on_layout))
                who = self.table.seats[seat_index].label
                c.print(Panel(self._bet_summary(pending),
                              title=f"Bets - seat {seat_index + 1}, {who}",
                              border_style="grey37", padding=(0, 1)))
                if not self.table.seats[seat_index].bankroll:
                    # R2.8 chips are bought at the table, so say so rather than
                    # letting somebody stare at an empty rack.
                    empty = Text("  You have no chips. ", style="yellow")
                    empty.append("i", style="bold")
                    empty.append(" 500", style="cyan")
                    empty.append(" buys in for $500.", style="yellow")
                    c.print(empty)
                c.print(self._command_help())
            raw = c.input("\n  > ").strip()
            note = None
            if overlay is not None and raw == "":
                continue      # they were reading, not asking to deal
            if raw == "":
                if not pending["base"]:
                    note = Text("Nothing on the base spot. Put a bet up first, "
                                "or sit this one out with o.", style="yellow")
                    continue
                refused = self._bet_rejection(pending)
                if refused is not None:
                    note = Text(refused, style="red")
                    continue
                self.seat_bets[seat_index] = dict(pending)
                self.last_bets = dict(pending)
                return pending
            cmd, *args = raw.split()
            cmd = cmd.lower()
            if cmd == "q":
                return None
            if cmd == "o":
                return "sit out"
            if cmd == "go":
                # R2.15 hop between the boxes you are playing, to change a bet
                # you have already put down.
                try:
                    target = self._seat_arg(args)
                except ValueError as exc:
                    note = Text(str(exc), style="red")
                    continue
                mine = self.table.seats_of(self.table.seats[seat_index].label)
                if target not in mine:
                    note = Text(f"Seat {target + 1} is not one of yours. "
                                f"You are playing {', '.join(str(i + 1) for i in mine)}.",
                                style="yellow")
                    continue
                # Keep what they had typed here, so hopping between boxes does
                # not quietly throw away an edit they had not confirmed yet.
                # A bet already down on the box they are going to stays down --
                # typing a new amount there is what changes it.
                self.seat_bets[seat_index] = dict(pending)
                self._seat_index = target
                return "switch"
            if cmd == "stand":
                try:
                    self.table.stand_up(seat_index)      # R2.18
                    return "stood up"
                except BetError as exc:
                    note = Text(str(exc), style="red")
                    continue
            try:
                note = self._bet_command(cmd, args, pending)
            except (ValueError, BetError, IndexError, KeyError) as exc:
                note = Text(str(exc), style="red")
            # `remove` can take away the very seat being bet from, so there may
            # be nothing left to ask about here.
            if not self.table.seats[self._seat_index].occupied:
                return "stood up"

    def _bet_command(self, cmd, args, pending) -> Text | None:
        keys = {"b": "base", "p": "push22", "u": "buster",
                "k": "toke_base", "kp": "toke_push22", "ku": "toke_buster"}
        # R2.11 the dealer can only be backed on a spot the player is already on
        backing = {"toke_push22": ("push22", "PUSH 22"),
                   "toke_buster": ("buster", "BUSTER")}
        if cmd in keys:
            spot = keys[cmd]
            amount = parse_money(args[0])
            if amount and spot in backing:
                own, label = backing[spot]
                if not pending[own]:
                    return Text(f"Put a {label} bet up yourself first "
                                f"(`{'p' if own == 'push22' else 'u'} 5`), then you can "
                                f"back the dealer on it.", style="yellow")
            was = pending[spot]
            if spot == "base" and not amount:
                # R2.4 taking the base bet down takes the whole layout with it:
                # the side bets and the dealer's ride have nothing to stand on.
                resting = self.table.seats[self._seat_index].toke_on_table
                had = any(v for k, v in pending.items() if k != "base")
                for key in pending:
                    pending[key] = 0
                self._place(pending)
                pending["toke_base"] = self.table.seats[self._seat_index].toke_on_table
                if had:
                    return Text("Your base bet is down, so everything else on "
                                "the spot came off with it.", style="yellow")
                return None
            pending[spot] = amount
            # The rules get first say: "over the limit" is more use than
            # "you cannot afford it" when the amount is both.
            refused = self._place(pending)
            if refused is not None:
                pending[spot] = was          # nothing moved, so say so plainly
                return Text(refused, style="red")
            # R2.11 the chip is on the layout now, so the dealer says something
            if spot.startswith("toke_") and amount > was:
                return render_dealer_says(
                    self.table.dealer.name,
                    say("toke_placed", self.rng, name=self.table.dealer.name))
            # Dropping your own bet takes the dealer's ride with it.
            for toke, (own, label) in backing.items():
                if spot == own and not amount and pending[toke]:
                    pending[toke] = 0
                    return Text(f"Your {label} bet is off, so the dealer's "
                                f"{label} came down too.", style="yellow")
            return None
        if cmd == "x":
            if len(args) != 3:
                return Text("Usage: x <from> <count> <to>   e.g.  x 25 2 5",
                            style="yellow")
            self.table.exchange(self._seat_index, parse_money(args[0]),
                                int(args[1]), parse_money(args[2]))
            return Text("Chips exchanged.", style="green")
        if cmd in ("ask", "nice", "rude", "move", "join", "drop", "add", "remove"):
            return self._table_command(cmd, args)
        if cmd == "i":
            # R2.13 already seated, so more chips can be bought between hands
            amount = parse_money(args[0])
            self.table.buy_in(self._seat_index, amount)
            return Text(f"Bought in for {format_money(amount)}.", style="green")
        if cmd == "t":
            amount = parse_money(args[0])
            self.table.tip(self._seat_index, amount)
            return render_dealer_says(self.table.dealer.name,
                                      say("tip", self.rng, name=self.table.dealer.name))
        return Text(f"Unknown command: {cmd}", style="yellow")

    # --- Talking to the table (R2.19) --------------------------------------
    def _seat_arg(self, args) -> int:
        if not args:
            raise ValueError("which seat?")
        index = int(args[0]) - 1
        if not 0 <= index < len(self.table.seats):
            raise ValueError(f"there is no seat {args[0]}")
        return index

    def _table_command(self, cmd, args) -> Text:
        t = self.table
        me = self._seat_index

        if cmd == "add":
            # R2.16 / R2.18 one way in for everybody. A name that belongs to a
            # character offers that character; anything else is a person, who
            # buys their chips at the table like everyone else.
            query = " ".join(args).strip()
            if not query or query in ("?", "list"):
                _, panel = self.pick_character("?", t.available_characters())
                self._overlay = panel
                return Text("")

            # An exact name only, the same rule as the seat planner. Matching
            # fragments turned a typo into a person nobody meant to create.
            ch = {c.name.casefold(): c for c in roster()}.get(query.casefold())
            if ch is not None:
                if ch.name in t.bot_names():
                    kind = self.console.input(
                        f"      [dim]the character {ch.name} is already at this "
                        f"table.[/dim] a [bold]p[/bold]erson called {query}? [p]/n "
                    ).strip().lower()
                    if kind in ("n", "no"):
                        return Text("")
                else:
                    kind = self.console.input(
                        f"      [dim]{ch.name} is one of the characters.[/dim] "
                        f"[bold]c[/bold]haracter or a [bold]p[/bold]erson called "
                        f"{query}? [c] ").strip().lower()
                    if kind in ("", "c", "character"):
                        bot = Bot(ch, self.rng)
                        t.queue_arrival(Occupant(ch.name, bot), ch.buy_in * DOLLAR)
                        bot.started_with = ch.buy_in * DOLLAR
                        return Text(f"{ch.name} is standing by, and takes a seat "
                                    f"when the next shoe starts.", style="green")

            # R2.8 a person arrives with nothing and buys chips with `i`
            who = t.arrive_standing(query, 0)
            return Text(f"{who.name} joins and is standing at the rail. They can "
                        f"buy chips and take a seat when the next shoe starts.",
                        style="green")


        index = self._seat_arg(args)
        seat = t.seats[index]

        if cmd == "move":
            released = t.move_seat(me, index)
            self.seat_bets[index] = self.seat_bets.pop(me, dict(self.last_bets))
            for i in released:
                self.seat_bets.pop(i, None)
            self._seat_index = index
            if released:
                # R2.15 boxes have to be side by side, so moving gives up the rest
                gave = ", ".join(str(i + 1) for i in released)
                return Text(f"You move to seat {index + 1}. Your extra "
                            f"{'box' if len(released) == 1 else 'boxes'} "
                            f"({gave}) came down and any bet on "
                            f"{'it' if len(released) == 1 else 'them'} is back "
                            f"in your rack.", style="green")
            return Text(f"You move to seat {index + 1}.", style="green")

        if cmd == "remove":
            who = t.seats[index].occupant
            gone = t.remove(index)
            note = Text(f"{gone.name} leaves the table.", style="green")
            if index == me or not t.seats_of(t.seats[me].label if
                                             t.seats[me].occupied else ""):
                mine = self.human_seats()
                self._seat_index = mine[0] if mine else 0
                return note
            return note

        if cmd == "drop":
            # R2.15 hand back an extra box. `stand` is for leaving altogether.
            mine = t.seats_of(t.seats[me].label)
            if index not in mine:
                return Text(f"Seat {index + 1} is not one of yours.", style="yellow")
            t.release_seat(index)
            if index == me:
                self._seat_index = t.seats_of(t.seats[mine[0] if mine[0] != index
                                                      else mine[1]].label)[0]
            return Text(f"You give up seat {index + 1}. Any bet on it comes back.",
                        style="green")

        if cmd == "join":
            # R2.15 no money is set aside: both boxes bet out of the one rack.
            t.take_extra_seat(me, index)
            self.seat_bets[index] = dict(self.last_bets)
            return Text(f"You take seat {index + 1} as a second hand. "
                        f"Both boxes bet from the same chips.", style="green")

        if not seat.occupied:
            return Text(f"Seat {index + 1} is empty.", style="yellow")
        if index == me:
            return Text("Talking to yourself is not a strategy.", style="yellow")

        who = seat.occupant
        if cmd == "nice":
            who.goodwill = min(1.0, who.goodwill + 0.25)
            said, reply = "compliment", "compliment_reply"
        elif cmd == "rude":
            who.goodwill = max(-1.0, who.goodwill - 0.4)
            said, reply = "insult", "insult_reply"
        else:
            return self._ask_to_move(index)

        out = Text()
        out.append(f"  {t.seats[me].label}: ", style="bold")
        out.append(f"\u201c{talk(said, self.rng, name=who.name)}\u201d\n",
                   style="italic")
        if who.is_bot:
            out.append(f"  {who.name}: ", style="bold cyan")
            out.append(f"\u201c{talk(reply, self.rng, name=who.name)}\u201d",
                       style="italic cyan")
        return out

    def _ask_to_move(self, index: int) -> Text:
        """R2.19 ask somebody to shift along, usually to free up a seat."""
        t = self.table
        who = t.seats[index].occupant
        out = Text()
        out.append(f"  {t.seats[self._seat_index].label}: ", style="bold")
        out.append(f"\u201c{talk('ask_move', self.rng, name=who.name)}\u201d\n",
                   style="italic")

        if not who.is_bot:
            out.append("  (they will have to move themselves)", style="dim")
            return out
        free = t.empty_seats()
        if not free:
            out.append(f"  {who.name}: ", style="bold cyan")
            out.append("\u201cWhere exactly would I go? The table is full.\u201d",
                       style="italic cyan")
            return out

        agreed = will_move(who.bot.character.agreeable, who.goodwill, self.rng)
        out.append(f"  {who.name}: ", style="bold cyan")
        out.append(f"\u201c{talk('agree_move' if agreed else 'refuse_move', self.rng)}"
                   f"\u201d", style="italic cyan")
        if agreed:
            t.move_seat(index, self.rng.choice(free))
        return out

    def _command_help(self):
        """The commands, a few to a line.

        Three styles on purpose: the word you type, the argument standing in for
        your own number or name, and the explanation. Running them together in
        one colour made `add Ernie` read as though Ernie were part of the command.
        """
        def cmd(word, arg=None, what=None):
            out = Text("  ")
            out.append(word, style="bold")
            if arg:
                out.append(f" {arg}", style="cyan")
            if what:
                out.append(f" {what}", style="dim")
            return out

        rows = [
            [cmd("enter", None, "deal"), cmd("b", "50", "base"),
             cmd("p", "5", "push22"), cmd("u", "5", "buster"),
             cmd("o", None, "sit out"), cmd("q", None, "quit")],
            [cmd("k / kp / ku", "5", "bet for the dealer, 0 removes it"),
             cmd("x", "25 2 5", "exchange chips")],
            [cmd("i", "200", "buy more"), cmd("t", "5", "tip the dealer")],
        ]
        if len(self.table.seats) > 1:
            rows[-1] += [cmd("move", "5", "change seats"),
                         cmd("stand", None, "leave seat")]
            rows += [
                [cmd("ask / nice / rude", "2", "talk to a seat"),
                 cmd("join", "2", "second hand next door"),
                 cmd("remove", "3", "send somebody away")],
                [cmd("add", "Ernie",
                     "invite a character or a person, ? lists the characters")],
            ]
        out = Text()
        for i, row in enumerate(rows):
            if i:
                out.append("\n")
            for part in row:
                out.append_text(part)
        return out

    def _bet_summary(self, pending) -> Text:
        seat = self.table.seats[self._seat_index]
        rows = [("base", pending["base"], "bold"),
                ("PUSH 22", pending["push22"], "bold"),
                ("BUSTER", pending["buster"], "bold"),
                ("dealer's base", pending["toke_base"], "magenta"),
                ("dealer's PUSH 22", pending["toke_push22"], "magenta"),
                ("dealer's BUSTER", pending["toke_buster"], "magenta")]
        out = Text()
        for name, amount, style in rows:
            out.append(f"{name:>18}  ", style="dim")
            out.append(format_money(amount) if amount else "-",
                       style=style if amount else "dim")
            out.append("\n")
        if seat.toke_on_table:
            out.append(f"{'already on the felt':>18}  ", style="dim")
            out.append(format_money(seat.toke_on_table)
                       + "  of the dealer's base, from a push\n", style="magenta")
        out.append(f"{'chips out':>18}  ", style="dim")
        out.append(format_money(self._chips_out(pending)), style="bold")
        mine = self.table.seats_of(seat.label)
        if len(mine) > 1:
            out.append("\n")
            out.append(f"{'your boxes':>18}  ", style="dim")
            for i in mine:
                where = self.table.seats[i]
                mark = ("betting" if i == self._seat_index
                        else "set" if where.pending
                        else "out" if where.sitting_out else "to do")
                style = ("bold" if i == self._seat_index
                         else "green" if where.pending else "yellow")
                out.append(f"seat {i + 1} ({mark})  ", style=style)
            other = next((i for i in mine if i != self._seat_index), None)
            if other is not None:
                out.append("\n")
                out.append(f"{'':>18}  ", style="dim")
                out.append("go", style="bold")          # same shape as the help
                out.append(f" {other + 1}", style="cyan")
                out.append(" switches between them,  ", style="dim")
                out.append("drop", style="bold")
                out.append(f" {other + 1}", style="cyan")
                out.append(" gives one up", style="dim")
        return out

    def _rack_preview(self, seat_index: int, pending):
        """The rack as it will look with this seat's chips already on the spots.

        Chips pushed onto a betting spot have left the player's hands whether or
        not the bet is confirmed, so the rack should not still be counting them.
        A bet already placed has really gone, so it is added back before the new
        amount is taken out -- otherwise lowering a bet would be counted twice.
        """
        seat = self.table.seats[seat_index]
        already = seat.committed if seat.pending else 0
        going_out = self._chips_out(pending)
        stack = seat.chips.copy()
        if already:
            stack.receive(already)
        # Spot by spot, in the same order the dealer would take them, so the
        # denominations left in the rack match what really happens on the deal.
        resting = seat.toke_on_table
        spots = [pending["base"], pending["push22"], pending["buster"],
                 max(pending["toke_base"] - resting, 0),
                 pending["toke_push22"], pending["toke_buster"]]
        try:
            for amount in spots:
                if amount:
                    stack.pay_with_change(amount)
        except ValueError:
            return seat.chips, already          # cannot cover it; show the truth
        return stack, going_out

    def _place(self, pending) -> str | None:
        """Put these bets on the layout for real, or say why they cannot go there.

        Typing an amount *is* pushing the chips out (Junze, 2026-09-08), so the
        felt and the rack both move now rather than at the deal. Nothing sits in
        a buffer: what the screen shows is what is really on the table. A bet
        that will not go down is refused and the spot keeps what it had.
        """
        seat = self._seat_index
        was = self.table.seats[seat].pending

        def restore():
            if was is not None:
                self.table.place_bets(
                    seat, base=was.base, push22=was.push22, buster=was.buster,
                    toke_base=was.toke_base, toke_push22=was.toke_push22,
                    toke_buster=was.toke_buster)

        self.table.cancel_bets(seat)
        if not pending["base"]:
            # R2.4 nothing stands on the layout without a base bet behind it
            resting = self.table.seats[seat].toke_on_table
            others = {k: v for k, v in pending.items() if k != "base"}
            others["toke_base"] = max(others["toke_base"] - resting, 0)
            if any(others.values()):
                restore()
                return "a side bet requires a base bet"
            return None
        try:
            self.table.place_bets(seat, **pending)
        except ChipsError:
            # R2.8 the rack, not the rules -- _bet_rejection says it better
            restore()
            return self._bet_rejection(pending) or "Your chips will not make that."
        except BetError as exc:
            restore()
            return str(exc)
        return None

    def _bet_rejection(self, pending) -> str | None:
        """Why this seat cannot put these bets up, or None if it can.

        Checked the moment an amount is typed, not just at the deal (R2.8). A
        number that sits on the screen looking accepted and then turns out to be
        unpayable is worse than being told straight away (Junze, 2026-09-08).
        """
        seat = self.table.seats[self._seat_index]
        # A bet already down has left the rack; it comes back before the new one
        # goes out, or lowering a bet would look unaffordable.
        already = seat.committed if seat.pending else 0
        available = seat.bankroll + already
        going_out = self._chips_out(pending)
        if going_out > available:
            return (f"That comes to {format_money(going_out)} and you have "
                    f"{format_money(available)}.")
        stack = seat.chips.copy()
        if already:
            stack.receive(already)
        resting = seat.toke_on_table
        spots = [pending["base"], pending["push22"], pending["buster"],
                 max(pending["toke_base"] - resting, 0),
                 pending["toke_push22"], pending["toke_buster"]]
        try:
            for amount in spots:
                if amount:
                    stack.pay_with_change(amount)
        except ValueError:
            # R2.8 the dealer makes change, but only out of chips that exist
            return "Your chips will not make that. Break one up with `x` first."
        return None

    def _chips_out(self, pending) -> int:
        """What actually leaves the rack: the dealer's resting toke is already out."""
        resting = self.table.seats[self._seat_index].toke_on_table
        return (pending["base"] + pending["push22"] + pending["buster"]
                + max(pending["toke_base"] - resting, 0)
                + pending["toke_push22"] + pending["toke_buster"])

    # --- One round ---------------------------------------------------------
    def play_round(self) -> None:
        c, t = self.console, self.table
        state = t.start_round()

        while not state.is_over:
            if state.is_chance_node():
                pace = DEALER_PACE if state.phase is Phase.DEALER else 0.0
                self._draw(state, pace)
                continue
            seat_index = t.current_seat()
            occupant = t.seats[seat_index].occupant
            if state.phase is Phase.INSURANCE:
                if occupant.is_bot:
                    self._bot_insurance(state, occupant)
                else:
                    self._insurance(state, seat_index)
                continue
            if occupant.is_bot:
                self._bot_turn(state, seat_index, occupant)
            else:
                self._player_turn(state, seat_index)

        self._reveal_hands(state)

        # Read after the round, so insurance taken part-way through is counted.
        staked = [t.seats[t.table_seat_of(i)].committed
                  for i in range(len(t._round_seats))]
        t._committed_this_round = staked[0] if staked else 0
        results = t.finish_round()
        for seat in t.seats:
            seat.sitting_out = False        # R2.17 lasts one round only
        self._carry_bets_over(results)

        c.clear()
        c.print(render_screen(state, t, rack_seat=self._first_human(),
                              viewer=self._viewer()))
        c.print(render_results(state, results, t, staked))
        for line in self._settlement_chatter(state, results, staked):
            c.print(line)
        c.input("\n  [dim]enter for the next round[/dim] ")
        self._offer_empty_seats()

    def _carry_bets_over(self, results) -> None:
        """What each spot starts the next round with.

        A bet that lost was swept off the layout, so the spot is empty and has to
        be put up again. A bet that won or pushed is still sitting there and
        rides. A bet placed for the dealer never rides: they take it when it
        wins, and it is gone when it loses -- the one exception is a pushed toke
        on the base spot, which stays on the felt and is added back by the
        betting screen (R2.11).
        """
        for round_index, res in enumerate(results):
            seat_index = self.table.table_seat_of(round_index)
            remembered = self.seat_bets.get(seat_index)
            if remembered is None:
                continue
            # The player's own chips all sit on the first box: a free split is
            # the casino's money, and a paid double goes onto that same box.
            if res.hands and res.hands[0].outcome is Outcome.LOSE:
                remembered["base"] = 0
            for spot, side in (("push22", res.push22), ("buster", res.buster)):
                if not (side and side.won):
                    remembered[spot] = 0
            for spot in ("toke_base", "toke_push22", "toke_buster"):
                remembered[spot] = 0

    def _viewer(self, seat: int | None = None) -> set[int]:
        """R3.7 the table seats whose cards the person reading the screen can see.

        Somebody playing two boxes picks both of them up, so this follows the
        person and not the chair. Identity, not name: a person may be sharing a
        character's name and must not be shown that character's cards.
        """
        if seat is None:
            seat = self._seat_index
        if not 0 <= seat < len(self.table.seats):
            return set()
        who = self.table.seats[seat].occupant
        if who is None or who.is_bot:
            return set()
        return {i for i, s in enumerate(self.table.seats)
                if s.occupied and s.occupant is who}

    def _first_human(self) -> int:
        seats = self.human_seats()
        return seats[0] if seats else 0

    def _reveal_hands(self, state) -> None:
        """R3.7 the dealer turns the face-down hands over, seat 5 down to seat 1.

        One seat at a time with a pause, because the point of it is that the
        table gets to see each hand -- flashing them all up at once is the same
        as not showing them.
        """
        c, t = self.console, self.table
        for seat_no in range(len(t.seats) - 1, -1, -1):
            index = t.round_index_of(seat_no)
            if index is None or index >= len(state.seats):
                continue
            if not state.reveal_seat(index):
                continue                     # nothing was face down here
            c.clear()
            c.print(render_screen(state, t, rack_seat=self._first_human(),
                                  acting_seat=seat_no, viewer=self._viewer()))
            time.sleep(REVEAL_PACE)

    def _bot_turn(self, state, seat_index, occupant) -> None:
        """R9.6 show the seat, pause long enough to read it, then play it."""
        c = self.console
        hand = state.current()
        c.clear()
        c.print(render_screen(state, self.table, focus_hand=hand,
                              rack_seat=seat_index, acting_seat=seat_index,
                              viewer=self._viewer()))
        time.sleep(BOT_PACE)
        legal = self.table.legal_actions()
        want = occupant.bot.decide(hand, value_of(state.upcard), self.rules)
        if want not in legal:
            want = Action.STAND if Action.STAND in legal else legal[0]
        self.table.act(want)

    def _bot_insurance(self, state, occupant) -> None:
        hand = state.seats[state.insurance_seat].hands[0]
        want = (Action.TAKE_INSURANCE if occupant.bot.wants_insurance(hand)
                else Action.DECLINE_INSURANCE)
        try:
            self.table.act(want)
        except ValueError:
            self.table.act(Action.DECLINE_INSURANCE)

    def _offer_empty_seats(self) -> None:
        """R2.16 a new shoe is the one moment somebody may join, so ask."""
        t, c = self.table, self.console
        if not t.new_shoe:
            return
        if not t.empty_seats() or not t.available_characters():
            return
        for index in list(t.empty_seats()):
            free = t.available_characters()
            if not free:
                return
            c.print(f"\n  Seat {index + 1} is open.")
            c.print(render_character_list(free, title="Who could sit down"))
            raw = c.input("  Who sits down? [dim](enter to leave it empty)[/dim] "
                          ).strip()
            if not raw:
                continue
            ch, panel = self.pick_character(raw, free)
            if panel is not None:
                c.print(panel)
            if ch is None:
                continue
            bot = Bot(ch, self.rng)
            t.seat_occupant(index, Occupant(ch.name, bot), ch.buy_in * DOLLAR)
            bot.started_with = t.seats[index].bankroll
            c.print(f"  [green]{ch.name} sits down.[/green] [dim]{ch.bio}[/dim]")

    def _draw(self, state, pace: float) -> None:
        state.deal_next()
        if pace:
            self.console.clear()
            self.console.print(render_screen(state, self.table,
                                             banner=self._scenario_banner(),
                                             viewer=self._viewer()))
            time.sleep(pace)

    def _insurance(self, state, seat_index: int = 0) -> None:
        c = self.console
        # R5.4 exactly half the base bet, rounded up when chips cannot make the half
        premium = round_up_payment(
            state.seats[state.insurance_seat].bets.base // 2)
        while True:
            c.clear()
            c.print(render_screen(state, self.table, rack_seat=seat_index,
                                  acting_seat=seat_index,
                                  viewer=self._viewer(seat_index),
                                  note=Text("Dealer shows an ace.", style="bold yellow")))
            # Enter declines. Insurance costs real money, so the default must be
            # the one that spends nothing; the brackets mark it.
            prompt = Text("\n  Insurance for ")
            prompt.append(format_money(premium), style="bold")
            prompt.append("?   ")
            prompt.append("y", style="bold")
            prompt.append(" / ")
            prompt.append("[n]", style="bold yellow")
            prompt.append("   enter = no", style="dim")
            prompt.append("  ")
            raw = c.input(prompt).strip().lower()
            if raw in ("y", "n", ""):
                action = (Action.TAKE_INSURANCE if raw == "y"
                          else Action.DECLINE_INSURANCE)
                try:
                    self.table.act(action)
                    return
                except ValueError as exc:
                    c.print(f"  [red]{exc}[/red]")
                    time.sleep(1.2)

    def _player_turn(self, state, seat_index: int = 0) -> None:
        c = self.console
        hand = state.current()
        actions = self.table.legal_actions()
        while True:
            c.clear()
            c.print(render_screen(state, self.table, focus_hand=hand,
                                  note=self._action_bar(hand, actions),
                                  banner=self._scenario_banner(),
                                  rack_seat=seat_index, acting_seat=seat_index,
                                  viewer=self._viewer(seat_index)))
            raw = c.input("\n  > ").strip().lower()
            if raw in ACTION_KEYS and ACTION_KEYS[raw] in actions:
                self.table.act(ACTION_KEYS[raw])
                return
            c.print("  [yellow]Not one of the options.[/yellow]")
            time.sleep(0.6)

    def _action_bar(self, hand, actions) -> Text:
        out = Text("  ")
        labels = {
            Action.HIT: ("h", "Hit"),
            Action.STAND: ("s", "Stand"),
        }
        for action in actions:
            # A free move is the casino's money, so it gets the cyan highlight;
            # a paid one costs the player and reads as an ordinary choice.
            if action is Action.DOUBLE:
                free = hand.double_is_free(self.rules)
                key, label = "d", "Double (FREE)" if free else "Double"
                style = "bold cyan" if free else "bold"
            elif action is Action.SPLIT:
                free = hand.split_is_free(self.rules)
                key, label = "p", "Split (FREE)" if free else "Split"
                style = "bold cyan" if free else "bold"
            else:
                key, label = labels[action]
                style = "bold"
            out.append(f"[{key}] ", style="bold yellow")
            out.append(f"{label}   ", style=style)
        return out

    # --- Session -----------------------------------------------------------
    def run(self) -> None:
        self.setup()
        while True:
            what = self.betting_phase()
            if what == "quit":
                # Bets are already on the felt by now; nobody should lose money
                # to a hand that was never dealt.
                self.table.cancel_bets()
                break
            if what == "skip":
                continue
            try:
                self.play_round()
            except BetError as exc:
                self.console.print(f"  [red]{exc}[/red]")
                time.sleep(1.5)
        self.console.clear()
        self.console.print(render_session(self.table))
        self.console.print("\n  [dim]Thanks for playing.[/dim]\n")
