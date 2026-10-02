// Runs inside a Firecrawl stealth page on bestbuy.com (same origin, the page's
// cookies). Best Buy blocks this VM directly, so every call goes from here.
// Returns one JSON string; adapters/bestbuy.py normalizes it.
//
// Measured 2026-10-02 (docs/retailer-recon.md):
//  - Search results are li.product-list-item[data-product-id]; pages 2+ come
//    from ?cp=N and arrive as skeleton tiles, so names come from the Apollo
//    SSR payloads, which carry JS `undefined` and need it nulled to parse.
//  - Product.seller.classification is "1P" (sold by Best Buy) or "3P"
//    (marketplace). JSON-LD says seller "Best Buy" and InStock even for a 3P
//    listing that is sold out, so it is trusted for name and price only.
(async () => {
  const STORE = "511", ZIP = "30606", MAX_PAGES = 6, PDP_BATCH = 6;
  const out = { pages: 0, items: [], errors: [], picked: [] };
  const apollo = {};
  const dom = {};
  const order = [];

  const walk = (o, d) => {
    if (!o || typeof o !== "object" || d > 60) return;
    if (o.__typename === "Product" && o.skuId) apollo[o.skuId] = Object.assign(apollo[o.skuId] || {}, o);
    for (const v of Object.values(o)) walk(v, d + 1);
  };
  const eatApollo = doc => {
    for (const s of doc.querySelectorAll("script")) {
      const t = s.textContent;
      if (!t.includes("ApolloSSRDataTransport")) continue;
      try {
        walk(JSON.parse(t.slice(t.indexOf(".push(") + 6, t.lastIndexOf(")")).replace(/:undefined([,}\]])/g, ":null$1")), 0);
      } catch (e) { /* a payload that will not parse only costs names */ }
    }
  };
  const money = s => { const m = /\$([\d,]+\.\d{2})/.exec(s || ""); return m ? parseFloat(m[1].replace(/,/g, "")) : null; };
  const readTiles = doc => {
    let added = 0;
    for (const li of doc.querySelectorAll("li.product-list-item[data-product-id]")) {
      const sku = li.getAttribute("data-product-id");
      if (dom[sku]) continue;
      const a = li.querySelector("a.product-list-item-link");
      const t = li.querySelector("h3.product-title");
      const p = li.querySelector('[data-testid="price-block-customer-price"]');
      dom[sku] = {
        name: (t ? t.textContent : "").replace(/\s+/g, " ").trim(),
        url: a ? a.getAttribute("href") : null,
        price: money(p ? p.textContent : ""),
      };
      order.push(sku);
      added++;
    }
    return added;
  };

  eatApollo(document);
  readTiles(document);
  out.pages = 1;
  const base = new URL(location.href);
  for (let cp = 2; cp <= MAX_PAGES; cp++) {
    base.searchParams.set("cp", String(cp));
    try {
      const r = await fetch(base.toString(), { credentials: "include" });
      if (!r.ok) { out.errors.push("page " + cp + ": http " + r.status); break; }
      const doc = new DOMParser().parseFromString(await r.text(), "text/html");
      eatApollo(doc);
      out.pages = cp;
      if (!readTiles(doc)) break;
    } catch (e) { out.errors.push("page " + cp + ": " + e); break; }
  }

  const avail = {};
  if (order.length) {
    const body = { locationId: STORE, zipCode: ZIP, showOnShelf: true, lookupInStoreQuantity: true, xboxAllAccess: false,
      consolidated: true, showOnlyOnShelf: false, showInStore: true, pickupTypes: ["UPS_ACCESS_POINT", "FEDEX_HAL"],
      onlyBestBuyLocations: true,
      items: order.map((s, i) => ({ sku: s, condition: null, quantity: 1, itemSeqNumber: String(i + 1), reservationToken: null,
        selectedServices: [], requiredAccessories: [], isTradeIn: false, isLeased: false })) };
    try {
      const r = await fetch("/productfulfillment/c/api/2.0/storeAvailability", { method: "POST", credentials: "include",
        headers: { "content-type": "application/json", accept: "application/json" }, body: JSON.stringify(body) });
      out.sa_status = r.status;
      const j = await r.json();
      for (const b of j.buttonState || []) (avail[b.skuId] = avail[b.skuId] || {}).button = b.buttonState;
      for (const i of (j.ispu || {}).items || []) {
        const a = (avail[i.sku] = avail[i.sku] || {});
        a.eligible = !!i.pickupEligible;
        a.locs = (i.locations || []).map(l => String(l.locationId));
        // Never seen populated yet (everything was sold out at recon), so keep
        // the raw shape of the first one for the log.
        if (a.eligible && !out.loc_sample) out.loc_sample = JSON.stringify(i).slice(0, 800);
      }
    } catch (e) { out.errors.push("storeAvailability: " + e); }
  }

  const pickup = s => { const a = avail[s] || {}; return !!a.eligible && (!a.locs.length || a.locs.includes(STORE)); };
  const pdp = {};
  // Every pickup-eligible SKU gets a price: a missing price blocks its alert.
  const readPdp = async s => {
    out.picked.push(s);
    try {
      const r = await fetch("/site/" + s + ".p?skuId=" + s, { credentials: "include" });
      if (!r.ok) { out.errors.push("pdp " + s + ": http " + r.status); return; }
      const doc = new DOMParser().parseFromString(await r.text(), "text/html");
      eatApollo(doc);
      for (const x of doc.querySelectorAll('script[type="application/ld+json"]')) {
        try {
          const j = JSON.parse(x.textContent);
          for (const it of Array.isArray(j) ? j : [j]) {
            if (it["@type"] !== "Product" || String(it.sku) !== s) continue;
            const offer = [].concat(it.offers || [])[0] || {};
            pdp[s] = { name: it.name, price: typeof offer.price === "number" ? offer.price : parseFloat(offer.price) };
          }
        } catch (e) { /* not this block */ }
      }
    } catch (e) { out.errors.push("pdp " + s + ": " + e); }
  };
  const want = order.filter(pickup);
  for (let i = 0; i < want.length; i += PDP_BATCH) await Promise.all(want.slice(i, i + PDP_BATCH).map(readPdp));

  for (const s of order) {
    const ap = apollo[s] || {};
    const a = avail[s] || {};
    out.items.push({
      sku: s,
      name: (pdp[s] || {}).name || (ap.name || {}).short || dom[s].name || null,
      url: (ap.url || {}).pdp || dom[s].url || null,
      price: pdp[s] && !isNaN(pdp[s].price) ? pdp[s].price : dom[s].price,
      seller: (ap.seller || {}).classification || null,
      pickup: pickup(s),
      button: a.button || null,
    });
  }
  return JSON.stringify(out);
})()
