# Red River ticket watch

Hunting tickets to Oklahoma vs Texas, Cotton Bowl, Sat Oct 10 2026 2:30pm CT,
for **under $400 per ticket all-in**, preferably lower bowl. Lots of **1, 2 or 3**
are all in play — Gametime prices are per ticket, so a pair can be cheaper per
seat than any single on the board.

## How it works

`check_tickets.py` polls Gametime's public listings API every 20 minutes from a
GitHub Actions cron, keeps only listings that can be bought as a *single* ticket,
and pushes to your phone through [ntfy](https://ntfy.sh).

Three notification tiers:

| Tier | Trigger | ntfy priority | Behaviour |
|---|---|---|---|
| Summary | every 2 hours | `low` | lowest overall + lowest lower bowl |
| Any seat | any group/lot ≤ $400/ticket all-in | `urgent` | 2 pushes, 2h cooldown |
| **Lower bowl** | lower bowl, any lot ≤ $400/ticket | `max` | **10 pushes, every run, no cooldown** |

### Why Gametime and not SeatGeek

The SeatGeek Platform API returns `stats: {}` for this event — its primary
marketplace is Paciolan and `is_open` is false, so SeatGeek has no price data to
give. Gametime returns per-listing `section`, `row`, `section_group` and both
`prefee` and `total` (all-in) prices, which is what makes the lower-bowl split
and the true all-in comparison possible.

### Report format

Terse on purpose; it is read at a glance on a lock screen.

```
$499 (2, upper; $514 single); $636 (1, lower)
```

Best upper-bowl price is $499/ticket buying a pair, and a single up there would
be $514. Best lower-bowl price is $636/ticket buying one. Groups are listed
cheapest first, and the single price is appended only when the best deal needs
more than one seat.

### Screening each lot size separately matters

A listing sells only in the lot sizes in its `lots` array: `[2, 4]` is a valid
pair but can never be a single, and `[1, 3]` is a valid single and triplet but
not a pair. Prices are per ticket, so the best pair price is not derivable from
the best single price — they have to be screened independently. First reading:

| | Buy 1 | Buy 2 | Buy 3 |
|---|---|---|---|
| Lower | **$636** | $640 | $651 |
| Upper | $514 | **$499** | $514 |

Pairs are cheapest in the upper bowl, singles in the lower. Collapsing these
into one number would hide that.

## Data

`price_history.csv` gains a row every run: overall and lower-bowl floors, both
pre-fee and all-in, plus section and row. After a week (~500 rows) it will show
whether the floor is drifting down or holding — which settles the contradiction
between "best prices 4–6 weeks out" and "prices rise 20–40% in the final week".

## Setup notes

- `NTFY_TOPIC` is an Actions secret. Subscribe to that exact topic in the ntfy
  app and allow it to bypass Do Not Disturb, or the alerts go nowhere.
- Schedules only fire from the default branch.
- No alerts looks identical to no cheap tickets, so the script pushes a
  `high`-priority warning after 3 consecutive failed fetches.

## What this cannot see

Student ticket transfers, r/CFB and r/OUfootball game-week threads, and
season-ticket holders you know personally. For a below-market lower bowl seat
those are better odds than this bot.
