"""Run one case end to end: plate agent first (if it has input), market agent, dialogue, one-page PDF.

    python -m shopper cases/001-gls580-sell
"""
import json
import sys
from pathlib import Path

from . import market, plate, report
from .bus import Bus

ROOT = Path(__file__).resolve().parent.parent


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def export_line(exp):
    parts = []
    for r in exp["rows"]:
        if r["net_vs_uae"] is None:
            parts.append(f"{r['name']}: excluded ({r['why']})")
        else:
            k = abs(r["net_vs_uae"]) / 1000
            parts.append(f"{r['name']}: about AED {k:,.0f}k {'better' if r['net_vs_uae'] > 0 else 'worse'} than UAE")
    head = "Go abroad." if exp["go_abroad"] else "UAE wins; export does not pay after duties, shipping and risk."
    return head + " " + "; ".join(parts) + "."


def run(case_dir):
    case_dir = Path(case_dir)
    case = load(case_dir / "case.json")
    research = load(case_dir / "comps.json")
    markets = load(case_dir / "markets.json") if (case_dir / "markets.json").exists() \
        else load(ROOT / "config" / "markets.json")
    comps = market.load_comps(research["comps"])
    bus = Bus()

    # 1. market agent drafts, then asks the plate agent before committing
    draft = market.decide(case, comps, markets)
    bus.post("dubbizle-shopper", "plate-check", "draft",
             f"Draft: {draft.decision}. Fair AED {draft.fair:,}. Any status or history issue before I finalise?",
             fair=draft.fair, decision=draft.decision)

    # 2. plate agent runs only if the case gives it something to look at
    plate_report = plate.run(case, case_dir, bus)

    # 3. market agent folds the plate findings in and finalises
    verdict = market.decide(case, comps, markets, plate_report)
    if plate_report.get("flags"):
        verdict.reasons.append("Plate check flags: " + "; ".join(plate_report["flags"]) + ". Fix or disclose first.")
    elif plate_report["status"] == "skipped" and verdict.intent != "flip":  # a flip carries its own checklist
        verdict.reasons.append("History is unverified. Pull the RTA vehicle history certificate and accident "
                               "report (about AED 100 each) before listing; buyers pay full price for proof.")
    verdict.reasons.extend(research.get("insights", []))
    if case.get("best_of"):
        from .inquiry import best_selection
        listings = market.load_comps(research["comps"], include_subject=True)
        for i, p in enumerate(best_selection(listings, case["intent"]), 1):
            c = p["listing"]
            verdict.shortlist.append(f"{i}. {c.year}, {c.km // 1000}k km, {c.spec}, asking AED "
                                     f"{int(c.price_aed) // 1000}k, {c.source.split(',')[0]}: {p['why']}")
    verdict.export_line = export_line(verdict.export)
    bus.post("dubbizle-shopper", "plate-check", "final", f"Final: {verdict.decision}. {verdict.action}",
             fair=verdict.fair, list_price=verdict.list_price, floor=verdict.floor)
    ack = "Agreed." if not plate_report.get("flags") else "Agreed, with flags disclosed."
    bus.post("plate-check", "dubbizle-shopper", "ack", ack)
    verdict.agents_line = (f"Market agent and plate agent exchanged {len(bus.messages)} messages; "
                           f"plate agent: {plate_report['status']}; final verdict {ack.lower()}")

    bus.save(case_dir / "messages.json")
    photo = case_dir / case["report_photo"] if case.get("report_photo") else None
    out = case_dir / "decision.pdf"
    report.render(out, case, verdict, plate_report, photo, research.get("sources", []))

    from pypdf import PdfReader
    pages = len(PdfReader(str(out)).pages)
    if pages != 1:
        raise SystemExit(f"Report is {pages} pages; the rule is one page.")
    return verdict, plate_report, out


def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv:
        sys.exit(__doc__)
    v, p, out = run(argv[0])
    print(f"{v.decision} | {v.action}")
    print(f"fair {v.fair:,} (range {v.low:,} to {v.high:,}) | list {v.list_price:,} | floor {v.floor:,} | "
          f"quick-sale {v.quick_sale:,} | confidence {v.confidence}")
    print(f"export: {v.export_line}")
    print(f"plate: {p['status']} | {p.get('line')}")
    print(f"pdf: {out}")
