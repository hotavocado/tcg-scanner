"""Price ceilings for the at-or-under-MSRP gate.

Ceilings, not Bandai's published MSRPs. Their job is catching scalper markup,
e.g. a 3P ST-31 at $44.99 on Best Buy. First-party prices seen on 2026-10-02:
Best Buy $4.99 for an OP-17 single pack; Target $11.99 to $16.99 for regular
starters, $19.99 for Starter Deck EX ST30, and $34.99 for ST-13 (Ultimate Deck)
and ST-21 (GEAR5). A product matching no pattern has no ceiling, because
the first-party gate already keeps out the marketplace resellers that price
above MSRP. Multi-pack products (double pack sets, lots, illustration boxes)
get no ceiling rather than a single pack's.
"""

import re

MULTI_PACK = re.compile(r"double|\blot\b|\bset\b|\d+\s*packs", re.I)
CEILINGS = (
    (re.compile(r"starter deck|ultimate deck|\bST-?\s?\d", re.I), 34.99),
    (re.compile(r"booster box|24 packs|box of 24", re.I), 119.99),
)
SINGLE_PACK = (re.compile(r"booster pack|\bpack\b", re.I), 5.99)


def ceiling(name):
    name = name or ""
    for pattern, cap in CEILINGS:
        if pattern.search(name):
            return cap
    pattern, cap = SINGLE_PACK
    if pattern.search(name) and not MULTI_PACK.search(name):
        return cap
    return None
