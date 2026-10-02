"""Target adapter, store 1453 (3065 Atlanta Hwy, Athens GA). A probe, not a watcher.

Target's search returns no first-party One Piece card products, so this polls a
fixed list of tcins through redsky (adapters/target.js, one Firecrawl credit).
On 2026-10-02 every starter here read OUT at 1453 while Mike saw Luffy/Ace decks
on that shelf; the hypothesis is that the card shelf is vendor-stocked and never
reaches Target's inventory. Two polls a day test it: the first IN on a card item
here kills the hypothesis, and it alerts like any other source.
"""

import html
import json

from . import firecrawl
from .msrp import ceiling

SOURCE = "target"
RUN_AT = ("08:00", "15:00")  # Mike 2026-10-02 (upper 84683): twice a day
RUN_TZ = "America/New_York"
STORE = "1453"
PAGE_URL = "https://www.target.com/p/one-piece-card-game-starter-deck-ex-luffy-38-ace-st30/-/A-95042136"
ITEM_URL = "https://www.target.com/p/-/A-{tcin}"
# From docs/retailer-recon.md. Add new sets here as they release.
TCINS = {
    "95042136": "ST30 Starter Deck EX Luffy & Ace",
    "89059000": "ST-09 Yamato",
    "89998332": "ST-12",
    "90259219": "ST-13",
    "91312539": "ST-14",
    "94262795": "ST-21",
    "94723221": "ST-22",
    "95120832": "ST-33 Kuzan",
    "95120846": "ST-35",
}
AVAILABLE = {"IN_STOCK", "LIMITED_STOCK"}
# Instrument control: a non-card item that read IN_STOCK qty 10 at 1453 on
# 2026-10-02. It must come back with a 1453 row or the poll is broken, since a
# query that silently drops the store would read OUT forever, which looks
# exactly like the vendor-shelf hypothesis holding.
CONTROL_TCIN = "95046363"  # LEGO One Piece Dr. Hiriluk's Hideout


def scrape():
    tcins = sorted(TCINS) + [CONTROL_TCIN]
    return firecrawl.run(PAGE_URL, "target.js", script_vars={"__TCINS__": json.dumps(tcins)})


def normalize(item):
    name = html.unescape(item.get("name") or TCINS.get(item["tcin"], f"Target {item['tcin']}"))
    price = item.get("price")
    cap = ceiling(name)
    within = price is not None and (cap is None or price <= cap)
    first_party = not item.get("marketplace")
    here = item.get("store") == STORE
    available = here and (item.get("pickup") in AVAILABLE or item.get("in_store") in AVAILABLE)
    return {
        "source": SOURCE,
        "id": item["tcin"],
        "name": name,
        "price": price,
        "currency": "USD",
        "on_sale": first_party,
        "in_stock": first_party and available and within,
        "drawing": False,
        "sale_start": None,
        "sale_end": None,
        "url": item.get("url") or ITEM_URL.format(tcin=item["tcin"]),
    }


def parse(raw):
    if raw.get("errors"):
        raise RuntimeError("; ".join(raw["errors"])[:300])
    if raw.get("status") != 200:
        raise RuntimeError(f"redsky http {raw.get('status')}")
    control = [i for i in raw["items"] if i["tcin"] == CONTROL_TCIN]
    if not control or control[0].get("store") != STORE:
        raise RuntimeError(f"control {CONTROL_TCIN} has no store {STORE} row; the stock query is not reading the store")
    return [normalize(i) for i in raw["items"] if i["tcin"] != CONTROL_TCIN]


def fetch():
    return parse(scrape())
