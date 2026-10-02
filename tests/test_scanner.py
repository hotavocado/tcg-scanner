import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scanner
from adapters import pbandai

NOW = dt.datetime(2026, 10, 2, 23, 0, tzinfo=dt.timezone.utc)


def prod(id="A1", on_sale=True, in_stock=True, drawing=False, sale_start="2026-09-30T02:00:00Z"):
    return {
        "source": "pbandai", "id": id, "name": f"Box {id}", "price": 120.0, "currency": "USD",
        "on_sale": on_sale, "in_stock": in_stock, "drawing": drawing,
        "sale_start": sale_start, "sale_end": "2026-10-08T06:59:59.999Z",
        "url": f"https://p-bandai.com/us/item/{id}",
    }


def state_of(*ps):
    return scanner.diff({}, list(ps), NOW)[1]


class Diff(unittest.TestCase):
    def kinds(self, old, new):
        return [k for k, _ in scanner.diff(old, new, NOW)[0]]

    def test_unchanged_is_silent(self):
        p = prod()
        self.assertEqual(self.kinds(state_of(p), [p]), [])

    def test_new_on_sale(self):
        self.assertEqual(self.kinds({}, [prod()]), ["NEW"])

    def test_new_upcoming(self):
        self.assertEqual(self.kinds({}, [prod(on_sale=False, sale_start="2026-11-01T00:00:00Z")]), ["NEW"])

    def test_new_but_ended_is_silent(self):
        self.assertEqual(self.kinds({}, [prod(on_sale=False)]), [])

    def test_goes_on_sale(self):
        self.assertEqual(self.kinds(state_of(prod(on_sale=False)), [prod()]), ["ON SALE"])

    def test_drawing_opens(self):
        self.assertEqual(self.kinds(state_of(prod(on_sale=False, drawing=True)), [prod(drawing=True)]),
                         ["DRAWING OPEN"])

    def test_restock(self):
        self.assertEqual(self.kinds(state_of(prod(in_stock=False)), [prod()]), ["RESTOCK"])

    def test_sells_out_is_silent(self):
        self.assertEqual(self.kinds(state_of(prod()), [prod(in_stock=False)]), [])

    def test_drawing_flag_added(self):
        self.assertEqual(self.kinds(state_of(prod()), [prod(drawing=True)]), ["DRAWING OPEN"])

    def test_on_sale_but_sold_out_is_silent_until_restock(self):
        old = state_of(prod(on_sale=False, in_stock=False))
        self.assertEqual(self.kinds(old, [prod(in_stock=False)]), [])
        mid = scanner.diff(old, [prod(in_stock=False)], NOW)[1]
        self.assertEqual(self.kinds(mid, [prod()]), ["RESTOCK"])

    def test_new_sold_out_is_silent(self):
        self.assertEqual(self.kinds({}, [prod(in_stock=False)]), [])

    def test_vanished_product_stays_in_state(self):
        old = state_of(prod("A1"), prod("B2"))
        alerts, st = scanner.diff(old, [], NOW)
        self.assertEqual(alerts, [])
        self.assertEqual(set(st), set(old))
        self.assertEqual(self.kinds(st, [prod("A1"), prod("B2")]), [])


class Format(unittest.TestCase):
    def test_drawing_says_lottery_and_deadline(self):
        s = scanner.format_alert("DRAWING OPEN", prod(drawing=True), NOW)
        self.assertIn("lottery, enter by Oct 08 06:59 UTC", s)
        self.assertIn("$120.00", s)

    def test_buy_now(self):
        self.assertIn("buy now", scanner.format_alert("ON SALE", prod(), NOW))

    def test_chunks_respect_cap(self):
        batches = list(scanner.chunks(["x" * 700] * 5))
        self.assertTrue(all(sum(len(l) for l in b) <= 1800 for b in batches))
        self.assertEqual(sum(len(b) for b in batches), 5)


class Normalize(unittest.TestCase):
    def test_flags_map(self):
        raw = {
            "productCode": "N9065181001", "productName": {"en": "OP-17 Booster Box"},
            "fixedListPrice": {"amount": 120.0, "currency": "USD"}, "saleStatus": "On",
            "flags": ["CHANCE_TO_BUY_DRAWING", "PRE_ORDER"],
            "saleStartExpectedDt": "2026-09-30T02:00:00Z", "saleEndExpectedDt": "2026-10-08T06:59:59.999Z",
        }
        p = pbandai.normalize(raw)
        self.assertTrue(p["on_sale"] and p["in_stock"] and p["drawing"])
        self.assertEqual(p["url"], "https://p-bandai.com/us/item/N9065181001")
        raw["flags"] = ["OUT_OF_STOCK"]
        self.assertFalse(pbandai.normalize(raw)["in_stock"])
        raw["flags"] = ["PRE_ORDER_CLOSED"]
        self.assertFalse(pbandai.normalize(raw)["in_stock"])


if __name__ == "__main__":
    unittest.main()
