import json
import shutil
from pathlib import Path

from shopper import cli, market, plate
from shopper.bus import Bus

ROOT = Path(__file__).resolve().parent.parent
CASE = ROOT / "cases" / "001-gls580-sell"


def comps():
    return market.load_comps(json.loads((CASE / "comps.json").read_text())["comps"])


def case(**over):
    c = json.loads((CASE / "case.json").read_text())
    c.update(over)
    return c


def test_stale_comps_are_excluded():
    assert all(c.price_aed < 350_000 for c in comps())


def test_sell_case_is_underpriced_and_stays_in_uae():
    v = market.decide(case(), comps(), json.loads((ROOT / "config" / "markets.json").read_text()))
    assert v.decision.startswith("SELL IN UAE")
    assert v.floor > 230_000 > v.quick_sale * 0.98
    assert not v.export["go_abroad"]


def test_buy_intent_flips_the_logic():
    v = market.decide(case(intent="buy", target_price_aed=200_000), comps(), [])
    assert v.decision == "BUY"
    v = market.decide(case(intent="buy", target_price_aed=320_000), comps(), [])
    assert v.decision == "PASS"


def test_accident_flag_from_plate_agent_lowers_value():
    clean = market.decide(case(), comps(), [])
    hit = market.decide(case(), comps(), [], {"accidents": True})
    assert hit.fair < clean.fair * 0.9


def test_plate_agent_skips_without_input(tmp_path):
    bus = Bus()
    r = plate.run(case(), tmp_path, bus)
    assert r["status"] == "skipped" and bus.messages[-1].sender == "plate-check"


def test_plate_agent_zooms_and_flags(tmp_path):
    shutil.copy(CASE / "photo.jpg", tmp_path / "car.jpg")
    c = case(photos=["car.jpg"], plate="Dubai X 12345",
             checks={"accident_history": {"accidents": True}, "police_status": {"fines_aed": 800}})
    r = plate.run(c, tmp_path, Bus())
    assert Path(r["zooms"][0]).exists()
    assert "accident on record" in r["flags"] and "claim mismatch: listed as accident-free" in r["flags"]


def test_full_run_writes_one_page_pdf(tmp_path):
    work = tmp_path / "case"
    shutil.copytree(CASE, work)
    v, p, out = cli.run(work)
    assert out.exists() and (work / "messages.json").exists()
    assert len(json.loads((work / "messages.json").read_text())) == 4


def test_flip_go_needs_checks_and_margin():
    v = market.decide(case(intent="flip"), comps(), [], {"status": "partial"})
    assert v.decision == "GO, BUT ONLY AFTER 3 CHECKS" and v.flip["net"]["base"] > 10_000
    assert v.flip["net"]["worst"] < 0 < v.flip["net"]["base"]
    assert market.decide(case(intent="flip", target_price_aed=255_000), comps(), []).decision.startswith("NO-GO")
    assert market.decide(case(intent="flip"), comps(), [], {"flags": ["accident on record"], "accidents": True}
                         ).decision == "NO-GO: CHECKS FAILED"


def test_quick_inquiry_from_a_dubizzle_link():
    from shopper import inquiry
    url = ("https://dubai.dubizzle.com/motors/used-cars/mercedes-benz/gls-class/2024/3/29/"
           "3800-km-warranty-june-2026-gls-580-full-op-2-952---19be6e05a2a34d90b18995ddd184964d/")
    html = ('<script type="application/ld+json">{"@type": "Car", "brand": {"name": "Mercedes-Benz"}, '
            '"model": "GLS 580", "vehicleModelDate": "2020", "mileageFromOdometer": {"value": "41000"}, '
            '"offers": {"price": "230000"}}</script><p>Regional Specs: Japanese Specs</p>')
    c = inquiry.build_case(f"should I flip this? {url}", html=html)
    assert c["intent"] == "flip" and c["target_price_aed"] == 230_000
    assert c["vehicle"]["km"] == 41_000 and c["vehicle"]["spec"] == "Japanese" and not c["missing"]
    assert inquiry.infer_intent("selling my car") == "sell" and inquiry.infer_intent(url) == "buy"


def test_best_selection_picks_the_most_underpriced_listing():
    from shopper import inquiry
    picks = inquiry.best_selection(comps(), "buy")
    assert picks and picks[0]["score"] >= picks[-1]["score"]
    assert picks[0]["listing"].price_aed < 300_000


def test_best_of_case_ranks_subject_first_and_never_values_it_against_itself():
    best = ROOT / "cases" / "003-best-gls580-flip"
    rows = json.loads((best / "comps.json").read_text())["comps"]
    assert len(market.load_comps(rows)) == len(market.load_comps(rows, include_subject=True)) - 1
    v, _, _ = cli.run(best)
    assert v.shortlist[0].startswith("1. 2020, 41k km")
    assert "AED 26,000" in v.shortlist[0] and v.tiles[1][1] == "AED 26,000"  # shortlist and report agree
