# Free Bet Blackjack, as dealt at Jamul Casino

Blackjack with two changes that pull opposite ways. The casino pays for most of
your doubles and splits. In exchange, a dealer 22 is not a bust. It pushes every
hand still standing.

Everything here was recorded at the table these rules came from. Where the house
has a choice, the choice written down is the one it makes.

[中文版](RULES.zh-CN.md) · [How to play this program](../README.md)

## Deck and shoe

Two standard decks, 104 cards, no jokers.

The dealer burns one card after every shuffle. It goes to the discard tray face
down and nobody sees it.

A yellow cut card sits near the back of the shoe. The number of cards behind it
comes from a normal distribution, mean 26, standard deviation 3, clamped to
between 16 and 36. When the cut card turns up mid-deal the dealer sets it aside
and carries on with the next card. The round it appears in is the last round of
the shoe.

If a round empties the shoe, the discard tray gets shuffled back in, minus
whatever is still on the felt. The cut card sits deep enough that this should
never happen. The shoe is built so it cannot deadlock either way.

Changing dealers mid-shoe costs one extra burned card. A change on a freshly
shuffled shoe costs nothing extra, because the shuffle already burned one.

## The table

Five seats, numbered 1 to 5. Seat 1 is on the far right at the real table, so
cards and turns run 1 to 5, right to left. Empty seats get skipped.

Dealers change every ten minutes or so, only between hands. A round in progress
when the clock runs out gets finished first.

## The bets

| Spot | Range |
|---|---|
| Base | $25 minimum, $50 when the house is busy. $1,000 maximum |
| PUSH 22 | $0 to $1,000 |
| BUSTER | $0 to $1,000 |

A side bet needs a base bet behind it.

Whatever sat on a winning or pushing spot stays there for the next round. A
losing bet gets swept and needs putting up again. The side bets settle on their
own, so a round where your hand loses and PUSH 22 hits leaves the base spot
empty and PUSH 22 riding.

## Chips and rounding

Chips come in $1, $2.50, $5, $25, $100, $500 and $1,000.

$2.50 chips are legal for the base bet, so a bet can carry fifty cents. $27.50
is a legal base bet. These chips make every multiple of fifty cents except $0.50
and $1.50.

Amounts that will not divide into payable chips round the way that favours the
house.

| Direction | Rounds |
|---|---|
| Money you pay (insurance, a paid double) | Up |
| Money the casino pays you (blackjack, side bet odds) | Down |

On a $27.50 base bet a blackjack pays 1.5 × $27.50 = $41.25, and you get $41.00.
Insurance on that bet is $13.75, and you pay $14.00.

**Buying in.** You sit down with nothing and buy chips at the table whenever you
like. The dealer hands out $25s and $5s so you can bet straight away. Twenty $5
chips first, then the rest in $25s, with $5 / $2.50 / $1 covering the remainder.
A $500 buy-in comes back as sixteen $25s and twenty $5s.

Exchange denominations with the dealer any time, including mid-hand.

**Getting paid.** The dealer pays in the denominations you bet with, rolling
small chips up as they pile. Five $5s become a $25. Two $2.50s become a $5.

## Tipping

Two ways, and they work differently.

**Hand the dealer chips.** The money is gone and takes no further part.

**Bet for the dealer.** You put your own money on a spot on the dealer's behalf.
You need your own money on that same spot first. No PUSH 22 of your own means no
PUSH 22 for the dealer. The base bet is compulsory anyway. These bets ignore the
table minimum, so $1 for the dealer is fine.

| Outcome | What happens |
|---|---|
| Win | Goes to the dealer, stake and all. You get none of it |
| Lose | Lost like any other bet |
| Push | Stays on the felt, riding for the dealer next round. Take it back any time before the next deal |

When the casino pays for your double or split, it puts up the dealer's matching
bet too. When you pay for a split yourself, you cover the dealer's bet on the
new box as well. The casino only backs boxes it is already paying for. A paid
double does not extend the dealer's bet at all.

## The deal

Every player gets two cards face down. The dealer takes one up and one down.

| Dealer's upcard | What happens |
|---|---|
| Ace | Insurance offered to everyone, then the dealer peeks |
| 10, J, Q, K | The dealer peeks. No insurance |
| Anything else | No peek, no insurance |

A peek that finds blackjack settles the round immediately. Nobody acts.

## What you can see

This is a rule, not an interface decision. It fixes exactly which cards a
counter is entitled to have seen.

Your two opening cards are face down. You can look at your own. Nobody else can.

A card you draw by hitting lands face up for the whole table. Your first two
stay down.

Four things turn a hand over, and a hand that is over stays over.

| Trigger | Why |
|---|---|
| Busting | The cards get thrown in |
| Blackjack | Shown at once so it can be paid, whether that seat acts before you or after |
| Asking to double | Raising means showing the hand |
| Asking to split | Same, and both halves stay face up |

Hitting to 21 is not a blackjack and does not turn the hand over. A hand that
stands stays face down until settlement. At settlement the dealer turns over
what is still hidden, working from seat 5 down to seat 1.

For counting, a face-down card on the felt counts as unseen, same as the
dealer's hole card. It has left the shoe. Nobody has been shown it.

## Player actions

Hit and stand as usual. There is no surrender.

### Doubling

Free when your first two cards total 9, 10 or 11. The casino puts a matching
free bet next to yours. You get exactly one more card.

Aces count either way for this test. A+8 counts as 9, A+9 as 10, and on a hand
that came out of a split, A+10 as 11.

Any other total can be doubled out of your own pocket, again for one card. The
amount must equal your base bet. Doubling for less is not offered.

### Splitting

Every pair splits free except a pair of ten-value cards. The casino puts up a
matching free bet on the new box.

Breaking up a twenty is your own idea, so a 10/J/Q/K pair is a paid split. You
put up another base bet yourself.

Pairs go by value. 10+K, J+Q and any other two ten-value cards count as a pair
and can be split. That split costs you money.

| Rule | Detail |
|---|---|
| Number of splits | No limit. A split hand that pairs again splits again |
| Doubling a split hand | Still free on 9, 10 or 11 |
| Split aces | One card each. A second ace can split again |
| A+10 after a split | Counts as 21, not blackjack. Same for a split ten that draws an ace |

## Blackjack and insurance

An ace with a ten-value card in your first two cards pays 3:2. Hitting your way
to 21 does not count.

| Dealer's upcard | What happens to your blackjack |
|---|---|
| Not an ace or a ten | Settles immediately at 3:2. The dealer cannot have one |
| A ten | The dealer peeks. Two blackjacks push, otherwise yours pays 3:2 |
| An ace | Insurance gets offered before the peek |

Insurance must be exactly half your base bet, no more and no less. It pays 2:1.
Half a bet that will not make chips rounds up, so a $27.50 base bet takes $14.00
of insurance.

There is no separate "even money" button. Even money is insurance on a
blackjack, and both paths land in the same place.

| Dealer has blackjack | Your hand | Insurance | Net |
|---|---|---|---|
| Yes | Push | Pays 2:1 | +1× your bet |
| No | Pays 1.5× | Lost, 0.5× | +1× your bet |

Declining insurance with a blackjack against an ace means you push if the dealer
has one and take 3:2 if not.

## How the dealer plays

The dealer hits soft 17 and stands on hard 17 or better.

The hole card always comes up. After that the dealer only keeps drawing while
something on the layout still waits on the final total.

| Still waiting | Why |
|---|---|
| Any player hand that has not busted | It has to be compared, and might push against a 22 |
| Any PUSH 22 or BUSTER bet | Those settle purely off the dealer's result |

Every hand busted or a blackjack, no side bets out, nothing left to find out.
The dealer turns the hole card and clears the table without drawing.

### Dealer 22

A dealer total of exactly 22 pushes every player who has not busted, including a
hand that hit its way to 21. Free bet markers come back. Money you paid for a
double gets returned in full.

Above 22 the dealer has busted normally and every standing hand wins.

Busting yourself still loses immediately. A dealer 22 does not rescue you. You
lose your base bet and anything you paid for a double, and the free bet markers
come off.

## Where the money goes

`B` is your base bet.

| Situation | Win | Lose | Push |
|---|---|---|---|
| Ordinary hand | +`B` | −`B` | 0 |
| Free double (`B` yours, `B` casino) | +`2B` | −`B` only | `B` back, marker taken |
| Free split (new box is the casino's `B`) | +`B` | nothing | 0 |
| Free split then free double (all `2B` on the casino) | +`2B` | nothing | 0 |
| Paid split, ten pair (new box is your `B`) | +`B` | −`B` | `B` back |
| Blackjack | +`1.5B` | | |
| Insurance | +2× premium | −premium | |

A free double doubles your win and not your loss. That is the whole game.

A dealer's bet `T` riding on a box you split for money gets a matching `T` on
the new box, also out of your pocket. Both go to the dealer if they win and are
lost if they do not.

## Side bet: PUSH 22

Wins when the dealer's final total is exactly 22. Loses otherwise. Completely
independent of your own hand, so it pays even when you busted.

Odds depend on all the cards making up the dealer's 22.

| The dealer's cards are | Pays |
|---|---|
| Mixed colours | 8:1 |
| All one colour, red or black | 20:1 |
| All one suit | 50:1 |

A dealer blackjack stops the hand at 21, so PUSH 22 loses.

## Side bet: BUSTER

Wins when the dealer's final total is over 21, 22 included. Loses otherwise.
Also independent of your own hand.

Odds depend on how many cards the dealer busted with.

| Dealer's cards | Pays | Bonus |
|---|---|---|
| 3 to 4 | 2:1 | |
| 5 | 4:1 | |
| 6 | 15:1 | |
| 7 | 50:1 | $1,000 |
| 8 or more | 250:1 | $8,000 |

The bonus needs two things at once. Your own hand has to be a blackjack, and
somebody else's does not count. You have to have a BUSTER bet down, any size.

The bonus is a flat amount and does not scale with the bet.

A dealer blackjack stops the hand at 21, so BUSTER loses.

## Coming and going

Leave whenever you like, but finish the hand you have money on first. After that
you can stay and watch from the rail.

Take any empty seat at any time. You cannot bet until the current shoe finishes.
The seat is yours in the meantime. That is what "no mid-shoe entry" means in
practice. It stops a counter from dropping in only when the count is good, and
it makes leaving a shoe a decision with a cost.

Already seated players can buy more chips and change seats freely. Neither is
held to the rule above.

Sitting out, meaning keeping your seat and not betting, is fine. The seat stays
yours.

Standing at the rail is a real place to be. You keep your chips and can watch as
long as you like, seat or no seat.

Playing more than one box is allowed on adjacent seats. Each box faces the same
minimum as a single box. All your boxes bet from one pile of chips, with nothing
allocated in advance. Standing up takes all your boxes. Moving seats gives up
every box except the one you move.

## Configurable parameters

These live in [`engine/config.py`](../engine/config.py) as a frozen dataclass. A
variant is a new `Rules` object, not an edit.

| Parameter | Default | Meaning |
|---|---|---|
| `num_decks` | 2 | 104 cards |
| `burn_cards_per_shuffle` | 1 | Cards burned after a shuffle |
| `cut_remaining_mean` | 26 | Cards left behind the cut card |
| `cut_remaining_sd` | 3 | Standard deviation |
| `cut_remaining_min` | 16 | Lower clamp |
| `cut_remaining_max` | 36 | Upper clamp |
| `num_seats` | 1 | 5 for the multi-seat table |
| `min_bet` | $25 | Chosen at setup |
| `max_bet` | $1,000 | |
| `max_side_bet` | $1,000 | Each side bet |
| `free_double_totals` | {9, 10, 11} | Aces count either way |
| `ten_ranks_are_pairs` | true | 10/J/Q/K pair with each other |
| `ten_pair_split_is_free` | false | A ten pair is a paid split |
| `max_splits` | none | No limit |
| `dealer_hits_soft_17` | true | |
| `dealer_push_total` | 22 | The signature rule |
| `dealer_shift_minutes` | 10 | 0 turns dealer changes off |

Money is stored as an integer number of cents. A 3:2 payout, 2:1 insurance and
$2.50 chips will drift together under floating point, and the books stop
balancing after a few hundred thousand hands.
