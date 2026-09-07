"""Chip inventory, exchanging denominations, and directional rounding.

Rules reference: RULES.md R2.5 - R2.10. All amounts are integer cents.
"""

from __future__ import annotations

from .config import CHIP_DENOMINATIONS, DOLLAR, PAYOUT_GRANULARITY

# Every denomination is a multiple of 50 cents, so the change search works in
# 50-cent units -- one order of magnitude smaller than working in cents.
UNIT = PAYOUT_GRANULARITY

# Amounts the casino cannot make: $0.50 and $1.50 (smallest chips are $1 and $2.50).
_UNPAYABLE = frozenset({50, 150})


def is_payable(amount: int) -> bool:
    """Can the casino, with unlimited chips, pay this amount exactly?"""
    return amount >= 0 and amount % UNIT == 0 and amount not in _UNPAYABLE


def round_down_payout(amount: int) -> int:
    """R2.6 when the casino pays, round the remainder DOWN to a payable amount.

    Example: a $27.50 blackjack pays 1.5x = $41.25, so the player receives $41.00.
    """
    r = (amount // UNIT) * UNIT
    while r > 0 and not is_payable(r):
        r -= UNIT
    return r


def round_up_payment(amount: int) -> int:
    """R2.6 when the player pays, round the remainder UP to a payable amount.

    Example: insurance on a $27.50 bet is half = $13.75, so the player pays $14.00.
    """
    r = -(-amount // UNIT) * UNIT
    while not is_payable(r):
        r += UNIT
    return r


def format_money(cents: int) -> str:
    if cents % DOLLAR == 0:
        return f"${cents // DOLLAR}"
    return f"${cents / DOLLAR:.2f}".rstrip("0")


class ChipStack:
    """The player's chips, counted per denomination (R2.8)."""

    def __init__(self, counts: dict[int, int] | None = None):
        self.counts: dict[int, int] = {d: 0 for d in CHIP_DENOMINATIONS}
        for denom, n in (counts or {}).items():
            if denom not in self.counts:
                raise ValueError(f"no such chip denomination: {format_money(denom)}")
            self.counts[denom] += n

    # --- Queries -----------------------------------------------------------
    @property
    def total(self) -> int:
        return sum(d * n for d, n in self.counts.items())

    @property
    def num_chips(self) -> int:
        return sum(self.counts.values())

    def find_payment(self, amount: int) -> dict[int, int] | None:
        """Can these chips make `amount` exactly? Returns the chips used, or None.

        Greedy is wrong here. Holding one $25 and one $5, $27.50 cannot be made,
        but greedy takes the $25 and then stalls -- it cannot tell "impossible"
        apart from "try a different combination". With few denominations and small
        counts, a bounded change search is cheap and exact.
        """
        if amount < 0 or amount % UNIT != 0 or amount > self.total:
            return None
        target = amount // UNIT
        reach: dict[int, tuple | None] = {0: None}
        for denom in sorted(self.counts, reverse=True):
            cnt = self.counts[denom]
            if not cnt:
                continue
            step = denom // UNIT
            grown = dict(reach)
            for base in reach:
                v = base
                for k in range(1, cnt + 1):
                    v += step
                    if v > target:
                        break
                    grown.setdefault(v, (denom, k, base))
            reach = grown
            if target in reach:
                break
        if target not in reach:
            return None
        used: dict[int, int] = {}
        node = target
        while reach[node] is not None:
            denom, k, prev = reach[node]
            used[denom] = used.get(denom, 0) + k
            node = prev
        return used

    def can_pay(self, amount: int) -> bool:
        return self.find_payment(amount) is not None

    # --- Adding and removing -----------------------------------------------
    def pay(self, amount: int) -> dict[int, int]:
        """Pay `amount` out of the stack. Raises if it cannot be made, so the caller
        can prompt the player to exchange chips first."""
        used = self.find_payment(amount)
        if used is None:
            raise ValueError(f"cannot make {format_money(amount)} from these chips; exchange first")
        for denom, n in used.items():
            self.counts[denom] -= n
        return used

    def pay_with_change(self, amount: int) -> tuple[dict[int, int], dict[int, int]]:
        """Pay `amount`, handing over a larger chip and taking change when needed.

        This is what a dealer actually does: toss a $25 chip at a $12.50 insurance
        spot and you get $12.50 back. Being unable to make an amount exactly is
        never a reason to refuse a bet the rack can clearly afford. Deliberately
        re-cutting the rack is a separate, explicit act -- see exchange() (R2.9).

        Returns (chips handed over, change received).
        """
        exact = self.find_payment(amount)
        if exact is not None:
            for denom, n in exact.items():
                self.counts[denom] -= n
            return exact, {}

        if amount > self.total:
            raise ValueError(f"cannot make {format_money(amount)} from these chips")

        # The common case: one bigger chip covers it and the change is payable.
        for denom in sorted(self.counts):
            if self.counts[denom] and denom > amount and is_payable(denom - amount):
                self.counts[denom] -= 1
                change = split_amount(denom - amount)
                for d, n in change.items():
                    self.counts[d] += n
                return {denom: 1}, change

        # Otherwise hand over the smallest payable total above the amount.
        step = UNIT
        total = amount + step
        while total <= self.total:
            if is_payable(total - amount):
                handed = self.find_payment(total)
                if handed is not None:
                    for denom, n in handed.items():
                        self.counts[denom] -= n
                    change = split_amount(total - amount)
                    for d, n in change.items():
                        self.counts[d] += n
                    return handed, change
            total += step
        raise ValueError(f"cannot make {format_money(amount)} even with change")

    def receive(self, amount: int, prefer_large: bool = True) -> dict[int, int]:
        """Take `amount` in, broken into chips and added to the stack."""
        got = split_amount(amount, prefer_large=prefer_large)
        for denom, n in got.items():
            self.counts[denom] += n
        return got

    def exchange(self, denom_from: int, count: int, denom_to: int) -> None:
        """R2.9 swap chips for an equal value in another denomination, at any time."""
        if self.counts.get(denom_from, 0) < count:
            raise ValueError(f"not enough {format_money(denom_from)} chips")
        value = denom_from * count
        if value % denom_to != 0:
            raise ValueError(
                f"{format_money(value)} is not a whole number of {format_money(denom_to)} chips"
            )
        self.counts[denom_from] -= count
        self.counts[denom_to] += value // denom_to

    def copy(self) -> "ChipStack":
        return ChipStack(dict(self.counts))

    def describe(self) -> str:
        parts = [
            f"{format_money(d)}×{n}"
            for d, n in sorted(self.counts.items(), reverse=True)
            if n
        ]
        return "  ".join(parts) + f"   total {format_money(self.total)}"


def split_amount(amount: int, prefer_large: bool = True) -> dict[int, int]:
    """Break an amount into chips, largest denomination first.

    Plain greedy over every denomination is wrong here, because $2.50 does not
    divide the ones below it: greedy makes $4 into one $2.50 and then strands
    $1.50, which no combination of chips can cover, even though $4 is simply four
    $1 chips. So the 50-cent part is handled first with a single $2.50, and the
    whole-dollar remainder is split over denominations that do form a chain
    ($1 | $5 | $25 | $100 | $500 | $1000), where greedy is exact.
    """
    if not is_payable(amount):
        raise ValueError(f"{format_money(amount)} cannot be made from the available chips")
    out: dict[int, int] = {}
    left = amount
    if left % DOLLAR:                       # a 50-cent part needs exactly one $2.50
        out[250] = 1
        left -= 250
    ladder = [d for d in CHIP_DENOMINATIONS if d % DOLLAR == 0]
    for denom in sorted(ladder, reverse=True) if prefer_large else sorted(ladder):
        if left >= denom:
            n, left = divmod(left, denom)
            out[denom] = out.get(denom, 0) + n
    if left:
        raise ValueError(f"{format_money(amount)} cannot be made from the available chips")
    return out


def default_buy_in(amount: int, small_chip_count: int = 20) -> ChipStack:
    """R2.8 buy-in: 20 chips of $5 first, the rest in $25, remainder in $5 / $2.50 / $1.

    Example: a $500 buy-in becomes 16 x $25 plus 20 x $5.
    """
    if not is_payable(amount):
        raise ValueError(f"buy-in of {format_money(amount)} cannot be made from chips")
    five = 5 * DOLLAR
    twenty_five = 25 * DOLLAR
    counts: dict[int, int] = {}
    left = amount

    n_five = min(small_chip_count, left // five)
    if n_five:
        counts[five] = n_five
        left -= n_five * five

    n_25, left = divmod(left, twenty_five)
    if n_25:
        counts[twenty_five] = n_25

    for denom in (five, 250, 1 * DOLLAR):
        if left >= denom:
            n, left = divmod(left, denom)
            counts[denom] = counts.get(denom, 0) + n
    if left:
        raise ValueError(f"buy-in of {format_money(amount)} leaves an unmakeable remainder")
    return ChipStack(counts)


def roll_up(counts: dict[int, int]) -> dict[int, int]:
    """R2.12 merge small chips into larger ones: five $5 become one $25, and so on.

    A dealer does not push twenty $5 chips across the felt; once enough of a
    denomination has piled up to make the next one, it gets colored up.
    """
    out = {d: n for d, n in counts.items() if n}
    ladder = sorted(CHIP_DENOMINATIONS)
    for i, denom in enumerate(ladder[:-1]):
        nxt = ladder[i + 1]
        if nxt % denom:
            continue  # e.g. $1 does not divide $2.50 evenly, so it cannot roll up
        per = nxt // denom
        n = out.get(denom, 0)
        if n >= per:
            merged, left = divmod(n, per)
            out[denom] = left
            out[nxt] = out.get(nxt, 0) + merged
    return {d: n for d, n in out.items() if n}


def payout_chips(amount: int, bet_counts: dict[int, int] | None = None) -> dict[int, int]:
    """R2.12 pay `amount` using the denominations the player bet with, then roll up.

    Falls back to the full chip ladder for any part the bet's denominations cannot
    cover -- betting a single $25 chip still has to be paid $12.50 somehow.
    """
    if not is_payable(amount):
        raise ValueError(f"{format_money(amount)} cannot be made from the available chips")
    out: dict[int, int] = {}
    left = amount
    if bet_counts:
        for denom in sorted((d for d, n in bet_counts.items() if n), reverse=True):
            if left >= denom:
                n, left = divmod(left, denom)
                out[denom] = out.get(denom, 0) + n
    if left:
        for denom, n in split_amount(left).items():
            out[denom] = out.get(denom, 0) + n
    return roll_up(out)
