# Red River ticket watch

Hunting tickets to Oklahoma vs Texas, Cotton Bowl, Sat Oct 10 2026 2:30pm CT,
for **under $400 per ticket all-in**, preferably lower bowl. Lots of **1, 2 or 3**
are all in play — Gametime prices are per ticket, so a pair can be cheaper per
seat than any single on the board.

## How it works

`check_tickets.py` polls Gametime's public listings API **every 4 minutes**,
screens each lot size (1, 2, 3) independently, and pushes to your phone through
[ntfy](https://ntfy.sh).

Four notification tiers:

| Tier | Trigger | ntfy priority | Behaviour |
|---|---|---|---|
| Summary | every 2 hours | `low` | lowest overall + lowest lower bowl |
| Any seat | any group/lot ≤ $400/ticket all-in | `urgent` | 2 pushes, re-fires on a new/cheaper listing |
| Lower bowl watch | lower bowl ≤ $500/ticket | `max` | 3 pushes, re-fires on a new/cheaper listing |
| **Lower bowl siren** | lower bowl ≤ $400/ticket | `max` | **10 pushes, every run, no cooldown** |

The lower bowl gets a looser line than everything else because it is the seat
we actually want. The two lower-bowl tiers exist because $500 is a plausible
resting price, not just a spike: if a $495 floor sat there for days, an
every-run siren would send 700+ pushes a day and train us to mute the topic —
which would then bury the $400 alert that matters. So $500 tells you the moment
it happens and whenever it improves; $400 never shuts up.

### Why the workflow loops instead of using cron

It used to run on `cron: "7,27,47 * * * *"`. On 2026-09-08 that asked for 12
fires and got **2**, both about twelve minutes late — a silent 6× degradation
from a 20-minute watch to a 2-hour one. Nothing errored, and the 2-hourly
summaries kept arriving perfectly on time because the drop rate happened to
match the summary interval, so from the phone it looked healthy.

So cron no longer drives the polling. One job polls in a loop for five hours,
then dispatches the workflow again before it exits; each link starts the next.
`github.token` with `actions: write` is enough — `workflow_dispatch` is the
documented exception to "GITHUB_TOKEN events don't create runs" — so there is
no PAT anywhere. The cron is now `13 */2 * * *` and exists only to restart a
chain that failed to hand off, and the concurrency group stops it forking a
second chain.

This is affordable only because the repo is public: unlimited Actions minutes.
A job that sits in a sleep loop bills wall-clock time, which would burn the
2,000-minute private allowance in under two days.

### Knowing it is still alive

Every poll posts the price line to `<topic>-hb` at `min` priority — a topic the
phone is not subscribed to. The daily 3am check counts those and takes the
*median gap* between them, so a slowdown shows up as a number instead of
hiding behind summaries that still look punctual. It also reads the Actions run
list over the public API to confirm a link is `in_progress`.

Counting summaries could never have caught the cron failure: six of them look
identical whether the watcher polled 6 times or 180.

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
