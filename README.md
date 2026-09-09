# Free Bet Blackjack

Free Bet Blackjack as dealt at Jamul Casino, in a terminal. The casino pays for
most doubles and splits. In exchange, a dealer 22 pushes every hand still
standing.

Rules were recorded at the table, not copied from a generic description of the
game. [Full rules here.](docs/RULES.md)

[中文说明](README.zh-CN.md)

```
╭──────────────────────────── Free Bet Blackjack - Jamul   $25-$1000   dealer: Gabriel ────────────────────────────╮
│                                                                                                                  │
│         Gabriel     8♠    ??                                                                                     │
│                                                                                                                  │
│                                                      yours                      dlr                              │
│                                                       base   free   P22   BST  base   P22   BST                  │
│   1     You         J♦    4♣    9♣        BUST 23      $50      -    $5     -    $5     -     - done             │
│   2 bot Delphine    ??    ??              ?            $25      -     -     -     -     -     - done             │
│   3 bot Big Ray     ??    ??              ?           $100      -     -    $5     -     -     - <- turn          │
│   4 bot Mrs. Pham   ??    ??              ?            $25      -    $5     -     -     -     -                  │
│   5     --        (empty)                                                                                        │
│                                                                                                                  │
╰───────────────────────────────────────── shoe 11% dealt - 93 cards left ─────────────────────────────────────────╯
```

## Run it

Needs Python 3.11+ and [rich](https://github.com/Textualize/rich).

```bash
pip install rich
python play.py
```

| Command | Does |
|---|---|
| `python play.py` | Deal a real shoe |
| `python play.py --seed 7` | Fix the shuffle so a session replays |
| `python play.py --list-scenarios` | List the stacked decks |

Give the window 110 columns. Every betting spot gets its own column. Narrower
than about 104 and rich starts clipping the labels. Cards are protected and
stay readable.

## Setting up

Four questions: table minimum ($25 or $50), table maximum ($1,000 on the main
floor, $2,000 in the high-limit room), how often dealers change (10 minutes, 0
to disable), and who sits in each of the five seats.

Type a name for each seat, or press enter to leave it empty. Type `?` to list
the computer characters with their bios. Type `-` to back up a seat. A
confirmation step at the end lets you change any of them.

You start with no chips. Buy in at the table with `i 500`.

## Betting

Type an amount and the bet goes down. Chips leave your rack, the table shows
it. A bet that cannot go down gets refused right there and the spot keeps its
old value. Nothing waits in limbo for a confirmation.

| Key | Does |
|---|---|
| `b 50` | Base bet |
| `p 5` | PUSH 22 |
| `u 5` | BUSTER |
| `k 5` `kp 5` `ku 5` | Bet for the dealer on those three spots. `0` takes it back |
| `b 0` | Clear the base bet. Side bets come off with it |
| `i 200` | Buy more chips |
| `x 25 2 5` | Exchange two $25 chips for $5s |
| `t 5` | Tip the dealer outright |
| `o` | Sit this round out |
| `q` | Quit |
| enter | Done betting |

A bet that won or pushed stays on the spot. Repeating it costs one keypress. A
losing bet gets swept and needs putting up again.

After everyone bets, the table shows all the bets for a last look. Type `go 1`
there to go back and change seat 1.

## Playing a hand

| Key | Does |
|---|---|
| `h` | Hit |
| `s` | Stand |
| `d` | Double. Cyan `Double (FREE)` means the casino pays |
| `p` | Split. Cyan `Split (FREE)` means the casino pays |

Only legal moves show up. A move you cannot afford does not show up at all.

9, 10 and 11 double free. Every pair splits free except two ten-value cards.
[The details are in the rules.](docs/RULES.md#what-you-can-do-with-your-hand)

## Reading the table

| What | Means |
|---|---|
| Seat order | Seat 1 is on the right at a real table. Cards go 1 to 5 |
| `bot` | A computer character. People are unmarked. A person can take a character's name, so the name alone will not tell you |
| `??` | A card you have not seen |
| `?` in the totals | Part of that hand is still hidden. Nobody can total it |
| `base` `free` | Your money, then the casino's free bet |
| `P22` `BST` | Your two side bets |
| `dlr` columns | The three spots you put up for the dealer |
| `-` in a spot | Nothing bet there. Empty spots always show a dash |
| `#2` | Second hand of a seat that split. Side bets stay on the seat's first line |

Your opening cards are face down to everyone else. Theirs are face down to you.
A card drawn by hitting lands face up. Busting, blackjack, doubling and
splitting turn a hand over. At settlement the dealer turns the rest over, seat 5
down to seat 1. [Full rule.](docs/RULES.md#what-you-can-see)

Under the table sit the shoe panel and the pay tables. The shoe panel's three
numbers always add to 104. Its `unseen` count includes every face-down card on
the felt, so it is the honest denominator for counting.

## Multiple players

Five seats. Any of them can hold a person or a character. Several people can
share one terminal and take the keyboard in turn.

| Key | Does |
|---|---|
| `add Ernie` | Invite a character or a person. `add ?` lists the characters |
| `remove 3` | Send seat 3 away. Undealt bets come back to them |
| `move 5` | Change seats, chips and all |
| `join 2` | Play a second hand next door |
| `go 2` | Switch between your own boxes to change a bet |
| `drop 2` | Give up one of your extra boxes |
| `stand` | Leave your seat, watch from the rail |
| `ask 2` | Ask seat 2 to move so you can `join` beside yourself |
| `nice 2` `rude 2` | Compliment or insult somebody |

Characters have their own bankrolls, betting habits and temperament. Insult one
and they get less likely to give up a seat later. They leave when they go
broke.

Everything they are lives in TOML, editable without touching Python:

| File | Holds |
|---|---|
| [`data/players.toml`](data/players.toml) | The characters |
| [`data/dealers.toml`](data/dealers.toml) | The dealers |
| [`data/chatter.toml`](data/chatter.toml) | What dealers say |
| [`data/table_talk.toml`](data/table_talk.toml) | What players say to each other |

Extra boxes go on adjacent seats and share one pile of chips. Standing up takes
all your boxes. Moving seats gives up every box except the one you move.

Take an empty seat any time, but you cannot bet until the shoe finishes. That
rule is what stops a counter from dropping in only when the count is good.

## Stacked decks

Some situations take a long time to turn up on their own. Six decks are arranged
to produce them.

```bash
python play.py --list-scenarios
python play.py --scenario resplit-chain
```

| Deck | Shows | Press |
|---|---|---|
| `split-double` | Free split, then a free double on both halves | `p d d` |
| `resplit-chain` | Four hands, three of them doubled free | `p p d p d s d` |
| `resplit-aces` | Split aces draw another ace and split again | `p p` |
| `dealer-22` | Two free doubles, dealer 22 pushes both | `p d d` |
| `ten-split` | Paying to split 10 and K, and why the A+10 is only 21 | `p s s` |
| `buster-jackpot` | Player blackjack against a seven-card dealer bust | nothing |

A banner shows what the deck is for and which keys to press. The shoe goes back
to random once the script runs out.

Two things differ from a normal session. Only one person sits down, because the
script is written for one seat. Chips and bets are set up for you, because the
payouts were worked out by hand for a fixed stake.

## Layout

| Directory | Holds |
|---|---|
| `engine/` | Shoe, hands, chips, settlement, table state |
| `tui/` | Terminal interface |
| `data/` | Dealers, characters, dialogue |
| `docs/` | Rules |
| `play.py` | Entry point |

`engine/` knows nothing about the display.

A round is an explicit state machine. It keeps decision nodes (somebody chooses)
apart from chance nodes (a card comes off the shoe). The shoe is a table of
counts, not a shuffled list. Both choices exist so a solver can ask "what is the
probability the next card is a ten" and take an expectation directly.

The dealer's hole card is not drawn until needed, so unseen cards really are
unseen.

Money is an integer number of cents throughout.

## License

MIT. See [LICENSE](LICENSE).
