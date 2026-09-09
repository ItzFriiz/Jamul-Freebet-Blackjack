"""Turning engine values into things a person can read."""

from __future__ import annotations

from rich.text import Text

from engine.cards import Card, card_name, is_red
from engine.chips import format_money
from engine.config import CHIP_DENOMINATIONS, DOLLAR

CARD_WIDTH = 5

# Chip colours roughly follow a real rack, so a glance at the rack reads the same
# way it would across the felt.
CHIP_STYLE = {
    1 * DOLLAR: "white",
    250: "bright_magenta",
    5 * DOLLAR: "red",
    25 * DOLLAR: "green",
    100 * DOLLAR: "black on white",
    500 * DOLLAR: "magenta",
    1000 * DOLLAR: "bright_yellow",
}


def card_text(card: Card | None, hidden: bool = False) -> Text:
    if hidden or card is None:
        return Text(" ?? ".center(CARD_WIDTH), style="bold blue on grey23")
    style = "bold red on white" if is_red(card) else "bold black on white"
    return Text(f" {card_name(card)} ".center(CARD_WIDTH), style=style)


def cards_text(cards) -> Text:
    """A row of cards. A `None` in the list is one still face down (R3.7)."""
    out = Text()
    for i, c in enumerate(cards):
        if i:
            out.append(" ")
        out.append(card_text(c))
    return out


def hand_total_text(hand, is_dealer: bool = False, push_total: int = 22,
                    known: bool = True) -> Text:
    if not hand.cards:
        return Text("")
    if not known:
        # R3.7 part of this hand is still face down, so nobody can total it.
        # Showing the total of what is showing would read as the whole hand.
        return Text("?", style="dim")
    if is_dealer and hand.hard_total == push_total:
        # R6.4 the signature rule: a dealer 22 is not a bust, it pushes every
        # hand still standing. Showing it as "BUST" would read as a player win.
        return Text(f"{push_total} PUSH", style="bold yellow")
    if hand.is_bust:
        return Text(f"BUST {hand.hard_total}", style="bold red")
    if hand.is_blackjack:
        return Text("BLACKJACK", style="bold yellow")
    label = f"soft {hand.total}" if hand.is_soft else str(hand.total)
    return Text(label, style="bold")


def rack_text(stack) -> Text:
    out = Text()
    for denom in sorted(CHIP_DENOMINATIONS, reverse=True):
        n = stack.counts.get(denom, 0)
        if not n:
            continue
        if len(out):
            out.append("  ")
        out.append(f" {format_money(denom)} ", style=CHIP_STYLE.get(denom, "white"))
        out.append(f"x{n}", style="dim")
    if not len(out):
        out.append("(no chips)", style="dim")
    return out


def parse_money(token: str) -> int:
    """Read '25', '$25', '27.5' as cents. Rejects anything finer than a cent."""
    token = token.strip().lstrip("$")
    if not token:
        raise ValueError("no amount given")
    value = round(float(token) * DOLLAR)
    if value < 0:
        raise ValueError("amount cannot be negative")
    return value
