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

ENV_FILE = os.path.expanduser(os.environ.get("TCG_ENV_FILE", "~/.config/tcg-scanner/env"))
STATE_DIR = os.path.expanduser(os.environ.get("TCG_STATE_DIR", "~/.local/state/tcg-scanner"))
STATE_FILE = os.path.join(STATE_DIR, "state.json")
LOCK_FILE = os.path.join(STATE_DIR, "lock")
TRACKED = ("on_sale", "in_stock", "drawing")


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
        state[k] = {f: p[f] for f in ("name",) + TRACKED}
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
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-alert", action="store_true")
    args = ap.parse_args()

    os.makedirs(STATE_DIR, exist_ok=True)
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("previous run still going, skipping")
        return 0

    products = []
    for a in ADAPTERS:
        got = a.fetch()
        log(f"{a.SOURCE}: {len(got)} products, {sum(p['on_sale'] for p in got)} on sale")
        products.extend(got)

    if args.test_alert:
        live = [p for p in products if p["on_sale"] and (p["in_stock"] or p["drawing"])] or products
        sample = live[0]
        kind = "DRAWING OPEN" if sample["drawing"] else "ON SALE"
        post(load_env(), ["**TEST** · scanner is wired up. Real alerts look like this:\n"
                          + format_alert(kind, sample)])
        log("test alert posted")
        return 0

    old = load_state()
    if old is None:
        _, state = diff({}, products)
        if args.dry_run:
            log(f"dry-run: no state yet; a real run would seed {len(state)} products silently")
        else:
            save_state(state)
            log(f"seeded {len(state)} products, no alerts")
        return 0

    alerts, state = diff(old, products)
    lines = [format_alert(k, p) for k, p in alerts]
    if args.dry_run:
        for line in lines:
            print(line, "\n")
        log(f"dry-run: {len(alerts)} alert(s), nothing posted or saved")
        return 0
    if lines:
        env = load_env()
        for batch in chunks(lines):
            post(env, batch)
        log(f"posted {len(alerts)} alert(s): " + ", ".join(f"{k} {p['id']}" for k, p in alerts))
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
