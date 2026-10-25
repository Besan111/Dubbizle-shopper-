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
