"""Quick inquiry: one line or one listing link is enough to start a case.

    python -m shopper.inquiry "https://dubai.dubizzle.com/motors/used-cars/mercedes-benz/gls-class/..." [--intent flip]

1. Parse the link (make, model) and, when the network allows, the listing page itself (price, km, year, spec).
2. Infer the role: a link to someone else's listing means buy (or flip if the text says so); "my car" means sell.
3. Write cases/<nnn>-<slug>/case.json. Fields the page could not give are listed in `missing`;
   the Claude session fills them from the search step, then runs `python -m shopper <case>`.
`best_selection` ranks candidate listings so a "find me the best X" inquiry ends on one pick.
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from . import market

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ("make", "model", "year", "km", "spec")


def infer_intent(text):
    t = text.lower()
    if "flip" in t or "resell" in t:
        return "flip"
    if re.search(r"\b(my car|selling my|sell my)\b", t):
        return "sell"
    return "buy"


def parse_listing_url(url):
    """dubizzle path: /motors/used-cars/<make>/<model>/<posted y>/<m>/<d>/<slug>-<id>/"""
    u = urlparse(url)
    parts = [p for p in u.path.split("/") if p]
    out = {"url": url, "source": u.netloc}
    if "used-cars" in parts:
        i = parts.index("used-cars")
        if len(parts) > i + 1:
            out["make"] = parts[i + 1].replace("-", " ").title()
        if len(parts) > i + 2:
            out["model"] = parts[i + 2].replace("-class", "").replace("-", " ").upper()
        slug = parts[-1] if len(parts) > i + 6 else ""
        km = re.search(r"(\d[\d,]*)-?km", slug)
        if km:
            out["km"] = int(km.group(1).replace(",", ""))
    return out


def parse_listing_html(html):
    """Best effort: schema.org JSON-LD first, then plain-text patterns."""
    out = {}
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for d in data if isinstance(data, list) else [data]:
            if not isinstance(d, dict) or d.get("@type") not in ("Car", "Vehicle", "Product"):
                continue
            offer = d.get("offers") or {}
            if isinstance(offer, list):
                offer = offer[0] if offer else {}
            if offer.get("price"):
                out["price_aed"] = int(float(str(offer["price"]).replace(",", "")))
            odo = d.get("mileageFromOdometer") or {}
            if isinstance(odo, dict) and odo.get("value"):
                out["km"] = int(float(str(odo["value"]).replace(",", "")))
            year = d.get("vehicleModelDate") or d.get("modelDate") or d.get("productionDate")
            if year:
                out["year"] = int(str(year)[:4])
            brand = d.get("brand")
            if brand:
                out["make"] = brand.get("name") if isinstance(brand, dict) else brand
            if d.get("model"):
                out["model"] = d["model"]
    text = re.sub(r"<[^>]+>", " ", html)
    spec = re.search(r"\b(GCC|Japanese|American|European|Canadian|Korean) Specs?\b", text, re.I)
    if spec:
        out["spec"] = spec.group(1).title() if spec.group(1).upper() != "GCC" else "GCC"
    if "price_aed" not in out:
        price = re.search(r"AED\s*([\d,]{5,})", text)
        if price:
            out["price_aed"] = int(price.group(1).replace(",", ""))
    return out


def fetch(url, timeout=20):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (DubbizleShopper)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def build_case(text, intent=None, html=None):
    link = re.search(r"https?://\S+", text)
    listing = parse_listing_url(link.group(0)) if link else {}
    note = ""
    if link and html is None:
        try:
            html = fetch(link.group(0))
        except Exception as e:  # blocked network, 403, timeout: the search step fills the gaps
            note = f"listing page not reachable ({type(e).__name__}); fill from search"
    if html:
        listing.update({k: v for k, v in parse_listing_html(html).items() if v})
    vehicle = {k: listing.get(k) for k in REQUIRED}
    vehicle.update(condition="good", accidents=None, extras=[])
    case = {"intent": intent or infer_intent(text), "location": "UAE", "inquiry": text,
            "listing_url": listing.get("url"), "target_price_aed": listing.get("price_aed"),
            "vehicle": vehicle, "photos": [], "plate": None, "vin": None, "checks": {},
            "missing": [k for k in REQUIRED if not vehicle.get(k)] +
                       ([] if listing.get("price_aed") else ["target_price_aed"])}
    if note:
        case["notes"] = note
    return case


def new_case_dir(case):
    v = case["vehicle"]
    slug = "-".join(str(x) for x in (v.get("model") or "car", v.get("year") or "", case["intent"]) if x)
    slug = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")
    nums = [int(p.name[:3]) for p in (ROOT / "cases").glob("[0-9][0-9][0-9]-*")]
    d = ROOT / "cases" / f"{max(nums, default=0) + 1:03d}-{slug}"
    d.mkdir(parents=True, exist_ok=True)
    case["id"] = d.name
    (d / "case.json").write_text(json.dumps(case, indent=2, ensure_ascii=False))
    return d


def best_selection(listings, intent="buy", top=3):
    """Rank real listings by how far each sits under its own fair value (net of costs for a flip).
    Each listing is valued against all the others, so one cheap outlier cannot vouch for itself."""
    ranked = []
    for i, c in enumerate(listings):
        others = [o for j, o in enumerate(listings) if j != i]
        if len(others) < 2:
            continue
        subject = {"year": c.year, "km": c.km, "spec": c.spec, "condition": "good"}
        fair, _, high = market.value(subject, others)
        pay = c.price_aed * 0.97  # typical negotiation off the ask
        if intent == "flip":
            f = market.flip_math(pay, fair, high, fair * market.QUICK_SALE)
            score, why = f["net"]["base"], f"flip profit about {market.aed(f['net']['base'])}"
        else:
            score, why = fair - pay, f"{(fair - pay) / fair:+.0%} vs fair {market.aed(fair)}"
        if c.weight < 0.8:
            why += f" (verify first: {c.note or 'listing data uncertain'})"
        ranked.append({"listing": c, "score": int(score * c.weight), "fair": market.r1000(fair), "why": why})
    return sorted(ranked, key=lambda r: r["score"], reverse=True)[:top]


def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv:
        sys.exit(__doc__)
    intent = argv[argv.index("--intent") + 1] if "--intent" in argv else None
    text = " ".join(a for i, a in enumerate(argv) if a != "--intent" and (i == 0 or argv[i - 1] != "--intent"))
    case = build_case(text, intent)
    d = new_case_dir(case)
    print(f"case: {d.relative_to(ROOT)} | intent: {case['intent']}")
    if case["missing"]:
        print("missing (fill from search, then run `python -m shopper " f"{d.relative_to(ROOT)}`): "
              + ", ".join(case["missing"]))


if __name__ == "__main__":
    main()
