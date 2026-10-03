// Runs inside a Firecrawl stealth page on walmart.com, as separate steps (each
// "// @step" line starts one executeJavascript action after a wait, see
// firecrawl.run). Returns one JSON string from the last step; walmart.py
// normalizes it.
//
// Measured 2026-10-03 (docs/retailer-recon.md):
//  - Walmart picks the store from the visitor's location, and URL params and
//    cookie overwrites do not move it. The site's own store chooser does: it
//    sends setPickup {storeId, accessPointId}. So the steps drive the chooser
//    to Epps Bridge (1400), capture that request, and replay it with 2811's
//    accessPointId from the chooser's own nearByNodes answer.
//  - Product pages show the marketplace buy box, so first party is read from
//    search, where Walmart-sold items carry sellerName "Walmart.com".

// @step wait=1500
(async () => {
  const WALMART = "Walmart.com";
  const of = window.fetch;
  window.fetch = async function (u, o) {
    const url = typeof u === "string" ? u : (u && u.url) || "";
    const r = await of.apply(this, arguments);
    if (/setPickup/.test(url)) window.__setPickup = { url, headers: o && o.headers, body: o && o.body };
    if (/nearByNodes/.test(url)) { try { window.__nodes = await r.clone().text(); } catch (e) { /* replay reports it */ } }
    return r;
  };
  window.__vis = el => el && el.offsetParent !== null;
  window.__dlg = () => { const ds = [...document.querySelectorAll("[role=dialog],[aria-modal=true]")].filter(window.__vis); return ds.length ? ds[ds.length - 1] : document.body; };
  window.__next = async url => {
    const t = await (await of(url, { credentials: "include" })).text();
    const m = t.match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/);
    if (!m) throw new Error("no __NEXT_DATA__ at " + url);
    return JSON.parse(m[1]).props.pageProps.initialData || {};
  };
  window.__price = it => {
    const p = it.priceInfo || {};
    if (p.currentPrice && typeof p.currentPrice.price === "number") return p.currentPrice.price;
    const m = /\$([\d,]+\.\d{2})/.exec(p.linePrice || p.linePriceDisplay || "");
    return m ? parseFloat(m[1].replace(/,/g, "")) : null;
  };
  window.__firstParty = async url => {
    const sr = (await window.__next(url)).searchResult || {};
    const items = [];
    for (const st of sr.itemStacks || []) for (const it of st.items || []) {
      if (!it || !it.usItemId || it.sellerName !== WALMART) continue;
      items.push({ id: String(it.usItemId), name: it.name || null, price: window.__price(it),
        avail: (it.availabilityStatusV2 || {}).value || it.availabilityStatus || null, ftype: it.fulfillmentType || null,
        pickup: (it.fulfillmentSummary || []).filter(f => f.fulfillment === "PICKUP").map(f => String(f.storeId)) });
    }
    return items;
  };
  window.__out = { catalog: [], stores: [], errors: [] };
  // Discovery, before any store is pinned: every Walmart-sold item for the query,
  // whatever its stock. Pinned searches list only what the store has.
  try { window.__out.catalog = await window.__firstParty("/search?q=one+piece+card+game&facet=retailer_type%3AWalmart"); }
  catch (e) { window.__out.errors.push("catalog: " + e); }
  return "ok";
})()

// @step wait=1000
(() => {
  const el = [...document.querySelectorAll("button,a,[role=button]")].find(e => window.__vis(e) && /pickup or delivery/i.test((e.getAttribute("aria-label") || "") + " " + e.textContent));
  if (el) el.click(); else window.__out.errors.push("chooser: no 'Pickup or delivery?' button");
  return "open";
})()

// @step wait=2500
(() => {
  const tab = [...window.__dlg().querySelectorAll("button,[role=tab],a,label")].find(e => window.__vis(e) && /^\s*pickup\s*$/i.test(e.textContent));
  if (tab) tab.click(); else window.__out.errors.push("chooser: no Pickup tab");
  return "pickup";
})()

// @step wait=1500
(() => {
  const row = [...window.__dlg().querySelectorAll("button,a,[role=button]")].find(e => window.__vis(e) && /supercenter|neighborhood market/i.test((e.getAttribute("aria-label") || "") + " " + e.textContent));
  if (row) row.click(); else window.__out.errors.push("chooser: no current-store row");
  return "store";
})()

// @step wait=3000
(() => {
  const d = window.__dlg();
  const inp = [...d.querySelectorAll("input")].filter(window.__vis).find(i => /zip|city|address|location|store/i.test((i.getAttribute("aria-label") || "") + (i.placeholder || "") + (i.name || "") + (i.id || "")));
  if (!inp) { window.__out.errors.push("chooser: no zip input"); return "zip"; }
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  inp.focus(); set.call(inp, "30606");
  inp.dispatchEvent(new Event("input", { bubbles: true })); inp.dispatchEvent(new Event("change", { bubbles: true }));
  inp.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true }));
  const go = [...d.querySelectorAll("button")].find(b => window.__vis(b) && /^\s*(find|search|apply|update|submit|go)\s*$/i.test(b.textContent));
  if (go) go.click();
  return "zip";
})()

// @step wait=3000
(() => {
  const row = [...window.__dlg().querySelectorAll("button,label,[role=radio],input[type=radio],li,a")].filter(window.__vis).find(e => /epps bridge/i.test(e.textContent || e.getAttribute("aria-label") || ""));
  if (row) row.click(); else window.__out.errors.push("chooser: Epps Bridge not listed for 30606");
  return "pick";
})()

// @step wait=1000
(() => {
  const b = [...window.__dlg().querySelectorAll("button")].filter(window.__vis).find(e => /^\s*(save|confirm|set as my store|select store|done|apply|continue|update)\s*$/i.test(e.textContent));
  if (b) b.click(); else window.__out.errors.push("chooser: no Save button");
  return "save";
})()

// @step wait=3000
(async () => {
  const out = window.__out;
  const CONTROL = "17708161715"; // TACTA 2nd Edition, sold by Walmart.com; PICKUP IN_STOCK at 1400 and 2811 on 2026-10-03
  const read = async store => {
    const r = { store, pinned: (document.cookie.match(/assortmentStoreId=(\d+)/) || [])[1] || null, items: [] };
    const seen = new Set();
    for (const u of ["/search?q=one+piece+card+game&facet=retailer_type%3AWalmart", "/search?q=one+piece+card+game"]) {
      for (const it of await window.__firstParty(u)) if (!seen.has(it.id)) { seen.add(it.id); r.items.push(it); }
    }
    const p = ((await window.__next("/ip/" + CONTROL)).data || {}).product || {};
    const pick = (p.fulfillmentOptions || []).find(o => o.type === "PICKUP") || {};
    r.control = { seller: p.sellerName || null, storeIds: ((p.location || {}).storeIds || []).map(String), pickup: pick.availabilityStatus || null };
    return r;
  };
  try {
    if (!window.__setPickup) throw new Error("the chooser never sent setPickup");
    out.stores.push(await read("1400"));
    let ap = null;
    const walk = (o, here) => {
      if (!o || typeof o !== "object" || ap) return;
      const h = here || String(o.id) === "2811";
      for (const [k, v] of Object.entries(o)) { if (h && k === "accessPointId") { ap = v; return; } walk(v, h); }
    };
    walk(JSON.parse(window.__nodes || "{}"), false);
    if (!ap) throw new Error("2811 has no accessPointId in nearByNodes");
    const sp = window.__setPickup;
    const body = JSON.parse(sp.body);
    body.variables.input.storeId = 2811;
    body.variables.input.accessPointId = ap;
    const r = await fetch(sp.url, { method: "POST", credentials: "include", headers: sp.headers, body: JSON.stringify(body) });
    if (r.status !== 200) throw new Error("setPickup replay for 2811: http " + r.status);
    out.stores.push(await read("2811"));
  } catch (e) { out.errors.push("measure: " + e); }
  return JSON.stringify(out);
})()
