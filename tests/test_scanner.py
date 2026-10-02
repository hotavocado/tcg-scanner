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


class Plan(unittest.TestCase):
    def test_empty_first_poll_does_not_seed(self):
        st, alerts = scanner.plan({}, {"pbandai": []}, NOW)
        self.assertEqual((st, alerts), ({}, []))
        st, alerts = scanner.plan(st, {"pbandai": [prod("A1"), prod("B2")]}, NOW)
        self.assertEqual(alerts, [])
        self.assertEqual(len(st), 2)

    def test_new_source_seeds_silently_beside_existing(self):
        old = state_of(prod("A1"))
        tgt = dict(prod("T1"), source="target")
        st, alerts = scanner.plan(old, {"pbandai": [prod("A1"), prod("B2")], "target": [tgt]}, NOW)
        self.assertEqual([(k, p["id"]) for k, p in alerts], [("NEW", "B2")])
        self.assertIn("target:T1", st)


class Presence(unittest.TestCase):
    def test_vanished_product_is_marked_absent_then_back(self):
        a, b = prod("A1"), prod("B2")
        state, _ = scanner.plan(state_of(a, b), {"pbandai": [a]}, NOW)
        self.assertIs(state["pbandai:B2"]["present"], False)
        self.assertIs(state["pbandai:A1"]["present"], True)
        state, _ = scanner.plan(state, {"pbandai": [a, b]}, NOW)
        self.assertIs(state["pbandai:B2"]["present"], True)

    def test_empty_poll_does_not_mark_everything_absent(self):
        old = state_of(prod("A1"))
        state, _ = scanner.plan(old, {"pbandai": []}, NOW)
        self.assertIs(state["pbandai:A1"]["present"], True)


class Main(unittest.TestCase):
    """main() end to end with a fake adapter, a temp state dir and no git remote."""

    def setUp(self):
        import tempfile
        from unittest import mock
        self.dir = tempfile.mkdtemp()
        self.got = [prod("A1")]
        self.posted = []
        fake = type("Fake", (), {"SOURCE": "pbandai", "fetch": staticmethod(lambda: list(self.got))})
        for name, val in {
            "STATE_DIR": self.dir, "STATE_FILE": os.path.join(self.dir, "state.json"),
            "LOCK_FILE": os.path.join(self.dir, "lock"), "ADAPTERS": [fake],
            "pages_remote": lambda: None, "load_env": lambda: {},
            "post": lambda env, lines: self.posted.append(lines),
        }.items():
            p = mock.patch.object(scanner, name, val)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(sys, "argv", ["scanner.py"])
        p.start()
        self.addCleanup(p.stop)

    def health(self):
        import json
        return json.load(open(os.path.join(self.dir, "health.json")))

    def test_empty_poll_is_a_failure(self):
        self.assertEqual(scanner.main(), 0)
        self.got = []
        self.assertEqual(scanner.main(), 1)
        self.assertEqual(self.health()["consecutive_failures"], 1)
        self.assertIn("returned 0 products", self.health()["last_error"])

    def test_failed_alert_post_is_a_failure_and_retries(self):
        scanner.main()  # seeds silently
        self.got = [prod("A1"), prod("B2")]

        def boom(env, lines):
            raise RuntimeError("discord 500")
        with __import__("unittest").mock.patch.object(scanner, "post", boom):
            self.assertEqual(scanner.main(), 1)
        self.assertEqual(self.health()["consecutive_failures"], 1)
        self.assertNotIn("pbandai:B2", scanner.load_state())  # unsaved, so the alert fires again
        h = self.health()
        h["last_run"] = "2000-01-01T00:00:00Z"
        import json
        json.dump(h, open(os.path.join(self.dir, "health.json"), "w"))
        self.assertEqual(scanner.main(), 0)
        self.assertTrue(any("B2" in line for batch in self.posted for line in batch))


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


class Health(unittest.TestCase):
    def setUp(self):
        import tempfile
        from dashboard import Site
        self.dir = tempfile.mkdtemp()
        self.site = Site(self.dir)

    def test_flip_only_on_change_and_backoff_widens(self):
        self.assertFalse(self.site.record(True, {"pbandai": {"count": 1}}))
        self.assertTrue(self.site.record(False, error="HTTPError 403"))
        self.assertFalse(self.site.record(False, error="HTTPError 403"))
        self.assertEqual(self.site.failures(), 2)
        self.assertTrue(scanner.backing_off(self.site))
        self.assertTrue(self.site.record(True, {}))
        self.assertFalse(scanner.backing_off(self.site))

    def test_alert_log_newest_first_and_capped(self):
        for i in range(60):
            self.site.record(True, {}, alerts=[("NEW", prod(f"A{i}"))])
        import json
        log = json.load(open(os.path.join(self.dir, "alerts.json")))
        self.assertEqual(len(log), 50)
        self.assertEqual(log[0]["id"], "A59")

    def test_publish_without_remote_is_a_noop(self):
        self.assertFalse(self.site.publish({}))


if __name__ == "__main__":
    unittest.main()
