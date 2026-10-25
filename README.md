# Dubbizle Shopper

Two AI agents that decide whether to **buy or sell a car**, UAE first, abroad only when it pays. The answer is always **one decision on a one-page PDF with a photo**.

| Agent | Job |
|---|---|
| `agents/dubbizle-shopper` | Market agent. Researches dubizzle first, values the car from comparables, checks export markets, decides. |
| `agents/plate-check` | Status agent. Zooms into the plate from a photo, checks chassis status and history (Police, RTA, accidents, bank NOC). Runs only when a photo, plate or VIN is given. |

## Workflow

```
 user: car + intent + price (+ optional photo / plate / VIN)
                      |
             1. case.json  (cases/<nnn>-<slug>/)
                      |
             2. research: dubizzle -> dubicars / yallamotor -> comps.json
                      |
             3. market agent DRAFT verdict -------- asks -------> plate-check agent
                      |                                              | photo/plate/VIN?
                      |                                         no -> "skipped"
                      |                                        yes -> zoom plate, read it,
                      |                                               Police / RTA / accident / NOC
                      | <------------- reply or FLAGS ---------------+
             4. market agent FINAL verdict (accident = -12%, mortgage = NOC first)
                      | ------------- final -------------> plate-check ACKs
                      |
             5. export check: UAE vs config/markets.json (net after duty, shipping, risk)
                      |
             6. decision.pdf  (1 page, enforced)  +  messages.json
                      |
             7. learning loop: log run, file lessons, promote rules on user's yes
                       (tools/evolve.py, agents/*/loop/)
```

## How the price is worked out

1. Each comparable asking price becomes "what this car would realise privately in the UAE":
   dealer ask x0.90 or private ask x0.95, then AED 0.30 per km difference, then the spec factor (GCC 1.00, European 0.97, Japanese 0.95, American 0.88).
2. A weighted median gives **fair value**. Weights fall for stale, conflicting or far-mileage listings. Stale ones are excluded, with the reason kept.
3. Condition premium (+2% excellent), accident discount (-12%, from plate-check).
4. **List at** = fair +4%, **Accept from** = fair -4%, **Instant dealer cash** = fair x0.90.
5. Abroad wins only if it beats the UAE by more than AED 10k or 5% after duties, shipping and fees, and the risk is not high.

## Run it

```bash
pip install reportlab pypdf pillow pytest
python -m shopper cases/001-gls580-sell      # writes decision.pdf + messages.json
python -m pytest -q tests
```

In a Claude session: "Dubbizle Shopper: sell my <car>, <km>, <spec>, <price>". Claude follows `agents/dubbizle-shopper/SKILL.md`: it writes the case, researches comps, runs the engine, reviews the page and runs the learning step.

## Case 001: 2020 GLS 580, 41k km, Japanese spec, asking AED 230k

**SELL IN UAE, NOT AT 230K.** List on dubizzle at **AED 264,000**, accept from **AED 244,000**, take 230k only as instant dealer cash. Fair private value AED 254,000 (range 242k to 267k), confidence Medium. Kazakhstan export comes out about AED 9k worse after costs. Plate check was skipped because no photo, plate or VIN was received. See `cases/001-gls580-sell/decision.pdf`.

## Case 002: same car, you are the buyer and want to flip it

**GO, BUT ONLY AFTER 3 CHECKS.** Offer AED 218,000, pay at most AED 236,000, relist at AED 264,000 and accept from AED 244,000. Expected profit is about AED 16,000 (7%) in 4 to 8 weeks. A forced sale to a dealer means about -AED 9,000; a hidden accident means about -AED 36,000, which is what the checks are for. The plate (Dubai CC 53874) was read from the seller's photo. See `cases/002-gls580-flip/decision.pdf`.

Intents: `sell`, `buy`, `flip` (buy now, resell privately after about 6 weeks, net of costs and depreciation).

## Quick inquiry

Send one line or one listing link; no questionnaire.

```bash
python -m shopper.inquiry "flip this https://dubai.dubizzle.com/motors/used-cars/..."   # creates the case, infers the role
python -m shopper cases/<new case>                                                      # after the search fills any gaps
```

For "find me the best X", `shopper.inquiry.best_selection` ranks the researched listings (by discount to fair value, or by flip profit) and the report covers the top pick.

## MVP limits (honest)

1. Live dubizzle, dubicars and police portals were blocked by the build session's network policy, so case 001 used search-engine snapshots of listings. All prices are asking prices, not sold prices.
2. The UAE police, RTA and MOI checks need UAE PASS or an SMS code sent to the owner. The agent prepares and interprets them, but a person runs them.
3. Export duties for Kazakhstan are approximate. Confirm with a broker before shipping.
4. Success rate is measured, not claimed: log every real sale with `tools/evolve.py log` and recalibrate after 3 misses in the same direction.

## Skills used

1. `call-my-assistance:k2-self-evolving-loop`: the learning loop (`tools/evolve.py`, `agents/*/loop/`).
2. `anthropic-skills:pdf`: the one-page PDF (reportlab, page count checked with pypdf).
3. Higgsfield image generation: the photo when the user's own photo is missing (labelled as an illustration).
