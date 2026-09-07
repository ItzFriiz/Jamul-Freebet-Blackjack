#!/usr/bin/env python
"""Play Free Bet Blackjack in the terminal.

    python play.py            deal a real shoe
    python play.py --seed 7   reproducible shuffles, for chasing down a bug
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tui.app import App


def main() -> int:
    ap = argparse.ArgumentParser(description="Free Bet Blackjack - Jamul Casino")
    ap.add_argument("--seed", type=int, default=None,
                    help="fix the shuffle so a session can be replayed")
    ap.add_argument("--scenario", default=None,
                    help="deal a stacked deck that sets up a particular situation")
    ap.add_argument("--list-scenarios", action="store_true",
                    help="show the stacked decks that are available")
    args = ap.parse_args()

    if args.list_scenarios:
        from engine.scripted import SCENARIOS
        print("\nStacked decks  (python play.py --scenario <name>)\n")
        for key, sc in SCENARIOS.items():
            print(f"  {key:<16} {sc.title}")
            print(f"  {'':<16} press: {sc.hint}\n")
        return 0

    try:
        App(seed=args.seed, scenario=args.scenario).run()
    except (KeyboardInterrupt, EOFError):
        print("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
