"""Table configuration. Mirrors the parameter table in RULES.md section R11."""

from __future__ import annotations

from dataclasses import dataclass

# --- Money -----------------------------------------------------------------
# R2.7: all amounts are integer cents. Never use floats for money.
DOLLAR = 100

# R2.5: chip denominations the casino deals in, in cents.
# The $2.50 chip may be used for a base bet, so bets can carry a 50-cent part.
CHIP_DENOMINATIONS = (
    1 * DOLLAR,
    250,  # $2.50
    5 * DOLLAR,
    25 * DOLLAR,
    100 * DOLLAR,
    500 * DOLLAR,
    1000 * DOLLAR,
)

# R2.6: rounding step for amounts that cannot be made exactly.
# The smallest difference the chips can express is $0.50 ($2.50 minus two $1).
PAYOUT_GRANULARITY = 50


@dataclass(frozen=True)
class Rules:
    """Every configurable property of one table. Frozen: build a new one to change rules."""

    # --- Cards and shoe (R1) ---
    num_decks: int = 2                      # R1.1
    burn_cards_per_shuffle: int = 1         # R1.2
    cut_remaining_mean: float = 26.0        # R1.3 cards left behind the cut card
    cut_remaining_sd: float = 3.0           # R1.3
    cut_remaining_min: int = 16             # R1.3 lower clamp
    cut_remaining_max: int = 36             # R1.3 upper clamp

    # --- Table and betting (R2) ---
    num_seats: int = 1                      # R2.1 single seat for v1
    min_bet: int = 25 * DOLLAR              # R2.2 rises to $50 when the house is busy
    max_bet: int = 1000 * DOLLAR            # R2.2 main floor; high limit is 2000
    max_side_bet: int = 1000 * DOLLAR       # R2.3

    # --- Player actions (R4) ---
    free_double_totals: frozenset[int] = frozenset({9, 10, 11})   # R4.2
    ten_ranks_are_pairs: bool = True        # R4.10 10/J/Q/K all pair with each other
    ten_pair_split_is_free: bool = False    # R4.4 a 10/J/Q/K pair splits on the player's money
    max_splits: int | None = None           # R4.5 None means no limit

    # --- Dealer shifts (R2.14) ---
    dealer_shift_minutes: float = 10.0      # 0 turns rotation off

    # --- Dealer (R6) ---
    dealer_hits_soft_17: bool = True        # R6.1
    dealer_push_total: int = 22             # R6.4 every unbusted player pushes on this total

    @property
    def deck_size(self) -> int:
        return self.num_decks * 52


# What the main floor runs. The high-limit room caps the base bet at $2,000,
# and other houses go higher again, so the interface asks rather than assumes.
DEFAULT_MAX_BET = 1000 * DOLLAR
DEFAULT_MAX_SIDE_BET = 1000 * DOLLAR

DEFAULT_RULES = Rules()
