# Retailer recon (2026-10-02)

What each store's stock data looks like from this box, measured on 2026-10-02 between 23:13Z and 23:40Z.
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

## Target: LIVE as a probe (adapters/target.py, 08:00 and 15:00 America/New_York)

**What the twice-daily polls test:** the hypothesis that Target's trading-card shelf is vendor-stocked and never reaches
Target's inventory system. If it holds, every starter here reads OUT forever and the scanner never pings for Target. The first
IN reading on a card item kills it, and that reading alerts like any other source. Mike's call (upper 84683): it's a probe, not a watcher.
- Watchlist: the 9 starter tcins below, because search can't discover first-party OP products. Add new sets to `TCINS` by hand.
- In stock = first party (`item.fulfillment.is_marketplace` not true), a store-1453 row with `order_pickup` or `in_store_only`
  at IN_STOCK or LIMITED_STOCK, and price under the ceiling. In-store-only counts, because the question is whether it's on the shelf.
- Instrument control: LEGO One Piece 95046363 goes in every query and must come back with a 1453 row, or the poll fails.
  A query that silently dropped the store would read OUT forever, which looks exactly like the hypothesis holding.
- Re-read 2026-10-02 by the adapter's first live run: all 9 OUT at 1453, LEGO IN_STOCK qty 10. Target's first-party prices: $11.99 to $16.99 for regular
  starters, $19.99 for ST30 EX, $34.99 for ST-13 (Ultimate Deck) and ST-21 (GEAR5). Hence the $34.99 starter ceiling in `adapters/msrp.py`.

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

## Best Buy: LIVE in the scanner (adapters/bestbuy.py, 08:00 and 15:00 America/New_York)

- Store: **511 = 1791 Oconee Connector** (stores.bestbuy.com/ga/athens/1791-oconee-connector-511.html).
- One Firecrawl stealth scrape of `/site/searchpage.jsp?st=one+piece+card+game` per poll runs `adapters/bestbuy.js` in-page.
  A quiet poll takes about 20s and 1 credit. Cadence is Mike's call (2026-10-02): twice a day, 2 credits/day. 08:00 ET is the
  overnight-restock-before-open check. For reference, the plan is 5,000 credits/month: every 10 min (144/day) would fit,
  every 2 min (720/day) would use it up in under a week.
- Discovery: `li.product-list-item[data-product-id]`, with pages 2+ fetched in-page from `?cp=N` (5 pages, 68 to 70 SKUs, which matches
  the page's own listCount of 70). Pages 2+ come back as skeleton tiles with no title or price. Names come from the Apollo SSR payloads
  (`window[Symbol.for("ApolloSSRDataTransport")]` pushes, which hold JS `undefined` and have to be nulled before `JSON.parse`); about 28 of 68 carry one.
  The search also returns Pokemon and sports cards, so named non-One-Piece SKUs are dropped and unnamed ones kept.
- Stock: `POST /productfulfillment/c/api/2.0/storeAvailability` with every SKU in one call, body
  `{locationId:"511", zipCode:"30606", showOnShelf:true, lookupInStoreQuantity:true, xboxAllAccess:false, consolidated:true, showOnlyOnShelf:false,
  showInStore:true, pickupTypes:["UPS_ACCESS_POINT","FEDEX_HAL"], onlyBestBuyLocations:true, items:[{sku, condition:null, quantity:1, itemSeqNumber,
  reservationToken:null, selectedServices:[], requiredAccessories:[], isTradeIn:false, isLeased:false}]}`.
  Returns `buttonState[].{skuId, buttonState}`, `shipping.items[].shippingEligible`, `ispu.items[].{sku, pickupEligible, inStoreOnly, locations}`.
  All 68 read SOLD_OUT with no pickup at 23:37Z, so the shape of a populated `locations` entry is still unseen; the adapter logs the first one it gets.
- Price: product page `/site/<sku>.p?skuId=<sku>` JSON-LD `offers.price` (4.99 on control 6685240, matching the tile). It's fetched only for SKUs
  that are pickup-eligible at 511, so a quiet poll stays one scrape. `/api/3.0/priceBlocks` is dead: it returns "product not found" for current SKUs.
- Seller: Apollo `Product.seller.classification` reads `1P` or `3P` where it's present: 1 x 1P, 15 x 3P and 52 missing out of 68, and the product page
  doesn't fill the gap (12599151 is null there too). JSON-LD is useless for this: it says seller "Best Buy" and InStock on ST-31 (12940921),
  which is a sold-out 3P listing at $44.99.
- **Gate, and which part is inferred:** an explicit `3P` never alerts. A missing seller alerts only with pickup at 511, on the rule that
  marketplace items can't be picked up in a Best Buy store. **That rule is INFERRED, not measured.** Every alert prints the seller as read
  (`seller 1P / 3P / none`), so the first alert on a `none` SKU is the rule's test. Price must also be at or under a ceiling
  (pack $5.99, starter deck $14.99, 24-pack box $119.99; the ceilings are ours, not Bandai's published MSRPs). A price we can't read means no alert.
- Controls: positive = 6685240 (OP-17 single pack, $4.99, 1P), SOLD_OUT tonight; confirmed only when the scanner catches its next restock.
  Negative = the 15 3P listings, e.g. ST-31 12940921 and the C3747 Japanese imports, which must never alert.

## Walmart: recon done, store pinning SOLVED, NOT built (2026-10-03, ~15 Firecrawl credits, zero requests from this VM)

- Stores (nearByNodes at zip 30606): **1400 = Athens Epps Bridge Parkway Supercenter** (1911 Epps Bridge Pkwy, 30606),
  **2811 = Athens Lexington Rd Supercenter** (4375 Lexington Rd, 30605). Mike said "Old Lexington Rd"; 2811 is the only
  Lexington Rd store, so that mapping is INFERRED. 3130 Atlanta Hwy is a Neighborhood Market, not in scope.
- Access: a Firecrawl stealth page on walmart.com loads; same-origin `fetch` of `/search` and `/ip/<id>` returns `__NEXT_DATA__`.
- First party = `sellerName` "Walmart.com", `sellerId` F55CDC31AB754BB68FE0B39041159D63. Plain search for "one piece card game"
  is marketplace scalpers ($10 to $360, IN_STOCK for shipping). On product pages the buy box is the marketplace seller, so
  the product page's primary offer must never be read as Walmart's.
- **Store pinning.** Walmart picks the store server-side from the visitor's location (the proxy landed in MD, FL, NC and GA
  tonight). URL params (`stores=`, `/store/<id>/search`) and cookie overwrites do NOT move it: the server re-sets
  `assortmentStoreId`. What works is the site's own chooser:
  - `GET /orchestra/home/graphql/nearByNodes/<hash>?variables={input:{postalCode:"30606",accessTypes:[PICKUP_INSTORE,PICKUP_CURBSIDE],...}}`
    lists stores with `capabilities[].accessPointId` (2811 PICKUP_INSTORE = 863aa04a-5312-4520-a9fa-9eb334b0890d).
  - `POST /orchestra/cartxo/graphql/setPickup/<hash>` with `{input:{accessPointId, cartId:"000...0", postalCode, storeId}}`
    pins the store (`assortmentStoreId` and `isoLoc=US_GA` follow). Measured: driving the UI to Epps Bridge fired it with
    storeId 1400. Replaying the captured request (same headers) with 2811's accessPointId returned 200 and moved the session to 2811.
  - The hashes are persisted-query ids taken from the live bundle and will rotate, so the adapter should drive the UI once
    per poll and replay `setPickup` for the second store, not hardcode the hash.
- **Readings at the pinned stores (00:1xZ):**
  - Instrument control: TACTA 2nd Edition 17708161715, sold by Walmart.com, $7.97, reads PICKUP:IN_STOCK at 1400 and at 2811.
    Store-scoped pickup is real data, not a national default.
  - One Piece: ST-23 15840957168, Ace & Newgate 16810169805 and Double Pack 16417069740 show only marketplace offers on their
    product pages, with PICKUP:NOT_AVAILABLE at the pinned store and no Walmart.com offer in the payload. The Walmart-only search
    returned zero One Piece items at both stores, where unpinned it had listed 20 One Piece items as Walmart.com, STORE-only, OUT_OF_STOCK.
- **What is NOT measured:** whether a Walmart-sold One Piece item that IS on the shelf shows up in the pinned search or on its
  product page. Every reading tonight is negative. Positive control: none yet (Mike's "tons left", dm 84458, was Target).
- Proposed adapter, one scrape per poll for both stores: drive the chooser to 1400, read the Walmart-only and plain searches
  pinned, replay setPickup for 2811, read again. Alert only on sellerName Walmart.com, PICKUP IN_STOCK at the pinned store,
  One Piece by name, under the MSRP ceiling. Fail the poll if TACTA (or another Walmart-sold control) doesn't read IN at each store.

## Not started

Barnes & Noble (Atlanta Hwy).
