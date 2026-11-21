# Vessel-Traffic

Predicts large-vessel traffic for kayakers crossing shipping lanes in the Salish Sea, starting with Rosario Strait.

**Planning aid only, not for navigation.** Some vessels don't broadcast AIS, ships can leave docks or anchorages without warning, and coverage has gaps. Keep a visual watch.

## What's here

| Path | Purpose |
|---|---|
| `config/gates.json` | The three crossing gates (G1 North Lummi–Clark Island, G2 North Cypress–Obstruction Pass, G3 Washington Park–Lopez Pass), the AIS collection box, and the vessel filter |
| `collector/collect.py` | Listens to aisstream.io for a short window and saves large-vessel positions |
| `.github/workflows/collect.yml` | Runs the collector every 10 minutes and commits the data |
| `data/positions/YYYY/MM/YYYY-MM-DD.csv` | Vessel positions (UTC), at most one row per vessel per 30 s |
| `data/static/vessels.json` | Cached ship details by MMSI: type, length, name, destination |
| `data/status.json` | Result of the last run (message count, rows saved, any error) |
| `tests/test_collect.py` | Offline tests for parsing and filtering |

## Vessel filter

Kept: cargo ships (AIS type 70–79) and tankers (80–89) at least 100 m long, plus any vessel reporting that it is towing (type 31/32, or navigation status 11/12), whatever its length. Dropped: tugs that aren't towing, ferries and other passenger vessels, and small craft. A vessel whose details haven't been received yet is kept until its details arrive, and sorted out later.

## Setup

- Repository secret `AISSTREAM_API_KEY` (free key from aisstream.io)
- Settings → Actions → General → Workflow permissions: **Read and write**

To test right away: Actions tab → **Collect AIS snapshot** → **Run workflow**.

## Roadmap

1. Collector and archive ← *now*
2. Live predictor: ETA ± at each gate, used by the `ms` command in hrdps-ingest
3. Delivery: inReach/SMS reply and a weak-signal web page
4. Backtest: measure the real ± from archived tracks
5. Field test, then expand to Haro Strait, Boundary Pass and Puget Sound
