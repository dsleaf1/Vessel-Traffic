"""Offline tests for the collector's parsing and vessel filter (no network)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))
import collect  # noqa: E402

VF = collect.load_config()["vessel_filter"]


def pos(mmsi, t, lat=48.6, lon=-122.75, nav=0, sog=12.0):
    return {
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": mmsi, "time_utc": f"2026-11-21 {t}.123456 +0000 UTC",
                     "latitude": lat, "longitude": lon},
        "Message": {"PositionReport": {"UserID": mmsi, "Latitude": lat, "Longitude": lon,
                                       "Sog": sog, "Cog": 180.0, "TrueHeading": 181,
                                       "NavigationalStatus": nav}},
    }


def static(mmsi, typ, a, b, name="TEST"):
    return {
        "MessageType": "ShipStaticData",
        "MetaData": {"MMSI": mmsi, "time_utc": "2026-11-21 21:00:00.0 +0000 UTC"},
        "Message": {"ShipStaticData": {"Type": typ, "Name": name, "ImoNumber": 1,
                                       "Dimension": {"A": a, "B": b, "C": 10, "D": 10}}},
    }


def run(msgs, cache=None):
    snap = collect.Snapshot(VF, cache or {})
    for m in msgs:
        snap.handle(json.loads(json.dumps(m)))
    return snap.kept_rows()


def test_large_tanker_kept():
    rows, s = run([static(1, 80, 150, 50), pos(1, "21:00:00")])
    assert s["keep"] == 1 and len(rows) == 1


def test_small_cargo_dropped():
    rows, s = run([static(2, 70, 40, 30), pos(2, "21:00:00")])
    assert s["drop"] == 1 and rows == []


def test_tug_not_towing_dropped():
    rows, s = run([static(3, 52, 20, 10), pos(3, "21:00:00", nav=0)])
    assert s["drop"] == 1 and rows == []


def test_tug_towing_status_kept():
    rows, s = run([static(4, 52, 20, 10), pos(4, "21:00:00", nav=12)])
    assert s["keep"] == 1 and len(rows) == 1


def test_towing_ship_type_kept():
    rows, s = run([static(5, 31, 25, 10), pos(5, "21:00:00")])
    assert s["keep"] == 1


def test_passenger_ferry_dropped():
    rows, s = run([static(6, 60, 100, 40), pos(6, "21:00:00")])
    assert s["drop"] == 1


def test_unknown_static_kept_for_later():
    rows, s = run([pos(7, "21:00:00")])
    assert s["unknown"] == 1 and len(rows) == 1


def test_cached_static_used():
    cache = {"8": {"type": 70, "dimension": {"A": 200, "B": 30}}}
    rows, s = run([pos(8, "21:00:00")], cache)
    assert s["keep"] == 1


def test_rows_thinned_to_30s():
    msgs = [static(9, 80, 150, 50)] + [pos(9, f"21:00:{sec:02d}") for sec in range(0, 60, 5)]
    rows, _ = run(msgs)
    assert [r["time_utc"] for r in rows] == ["2026-11-21T21:00:00Z", "2026-11-21T21:00:30Z"]


def test_missing_position_ignored():
    rows, _ = run([static(10, 80, 150, 50), pos(10, "21:00:00", lat=91, lon=181)])
    assert rows == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
