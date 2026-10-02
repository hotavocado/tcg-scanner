#!/usr/bin/env python3
"""One Piece TCG drop and restock scanner.

Polls each adapter, diffs against saved state, and posts alerts to a Discord
webhook with a real user mention. Run once per minute from cron.

  scanner.py               poll, alert, save state (seeds silently if no state)
  scanner.py --dry-run     poll and print alerts; post nothing, save nothing
  scanner.py --test-alert  post one forced alert to prove the path end to end

Config (env file, mode 600, outside the repo): TCG_WEBHOOK_URL,
TCG_MENTION_USER_ID. Default path ~/.config/tcg-scanner/env.
"""

import argparse
import datetime as dt
import fcntl
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request

from adapters import ADAPTERS
from dashboard import Site

ENV_FILE = os.path.expanduser(os.environ.get("TCG_ENV_FILE", "~/.config/tcg-scanner/env"))
STATE_DIR = os.path.expanduser(os.environ.get("TCG_STATE_DIR", "~/.local/state/tcg-scanner"))
STATE_FILE = os.path.join(STATE_DIR, "state.json")
LOCK_FILE = os.path.join(STATE_DIR, "lock")
BACKOFF_MINS = (1, 2, 5, 15)
TRACKED = ("on_sale", "in_stock", "drawing")
KEPT = ("source", "name", "price", "currency", "url", "sale_start", "sale_end")


def log(msg):
    print(f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ} {msg}", flush=True)


def load_env():
    env = {}
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=".state.")
    with os.fdopen(fd, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, STATE_FILE)


def pages_remote():
    import subprocess
    r = subprocess.run(["git", "-C", os.path.dirname(os.path.abspath(__file__)), "remote", "get-url", "origin"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def key(p):
    return f"{p['source']}:{p['id']}"


def _future(ts, now):
    if not ts:
        return False
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")) > now
    except ValueError:
        return False


def diff(old, products, now=None):
    """Return (alerts, new_state). Products that vanish stay in state, so a
    partial or empty poll can never make the next full poll look all-new."""
    now = now or dt.datetime.now(dt.timezone.utc)
    state = dict(old)
    alerts = []
    for p in products:
        k = key(p)
        prev = old.get(k)
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        changed = prev is None or any(prev.get(f) != p[f] for f in TRACKED)
        state[k] = {f: p.get(f) for f in KEPT + TRACKED}
        state[k]["first_seen"] = (prev or {}).get("first_seen") or stamp
        state[k]["last_change"] = stamp if changed else (prev or {}).get("last_change")
        actionable = p["on_sale"] and (p["in_stock"] or p["drawing"])
        if prev is None:
            if actionable or (not p["on_sale"] and _future(p["sale_start"], now)):
                alerts.append(("NEW", p))
            continue
        if actionable and not prev.get("on_sale"):
            alerts.append(("DRAWING OPEN" if p["drawing"] else "ON SALE", p))
        elif p["on_sale"] and p["in_stock"] and not prev.get("in_stock"):
            alerts.append(("RESTOCK", p))
        elif p["on_sale"] and p["drawing"] and not prev.get("drawing"):
            alerts.append(("DRAWING OPEN", p))
    return alerts, state


def plan(old, by_source, now=None):
    """Diff each source on its own. A source with no saved products yet seeds
    silently (a fresh install, or a newly added retailer), and a source that
    returns nothing is skipped, so neither can flood alerts."""
    state, alerts = dict(old), []
    for source, products in by_source.items():
        if not products:
            log(f"{source}: empty poll, skipped")
            continue
        if not any(k.startswith(source + ":") for k in old):
            _, state = diff(state, products, now)
            log(f"{source}: seeded {len(products)} products, no alerts")
            continue
        found, state = diff(state, products, now)
        alerts.extend(found)
    return state, alerts


def _when(ts):
    if not ts:
        return "?"
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%b %d %H:%M UTC")
    except ValueError:
        return ts


def format_alert(kind, p, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    price = f"${p['price']:.2f}" if isinstance(p.get("price"), (int, float)) else "price ?"
    if p["drawing"]:
        action = f"lottery, enter by {_when(p['sale_end'])}"
    elif not p["on_sale"] and _future(p["sale_start"], now):
        action = f"opens {_when(p['sale_start'])}"
    elif not p["in_stock"]:
        action = "listed, out of stock"
    else:
        action = "buy now"
    return f"**{kind}** · {p['name']}\n{price} · {action}\n<{p['url']}>"


def post(env, lines):
    uid = env["TCG_MENTION_USER_ID"]
    body = {
        "username": "tcg-scanner",
        "content": f"<@{uid}>\n" + "\n\n".join(lines),
        "allowed_mentions": {"users": [uid]},
    }
    data = json.dumps(body).encode()
    for attempt in range(3):
        req = urllib.request.Request(
            env["TCG_WEBHOOK_URL"],
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "tcg-scanner/1"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15):
                return
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 2:
                raise
            time.sleep(float(json.load(e).get("retry_after", 2)))


def chunks(lines, limit=1800):
    """Group alert blocks under Discord's 2000-char message cap."""
    batch, size = [], 0
    for line in lines:
        if batch and size + len(line) > limit:
            yield batch
            batch, size = [], 0
        batch.append(line)
        size += len(line) + 2
    if batch:
        yield batch


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--test-alert", action="store_true")
    args = ap.parse_args()

    os.makedirs(STATE_DIR, exist_ok=True)
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("previous run still going, skipping")
        return 0
    site = Site(STATE_DIR, pages_remote())

    if not args.dry_run and not args.test_alert and backing_off(site):
        return 0

    by_source, errors = {}, []
    for a in ADAPTERS:
        try:
            got = a.fetch()
        except Exception as e:  # one broken source must not stop the others
            errors.append(f"{a.SOURCE}: {type(e).__name__}: {e}")
            log(f"{a.SOURCE}: FAILED {errors[-1]}")
            continue
        log(f"{a.SOURCE}: {len(got)} products, {sum(p['on_sale'] for p in got)} on sale")
        by_source[a.SOURCE] = got
    products = [p for got in by_source.values() for p in got]

    if args.test_alert:
        live = [p for p in products if p["on_sale"] and (p["in_stock"] or p["drawing"])] or products
        sample = live[0]
        kind = "DRAWING OPEN" if sample["drawing"] else "ON SALE"
        post(load_env(), ["**TEST** · scanner is wired up. Real alerts look like this:\n"
                          + format_alert(kind, sample)])
        log("test alert posted")
        return 0

    old = load_state() or {}
    state, alerts = plan(old, by_source)
    lines = [format_alert(k, p) for k, p in alerts]
    if args.dry_run:
        for line in lines:
            print(line, "\n")
        log(f"dry-run: {len(alerts)} alert(s), nothing posted or saved")
        return 0
    env = load_env()
    if lines:
        for batch in chunks(lines):
            post(env, batch)
        log(f"posted {len(alerts)} alert(s): " + ", ".join(f"{k} {p['id']}" for k, p in alerts))
    save_state(state)

    ok = not errors
    sources = {src: {"count": len(got), "on_sale": sum(p["on_sale"] for p in got)} for src, got in by_source.items()}
    flipped = site.record(ok, sources, "; ".join(errors), alerts)
    fails = site.failures()
    if flipped:
        msg = (f"**SCANNER BACKING OFF** · {'; '.join(errors)[:300]}\nRetrying in {BACKOFF_MINS[1]} min, "
               f"widening to {BACKOFF_MINS[-1]} min while it keeps failing." if not ok
               else "**SCANNER RECOVERED** · polling every minute again.")
        try:
            post(env, [msg])
        except Exception as e:
            log(f"health alert failed: {e}")
    changed = state != old
    if site.due(force=changed or flipped):
        try:
            site.publish(state)
            log("dashboard published")
        except Exception as e:
            log(f"dashboard publish failed: {e}")
    return 0 if ok else 1


def backing_off(site):
    """After failures, skip runs until the backoff interval has passed."""
    fails = site.failures()
    if not fails:
        return False
    wait = BACKOFF_MINS[min(fails, len(BACKOFF_MINS) - 1)] * 60
    since = site.since_last_run()
    if since is not None and since < wait - 5:
        log(f"backing off: {fails} failure(s), next try in {int(wait - since)}s")
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
