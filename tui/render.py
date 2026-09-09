"""Drawing the table. Reads engine state; never changes it."""

from __future__ import annotations

from rich.align import Align
from rich.console import Group
from rich.panel import Panel
from rich.table import Table as RichTable
from rich.text import Text

from engine.chips import format_money
from engine.players import traits
from engine.state import Phase
from .format import cards_text, hand_total_text, rack_text

FELT = "green4"


def dealer_display(state) -> list:
    """The dealer's cards as the table sees them; `None` is one still face down."""
    if state is None or not state.dealer.cards:
        return []
    if state.hole_revealed or state.phase is Phase.SETTLE:
        return list(state.dealer.cards)
    # One card is showing; the hole card is either not drawn yet or drawn but
    # still face down after a peek (R3.3/R3.4).
    return [state.dealer.cards[0], None]


def _render_prep(table, acting_seat=None):
    """The table between rounds: who is here and exactly what they have out.

    A column per betting spot, the player's three and the dealer's three kept
    apart. Squashing them into one string was unreadable once several seats had
    side bets down (Junze, 2026-09-07).
    """
    grid = RichTable.grid(padding=(0, 0))
    grid.add_column(justify="right", width=3)                     # seat number
    grid.add_column(justify="left", width=5, no_wrap=True)        # bot or person
    grid.add_column(justify="left", width=9, no_wrap=True)        # who
    for _ in range(6):                                            # six spots
        grid.add_column(justify="right", width=7, no_wrap=True)
    grid.add_column(justify="left", width=14, no_wrap=True)       # status

    def head(text, style="dim"):
        return Text(text, style=style)

    # Two header rows: the group over its first column, then the spot names.
    grid.add_row(head(""), head(""), head(""),
                 head("yours"), head(""), head(""),
                 head("dealer", "dim magenta"), head(""), head(""), head(""))
    grid.add_row(head(""), head(""), head(""),
                 head("base"), head("PUSH22"), head("BUSTER"),
                 head("base", "dim magenta"), head("PUSH22", "dim magenta"),
                 head("BUSTER", "dim magenta"), head(""))

    for seat_no, seat in enumerate(table.seats):
        number = Text(f"{seat_no + 1}", style="bold" if seat.occupied else "dim")
        if not seat.occupied:
            grid.add_row(number, Text(""), Text("--", style="dim"),
                         *[Text("") for _ in range(6)],
                         Text("  (empty)", style="dim"))
            continue

        name_style = "bold yellow" if seat_no == acting_seat else "bold"
        if seat.waiting_for_shoe:
            status = Text("  next shoe", style="dim yellow")
        elif seat.sitting_out:
            status = Text("  sitting out", style="dim")
        elif seat.pending:
            status = Text("  bet placed", style="dim green")
        else:
            status = Text("  no bet yet", style="dim")

        b = seat.pending
        amounts = [(b.base if b else 0, "bold"),
                   (b.push22 if b else 0, "cyan"),
                   (b.buster if b else 0, "cyan"),
                   (b.toke_base if b else 0, "magenta"),
                   (b.toke_push22 if b else 0, "magenta"),
                   (b.toke_buster if b else 0, "magenta")]
        spots = [Text(format_money(a), style=s) if a else Text("-", style="dim")
                 for a, s in amounts]
        grid.add_row(number, Text.assemble(" ", occupant_tag(seat.occupant)),
                     Text(seat.label, style=name_style), *spots, status)
    return grid


def render_felt(state, table, focus_hand=None, acting_seat=None,
                viewer=()) -> Panel:
    # Padding is tight on purpose: at 80 columns all five columns have to fit,
    # and rich squeezes the last one first if they do not.
    # A name column of its own, so everyone's cards start at the same place
    # however long their name is.
    # A column of its own for the marker, because a person is allowed to share a
    # character's name and the two must still be told apart at a glance.
    grid = RichTable.grid(padding=(0, 1))
    grid.add_column(justify="right", width=2)                     # seat number
    grid.add_column(justify="left", width=3, no_wrap=True)        # bot or person
    grid.add_column(justify="left", width=9, no_wrap=True)        # who
    grid.add_column(justify="left", min_width=19)                 # cards
    grid.add_column(justify="left", width=9)                      # total
    grid.add_column(justify="left", min_width=16, no_wrap=True)   # stakes
    grid.add_column(justify="left", width=9, no_wrap=True)        # whose turn

    cards = dealer_display(state)
    dealer_total = Text("")
    if cards and None not in cards:
        dealer_total = hand_total_text(state.dealer, is_dealer=True,
                                       push_total=table.rules.dealer_push_total)
    who = Text(table.dealer.name if getattr(table, "dealer", None) else "DEALER",
               style="bold cyan")
    grid.add_row(Text(""), Text(""), who, cards_text(cards),
                 dealer_total, Text(""), Text(""))
    grid.add_row("", "", "", "", "", "", "")

    for seat_no in range(len(table.seats)):
        _add_seat_rows(grid, table, state, seat_no, focus_hand, acting_seat,
                       set(viewer))

    # R2.18 / R9.5 anyone watching from the rail is still at the table, and so
    # is anyone who has been invited but is waiting for the shoe to change.

    if state is None:
        grid = _render_prep(table, acting_seat)

    header = _shoe_header(table)
    body = grid
    rail = _rail_line(table)
    if rail is not None:
        body = Group(grid, rail)
    title = "[bold]Free Bet Blackjack[/bold] - Jamul"
    # R2.2 the limit placard sits on the table, so it is on every screen
    r = table.rules
    title += (f"   [bold]{format_money(r.min_bet)}[/bold]"
              f"-[bold]{format_money(r.max_bet)}[/bold]")
    if getattr(table, "dealer", None) is not None:
        title += f"   dealer: [bold]{table.dealer.name}[/bold]"
    return Panel(body, title=title, subtitle=header,
                 border_style=FELT, padding=(1, 2))


def occupant_tag(occupant) -> Text:
    """The mark that says whether a seat holds a person or a character.

    Needed because a person may take a character's name (Junze, 2026-09-06), so
    the name alone no longer says which is which.
    """
    if occupant is None or not occupant.is_bot:
        # People are left unmarked on purpose. Tagging every human seat "you" is
        # wrong the moment there is more than one person at the table, and an
        # empty seat already reads as "--", so a blank here can only mean a person.
        return Text("")
    return Text("bot", style="dim cyan")


def _rail_line(table):
    """R2.18 one line for everybody without a seat.

    People and characters wait in the same place; the only difference is who
    picks the seat, and that is not worth two rows on the felt. Kept out of the
    seat grid so a long list of names is not squeezed into a column of bets.
    """
    standing = list(getattr(table, "standing", ()))
    if not standing:
        return None
    out = Text("\n  ")
    out.append("standing   ", style="dim")
    out.append(", ".join(
        f"{o.name}{' (bot)' if o.is_bot else ''} ({format_money(o.bankroll)})"
        for o in standing), style="dim")
    return out


def _add_seat_rows(grid, table, state, seat_no, focus_hand, acting_seat,
                   viewer=frozenset()) -> None:
    """One table seat, however many hands it is playing.

    Every seat is drawn, not just the ones in the round -- an empty chair and a
    player sitting a hand out are both things the table can see (R9.2, R9.5).
    `viewer` is the set of table seats the person reading the screen is holding:
    they see their own cards in full and everyone else's face-up ones (R3.7).
    """
    seat = table.seats[seat_no]
    number = Text(f"{seat_no + 1}", style="bold" if seat.occupied else "dim")
    blank = Text("")

    if not seat.occupied:
        grid.add_row(number, blank, Text("--", style="dim"),
                     Text("(empty)", style="dim"), blank, blank, blank)
        return

    kind = occupant_tag(seat.occupant)
    name_style = "bold yellow" if seat_no == acting_seat else "bold"
    # Read the round off the state that was handed in, not off the table: once
    # settlement is done the table has let go of the round, but the screen still
    # has to show everyone the hands that just played.
    index = table.round_index_of(seat_no)
    round_seat = (state.seats[index]
                  if state is not None and index is not None
                  and index < len(state.seats) else None)

    if round_seat is None:
        note = ("waits for the shoe" if seat.waiting_for_shoe
                else "sitting out" if seat.sitting_out else "watching")
        grid.add_row(number, kind, Text(seat.label, style="dim"),
                     Text(note, style="dim"), blank, blank, blank)
        return

    for hand_no, hand in enumerate(round_seat.hands):
        if hand is focus_hand:
            marker = Text("<- acting", style="bold yellow")
        elif hand.is_finished:
            marker = Text("done", style="dim")
        else:
            marker = Text("")
        who = (Text(seat.label, style=name_style) if hand_no == 0
               else Text(f" #{hand_no + 1}", style="dim"))
        mine = seat_no in viewer
        shown = hand.visible_to(mine)
        grid.add_row(number if hand_no == 0 else blank,
                     kind if hand_no == 0 else blank, who,
                     cards_text(shown),
                     hand_total_text(hand, known=None not in shown),
                     _stake_text(hand), marker)

    # Side bets belong to the seat rather than to any one hand, so they get their
    # own line under it instead of being crammed into a stake column that is
    # already carrying the free bets. The player's and the dealer's go on
    # separate lines so neither can be long enough to wrap.
    # Everything riding on this seat that is not one hand's own stake: the
    # player's two side bets, and every spot they put up for the dealer --
    # base included, so the dealer's money is laid out like everyone else's
    # rather than squashed onto a hand as "+$5 dlr" (Junze, 2026-09-07).
    bets = round_seat.bets
    toke_base = sum(h.toke_stake for h in round_seat.hands)
    for prefix, spots, style in (
            ("", (("P22", bets.push22), ("BST", bets.buster)), "cyan"),
            ("dlr ", (("base", toke_base), ("P22", bets.toke_push22),
                      ("BST", bets.toke_buster)), "magenta")):
        tokens = [(name, amount) for name, amount in spots if amount]
        if not tokens:
            continue
        # Short labels, and wrapped by hand: this column is only about nineteen
        # characters wide once the cards have had their share. The full names
        # are on the betting table before every deal.
        for chunk in _pack(prefix, tokens, width=19, style=style):
            grid.add_row(blank, blank, blank, chunk, blank, blank, blank)


def _pack(prefix: str, tokens, width: int, style: str = "cyan") -> list:
    """Lay label/amount pairs out over as few lines as fit the column."""
    lines, current, plain = [], None, ""
    for name, amount in tokens:
        piece = f"{name} {format_money(amount)}"
        start = prefix if current is None else ""
        if current is not None and len(plain) + 2 + len(piece) > width:
            lines.append(current)
            current, plain, start = None, "", " " * len(prefix)
        if current is None:
            current = Text(start, style="dim")
            plain = start
        else:
            current.append("  ")
            plain += "  "
        current.append(f"{name} ", style="dim")
        current.append(format_money(amount), style=style)
        plain += piece
    if current is not None:
        lines.append(current)
    return lines


def _stake_text(hand) -> Text:
    out = Text()
    if hand.player_stake:
        out.append(format_money(hand.player_stake), style="bold white")
    if hand.house_stake:
        if len(out):
            out.append(" + ")
        out.append(f"{format_money(hand.house_stake)} FREE", style="bold cyan")
    # The dealer's money is listed under the seat, not tacked onto a hand.
    return out


def _shoe_header(table) -> str:
    shoe = table.shoe
    total = table.rules.deck_size
    dealt = total - shoe.remaining
    pct = 100 * dealt / total
    tail = " [bold yellow]CUT CARD OUT - last round[/bold yellow]" if shoe.cut_card_seen else ""
    return f"shoe {pct:.0f}% dealt - {shoe.remaining} cards left{tail}"


def shoe_counts(table, state=None) -> tuple[int, int, int]:
    """(unseen, face up, in the discard tray). Always sums to the whole deck.

    The hole card is drawn only when the dealer needs it, and stays face down
    until the peek resolves, so it counts as unseen -- that is what a person at
    the table actually knows. R3.7 the players' opening cards are face down too,
    and count the same way: on the felt, but not yet seen by anyone counting.
    Once settlement sweeps the hands into the tray the cards are no longer on
    the felt, even though the screen still shows them.
    """
    shoe = table.shoe
    hidden = 0
    face_up = 0
    if state is not None and not state.swept:
        face_up = state.cards_in_play
        if state.hole_drawn and not state.hole_revealed:
            hidden += 1
        hidden += state.face_down_on_felt
        face_up -= hidden
    return shoe.remaining + hidden, face_up, shoe.discarded


def render_shoe(table, state=None) -> Panel:
    """Where every card in the deck currently is.

    A real player can see the discard tray growing but cannot read the exact cut
    position, so that stays hidden -- only the public fact that the yellow card
    has come out is shown (R1.4).
    """
    shoe = table.shoe
    total = table.rules.deck_size
    dealt = total - shoe.remaining
    unseen, face_up, discarded = shoe_counts(table, state)

    width = 44
    filled = round(width * dealt / total)
    bar = Text()
    bar.append("█" * filled, style="yellow" if shoe.cut_card_seen else "green4")
    bar.append("░" * (width - filled), style="grey30")

    line = Text()
    line.append(f"{unseen:>3}", style="bold")
    line.append(" unseen    ", style="dim")
    line.append(f"{face_up:>2}", style="bold")
    line.append(" face up    ", style="dim")
    line.append(f"{discarded:>3}", style="bold")
    line.append(" in the discard tray    ", style="dim")
    line.append(f"= {unseen + face_up + discarded}", style="bold")

    detail = Text()
    detail.append(f"shuffle #{shoe.shuffles}", style="dim")
    detail.append(f"   burned {shoe.burned}", style="dim")
    detail.append(f"   {100 * dealt / total:.0f}% dealt", style="dim")
    if shoe.midround_reshuffles:
        detail.append(f"   discards washed back in {shoe.midround_reshuffles}x",
                      style="yellow")
    if shoe.cut_card_seen:
        detail.append("   CUT CARD IS OUT - last round of this shoe",
                      style="bold yellow")

    return Panel(Group(line, bar, detail), title="Shoe",
                 border_style="grey37", padding=(0, 1))


def render_payouts() -> Panel:
    """R9.1 the side bet pay tables have to be printed on the layout."""
    t = RichTable.grid(padding=(0, 2))
    t.add_column(style="bold cyan", width=9)
    t.add_column()
    t.add_row("PUSH 22", "dealer 22: other [bold]8:1[/bold]   "
                         "same color [bold]20:1[/bold]   same suit [bold]50:1[/bold]")
    t.add_row("BUSTER", "dealer busts: 3-4 cards [bold]2:1[/bold]   5 [bold]4:1[/bold]   "
                        "6 [bold]15:1[/bold]   7 [bold]50:1[/bold]   8+ [bold]250:1[/bold]")
    t.add_row("", "[dim]with a player blackjack: 7 cards +$1,000   "
                  "8+ cards +$8,000   (BUSTER bet required)[/dim]")
    return Panel(t, title="Pay tables", border_style="grey37", padding=(0, 1))


def render_rack(table, seat_no: int = 0, standing=None,
                stack=None, on_layout: int = 0) -> Panel:
    """One person's chips.

    Somebody standing at the rail is holding their chips, not laying them out in
    a rack, so only the total is shown -- there is nothing to read the
    denominations off (Junze, 2026-09-06).
    """
    if standing is not None:
        body = Text.assemble(("carrying ", "dim"),
                             (format_money(standing.bankroll), "bold"))
        return Panel(body, title=f"{standing.name} - standing",
                     border_style="grey37", padding=(0, 1))
    seat = table.seats[seat_no]
    if not seat.occupied:
        return Panel(Text("(empty seat)", style="dim"), border_style="grey37",
                     padding=(0, 1))
    # `stack` lets the caller show what the rack will look like once the chips
    # being placed are out of it. A chip pushed onto a spot has left your hands,
    # confirmed or not, so the rack should stop counting it straight away.
    shown = seat.chips if stack is None else stack
    body = Text.assemble(rack_text(shown), "\n",
                         ("total ", "dim"), (format_money(shown.total), "bold"))
    if on_layout:
        body.append("   on the layout ", style="dim")
        body.append(format_money(on_layout), style="bold cyan")
    if seat.toke_on_table:
        body.append(f"\n{format_money(seat.toke_on_table)} of the dealer's base bet is "
                    f"resting on the felt  (set it with k)", style="magenta")
    tag = " (bot)" if seat.occupant.is_bot else ""
    title = f"Seat {seat_no + 1} - {seat.label}{tag}"
    return Panel(body, title=title, border_style="grey37", padding=(0, 1))


def render_dealer_says(name: str, line: str) -> Text:
    out = Text("  ")
    out.append(f"{name}: ", style="bold cyan")
    out.append(f"\u201c{line}\u201d", style="italic cyan")
    return out


def render_shift_change(outgoing, incoming, farewell: str, greeting: str) -> Panel:
    """R2.14 the handover, and what the departing dealer made on their shift."""
    body = Text()
    body.append(f"{outgoing.name} ", style="bold")
    body.append(f"worked {outgoing.rounds} rounds and collected ", style="dim")
    body.append(format_money(outgoing.tokes), style="bold yellow")
    body.append(" in tips and toke bets.\n\n", style="dim")
    body.append(render_dealer_says(outgoing.name, farewell))
    body.append("\n")
    body.append(render_dealer_says(incoming.name, greeting))
    body.append("\n\n")
    body.append("  A card is burned when the dealer changes mid-shoe.",
                style="dim")
    return Panel(body, title="Dealer change", border_style="cyan", padding=(0, 1))


def render_session(table, seats=None) -> Panel:
    """Net is measured against every buy-in, so a re-buy is not mistaken for a win.

    Counted per person rather than per seat, because chips follow the player: they
    may have moved seats, opened a second box, or be standing at the rail (R2.18).
    """
    t = RichTable.grid(padding=(0, 2))
    t.add_column(justify="right", style="dim")
    t.add_column()
    t.add_row("rounds", str(table.rounds_played))

    # Everyone who was here tonight, not just whoever is still in a seat -- a
    # player who left or was sent away still had a night, and it still counts.
    #
    # Grouped by name, so leaving and coming back carries on the same account
    # whether it is a character or a person. Anybody who wants a fresh set of
    # books can sit down under a different name (Junze, 2026-09-07).
    here = {id(o) for o in table.everyone()}
    totals: dict[str, list] = {}
    for occ in getattr(table, "known", table.everyone()):
        got = totals.setdefault(occ.name, [0, 0, occ.is_bot, False])
        got[0] += occ.bought_in
        got[1] += occ.bankroll
        got[2] = got[2] or occ.is_bot
        got[3] = got[3] or id(occ) in here
    for seat in table.seats:
        if seat.occupied and seat.toke_on_table:
            totals[seat.occupant.name][1] += seat.toke_on_table

    for name, (out, now, is_bot, still_here) in totals.items():
        if not out:
            continue
        label = f"{name} (bot)" if is_bot else name
        if not still_here:
            label += " (left)"
        net = now - out
        style = "bold green" if net > 0 else ("bold red" if net < 0 else "bold")
        t.add_row(label, Text.assemble(
            ("bought in ", "dim"), format_money(out),
            ("   chips ", "dim"), format_money(now),
            ("   net ", "dim"), Text(format_money(net), style=style)))
    t.add_row("tipped to dealer", format_money(table.dealer_tokes))
    # A dealer who comes back for a second shift is the same person, so their
    # takings are added up rather than listed twice.
    shifts = table.past_dealers + [table.dealer]
    if len(shifts) > 1:
        by_name: dict[str, int] = {}
        for shift in shifts:
            by_name[shift.name] = by_name.get(shift.name, 0) + shift.tokes
        t.add_row("dealers seen", ", ".join(
            f"{name} ({format_money(amount)})" for name, amount in by_name.items()))
    return Panel(t, title="Session", border_style="grey37", padding=(0, 1))


def render_screen(state, table, focus_hand=None, note=None, banner=None,
                  rack_seat=0, acting_seat=None, standing=None,
                  rack_stack=None, on_layout: int = 0, viewer=()) -> Group:
    parts = [banner,
             render_felt(state, table, focus_hand, acting_seat, viewer),
             render_shoe(table, state), render_payouts(),
             render_rack(table, rack_seat, standing, rack_stack, on_layout)]
    if note:
        parts.append(note if isinstance(note, (Text, Panel)) else Text(str(note)))
    return Group(*[p for p in parts if p is not None])


def render_results(state, results, table, committed=None) -> Panel:
    """What each spot paid, laid out the way a dealer settles: hands, then side bets.

    `committed` is what each seat of the round staked, in the same order as
    `results`, so the net can be shown per seat rather than for the table.
    """
    t = RichTable.grid(padding=(0, 2))
    t.add_column(justify="right", style="dim", width=13)
    t.add_column(min_width=20)
    t.add_column(justify="right", min_width=10)

    # Every outcome reads the same way: what happened, in the past, in capitals.
    # The subject goes in the left column, so "WON" never has to be reworded
    # into "dealer's bet won" halfway down the panel.
    styles = {"blackjack": "bold yellow", "win": "bold green",
              "push": "bold white", "lose": "bold red"}
    words = {"blackjack": "BLACKJACK", "win": "WON", "push": "PUSHED",
             "lose": "LOST"}
    for seat_no, res in enumerate(results):
        if len(results) > 1:
            who = table.seats[table.table_seat_of(seat_no)].label
            t.add_row("", Text(who, style="bold cyan"), "")
        for i, hr in enumerate(res.hands):
            label = "hand" if len(res.hands) == 1 else f"hand #{i + 1}"
            paid = format_money(hr.to_player) if hr.to_player else "-"
            t.add_row(label, Text(words[hr.outcome.value],
                                  style=styles[hr.outcome.value]), paid)
            if hr.to_dealer:
                t.add_row("for dealer", Text("WON", style="magenta"),
                          format_money(hr.to_dealer))
            if hr.left_on_table:
                t.add_row("for dealer", Text("PUSHED", style="magenta"),
                          format_money(hr.left_on_table))
        if res.insurance_bet:
            won = res.insurance_to_player > 0
            t.add_row("insurance", Text("WON 2:1" if won else "LOST",
                                        style="bold green" if won else "bold red"),
                      format_money(res.insurance_to_player) if won else "-")
        for side in (res.push22, res.buster):
            if not side or not (side.bet or side.to_dealer):
                continue
            if side.won:
                label = Text(f"WON {side.odds}:1", style="bold green")
                if side.bonus:
                    label = Text.assemble(label, (f"  +{format_money(side.bonus)} bonus",
                                                  "bold yellow"))
            else:
                label = Text("LOST", style="bold red")
            if side.bet or side.to_player:
                t.add_row(side.name, label,
                          format_money(side.to_player) if side.to_player else "-")
            if side.to_dealer:
                t.add_row("for dealer", Text(f"WON {side.odds}:1", style="magenta"),
                          format_money(side.to_dealer))

        staked = (committed[seat_no] if committed
                  else getattr(table, "_committed_this_round", 0))
        net = res.to_player - staked
        style = "bold green" if net > 0 else ("bold red" if net < 0 else "bold")
        t.add_row("", Text("net", style="dim"), Text(format_money(net), style=style))
        if seat_no != len(results) - 1:
            t.add_row("", "", "")
    return Panel(t, title="Settlement", border_style="grey37", padding=(0, 1))


def render_character(ch, taken: bool = False) -> Text:
    """One character's traits line (R2.16)."""
    out = Text(traits(ch), style="dim")
    if taken:
        out.append("   (already at the table)", style="yellow")
    return out


def render_character_list(characters, taken=(), title="Characters",
                          compact: bool = False) -> Panel:
    """Every character on offer, with their bio, so a name means something.

    Laid out as a grid rather than plain text so a bio that wraps stays lined up
    under itself instead of falling back to the left margin.
    """
    grid = RichTable.grid(padding=(0, 1))
    grid.add_column(justify="left", width=10, no_wrap=True)
    grid.add_column(justify="left", overflow="fold")
    if not characters:
        grid.add_row("", Text("(nobody left to seat)", style="dim"))
    for i, ch in enumerate(characters):
        if i and not compact:
            grid.add_row("", "")
        here = ch.name in taken
        grid.add_row(Text(ch.name, style="dim" if here else "bold"),
                     render_character(ch, here))
        grid.add_row("", Text(ch.bio, style="italic dim"))
    return Panel(grid, title=title, border_style="grey37", padding=(0, 1))
