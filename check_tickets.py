#!/usr/bin/env python3
"""
Red River Rivalry (OU vs Texas, Cotton Bowl, 2026-10-10) ticket watcher.

Polls Gametime's public listings API for the game, filters to listings that
can actually be bought as a SINGLE ticket, logs every observation to CSV, and
pushes phone notifications via ntfy.

Why Gametime and not SeatGeek: the SeatGeek Platform API returns `stats: {}`
for this event (primary is Paciolan, is_open=false), so it has no price data
to give. Gametime returns per-listing section, row, section_group and both
pre-fee and all-in prices -- which is what makes the lower-bowl split possible.

Notification tiers:
  SUMMARY      every 2 hours, low priority. Lowest overall + lowest lower bowl.
  ANY <=400    urgent, one alert per cooldown window.
  LOWER <=400  max priority, repeated burst, every single run, no cooldown.

Prices from the API are in CENTS. Everything below works in dollars.

Environment variables:
  NTFY_TOPIC          required for alerts.
  GT_EVENT_ID         default 692f4b0348de0b1d9c246950
  PRICE_CEILING       default 400   (all-in dollars)
  REPORT_EVERY_MIN    default 60  (workflow sets 115)
  ALERT_COOLDOWN_MIN  default 120   (ANY alert, same listing only)
  LOWER_BURST         default 10    (repeats of the lower-bowl alert)
  FORCE_NOTIFY        "1" to force a report + fake alerts (dispatch testing)
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
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
REPORT_EVERY_MIN = int(os.environ.get("REPORT_EVERY_MIN", "60"))
COOLDOWN_MIN = int(os.environ.get("ALERT_COOLDOWN_MIN", "120"))
LOWER_BURST = int(os.environ.get("LOWER_BURST", "10"))
FORCE_NOTIFY = os.environ.get("FORCE_NOTIFY", "").strip() == "1"
STATE_PATH = os.environ.get("STATE_PATH", "state.json")
HISTORY_PATH = os.environ.get("HISTORY_PATH", "price_history.csv")

# Cotton Bowl: Gametime groups sections as "Lower" / "Upper". Anything it
# doesn't classify goes in its own bucket rather than being dropped or
# silently counted as lower bowl.
LOWER_GROUP = "Lower"

TIMEOUT = 25
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def fetch_listings():
    """Return the list of listing dicts, or None on failure."""
    url = LISTINGS_API.format(event_id=EVENT_ID)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
    })
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


def buyable_single(listing):
    """True if this listing can be purchased as exactly one ticket.

    Gametime prices a listing 'from' a lot size; a listing whose lots are
    [2, 4] cannot be bought as a single and its price is not a price we
    could ever pay.
    """
    lots = listing.get("lots") or []
    return 1 in lots


def dollars(cents):
    return None if cents is None else cents / 100.0


def summarize(listings):
    """Cheapest single-ticket listing overall and per section group."""
    singles = [x for x in listings if buyable_single(x)]

    def cheapest(pool):
        pool = [x for x in pool if (x.get("price") or {}).get("total") is not None]
        return min(pool, key=lambda x: x["price"]["total"]) if pool else None

    groups = {}
    for x in singles:
        groups.setdefault(x.get("section_group") or "Unknown", []).append(x)

    return {
        "n_listings": len(listings),
        "n_singles": len(singles),
        "overall": cheapest(singles),
        "lower": cheapest(groups.get(LOWER_GROUP, [])),
        "by_group": {g: cheapest(v) for g, v in groups.items()},
    }


def describe(listing):
    if not listing:
        return "none"
    p = listing["price"]
    return (f"${dollars(p['total']):.0f} all-in (${dollars(p['prefee']):.0f} pre-fee) "
            f"sec {listing.get('section')} row {listing.get('row')}")


def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def append_history(row):
    exists = os.path.exists(HISTORY_PATH)
    with open(HISTORY_PATH, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow([
                "timestamp_utc", "n_listings", "n_single_listings",
                "low_all_in", "low_prefee", "low_group", "low_section", "low_row",
                "lower_all_in", "lower_prefee", "lower_section", "lower_row",
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
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": tags,
                "Click": BUY_URL,
            },
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
        # Silence is indistinguishable from "no cheap tickets", so say it out loud.
        if fails in (3, 12, 48):
            notify(
                "Ticket watcher is blind",
                f"{fails} consecutive failed fetches from Gametime. "
                f"The watcher is not checking prices right now -- go look at the "
                f"Actions tab.",
                priority="high",
                tags="warning",
            )
        save_state(state)
        return 0

    state["consecutive_failures"] = 0
    s = summarize(listings)
    overall, lower = s["overall"], s["lower"]

    if overall is None:
        log("no single-ticket listings at all right now")
        state["last_seen"] = {"at": now.isoformat(timespec="seconds"), "singles": 0}
        save_state(state)
        return 0

    o_total = dollars(overall["price"]["total"])
    o_pre = dollars(overall["price"]["prefee"])
    l_total = dollars(lower["price"]["total"]) if lower else None
    l_pre = dollars(lower["price"]["prefee"]) if lower else None

    append_history([
        now.isoformat(timespec="seconds"), s["n_listings"], s["n_singles"],
        f"{o_total:.2f}", f"{o_pre:.2f}", overall.get("section_group"),
        overall.get("section"), overall.get("row"),
        f"{l_total:.2f}" if l_total else "", f"{l_pre:.2f}" if l_pre else "",
        lower.get("section") if lower else "", lower.get("row") if lower else "",
    ])

    log(f"singles={s['n_singles']}/{s['n_listings']} "
        f"overall={describe(overall)} | lower={describe(lower)}")

    # --- Tier 3: lower bowl under the ceiling. The one that matters. -----------
    lower_hit = (l_total is not None and l_total <= PRICE_CEILING) or FORCE_NOTIFY
    if lower_hit:
        notify(
            f"LOWER BOWL ${l_total:.0f}!! BUY NOW" if l_total else "LOWER BOWL TEST",
            (f"Lower bowl single at ${l_total:.0f} all-in "
             f"(sec {lower.get('section')}, row {lower.get('row')}). "
             f"This is the one. Open Gametime and buy it."
             if lower else "forced test of the lower-bowl alert path"),
            priority="max",
            tags="rotating_light,fire",
            burst=LOWER_BURST,
        )
        state["last_lower_alert"] = now.isoformat(timespec="seconds")

    # --- Tier 2: anything under the ceiling. -----------------------------------
    # The cooldown suppresses re-alerting on the SAME listing. A different
    # listing, or a cheaper price, always re-fires: a $390 seat that sells and
    # is replaced by a $380 seat 40 minutes later is news, not a repeat.
    any_hit = o_total <= PRICE_CEILING or FORCE_NOTIFY
    prev_id = state.get("last_any_alert_id")
    prev_price = state.get("last_any_alert_price")
    is_new_offer = (overall.get("id") != prev_id
                    or prev_price is None or o_total < prev_price)
    if any_hit and (is_new_offer or due(state, "last_any_alert", COOLDOWN_MIN)):
        notify(
            f"OU-TX under ${PRICE_CEILING:.0f}: ${o_total:.0f} all-in",
            (f"Cheapest single is ${o_total:.0f} all-in in the "
             f"{overall.get('section_group')} bowl "
             f"(sec {overall.get('section')}, row {overall.get('row')}). "
             f"Lower bowl floor is "
             f"{f'${l_total:.0f}' if l_total else 'n/a'}."),
            priority="urgent",
            tags="rotating_light",
            burst=2,
        )
        state["last_any_alert"] = now.isoformat(timespec="seconds")
        state["last_any_alert_id"] = overall.get("id")
        state["last_any_alert_price"] = o_total

    # --- Tier 1: the routine hourly report. ------------------------------------
    if due(state, "last_report", REPORT_EVERY_MIN) or FORCE_NOTIFY:
        prev = (state.get("last_seen") or {}).get("overall_all_in")
        trend = ""
        if prev is not None:
            delta = o_total - prev
            if abs(delta) >= 1:
                trend = f" ({'+' if delta > 0 else ''}{delta:.0f} vs last report)"
        notify(
            f"OU-TX: ${o_total:.0f} low / "
            f"{f'${l_total:.0f}' if l_total else 'n/a'} lower bowl",
            (f"Cheapest single ticket: ${o_total:.0f} all-in "
             f"(${o_pre:.0f} pre-fee), {overall.get('section_group')} "
             f"sec {overall.get('section')} row {overall.get('row')}{trend}.\n"
             f"Lower bowl: "
             f"{describe(lower) if lower else 'no single-ticket listings'}.\n"
             f"{s['n_singles']} of {s['n_listings']} listings sell as singles. "
             f"Target ${PRICE_CEILING:.0f} all-in."),
            priority="low",
            tags="chart_with_upwards_trend",
        )
        state["last_report"] = now.isoformat(timespec="seconds")

    # All-time lows, for the "is the floor drifting down" question.
    for key, val in (("all_time_low_overall", o_total), ("all_time_low_lower", l_total)):
        if val is not None and (state.get(key) is None or val < state[key]):
            state[key] = val

    state["last_seen"] = {
        "at": now.isoformat(timespec="seconds"),
        "overall_all_in": o_total,
        "lower_all_in": l_total,
        "singles": s["n_singles"],
    }
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
