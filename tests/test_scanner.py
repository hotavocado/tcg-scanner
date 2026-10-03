import datetime as dt
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scanner
from adapters import bestbuy, firecrawl, pbandai, target, walmart

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
        fake = self.fake = type("Fake", (), {"SOURCE": "pbandai", "fetch": staticmethod(lambda: list(self.got))})
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

    def later(self, mins=20):
        """Pretend every source last polled `mins` ago."""
        sched = scanner.load_schedule()
        then = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=mins)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for rec in sched.values():
            rec["last_try"] = then
        scanner.save_schedule(sched)

    def test_runs_inside_the_interval_skip_the_source(self):
        calls = []
        self.fake.fetch = staticmethod(lambda: calls.append(1) or list(self.got))
        scanner.main()
        scanner.main()
        self.assertEqual(len(calls), 1)
        self.later(1)
        scanner.main()
        self.assertEqual(len(calls), 2)

    def test_slow_source_keeps_its_own_cadence(self):
        calls = []
        slow = type("Slow", (), {"SOURCE": "bestbuy", "INTERVAL_MINS": 2,
                                 "fetch": staticmethod(lambda: calls.append(1) or [dict(prod("S1"), source="bestbuy")])})
        with mock.patch.object(scanner, "ADAPTERS", [self.fake, slow]):
            scanner.main()
            self.later(1)
            scanner.main()
            self.assertEqual(len(calls), 1)
            self.later(2)
            scanner.main()
            self.assertEqual(len(calls), 2)

    def test_fixed_times_poll_once_per_slot_in_new_york_time(self):
        a = type("A", (), {"RUN_AT": ("08:00", "15:00"), "RUN_TZ": "America/New_York"})
        z = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        # EDT: 08:00 ET is 12:00Z
        self.assertEqual(scanner.latest_slot(a.RUN_AT, a.RUN_TZ, z("2026-10-03T12:30:00Z")), z("2026-10-03T12:00:00Z"))
        self.assertEqual(scanner.latest_slot(a.RUN_AT, a.RUN_TZ, z("2026-10-03T03:00:00Z")), z("2026-10-02T19:00:00Z"))
        # EST after Nov 1: 08:00 ET is 13:00Z
        self.assertEqual(scanner.latest_slot(a.RUN_AT, a.RUN_TZ, z("2026-11-10T13:05:00Z")), z("2026-11-10T13:00:00Z"))
        rec = {"last_try": "2026-10-02T23:50:00Z"}
        self.assertFalse(scanner.source_due(a, rec, z("2026-10-03T11:59:00Z")))
        self.assertTrue(scanner.source_due(a, rec, z("2026-10-03T12:00:30Z")))
        rec = {"last_try": "2026-10-03T12:00:30Z"}
        self.assertFalse(scanner.source_due(a, rec, z("2026-10-03T12:01:30Z")))

    def test_failed_slot_retries_then_waits_for_next_slot(self):
        a = type("A", (), {"RUN_AT": ("08:00", "15:00"), "RUN_TZ": "America/New_York"})
        z = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        rec = {"last_try": "2026-10-03T12:00:00Z", "fails": 1}
        self.assertTrue(scanner.source_due(a, rec, z("2026-10-03T12:02:00Z")))
        rec = {"last_try": "2026-10-03T12:30:00Z", "fails": 4}
        self.assertFalse(scanner.source_due(a, rec, z("2026-10-03T14:00:00Z")))
        self.assertTrue(scanner.source_due(a, rec, z("2026-10-03T19:00:30Z")))

    def test_failing_source_backs_off_alone(self):
        good, bad = [], []

        def boom():
            bad.append(1)
            raise RuntimeError("akamai")
        broken = type("Broken", (), {"SOURCE": "bestbuy", "fetch": staticmethod(boom)})
        self.fake.fetch = staticmethod(lambda: good.append(1) or list(self.got))
        with mock.patch.object(scanner, "ADAPTERS", [self.fake, broken]):
            self.assertEqual(scanner.main(), 1)
            self.assertTrue(any("BACKING OFF" in line for batch in self.posted for line in batch))
            self.later(1)  # bandai is due again, bestbuy is backing off (2 min)
            self.assertEqual(scanner.main(), 1)
            self.assertEqual((len(good), len(bad)), (2, 1))
            self.assertIn("akamai", self.health()["last_error"])
            self.later(2)
            scanner.main()
            self.assertEqual(len(bad), 2)
            self.assertEqual(scanner.load_schedule()["bestbuy"]["fails"], 2)
            broken.fetch = staticmethod(lambda: [dict(prod("S1"), source="bestbuy")])
            self.later(5)
            self.assertEqual(scanner.main(), 0)
            self.assertTrue(any("RECOVERED" in line for batch in self.posted for line in batch))

    def test_empty_poll_is_a_failure(self):
        self.assertEqual(scanner.main(), 0)
        self.got = []
        self.later()
        self.assertEqual(scanner.main(), 1)
        self.assertEqual(self.health()["consecutive_failures"], 1)
        self.assertIn("returned 0 products", self.health()["last_error"])

    def test_failed_alert_post_is_a_failure_and_retries(self):
        scanner.main()  # seeds silently
        self.got = [prod("A1"), prod("B2")]
        self.later()

        def boom(env, lines):
            raise RuntimeError("discord 500")
        with mock.patch.object(scanner, "post", boom):
            self.assertEqual(scanner.main(), 1)
        self.assertEqual(self.health()["consecutive_failures"], 1)
        self.assertNotIn("pbandai:B2", scanner.load_state())  # unsaved, so the alert fires again
        self.later()
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

    def test_flip_only_on_change(self):
        self.assertFalse(self.site.record(True, {"pbandai": {"count": 1}}))
        self.assertTrue(self.site.record(False, error="HTTPError 403"))
        self.assertFalse(self.site.record(False, error="HTTPError 403"))
        self.assertEqual(self.site.failures(), 2)
        self.assertTrue(self.site.record(True, {}))

    def test_source_backoff_widens(self):
        a = type("A", (), {})
        self.assertEqual(scanner.source_wait(a, {}), 60)
        self.assertEqual(scanner.source_wait(a, {"fails": 2}), 300)
        self.assertEqual(scanner.source_wait(a, {"fails": 9}), 900)
        slow = type("S", (), {"INTERVAL_MINS": 2})
        self.assertEqual(scanner.source_wait(slow, {}), 120)
        self.assertEqual(scanner.source_wait(slow, {"fails": 1}), 120)

    def test_alert_log_newest_first_and_capped(self):
        for i in range(60):
            self.site.record(True, {}, alerts=[("NEW", prod(f"A{i}"))])
        import json
        log = json.load(open(os.path.join(self.dir, "alerts.json")))
        self.assertEqual(len(log), 50)
        self.assertEqual(log[0]["id"], "A59")

    def test_publish_without_remote_is_a_noop(self):
        self.assertFalse(self.site.publish({}))


def bb(sku="6685240", name="Bandai - One Piece Card Game Booster Pack OP-17 (1 Pack per Order)",
       price=4.99, seller="1P", pickup=True):
    return {"sku": sku, "name": name, "url": None, "price": price, "seller": seller, "pickup": pickup, "button": None}


class BestBuy(unittest.TestCase):
    def one(self, **kw):
        return bestbuy.parse({"sa_status": 200, "errors": [], "items": [bb(**kw)]})

    def test_first_party_pickup_under_msrp_is_buyable(self):
        p, = self.one()
        self.assertTrue(p["on_sale"] and p["in_stock"])
        self.assertEqual(p["url"], "https://www.bestbuy.com/site/6685240.p?skuId=6685240")

    def test_marketplace_listing_never_alerts(self):
        # ST-31 measured 2026-10-02: 3P at $44.99, JSON-LD still claims seller Best Buy
        p, = self.one(sku="12940921", name="Bandai - One Piece Card Game Starter Deck 31 (ST-31)",
                      price=44.99, seller="3P")
        self.assertFalse(p["on_sale"] or p["in_stock"])
        old = scanner.diff({}, [dict(p, in_stock=False)], NOW)[1]
        self.assertEqual(scanner.diff(old, [p], NOW)[0], [])

    def test_unknown_seller_rides_on_store_pickup(self):
        p, = self.one(seller=None)
        self.assertTrue(p["in_stock"])
        p, = self.one(seller=None, pickup=False)
        self.assertFalse(p["in_stock"])

    def test_over_msrp_is_not_in_stock(self):
        p, = self.one(name="Bandai - One Piece Card Game Sleeved Booster Pack OP-17 (12 Cards)", price=29.98)
        self.assertTrue(p["on_sale"])
        self.assertFalse(p["in_stock"])

    def test_pickup_without_a_price_is_not_in_stock(self):
        p, = self.one(price=None)
        self.assertFalse(p["in_stock"])

    def test_ceilings(self):
        self.assertEqual(bestbuy.ceiling("One Piece Starter Deck 31: RED (ST-31)"), 34.99)
        self.assertEqual(bestbuy.ceiling("One Piece Trading Card Game: Yamato Starter Deck (ST 09)"), 34.99)
        self.assertEqual(bestbuy.ceiling("Royal Lineage Japanese Booster Pack OP-10 | Box of 24 Packs"), 119.99)
        self.assertIsNone(bestbuy.ceiling("Illustration Box Vol. 8 (IB-08) - 4 Packs, Promos"))
        self.assertEqual(bestbuy.ceiling("Booster Pack OP-17 (1 Pack per Order)"), 5.99)
        self.assertEqual(bestbuy.ceiling("Sleeved Booster Pack OP-17 (12 Cards)"), 5.99)
        self.assertIsNone(bestbuy.ceiling("One Piece Card Game: Double Pack Set Vol. 9 (DP-09)"))
        self.assertIsNone(bestbuy.ceiling("OP-17 Japanese Booster Pack Lot - 3 Packs - 18 Cards"))

    def test_relative_urls_become_absolute(self):
        p, = bestbuy.parse({"sa_status": 200, "errors": [], "items": [dict(bb(), url="/product/x/J3GW")]})
        self.assertEqual(p["url"], "https://www.bestbuy.com/product/x/J3GW")

    def test_restock_flip_alerts(self):
        p, = self.one()
        old = scanner.diff({}, [dict(p, in_stock=False)], NOW)[1]
        self.assertEqual([k for k, _ in scanner.diff(old, [p], NOW)[0]], ["RESTOCK"])

    def test_other_games_dropped_unnamed_kept(self):
        got = bestbuy.parse({"sa_status": 200, "errors": [], "items": [
            bb(sku="1", name="Pokemon - Trading Card Game: Tech Sticker Collection"),
            bb(sku="2", name=None), bb(sku="3")]})
        self.assertEqual([p["id"] for p in got], ["2", "3"])
        self.assertIsNone(got[0]["name"])

    def test_skeleton_tile_keeps_last_known_name(self):
        p, = self.one(pickup=False)
        old = scanner.diff({}, [p], NOW)[1]
        alerts, state = scanner.diff(old, [dict(p, name=None, in_stock=True)], NOW)
        self.assertEqual(state["bestbuy:6685240"]["name"], p["name"])
        self.assertEqual(alerts[0][1]["name"], p["name"])
        self.assertEqual(scanner.diff({}, [dict(p, name=None)], NOW)[1]["bestbuy:6685240"]["name"],
                         "bestbuy SKU 6685240")

    def test_alert_shows_seller_as_read(self):
        p, = self.one(seller=None)
        self.assertIn("seller none", scanner.format_alert("RESTOCK", p))
        self.assertNotIn("seller", scanner.format_alert("RESTOCK", prod()))

    def test_page_errors_and_bad_availability_raise(self):
        with self.assertRaises(RuntimeError):
            bestbuy.parse({"sa_status": 200, "errors": ["page 3: http 403"], "items": [bb()]})
        with self.assertRaises(RuntimeError):
            bestbuy.parse({"sa_status": 403, "errors": [], "items": [bb()]})

    def test_key_read_without_exporting(self):
        import tempfile
        f = tempfile.NamedTemporaryFile("w", delete=False)
        f.write("# comment\nexport FIRECRAWL_API_KEY='fc-test'\n")
        f.close()
        with mock.patch.object(firecrawl, "KEY_FILE", f.name):
            self.assertEqual(firecrawl.api_key(), "fc-test")


def tg(tcin="95042136", name="One Piece Card Game: Starter Deck Ex- Luffy &#38; Ace ST30", price=19.99,
       marketplace=False, store="1453", pickup="UNAVAILABLE", in_store="OUT_OF_STOCK"):
    return {"tcin": tcin, "name": name, "url": None, "price": price, "marketplace": marketplace,
            "store": store, "pickup": pickup, "in_store": in_store, "qty": 0}


LEGO = tg(tcin="95046363", name="LEGO ONE PIECE Dr. Hiriluk&#39;s Hideout 75641", in_store="IN_STOCK")


class Target(unittest.TestCase):
    def one(self, **kw):
        return target.parse({"status": 200, "errors": [], "items": [tg(**kw), LEGO]})

    def test_control_is_required_and_never_a_product(self):
        self.assertEqual([p["id"] for p in self.one()], ["95042136"])
        with self.assertRaises(RuntimeError):
            target.parse({"status": 200, "errors": [], "items": [tg()]})
        with self.assertRaises(RuntimeError):
            target.parse({"status": 200, "errors": [], "items": [tg(), dict(LEGO, store=None)]})

    def test_out_everywhere_is_out(self):
        p, = self.one()
        self.assertEqual(p["name"], "One Piece Card Game: Starter Deck Ex- Luffy & Ace ST30")
        self.assertTrue(p["on_sale"])
        self.assertFalse(p["in_stock"])

    def test_on_the_shelf_counts_even_without_pickup(self):
        p, = self.one(in_store="IN_STOCK")
        self.assertTrue(p["in_stock"])
        p, = self.one(pickup="LIMITED_STOCK")
        self.assertTrue(p["in_stock"])

    def test_stock_at_another_store_does_not_count(self):
        p, = self.one(store="3362", in_store="IN_STOCK")
        self.assertFalse(p["in_stock"])

    def test_marketplace_and_over_msrp_never_alert(self):
        p, = self.one(marketplace=True, in_store="IN_STOCK")
        self.assertFalse(p["on_sale"] or p["in_stock"])
        p, = self.one(price=44.99, in_store="IN_STOCK")
        self.assertFalse(p["in_stock"])
        p, = self.one(name="One Piece Trading Card Game GEAR5 ST 21 Starter Deck", price=34.99, in_store="IN_STOCK")
        self.assertTrue(p["in_stock"])

    def test_bad_status_raises(self):
        with self.assertRaises(RuntimeError):
            target.parse({"status": 435, "errors": [], "items": []})

    def test_script_error_reaches_the_log(self):
        err = "redsky: Error: http 503, non-JSON body: <!DOCTYPE html><html><head><title>Service Unavailable"
        with self.assertRaisesRegex(RuntimeError, "http 503, non-JSON body: <!DOCTYPE"):
            target.parse({"status": 503, "errors": [err], "items": []})

    def test_script_gets_the_watchlist(self):
        import json
        seen = {}
        with mock.patch.object(firecrawl, "run", lambda url, f, script_vars=None: seen.update(script_vars) or {}):
            target.scrape()
        self.assertEqual(sorted(json.loads(seen["__TCINS__"])), sorted(list(target.TCINS) + [target.CONTROL_TCIN]))
        self.assertIn("__TCINS__", open(os.path.join(os.path.dirname(target.__file__), "target.js")).read())


def wm(id="15840957168", name="Collectible One Piece Starter Deck 23: RED Shanks", price=None, avail="OUT_OF_STOCK", ftype="STORE", pickup=()):
    return {"id": id, "name": name, "price": price, "avail": avail, "ftype": ftype, "pickup": list(pickup)}


def wm_store(store, items=(), pinned=None, control=None):
    return {"store": store, "pinned": pinned or store, "items": list(items),
            "control": control or {"seller": "Walmart.com", "storeIds": [store], "pickup": "IN_STOCK"}}


class Walmart(unittest.TestCase):
    def raw(self, at1400=(), at2811=(), catalog=None, **kw):
        return {"errors": [], "catalog": [wm()] if catalog is None else catalog,
                "stores": [kw.get("s1400") or wm_store("1400", at1400), kw.get("s2811") or wm_store("2811", at2811)]}

    def test_catalog_item_out_at_both_stores(self):
        ps = walmart.parse(self.raw())
        self.assertEqual([p["id"] for p in ps], ["1400-15840957168", "2811-15840957168"])
        self.assertEqual(ps[0]["name"], "One Piece Starter Deck 23: RED Shanks · Walmart Epps Bridge")
        self.assertTrue(all(p["on_sale"] and not p["in_stock"] for p in ps))

    def test_pickup_at_the_pinned_store_is_in_stock(self):
        here = wm(price=12.97, avail="IN_STOCK", ftype="FC", pickup=["1400"])
        a, b = walmart.parse(self.raw(at1400=[here]))
        self.assertTrue(a["in_stock"])
        self.assertEqual(a["price"], 12.97)
        self.assertFalse(b["in_stock"])

    def test_in_store_only_counts_while_pinned(self):
        a, _ = walmart.parse(self.raw(at1400=[wm(price=12.97, avail="IN_STOCK")]))
        self.assertTrue(a["in_stock"])

    def test_pickup_at_another_store_does_not_count(self):
        a, _ = walmart.parse(self.raw(at1400=[wm(price=12.97, avail="IN_STOCK", ftype="FC", pickup=["3235"])]))
        self.assertFalse(a["in_stock"])

    def test_over_msrp_never_alerts(self):
        a, _ = walmart.parse(self.raw(at1400=[wm(price=45.0, avail="IN_STOCK")]))
        self.assertFalse(a["in_stock"])

    def test_missing_price_still_alerts(self):
        # Live 2026-10-03: every in-stock STORE item at both pinned stores had no price.
        a, b = walmart.parse(self.raw(at1400=[wm(avail="IN_STOCK")]))
        self.assertTrue(a["in_stock"])
        self.assertIsNone(a["price"])
        self.assertFalse(b["in_stock"])

    def test_new_item_found_only_when_pinned_is_kept(self):
        ps = walmart.parse(self.raw(at2811=[wm(id="999", name="One Piece Card Game Booster Pack OP-17", price=4.97, avail="IN_STOCK")]))
        self.assertEqual(sorted(p["id"] for p in ps), ["1400-15840957168", "2811-15840957168", "2811-999"])
        self.assertTrue([p for p in ps if p["id"] == "2811-999"][0]["in_stock"])

    def test_other_games_are_not_products(self):
        tacta = wm(id="17708161715", name="TACTA 2nd Edition Card Game", price=7.97, avail="IN_STOCK", pickup=["1400"])
        ps = walmart.parse(self.raw(at1400=[tacta], catalog=[wm(), tacta]))
        self.assertNotIn("1400-17708161715", [p["id"] for p in ps])

    def test_control_and_pin_are_required(self):
        with self.assertRaises(RuntimeError):
            walmart.parse(self.raw(s2811=wm_store("2811", pinned="3235")))
        with self.assertRaises(RuntimeError):
            walmart.parse(self.raw(s1400=wm_store("1400", control={"storeIds": ["1400"], "pickup": "NOT_AVAILABLE"})))
        with self.assertRaises(RuntimeError):
            walmart.parse(self.raw(s1400=wm_store("1400", control={"storeIds": ["3235"], "pickup": "IN_STOCK"})))
        with self.assertRaises(RuntimeError):
            walmart.parse({"errors": [], "catalog": [wm()], "stores": [wm_store("1400")]})

    def test_page_errors_and_empty_discovery_raise(self):
        with self.assertRaises(RuntimeError):
            walmart.parse(dict(self.raw(), errors=["chooser: no Save button"]))
        with self.assertRaises(RuntimeError):
            walmart.parse(self.raw(catalog=[]))

    def test_script_steps_become_actions(self):
        with open(os.path.join(os.path.dirname(walmart.__file__), "walmart.js")) as f:
            acts = firecrawl.actions(f.read())
        runs = [a for a in acts if a["type"] == "executeJavascript"]
        self.assertEqual(len(acts), 2 * len(runs))
        self.assertEqual(len(runs), 8)
        self.assertNotIn("@step", "".join(a["script"] for a in runs))
        self.assertEqual(firecrawl.actions("x()"), [{"type": "wait", "milliseconds": 1500}, {"type": "executeJavascript", "script": "x()"}])


if __name__ == "__main__":
    unittest.main()
