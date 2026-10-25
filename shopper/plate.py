"""Plate agent: zoom the number plate from a photo, then run the UAE status and history checks.

Runs only when the case carries a photo, a plate or a VIN. With none of them it reports `skipped`
and the market agent carries on. It never asks the user.

Reading the zoomed plate is done by the Claude session (vision) and written back into the case
as `plate`. Official portals need UAE PASS or an SMS code sent to the owner, so their results
are entered into `checks` by whoever ran them; this module turns them into flags.
"""
from pathlib import Path

CHECKS = [
    {"id": "police_status", "needs": "plate", "name": "Dubai Police vehicle status and fines",
     "where": "Dubai Police app / dubaipolice.gov.ae > Services > Vehicle status inquiry"},
    {"id": "rta_certificate", "needs": "vin", "name": "RTA vehicle history certificate (owners, mortgage, insurance)",
     "where": "rta.ae > Vehicle > Vehicle history certificate (about AED 100)"},
    {"id": "accident_history", "needs": "vin", "name": "Accident history by chassis",
     "where": "MOI UAE app or Emirates Vehicle Gate (evg.ae), about AED 100 to 120"},
    {"id": "bank_noc", "needs": "vin", "name": "Bank mortgage / NOC",
     "where": "Shown on the RTA certificate; if mortgaged, get the bank NOC before transfer"},
]


def zoom_plate(photo, out, box=None, scale=3):
    """Crop the plate and upscale it. `box` is (left, top, right, bottom) in 0-1 fractions;
    without it, use the lower-centre band where a front or rear plate usually sits."""
    from PIL import Image, ImageFilter, ImageOps
    im = Image.open(photo).convert("RGB")
    w, h = im.size
    l, t, r, b = box or (0.25, 0.55, 0.85, 0.85)
    crop = im.crop((int(l * w), int(t * h), int(r * w), int(b * h)))
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    crop = ImageOps.autocontrast(crop).filter(ImageFilter.SHARPEN)
    crop.save(out, quality=92)
    return str(out)


def run(case, case_dir, bus):
    v = case["vehicle"]
    photos = [p for p in case.get("photos", []) if (Path(case_dir) / p).exists()]
    plate, vin = case.get("plate"), case.get("vin")

    if not (photos or plate or vin):
        report = {"status": "skipped", "reason": "no photo, plate or VIN provided",
                  "line": "Not run: no plate photo or chassis number received."}
        bus.post("plate-check", "dubbizle-shopper", "reply",
                 "Skipped: no photo, plate or VIN in the case. Seller claims (no accidents) stay unverified.",
                 **report)
        return report

    zooms = [zoom_plate(Path(case_dir) / p, Path(case_dir) / f"plate_zoom_{i}.jpg",
                        case.get("plate_box")) for i, p in enumerate(photos)]
    have = {"plate": bool(plate), "vin": bool(vin)}
    results = case.get("checks", {})
    todo = [c for c in CHECKS if have[c["needs"]] and c["id"] not in results]
    blocked = [c for c in CHECKS if not have[c["needs"]]]

    flags = []
    if results.get("accident_history", {}).get("accidents"):
        flags.append("accident on record")
    if results.get("rta_certificate", {}).get("mortgaged") or results.get("bank_noc", {}).get("mortgaged"):
        flags.append("mortgaged: bank NOC needed")
    fines = results.get("police_status", {}).get("fines_aed", 0)
    if fines:
        flags.append(f"fines AED {fines:,}")
    if v.get("accidents") is False and "accident on record" in flags:
        flags.append("claim mismatch: listed as accident-free")

    report = {"status": "flags" if flags else ("clean" if results and not todo else "partial"),
              "plate": plate, "vin": vin, "zooms": zooms, "flags": flags,
              "accidents": "accident on record" in flags,
              "todo": [c["name"] + ": " + c["where"] for c in todo],
              "blocked": [c["name"] + " (needs " + c["needs"] + ")" for c in blocked]}
    if flags:
        report["line"] = "Flags: " + "; ".join(flags) + "."
    elif report["status"] == "clean":
        report["line"] = f"Clean: plate {plate or '-'}, VIN {vin or '-'}; no accidents, no mortgage, no fines."
    else:
        report["line"] = (f"Plate {plate or 'not readable yet'}, read from the photo. Status, accidents and mortgage "
                          f"are not verified yet: the portals need the owner's UAE PASS"
                          f"{' and the chassis number' if blocked else ''}.")
    if case.get("photo_notes"):
        report["line"] = case["photo_notes"] + " " + report["line"]
    bus.post("plate-check", "dubbizle-shopper", "flag" if flags else "reply", report["line"], **report)
    return report
