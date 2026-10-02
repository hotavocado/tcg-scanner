"""Premium Bandai USA adapter.

The brand page is a Vue shell over a JSON search API. Two headers are required:
without X-G1-Area-Code the API answers 500. The brand filter key is the `_f_`
prefix; plain `brands=` is silently ignored and returns the whole store.
"""

import json
import urllib.parse
import urllib.request

SOURCE = "pbandai"
API = "https://p-bandai.com/api/search"
ITEM_URL = "https://p-bandai.com/us/item/{code}"
PAGE = 200
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "X-G1-Area-Code": "us",
    "Accept-Language": "en",
    "Accept": "application/json",
}
BRANDS = {"06-0074": "ONE PIECE CARD GAME"}


def _get(params, timeout=15):
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def normalize(p):
    flags = set(p.get("flags") or [])
    price = p.get("fixedListPrice") or {}
    return {
        "source": SOURCE,
        "id": p["productCode"],
        "name": (p.get("productName") or {}).get("en") or p["productCode"],
        "price": price.get("amount"),
        "currency": price.get("currency", "USD"),
        "on_sale": p.get("saleStatus") == "On",
        "in_stock": "OUT_OF_STOCK" not in flags and "PRE_ORDER_CLOSED" not in flags,
        "drawing": "CHANCE_TO_BUY_DRAWING" in flags,
        "sale_start": p.get("saleStartExpectedDt"),
        "sale_end": p.get("saleEndExpectedDt"),
        "url": ITEM_URL.format(code=p["productCode"]),
    }


def fetch():
    """Return every product for the watched brands, normalized.

    Raises on any HTTP or shape error, so a bad poll never reaches the state.
    """
    out = []
    for brand in BRANDS:
        offset = 0
        while True:
            d = _get({"_f_brands": brand, "limit": PAGE, "offset": offset})
            res = d["productResults"]
            products = res["products"]
            out.extend(normalize(p) for p in products)
            offset += len(products)
            if not products or offset >= res["totalCount"]:
                break
    return out
