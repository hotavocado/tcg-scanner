# tcg-scanner

Watches retailers for One Piece TCG drops and restocks and pings Mike in Discord `#op-drops`.

- `scanner.py`: poll every adapter, diff against `~/.local/state/tcg-scanner/state.json`, post alerts.
- `adapters/`: one module per retailer, each returning the same normalized product shape.
  `pbandai` (Premium Bandai USA, every minute) and `bestbuy` (store 511 Oconee Connector, 08:00 and 15:00 ET,
  one Firecrawl credit per poll; see `docs/retailer-recon.md`).
- Config: `~/.config/tcg-scanner/env` (mode 600, never committed): `TCG_WEBHOOK_URL`, `TCG_MENTION_USER_ID`.
  The Best Buy adapter reads `FIRECRAWL_API_KEY` from `~/.claude/secrets/firecrawl.env`, the file the Firecrawl MCP uses.

Each source keeps its own schedule in `sources.json` next to the state: an interval (`INTERVAL_MINS`, default 1)
or fixed times (`RUN_AT` in `RUN_TZ`), plus its own backoff (1, 2, 5, 15 min). A failed fixed-time poll retries 3 times
and then waits for the next slot, so one failing retailer never slows the others or burns credits all day.

Alerts: NEW (listed and buyable, a lottery, or opening later), ON SALE / DRAWING OPEN, RESTOCK.
A first run with no state seeds silently. Products that vanish from a poll stay in state, so a bad poll can't cause an alert flood.

    python3 scanner.py --dry-run      # print alerts, post and save nothing
    python3 scanner.py --test-alert   # one forced alert to #op-drops
    python3 -m unittest discover -s tests

Runs every minute from cron under cron-guard.
