"""Best Buy adapter, store 511 (1791 Oconee Connector, Athens GA).

Best Buy blocks this VM outright, so one Firecrawl stealth scrape of the search
page runs bestbuy.js in-page: it walks the result pages, asks storeAvailability
about every SKU in one call, and reads price off the product page only for SKUs
that can be picked up at 511. One poll is one Firecrawl credit, which is why
this source polls at fixed times (RUN_AT) rather than every minute.

Gates (docs/retailer-recon.md): on_sale means not a known marketplace listing
(Apollo seller classification is not "3P"), in_stock means pickup at 511 at or
under the MSRP ceiling. A marketplace listing can never alert.
"""

import urllib.parse

from . import firecrawl
from .msrp import ceiling

SOURCE = "bestbuy"
# Mike 2026-10-02 (dm-alyssa 84680, upper 84683): twice a day. 08:00 is the
# overnight-restock-before-open check. 2 credits/day.
RUN_AT = ("08:00", "15:00")
RUN_TZ = "America/New_York"
SEARCH_URL = "https://www.bestbuy.com/site/searchpage.jsp?st=one+piece+card+game"
ITEM_URL = "https://www.bestbuy.com/site/{sku}.p?skuId={sku}"


def scrape():
    return firecrawl.run(SEARCH_URL, "bestbuy.js")


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
