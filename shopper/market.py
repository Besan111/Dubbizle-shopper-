"""Market agent: turns Dubizzle-first comparables into a fair value, a decision and an export check.

Every comparable is converted to "what the subject car would realise in a private UAE sale":
  asking price -> realised price (dealer or private haircut)
  mileage gap   -> AED per km
  spec gap      -> spec factor (GCC is the UAE reference)
Then a weighted median gives the fair value. Weights drop for stale, conflicting or far-mileage comps.
"""
from dataclasses import dataclass, field

SPEC_FACTOR = {"gcc": 1.00, "european": 0.97, "japanese": 0.95, "korean": 0.93,
               "canadian": 0.90, "american": 0.88, "unknown": 0.95}
ASK_TO_REALISED = {"dealer": 0.90, "private": 0.95}
KM_RATE_AED = 0.30            # value lost per extra km on a large luxury SUV, 30-60k km band
KM_WEIGHT_SCALE = 40_000      # comps 40k km away count half
CONDITION_PREMIUM = {"excellent": 0.02, "good": 0.0, "fair": -0.06, "poor": -0.15}
ACCIDENT_DISCOUNT = 0.12
QUICK_SALE = 0.90             # dealer instant-cash offer as a share of fair private value
MONTHLY_DEPRECIATION = 0.0104 # about 12.5% a year for a 5-6 year old luxury SUV
FLIP_COSTS = {"transfer and plates": 1500, "pre-purchase inspection": 700,
              "insurance while holding": 1000, "detailing and listing": 1000}
FLIP_HOLD_MONTHS = 1.5
FLIP_MIN_MARGIN = (10_000, 0.04)  # AED floor, share of buy price: whichever is higher


@dataclass
class Comp:
    source: str
    price_aed: float
    km: int
    spec: str
    seller: str
    year: int
    url: str = ""
    note: str = ""
    weight: float = 1.0       # research quality: 1 clean, <1 stale / conflicting / date unclear
    buyable: bool = True      # False for sold listings: price evidence only
    condition: str = "good"   # known condition of the listed car, when seen
    adjusted: float = 0.0
    final_weight: float = 0.0


@dataclass
class Verdict:
    intent: str
    decision: str             # headline, e.g. "SELL in UAE"
    action: str               # one line telling the user what to do
    fair: int
    low: int
    high: int
    list_price: int
    floor: int
    quick_sale: int
    target: int
    gap_pct: float
    confidence: str
    reasons: list = field(default_factory=list)
    export: dict = field(default_factory=dict)
    comps_used: int = 0
    export_line: str = ""
    agents_line: str = ""
    tiles: list = field(default_factory=list)       # [(label, text)]; default is list / accept / fair
    checklist: list = field(default_factory=list)   # conditions to clear before paying
    flip: dict = field(default_factory=dict)
    shortlist: list = field(default_factory=list)   # ranked alternatives for a "find me the best" inquiry


def r1000(x):
    return int(round(x / 1000.0) * 1000)


def aed(x):
    """AED amount rounded to the nearest thousand, sign in front: -AED 9,000."""
    return f"{'-' if x < 0 else ''}AED {abs(r1000(x)):,}"


def adjust(comp, subject):
    price = comp.price_aed * ASK_TO_REALISED.get(comp.seller, 0.92)
    price += (comp.km - subject["km"]) * KM_RATE_AED
    price *= SPEC_FACTOR.get(subject["spec"].lower(), 0.95) / SPEC_FACTOR.get(comp.spec.lower(), 0.95)
    price *= 1 + 0.08 * (subject["year"] - comp.year)   # rough year step if a comp is off-year
    return price


def weighted_quantile(pairs, q):
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= q * total:
            return v
    return pairs[-1][0]


def value(subject, comps):
    pairs = []
    for c in comps:
        c.adjusted = adjust(c, subject)
        c.final_weight = (c.weight / (1 + abs(c.km - subject["km"]) / KM_WEIGHT_SCALE)
                          / (1 + abs(c.year - subject["year"])))  # other model years count less
        pairs.append((c.adjusted, c.final_weight))
    if not pairs:
        raise ValueError("No comparables: research step must produce comps.json first")
    prem = 1 + CONDITION_PREMIUM.get(subject.get("condition", "good"), 0.0)
    if subject.get("accidents"):
        prem -= ACCIDENT_DISCOUNT
    fair = weighted_quantile(pairs, 0.5) * prem
    low = min(weighted_quantile(pairs, 0.25) * prem, fair * 0.95)
    high = max(weighted_quantile(pairs, 0.75) * prem, fair * 1.05)
    return fair, low, high


def confidence(comps):
    strong = sum(1 for c in comps if c.weight >= 0.8 and c.final_weight >= 0.5)
    if strong >= 5:
        return "High"
    return "Medium" if strong >= 2 else "Low"


def export_check(fair, markets):
    """Net extra AED from selling abroad instead of in the UAE, per destination."""
    rows = []
    for m in markets:
        if m.get("excluded"):
            rows.append({"name": m["name"], "net_vs_uae": None, "viable": False, "why": m["excluded"]})
            continue
        realised = m["local_price_aed"] * m.get("ask_to_realised", 0.92)
        costs = fair * m["duty_pct"] + m["fixed_costs_aed"]
        net = realised - fair - costs
        rows.append({"name": m["name"], "net_vs_uae": int(net), "weeks": m.get("weeks"),
                     "viable": net > max(10_000, 0.05 * fair) and m.get("risk", "medium") != "high",
                     "why": m.get("note", "")})
    best = max((r for r in rows if r["net_vs_uae"] is not None), key=lambda r: r["net_vs_uae"], default=None)
    return {"rows": rows, "best": best, "go_abroad": bool(best and best["viable"])}


def decide(case, comps, markets, plate_report=None):
    subject = case["vehicle"]
    if plate_report and plate_report.get("accidents"):
        subject = dict(subject, accidents=True)
    fair, low, high = value(subject, comps)
    target = case.get("target_price_aed") or fair
    list_price, floor, quick = r1000(fair * 1.04), r1000(fair * 0.96), r1000(fair * QUICK_SALE)
    gap = (target - fair) / fair
    exp = export_check(fair, markets)
    where = f"in {exp['best']['name']}" if exp["go_abroad"] else "in UAE"
    reasons = []

    if case["intent"] == "sell":
        if exp["go_abroad"]:
            decision = f"SELL {where.upper()}"
            action = f"Export beats UAE by about AED {exp['best']['net_vs_uae']:,} after costs."
        elif target >= fair * 1.08:
            decision, action = "SELL IN UAE, PRICE IS HIGH", f"List at AED {list_price:,}; above that it sits."
        elif target >= floor:
            decision, action = "SELL IN UAE AT YOUR PRICE", f"List at AED {max(target, list_price):,}, accept from AED {floor:,}."
        elif target >= quick * 0.98:
            decision = f"SELL IN UAE, NOT AT {int(target) // 1000}K"
            action = (f"List on Dubizzle at AED {list_price:,}. Accept from AED {floor:,}. "
                      f"Take AED {int(target):,} only as instant dealer cash.")
        else:
            decision, action = "HOLD, DO NOT SELL AT THIS PRICE", f"It is a giveaway. Fair private value is AED {r1000(fair):,}."
    elif case["intent"] == "flip":
        return decide_flip(case, comps, exp, fair, low, high, list_price, floor, quick, plate_report)
    else:  # buy
        if target <= fair * 0.92:
            decision, action = "BUY", f"Below fair value. Offer AED {r1000(target * 0.97):,}, pay up to AED {r1000(target):,}."
        elif target <= fair:
            decision, action = "BUY, NEGOTIATE", f"Offer AED {r1000(fair * 0.92):,}, walk away above AED {r1000(fair):,}."
        else:
            decision, action = "PASS", f"Overpriced by {gap:+.0%}. Fair value is AED {r1000(fair):,}."

    reasons.append(f"Your AED {int(target):,} is {gap:+.0%} vs fair private value AED {r1000(fair):,} "
                   f"(range {r1000(low):,} to {r1000(high):,}).")
    return Verdict(case["intent"], decision, action, r1000(fair), r1000(low), r1000(high), list_price, floor,
                   quick, int(target), gap, confidence(comps), reasons, exp, len(comps))


def load_comps(rows, include_subject=False):
    """`subject: true` marks the car being decided on; it never counts as its own comparable."""
    comps = []
    for r in rows:
        if r.get("exclude") or (r.get("subject") and not include_subject):
            continue
        comps.append(Comp(**{k: r[k] for k in Comp.__dataclass_fields__ if k in r}))
    return comps


def flip_math(buy, fair, high, quick, costs=None, months=FLIP_HOLD_MONTHS):
    """Buy now, resell privately after `months`. Returns net profit per scenario and the max buy price."""
    cost = sum((costs or FLIP_COSTS).values())
    drift = 1 - MONTHLY_DEPRECIATION * months
    scen = {"base": fair * drift, "best": high * drift, "worst": quick * drift,
            "hidden accident": fair * (1 - ACCIDENT_DISCOUNT) * QUICK_SALE * drift}
    net = {k: int(v - buy - cost) for k, v in scen.items()}
    margin = max(FLIP_MIN_MARGIN[0], FLIP_MIN_MARGIN[1] * buy)
    max_buy = int((scen["base"] - cost - margin) // 1000 * 1000)
    return {"cost": cost, "net": net, "max_buy": max_buy, "months": months,
            "exit_base": r1000(scen["base"]), "monthly_loss": int(round(fair * MONTHLY_DEPRECIATION, -2))}


def decide_flip(case, comps, exp, fair, low, high, list_price, floor, quick, plate_report):
    buy = int(case["target_price_aed"])
    f = flip_math(buy, fair, high, quick, case.get("flip_costs"))
    n, max_buy = f["net"], f["max_buy"]
    offer = min(r1000(buy * 0.95), max_buy)
    checks_clean = bool(plate_report) and plate_report.get("status") == "clean"
    flagged = bool(plate_report and plate_report.get("flags"))

    if flagged:
        decision, action = "NO-GO: CHECKS FAILED", "Walk away, or renegotiate only after an inspector prices the damage."
    elif buy <= max_buy:
        decision = "GO, BUY AND FLIP" if checks_clean else "GO, BUT ONLY AFTER 3 CHECKS"
        action = (f"Offer AED {offer:,}, pay at most AED {max_buy:,}. "
                  f"Relist at AED {list_price:,}, accept from AED {floor:,}.")
    elif buy <= max_buy + 5000:
        decision, action = f"ONLY AT AED {max_buy // 1000}K OR LESS", f"At {buy // 1000}k the margin is too thin for the risk."
    else:
        decision, action = "NO-GO: MARGIN TOO THIN", f"Flip pays only below AED {max_buy:,}."

    reasons = [
        f"Buy at AED {buy:,}, resell privately at about AED {f['exit_base']:,} in 4 to 8 weeks. After about "
        f"AED {f['cost']:,} costs you keep about {aed(n['base'])} ({n['base'] / buy:.0%}).",
        f"Risk: a forced sale to a dealer means {aed(n['worst'])}; a hidden accident means {aed(n['hidden accident'])}. "
        "The checks below remove most of that risk before you pay.",
        f"Japanese spec has fewer buyers than GCC, and each month you hold it costs about AED {f['monthly_loss']:,}. "
        "Price it right from day one.",
    ]
    if not exp["go_abroad"]:
        reasons.append("Exporting it does not pay; resell in the UAE.")
    tiles = [("Pay at most", f"AED {max_buy:,}"), ("Expected profit", aed(n["base"])),
             ("Worst case", aed(n["worst"]))]
    return Verdict("flip", decision, action, r1000(fair), r1000(low), r1000(high), list_price, floor, quick, buy,
                   (buy - fair) / fair, confidence(comps), reasons, exp, len(comps), tiles=tiles,
                   checklist=list(case.get("checklist", [])), flip=f)
