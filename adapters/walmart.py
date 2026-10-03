"""Walmart adapter, stores 1400 (Epps Bridge Pkwy) and 2811 (Lexington Rd), Athens GA.

Walmart picks the store from the visitor's location, so walmart.js drives the
site's own store chooser to 1400 and replays its setPickup request for 2811.
One Firecrawl scrape covers both stores (docs/retailer-recon.md).

Products are the Walmart-sold One Piece items found by an unpinned search (every
one, whatever its stock) plus any a pinned search turns up, once per store.
Gates: sold by Walmart.com, in stock at the pinned store (pickup there, or an
in-store-only item while that store is pinned), and not priced over the MSRP
ceiling. Marketplace sellers, the scalper listings that fill Walmart's search,
are dropped in the page and never become products.

Price is usually missing: in the 2026-10-03 live run, all 20 One Piece catalog
items and all 7 in-stock items at the pinned stores came back with no price
(in-store-only items carry neither currentPrice nor linePrice). So only a price
that is present and over the ceiling blocks; a missing one alerts as "price ?".
Requiring a price would mean Walmart never alerts at all.

Instrument control: TACTA 2nd Edition, a Walmart-sold item that read pickup
IN_STOCK at both stores on 2026-10-03. Each store's read must show the session
pinned there and the control in stock, or the poll fails: a pin that silently
fell back to the proxy's store would read OUT forever.
"""

import json

from . import firecrawl
from .msrp import ceiling

SOURCE = "walmart"
RUN_AT = ("08:00", "15:00")  # same slots as Best Buy and Target (upper 84713)
RUN_TZ = "America/New_York"
STORES = {"1400": "Epps Bridge", "2811": "Lexington Rd"}
SEARCH_URL = "https://www.walmart.com/search?q=one+piece+card+game"
ITEM_URL = "https://www.walmart.com/ip/{id}"
AVAILABLE = {"IN_STOCK", "LIMITED_STOCK"}


def scrape():
    return firecrawl.run(SEARCH_URL, "walmart.js", timeout=170)


def one_piece(item):
    return "one piece" in (item.get("name") or "").lower()


def clean(name):
    name = (name or "").strip()
    return name[len("Collectible "):] if name.startswith("Collectible ") else name


def check(rec, store):
    if rec is None:
        raise RuntimeError(f"no read for store {store}")
    if rec.get("pinned") != store:
        raise RuntimeError(f"store {store} read ran pinned to {rec.get('pinned')}")
    c = rec.get("control") or {}
    if store not in c.get("storeIds", []) or c.get("pickup") not in AVAILABLE:
        raise RuntimeError(f"control at {store} read {json.dumps(c)}; the store read is not trustworthy")


def normalize(item, store, here):
    name = clean(item.get("name"))
    price = (here or {}).get("price") or item.get("price")
    cap = ceiling(name)
    within = price is None or cap is None or price <= cap
    available = bool(here) and here.get("avail") in AVAILABLE and (store in here.get("pickup", []) or here.get("ftype") == "STORE")
    return {
        "source": SOURCE,
        "id": f"{store}-{item['id']}",
        "name": f"{name} · Walmart {STORES[store]}",
        "price": price,
        "currency": "USD",
        "on_sale": True,  # only Walmart.com-sold items get this far
        "in_stock": available and within,
        "drawing": False,
        "sale_start": None,
        "sale_end": None,
        "url": ITEM_URL.format(id=item["id"]),
    }


def parse(raw):
    if raw.get("errors"):
        raise RuntimeError("; ".join(raw["errors"])[:300])
    catalog = {i["id"]: i for i in raw.get("catalog", []) if one_piece(i)}
    if not catalog:
        raise RuntimeError("discovery found no Walmart-sold One Piece items")
    reads = {r.get("store"): r for r in raw.get("stores", [])}
    out = []
    for store in STORES:
        rec = reads.get(store)
        check(rec, store)
        here = {i["id"]: i for i in rec["items"] if one_piece(i)}
        for id_ in sorted(set(catalog) | set(here)):
            out.append(normalize(catalog.get(id_) or here[id_], store, here.get(id_)))
    return out


def fetch():
    return parse(scrape())
