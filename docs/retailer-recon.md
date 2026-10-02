# Retailer recon (2026-10-02)

What each store's stock data looks like from this box, measured on 2026-10-02 between 23:13Z and 23:25Z.
Stores, in Mike's order: Target Atlanta Hwy, Best Buy Oconee Connector, Walmart Old Lexington Rd,
Walmart Epps Bridge, Barnes & Noble Atlanta Hwy. All in Athens, GA.
Gates on every adapter: first-party stock only (sold by the retailer, pickup at the named store), price at or under MSRP.

## The access pattern that works: fetch from inside a Firecrawl stealth page

Target (HUMAN, formerly PerimeterX) and Best Buy (Akamai) both block this VM:
- Target redsky returns HTTP 435. That holds for bare curl and for local headless Chromium, which does get the _px cookies, and HUMAN then escalates to captcha.js.
- Best Buy resets the HTTP/2 stream in about 70ms (curl error 92).
- A Browserbase session on a residential proxy geolocated to Athens got Target's store lookups (200), but plp_search_v2 still returned 435.

What does work is a Firecrawl scrape with `proxy: "stealth"` plus an `executeJavascript` action that calls the retailer's
own JSON API from inside the loaded page (same origin, the page's cookies). Without LLM extraction that costs
1 credit per scrape, and one scrape can query every watched product at once. Poll cadence is a cost choice:
1/min = 1,440 credits/day per retailer.

## Target: PARKED (positive control failed)

- Public redsky key, embedded in every page: `9f36aeafbe60771e321a7cc95a78140772ab3e96`.
- Stores, from nearby_stores_v1 at zip 30606: **1453 = Athens, 3065 Atlanta Hwy**; 3362 = Athens Broad Street; 2493 = Winder.
  This VM geolocates to zip 20147 (Ashburn VA), so store ids must always be pinned and never left to IP location.
- Stock: `GET redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1?key=K&tcins=a,b&store_id=1453&zip=30606&state=GA&latitude=33.95&longitude=-83.42&required_store_id=1453&has_required_store_id=true&scheduled_delivery_store_id=1453&channel=WEB&visitor_id=<cookie visitorId>`
  returns per tcin `fulfillment.store_options[0]` with `order_pickup.availability_status`, `in_store_only.availability_status`,
  `location_available_to_promise_quantity`, plus `is_out_of_stock_in_all_store_locations`.
  It reports real counts for non-card items at 1453 (LEGO One Piece 95046363 qty 10, Kess board game 95028755 qty 6).
- Search: `plp_search_v2` takes keyword, store_ids, pricing_store_id, zip, default_purchasability_filter. It is no use for discovery:
  "one piece card game" returns 25 results with zero first-party OP card products (marketplace resellers carry `item.fulfillment.is_marketplace: true`),
  and "one piece starter" returns 178 with zero in the first 24. The category facet returns nothing. The server-rendered product page has no price
  (`isProductDetailServerSideRenderPriceEnabled=False`).
- **Control failed:** Luffy & Ace ST30 (tcin 95042136) reads OUT at 1453 with is_out_of_stock_in_all_store_locations=true, but Mike saw
  Luffy/Ace decks on that shelf. Every OP starter checked reads OUT at 1453: ST-09 89059000, ST-12 89998332, ST-13 90259219, ST-14 91312539,
  ST-21 94262795, ST-22 94723221, ST-33 95120832, ST-35 95120846.
  Hypothesis, NOT measured: Target's trading-card shelf is vendor-stocked and never reaches Target's inventory system. Waiting on Mike
  to confirm which deck he saw and when.

## Best Buy: in progress

- Store: **511 = 1791 Oconee Connector** (stores.bestbuy.com/ga/athens/1791-oconee-connector-511.html).
- Control SKU 6685240 (OP-17 single booster, "1 Pack per Order"), page price $4.99.
- Stock: `POST /productfulfillment/c/api/2.0/storeAvailability`, JSON body
  `{locationId:"511", zipCode:"30606", showOnShelf:true, lookupInStoreQuantity:true, consolidated:true, showInStore:true, onlyBestBuyLocations:true, pickupTypes:[...], items:[{sku, quantity:1, itemSeqNumber:"1", ...}]}`
  returns `buttonState[].buttonState` (SOLD_OUT at 23:22Z), `shipping.items[].shippingEligible`, `ispu.items[].{pickupEligible, inStoreOnly, locations}`.
  Multiple SKUs fit in one call. Store pickup implies sold by Best Buy, so pickup at 511 doubles as the first-party gate.
  `/button-state/api/v5/button-state` returns HTTP 400 with a "Page Not Found" HTML page, so it's not usable.
- Discovery: `/site/searchpage.jsp?st=one+piece+card+game` loads (listCount 70). The top tiles are OP-17 SKUs 1307787, 1307679, 6685240 and IB-07 1297155.
  OPEN: the in-page SKU-to-title extraction returned 0 titles, because the current markup is not `li.sku-item`.
  Next: return one tile's outerHTML (about 1.5KB) to learn the selectors, then build the adapter.
- The control can't be confirmed live tonight (sold out). It gets confirmed when the scanner catches the next restock flip.

## Not started

Walmart (Old Lexington Rd, Epps Bridge) and Barnes & Noble (Atlanta Hwy).
