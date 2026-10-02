"""Retailer adapters. Each exposes SOURCE and fetch() -> list of normalized products.

Normalized product: source, id, name, price, currency, on_sale, in_stock,
drawing, sale_start, sale_end, url.
"""

from . import pbandai

ADAPTERS = [pbandai]
