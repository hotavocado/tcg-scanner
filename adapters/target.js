// Runs inside a Firecrawl stealth page on target.com. Target's HUMAN bot wall
// answers this VM with HTTP 435, so redsky is called from here with the page's
// cookies and visitorId. Returns one JSON string; adapters/target.py
// normalizes it. TCINS is replaced by target.py before the script is sent.
(async () => {
  const KEY = "9f36aeafbe60771e321a7cc95a78140772ab3e96"; // public, embedded in every Target page
  const STORE = "1453"; // Athens, 3065 Atlanta Hwy
  const TCINS = __TCINS__;
  const out = { items: [], errors: [] };
  const visitor = (document.cookie.match(/visitorId=([^;]+)/) || [])[1] || "";
  const p = { key: KEY, tcins: TCINS.join(","), store_id: STORE, zip: "30606", state: "GA", latitude: "33.95",
    longitude: "-83.42", required_store_id: STORE, has_required_store_id: "true", scheduled_delivery_store_id: STORE,
    channel: "WEB", visitor_id: visitor };
  try {
    const r = await fetch("https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1?" +
      new URLSearchParams(p), { credentials: "include" });
    out.status = r.status;
    const body = await r.text();
    let j;
    try { j = JSON.parse(body); } catch (e) {
      throw new Error(`http ${r.status}, non-JSON body: ${body.slice(0, 120).replace(/\s+/g, " ")}`);
    }
    for (const s of ((j.data || {}).product_summaries) || []) {
      const item = s.item || {};
      const f = s.fulfillment || {};
      const so = (f.store_options || []).find(o => String(o.location_id) === STORE) || {};
      out.items.push({
        tcin: String(s.tcin),
        name: (item.product_description || {}).title || null,
        url: (item.enrichment || {}).buy_url || null,
        price: (s.price || {}).current_retail ?? null,
        marketplace: (item.fulfillment || {}).is_marketplace === true,
        store: so.location_id ? String(so.location_id) : null,
        pickup: (so.order_pickup || {}).availability_status || null,
        in_store: (so.in_store_only || {}).availability_status || null,
        qty: so.location_available_to_promise_quantity ?? null,
      });
    }
  } catch (e) { out.errors.push("redsky: " + e); }
  return JSON.stringify(out);
})()
