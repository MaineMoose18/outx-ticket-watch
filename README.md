# Red River ticket watch

Hunting **one** ticket to Oklahoma vs Texas, Cotton Bowl, Sat Oct 10 2026 2:30pm CT,
for **under $400 all-in**, preferably lower bowl.

## How it works

`check_tickets.py` polls Gametime's public listings API every 20 minutes from a
GitHub Actions cron, keeps only listings that can be bought as a *single* ticket,
and pushes to your phone through [ntfy](https://ntfy.sh).

Three notification tiers:

| Tier | Trigger | ntfy priority | Behaviour |
|---|---|---|---|
| Report | once an hour | `low` | lowest overall + lowest lower bowl |
| Any seat | cheapest single ≤ $400 all-in | `urgent` | 2 pushes, 2h cooldown |
| **Lower bowl** | lower bowl single ≤ $400 all-in | `max` | **10 pushes, every run, no cooldown** |

### Why Gametime and not SeatGeek

The SeatGeek Platform API returns `stats: {}` for this event — its primary
marketplace is Paciolan and `is_open` is false, so SeatGeek has no price data to
give. Gametime returns per-listing `section`, `row`, `section_group` and both
`prefee` and `total` (all-in) prices, which is what makes the lower-bowl split
and the true all-in comparison possible.

### The single-ticket filter matters

Of 363 listings at the time of writing, only **60** can be bought as one ticket.
The advertised "from" price is usually a pair. Filtering on `1 in listing["lots"]`
is the difference between a reported price you can pay and one you can't.

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
