#!/usr/bin/env python3
"""
Red River Rivalry (OU vs Texas, Cotton Bowl, 2026-10-10) ticket watcher.

Polls Gametime's public listings API and screens each lot size separately --
singles, pairs and triplets are three independent searches, because a listing
sells only in the lot sizes in its `lots` array and the per-ticket price
differs between them. Logs every observation to CSV and pushes phone alerts
via ntfy.

Why Gametime and not SeatGeek: the SeatGeek Platform API returns `stats: {}`
for this event (primary is Paciolan, is_open=false), so it has no price data
to give. Gametime returns per-listing section, row, section_group and both
pre-fee and all-in prices.

Report format is deliberately terse -- it is read at a glance on a lock screen:

    $505 (1, upper); $636 (2, lower; $670 single)

meaning: best upper-bowl price is $505/ticket buying 1; best lower-bowl price
is $636/ticket buying 2, and a single in the lower bowl would run $670.

Notification tiers:
  SUMMARY       every 2 hours, low priority.
  ANY <=400     urgent. Re-fires on a different or cheaper listing.
  LOWER <=500   max priority, short burst. Re-fires on a different or cheaper
                listing, so a floor parked at $495 does not scream forever.
  LOWER <=400   the siren: long burst, every single run, no cooldown.

Prices from the API are in CENTS and are PER TICKET.

Environment variables:
  NTFY_TOPIC          required for alerts.
  GT_EVENT_ID         default 692f4b0348de0b1d9c246950
  PRICE_CEILING       default 400   (per ticket, all-in, dollars; any group)
  LOWER_CEILING       default 500   (lower bowl "tell me now" line)
  MAX_QTY             default 3     (screen lot sizes 1..MAX_QTY separately)
  REPORT_EVERY_MIN    default 60    (workflow sets 115)
  ALERT_COOLDOWN_MIN  default 120   (ANY alert, same listing only)
  LOWER_BURST         default 10    (repeats of the lower-bowl alert)
  FORCE_NOTIFY        "1" to force all tiers (dispatch testing)
  STATE_PATH          default state.json
  HISTORY_PATH        default price_history.csv
"""

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

EVENT_ID = os.environ.get("GT_EVENT_ID", "692f4b0348de0b1d9c246950").strip()
LISTINGS_API = "https://mobile.gametime.co/v1/listings?event_id={event_id}"
BUY_URL = ("https://gametime.co/college-football/red-river-rivalry-texas-longhorns-vs-"
           "oklahoma-sooners-football-tickets/10-10-2026-dallas-tx-cotton-bowl/"
           "events/" + EVENT_ID)

PRICE_CEILING = float(os.environ.get("PRICE_CEILING", "400"))
# The lower bowl gets its own, looser trigger: worth knowing about immediately
# even well above the buy target. PRICE_CEILING remains the lower-bowl siren
# line -- see the two lower-bowl tiers in main().
LOWER_CEILING = float(os.environ.get("LOWER_CEILING", "500"))
MAX_QTY = int(os.environ.get("MAX_QTY", "3"))
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
REPORT_EVERY_MIN = int(os.environ.get("REPORT_EVERY_MIN", "60"))
COOLDOWN_MIN = int(os.environ.get("ALERT_COOLDOWN_MIN", "120"))
LOWER_BURST = int(os.environ.get("LOWER_BURST", "10"))
FORCE_NOTIFY = os.environ.get("FORCE_NOTIFY", "").strip() == "1"
STATE_PATH = os.environ.get("STATE_PATH", "state.json")
HISTORY_PATH = os.environ.get("HISTORY_PATH", "price_history.csv")

# Gametime groups Cotton Bowl sections as "Lower" / "Upper". Anything it does
# not classify gets its own bucket rather than being dropped or silently
# counted as lower bowl.
LOWER = "Lower"
UPPER = "Upper"

TIMEOUT = 25
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def fetch_listings():
    """Return the list of listing dicts, or None on failure."""
    req = urllib.request.Request(
        LISTINGS_API.format(event_id=EVENT_ID),
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        log(f"HTTP {e.code} from Gametime: {e.reason}")
        return None
    except Exception as e:
        log(f"fetch failed: {e}")
        return None
    listings = data.get("listings")
    if not isinstance(listings, list):
        log("unexpected response shape: no listings array")
        return None
    return listings


def dollars(cents):
    return None if cents is None else cents / 100.0


def price_of(listing):
    return (listing.get("price") or {}).get("total")


def cheapest_at(listings, group, qty):
    """Cheapest listing in `group` that can be bought in a lot of exactly `qty`.

    A listing sells only in the lot sizes in its `lots` array: lots [2, 4] is a
    valid pair but can never be a single, and lots [1, 3] is a valid single and
    triplet but not a pair. Screening each quantity separately is the point --
    the per-ticket price of the best pair is not derivable from the best single.
    """
    pool = [x for x in listings
            if x.get("section_group") == group
            and qty in (x.get("lots") or [])
            and price_of(x) is not None]
    return min(pool, key=price_of) if pool else None


def screen(listings):
    """grid[group][qty] -> cheapest listing, for every group and lot size."""
    groups = sorted({x.get("section_group") or "Unknown" for x in listings})
    return {g: {q: cheapest_at(listings, g, q) for q in range(1, MAX_QTY + 1)}
            for g in groups}


def best_in(row):
    """(qty, listing) of the cheapest per-ticket price across lot sizes."""
    have = [(q, l) for q, l in row.items() if l]
    return min(have, key=lambda t: price_of(t[1])) if have else (None, None)


def fmt_group(group, row):
    """'$636 (2, lower; $670 single)' -- terse, for a lock screen."""
    qty, listing = best_in(row)
    if not listing:
        return None
    price = dollars(price_of(listing))
    out = f"${price:.0f} ({qty}, {group.lower()}"
    if qty != 1:
        single = row.get(1)
        out += f"; ${dollars(price_of(single)):.0f} single" if single else "; no single"
    return out + ")"


def headline(grid):
    """Every group, cheapest first: '$505 (1, upper); $636 (2, lower)'."""
    parts = []
    for g, row in grid.items():
        qty, listing = best_in(row)
        if listing:
            parts.append((price_of(listing), fmt_group(g, row)))
    return "; ".join(p for _, p in sorted(parts)) or "nothing listed"


def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def cell(grid, group, qty, field="price"):
    l = grid.get(group, {}).get(qty)
    if not l:
        return ""
    if field == "price":
        return f"{dollars(price_of(l)):.2f}"
    return l.get(field, "")


def append_history(row):
    exists = os.path.exists(HISTORY_PATH)
    with open(HISTORY_PATH, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow([
                "timestamp_utc", "n_listings",
                "lower_q1", "lower_q2", "lower_q3",
                "upper_q1", "upper_q2", "upper_q3",
                "best_all_in", "best_qty", "best_group", "best_section", "best_row",
                "lower_best_all_in", "lower_best_qty", "lower_section", "lower_row",
            ])
        w.writerow(row)


def notify(title, body, priority="default", tags="ticket", burst=1, gap=8):
    """Push to phone via ntfy.sh. burst>1 repeats the message."""
    if not NTFY_TOPIC:
        log(f"(no NTFY_TOPIC) would notify [{priority}]: {title} -- {body}")
        return
    for i in range(burst):
        suffix = f" ({i + 1}/{burst})" if burst > 1 else ""
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=(body + suffix).encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": tags,
                     "Click": BUY_URL},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=TIMEOUT).read()
        except Exception as e:
            log(f"notify failed: {e}")
            return
        if i + 1 < burst:
            time.sleep(gap)
    log(f"notified [{priority}] x{burst}: {title}")


def heartbeat(line):
    """Silent proof-of-life, on a sidecar topic the phone is not subscribed to.

    The 3am health check used to count summary pushes, but those only land
    every two hours: six of them look identical whether we polled 6 times or
    180. That is exactly how a collapse from 20-minute checks to 2-hour checks
    went unnoticed. This fires on every single poll at `min` priority, so the
    checker can count real polls without anything reaching the lock screen.
    """
    if not NTFY_TOPIC:
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}-hb",
        data=line.encode("utf-8"),
        headers={"Priority": "min", "Tags": "heartbeat"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT).read()
    except Exception as e:
        log(f"heartbeat failed: {e}")


def due(state, key, minutes):
    ts = state.get(key)
    if not ts:
        return True
    try:
        last = datetime.fromisoformat(ts)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - last > timedelta(minutes=minutes)


def main():
    now = datetime.now(timezone.utc)
    state = load_state()

    listings = fetch_listings()
    if listings is None:
        fails = int(state.get("consecutive_failures", 0)) + 1
        state["consecutive_failures"] = fails
        log(f"fetch failure #{fails}")
        # Silence is indistinguishable from "no cheap tickets", so say it aloud.
        if fails in (3, 12, 48):
            notify("Ticket watcher is blind",
                   f"{fails} consecutive failed fetches from Gametime. Not "
                   f"checking prices right now -- look at the Actions tab.",
                   priority="high", tags="warning")
        save_state(state)
        return 0

    state["consecutive_failures"] = 0
    grid = screen(listings)
    line = headline(grid)

    lower_row = grid.get(LOWER, {})
    l_qty, l_best = best_in(lower_row)

    # Cheapest across every group and lot size.
    all_best = [(price_of(l), q, g, l)
                for g, row in grid.items() for q, l in row.items() if l]
    if not all_best:
        log(f"nothing buyable in lots of 1-{MAX_QTY} right now")
        state["last_seen"] = {"at": now.isoformat(timespec="seconds")}
        save_state(state)
        return 0
    b_cents, b_qty, b_group, b_listing = min(all_best)
    b_total = dollars(b_cents)
    l_total = dollars(price_of(l_best)) if l_best else None

    append_history([
        now.isoformat(timespec="seconds"), len(listings),
        cell(grid, LOWER, 1), cell(grid, LOWER, 2), cell(grid, LOWER, 3),
        cell(grid, UPPER, 1), cell(grid, UPPER, 2), cell(grid, UPPER, 3),
        f"{b_total:.2f}", b_qty, b_group,
        b_listing.get("section"), b_listing.get("row"),
        f"{l_total:.2f}" if l_total else "", l_qty or "",
        l_best.get("section") if l_best else "",
        l_best.get("row") if l_best else "",
    ])

    log(line)
    for g, row in grid.items():
        log("  " + g + ": " + ", ".join(
            f"q{q}=" + (f"${dollars(price_of(l)):.0f}" if l else "-")
            for q, l in sorted(row.items())))

    # --- Tier 3: the lower bowl, on two lines. -------------------------------
    # At or under PRICE_CEILING this is the outcome the whole system exists for,
    # so it fires every run, forever, until it is gone. Between there and
    # LOWER_CEILING it is worth knowing immediately but could plausibly sit for
    # days -- so it re-fires on a new or cheaper listing rather than every run,
    # which keeps a $495 floor from training us to mute the topic.
    if l_total is not None or FORCE_NOTIFY:
        siren = FORCE_NOTIFY or l_total <= PRICE_CEILING
        watch = FORCE_NOTIFY or l_total <= LOWER_CEILING
        lp_id = l_best.get("id") if l_best else None
        prev_lid = state.get("last_lower_id")
        prev_lprice = state.get("last_lower_price")
        fresh = (lp_id != prev_lid or prev_lprice is None or l_total < prev_lprice)

        if siren:
            notify(
                f"LOWER ${l_total:.0f} ({l_qty})!! BUY NOW" if l_best else "LOWER TEST",
                (f"{line}\nSec {l_best.get('section')} row {l_best.get('row')}. "
                 f"Under ${PRICE_CEILING:.0f}. This is the one -- buy it."
                 if l_best else "forced test of the lower-bowl alert path"),
                priority="max", tags="rotating_light,fire", burst=LOWER_BURST)
            state["last_lower_alert"] = now.isoformat(timespec="seconds")
        elif watch and (fresh or due(state, "last_lower_alert", COOLDOWN_MIN)):
            notify(
                f"LOWER ${l_total:.0f} ({l_qty}) - under ${LOWER_CEILING:.0f}",
                f"{line}\nSec {l_best.get('section')} row {l_best.get('row')}. "
                f"Lower bowl is worth a look right now.",
                priority="max", tags="rotating_light", burst=3)
            state["last_lower_alert"] = now.isoformat(timespec="seconds")

        if watch:
            state["last_lower_id"] = lp_id
            state["last_lower_price"] = l_total

    # --- Tier 2: anything under the ceiling. ---------------------------------
    # The cooldown suppresses re-alerting on the SAME listing. A different
    # listing, or a cheaper price, always re-fires: a $390 seat that sells and
    # is replaced by a $380 seat 40 minutes later is news, not a repeat.
    prev_id = state.get("last_any_alert_id")
    prev_price = state.get("last_any_alert_price")
    is_new_offer = (b_listing.get("id") != prev_id
                    or prev_price is None or b_total < prev_price)
    if (b_total <= PRICE_CEILING or FORCE_NOTIFY) and (
            is_new_offer or due(state, "last_any_alert", COOLDOWN_MIN)):
        notify(f"Under ${PRICE_CEILING:.0f}: {line}",
               f"Sec {b_listing.get('section')} row {b_listing.get('row')}, "
               f"buy {b_qty}. Check the map and move.",
               priority="urgent", tags="rotating_light", burst=2)
        state["last_any_alert"] = now.isoformat(timespec="seconds")
        state["last_any_alert_id"] = b_listing.get("id")
        state["last_any_alert_price"] = b_total

    # --- Tier 1: the routine summary. ----------------------------------------
    if due(state, "last_report", REPORT_EVERY_MIN) or FORCE_NOTIFY:
        prev = (state.get("last_seen") or {}).get("best")
        trend = ""
        if prev is not None and abs(b_total - prev) >= 1:
            trend = f" ({'+' if b_total > prev else ''}{b_total - prev:.0f})"
        detail = []
        for g, row in grid.items():
            detail.append(g + " " + " ".join(
                f"{q}:" + (f"${dollars(price_of(l)):.0f}" if l else "-")
                for q, l in sorted(row.items())))
        notify(line + trend,
               "\n".join(detail) + f"\nper ticket all-in, lots of 1-{MAX_QTY}. "
               f"Alert at ${PRICE_CEILING:.0f}, lower bowl ${LOWER_CEILING:.0f}.",
               priority="low", tags="chart_with_upwards_trend")
        state["last_report"] = now.isoformat(timespec="seconds")

    for key, val in (("all_time_low_overall", b_total), ("all_time_low_lower", l_total)):
        if val is not None and (state.get(key) is None or val < state[key]):
            state[key] = val

    state["last_seen"] = {"at": now.isoformat(timespec="seconds"),
                          "best": b_total, "lower": l_total, "line": line}
    heartbeat(line)
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
