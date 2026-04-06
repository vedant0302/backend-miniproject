"""
QuickCart Backend — both parsers confirmed working from debug output
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import requests, json, threading, uuid

app = Flask(__name__)
CORS(app)

state = {
    "zepto_cookie":  "_fbp=fb.1.1774019715558.384175330142702883; _gcl_au=1.1.1171135500.1774019717; _ga=GA1.1.629480075.1774019717;",
    "zepto_xsrf":    "bE2oHUJOyIEOvQzEz-CMr:GKn67JEgzrvftxNJuDFEriprMLM.XjjjXGYiOv3M5WYHM37wvuK/VXMMzZ9IDBjIm5IHiJo",
    "zepto_csrf":    "36Iq75TT5EI",
    "zepto_session": "1ae64c04-15b2-42eb-b07a-ff234b5caa58",
    "refreshing":    False,
}
lock = threading.Lock()
ZEPTO_IMG_BASE = "https://cdn.zeptonow.com/production//tr:w-500,ar-1-1,pr-true,f-webp,q-80/"


def refresh_zepto_cookies():
    with lock:
        if state["refreshing"]: return
        state["refreshing"] = True
    try:
        from playwright.sync_api import sync_playwright
        print("\n[Refresh] Refreshing Zepto cookies...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx  = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36")
            page = ctx.new_page()
            page.goto("https://www.zepto.com", timeout=25000)
            page.wait_for_timeout(4000)
            cookies = ctx.cookies("https://www.zepto.com")
            state["zepto_cookie"]  = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            state["zepto_xsrf"]    = next((c["value"] for c in cookies if "xsrf"        in c["name"].lower()), state["zepto_xsrf"])
            state["zepto_csrf"]    = next((c["value"] for c in cookies if "csrf-secret" in c["name"].lower()), state["zepto_csrf"])
            state["zepto_session"] = next((c["value"] for c in cookies if "session"     in c["name"].lower()), state["zepto_session"])
            print(f"[Refresh] ✅ Zepto: {len(cookies)} cookies")
            ctx.close(); browser.close()
    except Exception as e:
        print(f"[Refresh] Error: {e}")
    finally:
        state["refreshing"] = False

threading.Thread(target=refresh_zepto_cookies, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════════════
#  BLINKIT
#  Confirmed structure from debug_blinkit.py:
#  snippet.widget_type = "product_card_snippet_type_2"
#  snippet.data.name.text     = "Slovic Rubber Football (White)"
#  snippet.data.mrp.text      = "₹699"
#  snippet.data.price.text    = "₹549"   (selling price)
#  snippet.data.variant.text  = "1 pc"
#  snippet.data.image.url     = "https://cdn.grofers.com/..."
# ══════════════════════════════════════════════════════════════════════════════

def fetch_blinkit(query: str, lat: float, lon: float) -> list:
    try:
        from playwright.sync_api import sync_playwright
        print(f"  [Blinkit] Launching browser...")
        captured = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
                geolocation={"latitude": lat, "longitude": lon},
                permissions=["geolocation"],
            )
            ctx.add_cookies([
                {"name": "gr_1_lat",      "value": str(lat),   "domain": "blinkit.com", "path": "/"},
                {"name": "gr_1_lon",      "value": str(lon),   "domain": "blinkit.com", "path": "/"},
                {"name": "gr_1_locality", "value": "1849",     "domain": "blinkit.com", "path": "/"},
                {"name": "city",          "value": "Bhiwandi", "domain": "blinkit.com", "path": "/"},
            ])
            page = ctx.new_page()

            def on_response(response):
                if "layout/search" in response.url and response.status == 200:
                    try:
                        captured.append(response.json())
                    except Exception:
                        pass

            page.on("response", on_response)
            page.goto(f"https://blinkit.com/s/?q={query}", timeout=20000)
            page.wait_for_timeout(5000)
            browser.close()

        products = []
        for data in captured:
            snippets = data.get("response", {}).get("snippets", [])
            for snippet in snippets:
                if "product_card" not in snippet.get("widget_type", ""):
                    continue
                d = snippet.get("data", {})
                p = _parse_blinkit_card(d)
                if p:
                    products.append(p)

        # Deduplicate
        seen, unique = set(), []
        for p in products:
            k = (p["name"], p["price"])
            if k not in seen:
                seen.add(k); unique.append(p)

        print(f"  [Blinkit] ✅ {len(unique)} products")
        return unique

    except Exception as e:
        print(f"  [Blinkit] Error: {e}")
        return []


def _parse_price_text(obj):
    """Parse Blinkit display price object: {"text": "₹699", ...} → 699"""
    text = obj.get("text", "") if isinstance(obj, dict) else str(obj)
    text = text.replace("₹", "").replace(",", "").strip()
    try:
        return int(float(text))
    except:
        return 0


def _parse_blinkit_card(d: dict):
    name  = d.get("name",    {}).get("text", "")
    unit  = d.get("variant", {}).get("text", "")
    img   = d.get("image",   {}).get("url",  "")
    mrp   = _parse_price_text(d.get("mrp",   {}))
    price = _parse_price_text(d.get("price", d.get("selling_price", d.get("discounted_price", {}))))

    if not name:
        return None
    if not price:
        price = mrp   # if no discounted price, use MRP
    if not mrp:
        mrp = price
    if price < 5:
        return None

    return {
        "platform": "Blinkit", "name": name, "unit": unit, "image": img,
        "price": price, "mrp": mrp,
        "discount": round((1 - price/mrp)*100) if mrp > price else 0,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  ZEPTO
#  Confirmed structure from debug_zepto.py:
#  productResponse.product.name
#  productResponse.discountedSellingPrice  ÷ 100  = rupees
#  productResponse.mrp                     ÷ 100  = rupees
#  productResponse.productVariant.formattedPacksize
#  productResponse.productVariant.images[0].path  + CDN base
# ══════════════════════════════════════════════════════════════════════════════

def fetch_zepto(query: str, lat: float, lon: float) -> list:
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-encoding": "gzip, deflate, br",
        "accept-language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7,hi;q=0.6",
        "app_sub_platform": "WEB",
        "app_version": "14.33.2",
        "appversion": "14.33.2",
        "auth_from_cookie": "true",
        "auth_revamp_flow": "v2",
        "compatible_components": "CONVENIENCE_FEE,RAIN_FEE,EXTERNAL_COUPONS,STANDSTILL,BUNDLE,MULTI_SELLER_ENABLED,PIP_V1,ROLLUPS,SCHEDULED_DELIVERY,HOMEPAGE_V2,NEW_ETA_BANNER,SEARCH_PRODUCT_GRID_V2,DYNAMIC_FILTERS,SEARCH_FILTERS_V1,PLP_ON_SEARCH",
        "content-type": "application/json",
        "device_id": "957ae239-b8e1-4fa3-9e8d-6d30df853a41",
        "deviceid": "957ae239-b8e1-4fa3-9e8d-6d30df853a41",
        "marketplace_type": "SUPER_SAVER",
        "origin": "https://www.zepto.com",
        "platform": "WEB",
        "referer": f"https://www.zepto.com/search?query={query}",
        "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "session_id":  state["zepto_session"],
        "sessionid":   state["zepto_session"],
        "source": "DIRECT",
        "store_id":  "b4dc8d65-ed2e-4142-81b6-373982b13500",
        "store_ids": "b4dc8d65-ed2e-4142-81b6-373982b13500,0059ff6a-7eb0-477a-a7f5-69256f2c444b",
        "storeid":   "b4dc8d65-ed2e-4142-81b6-373982b13500",
        "store_etas": '{"b4dc8d65-ed2e-4142-81b6-373982b13500":-1,"0059ff6a-7eb0-477a-a7f5-69256f2c444b":-1}',
        "tenant": "ZEPTO",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        "x-csrf-secret":    state["zepto_csrf"],
        "x-without-bearer": "true",
        "x-xsrf-token":     state["zepto_xsrf"],
        "cookie":           state["zepto_cookie"],
    }

    body = {
        "query": query,
        "pageNumber": 0,
        "intentId": str(uuid.uuid4()),
        "mode": "AUTOSUGGEST",
        "userSessionId": state["zepto_session"],
    }

    try:
        resp = requests.post(
            "https://bff-gateway.zepto.com/user-search-service/api/v3/search",
            headers=headers, json=body, timeout=15,
        )
        print(f"  [Zepto] Status: {resp.status_code}")
        if resp.status_code in (401, 403):
            threading.Thread(target=refresh_zepto_cookies, daemon=True).start()
            return []
        if resp.status_code != 200:
            return []

        data = resp.json()
        products = []

        for widget in data.get("layout", []):
            items = widget.get("data", {}).get("resolver", {}).get("data", {}).get("items", [])
            for item in items:
                p = _parse_zepto_item(item)
                if p:
                    products.append(p)

        seen, unique = set(), []
        for p in products:
            k = (p["name"], p["price"])
            if k not in seen:
                seen.add(k); unique.append(p)

        print(f"  [Zepto] ✅ {len(unique)} products")
        return unique

    except Exception as e:
        print(f"  [Zepto] Error: {e}")
        return []


def _parse_zepto_item(item: dict):
    pr = item.get("productResponse", {})
    if not pr:
        return None

    name  = pr.get("product", {}).get("name", "")
    price = pr.get("discountedSellingPrice", pr.get("sellingPrice", 0))
    mrp   = pr.get("mrp", price)
    unit  = pr.get("productVariant", {}).get("formattedPacksize", "")

    images = pr.get("productVariant", {}).get("images", [])
    img = (ZEPTO_IMG_BASE + images[0].get("path", "")) if images else ""

    if not name or not price:
        return None
    try:
        price = int(price) // 100
        mrp   = int(mrp)   // 100
    except:
        return None
    if price < 5:
        return None

    return {
        "platform": "Zepto", "name": name, "unit": unit, "image": img,
        "price": price, "mrp": mrp,
        "discount": round((1 - price/mrp)*100) if mrp > price else 0,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    lat   = float(request.args.get("lat", 28.465204))
    lon   = float(request.args.get("lon", 77.06159))
    if not query:
        return jsonify({"error": "No query"}), 400

    print(f"\n{'='*50}")
    print(f"SEARCH: '{query}' @ ({lat}, {lon})")
    print(f"{'='*50}")

    results = {"blinkit": [], "zepto": []}
    def run_blinkit(): results["blinkit"] = fetch_blinkit(query, lat, lon)
    def run_zepto():   results["zepto"]   = fetch_zepto(query, lat, lon)

    t1 = threading.Thread(target=run_blinkit)
    t2 = threading.Thread(target=run_zepto)
    t1.start(); t2.start()
    t1.join(); t2.join()

    blinkit = sorted(results["blinkit"], key=lambda x: x["price"])
    zepto   = sorted(results["zepto"],   key=lambda x: x["price"])
    all_p   = sorted(blinkit + zepto,    key=lambda x: x["price"])

    print(f"\nRESULT → Blinkit={len(blinkit)}, Zepto={len(zepto)}")
    return jsonify({
        "query": query, "blinkit": blinkit[:10], "zepto": zepto[:10],
        "cheapest": all_p[:5], "count": {"blinkit": len(blinkit), "zepto": len(zepto)},
    })

@app.route("/refresh")
def manual_refresh():
    threading.Thread(target=refresh_zepto_cookies, daemon=True).start()
    return jsonify({"status": "Refresh started"})

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 QuickCart backend → http://0.0.0.0:{port}")
    app.run(debug=False, host="0.0.0.0", port=port)