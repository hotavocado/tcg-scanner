# tcg-scanner

Watches retailers for One Piece TCG drops and restocks and pings Mike in Discord `#op-drops`.

- `scanner.py`: poll every adapter, diff against `~/.local/state/tcg-scanner/state.json`, post alerts.
- `adapters/`: one module per retailer, each returning the same normalized product shape.
- Config: `~/.config/tcg-scanner/env` (mode 600, never committed): `TCG_WEBHOOK_URL`, `TCG_MENTION_USER_ID`.

Alerts: NEW (listed and buyable, a lottery, or opening later), ON SALE / DRAWING OPEN, RESTOCK.
A first run with no state seeds silently. Products that vanish from a poll stay in state, so a bad poll can't cause an alert flood.

    python3 scanner.py --dry-run      # print alerts, post and save nothing
    python3 scanner.py --test-alert   # one forced alert to #op-drops
    python3 -m unittest discover -s tests

Runs every minute from cron under cron-guard.
