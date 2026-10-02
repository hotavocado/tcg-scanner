"""Best Buy adapter, store 511 (1791 Oconee Connector, Athens GA).

Best Buy blocks this VM outright, so one Firecrawl stealth scrape of the search
page runs bestbuy.js in-page: it walks the result pages, asks storeAvailability
about every SKU in one call, and reads price off the product page only for SKUs
that can be picked up at 511. One poll is one Firecrawl credit, which is why
this source polls every INTERVAL_MINS rather than every minute.

Gates (docs/retailer-recon.md): on_sale means not a known marketplace listing
(Apollo seller classification is not "3P"), in_stock means pickup at 511 at or
under the MSRP ceiling. A marketplace listing can never alert.
"""

import json
import os
import re
import urllib.parse
import urllib.request

SOURCE = "bestbuy"
INTERVAL_MINS = 10
SEARCH_URL = "https://www.bestbuy.com/site/searchpage.jsp?st=one+piece+card+game"
API = "https://api.firecrawl.dev/v2/scrape"
KEY_FILE = os.path.expanduser(os.environ.get("TCG_FIRECRAWL_ENV", "~/.claude/secrets/firecrawl.env"))
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bestbuy.js")
ITEM_URL = "https://www.bestbuy.com/site/{sku}.p?skuId={sku}"

# Ceilings, not exact MSRPs: Best Buy's own price for the OP-17 single pack is
# $4.99. A product matching no pattern has no ceiling, because the 1P gate
# already keeps out the marketplace resellers that price above MSRP.
# Multi-pack products (double pack sets, lots, illustration boxes) get no
# ceiling rather than a single pack's.
MULTI_PACK = re.compile(r"double|\blot\b|\bset\b|\d+\s*packs", re.I)
MSRP_CEILINGS = (
    (re.compile(r"starter deck|\bST-?\d", re.I), 14.99),
    (re.compile(r"booster box|24 packs|box of 24", re.I), 119.99),
)
SINGLE_PACK = (re.compile(r"booster pack|\bpack\b", re.I), 5.99)


def api_key():
    """Read FIRECRAWL_API_KEY from the same file the MCP launcher reads.
    The key only ever goes into a request header."""
    with open(KEY_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):]
            if line.startswith("FIRECRAWL_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    raise RuntimeError(f"FIRECRAWL_API_KEY not found in {KEY_FILE}")


def scrape(timeout=150):
    with open(SCRIPT) as f:
        script = f.read()
    body = {
        "url": SEARCH_URL,
        "proxy": "stealth",
        "location": {"country": "US"},
        "storeInCache": False,
        "formats": ["markdown"],
        "includeTags": ["#probe-none"],  # matches nothing, so no page text comes back
        "actions": [{"type": "wait", "milliseconds": 1500}, {"type": "executeJavascript", "script": script}],
    }
    req = urllib.request.Request(
        API, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    if not d.get("success"):
        raise RuntimeError(f"firecrawl: {str(d.get('error'))[:200]}")
    returns = d["data"]["actions"]["javascriptReturns"]
    return json.loads(returns[0]["value"])


def ceiling(name):
    name = name or ""
    for pattern, cap in MSRP_CEILINGS:
        if pattern.search(name):
            return cap
    pattern, cap = SINGLE_PACK
    if pattern.search(name) and not MULTI_PACK.search(name):
        return cap
    return None


def normalize(item):
    name = item.get("name")  # None on a skeleton tile; the scanner keeps the last known name
    price = item.get("price")
    # Most SKUs carry no seller at all (52 of 68 on 2026-10-02, on the product
    # page too), so an explicit "3P" is the block and store pickup is the proof:
    # marketplace items cannot be picked up at a Best Buy store.
    first_party = item.get("seller") != "3P"
    cap = ceiling(name)
    within = price is not None and (cap is None or price <= cap)
    return {
        "source": SOURCE,
        "id": item["sku"],
        "name": name,
        "price": price,
        "currency": "USD",
        "on_sale": first_party,
        "in_stock": first_party and bool(item.get("pickup")) and within,
        "drawing": False,
        "sale_start": None,
        "sale_end": None,
        "url": urllib.parse.urljoin("https://www.bestbuy.com/", item.get("url") or ITEM_URL.format(sku=item["sku"])),
        "seller": item.get("seller") or "none",  # shown on alerts: pickup-implies-1P is inferred, not measured
    }


def relevant(item):
    """The search also returns Pokemon and sports cards. Keep One Piece, and
    keep unnamed SKUs (skeleton tiles) rather than drop a real one."""
    return not item.get("name") or "one piece" in item["name"].lower()


def parse(raw):
    if raw.get("errors"):
        raise RuntimeError("; ".join(raw["errors"])[:300])
    if raw.get("sa_status") != 200:
        raise RuntimeError(f"storeAvailability http {raw.get('sa_status')}")
    return [normalize(i) for i in raw["items"] if relevant(i)]


def fetch():
    raw = scrape()
    if raw.get("loc_sample"):
        print(f"bestbuy: pickup-eligible sample {raw['loc_sample']}", flush=True)
    return parse(raw)
