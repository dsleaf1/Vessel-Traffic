"""AIS snapshot collector for Rosario Strait.

Connects to aisstream.io for a short listening window, keeps positions of
large vessels (see config/gates.json "vessel_filter"), and appends them to
a daily CSV under data/positions/. Ship details (type, length, name) are
cached in data/static/vessels.json, because AIS sends them only every few
minutes and a short snapshot may not catch them.

Run:  AISSTREAM_API_KEY=... python collector/collect.py --seconds 120
"""

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "gates.json"
DATA = ROOT / "data"
STATIC_FILE = DATA / "static" / "vessels.json"
STATUS_FILE = DATA / "status.json"
STREAM_URL = "wss://stream.aisstream.io/v0/stream"

POSITION_FIELDS = ["time_utc", "mmsi", "lat", "lon", "sog", "cog", "heading", "nav_status"]
MIN_ROW_SPACING_S = 30  # keep at most one row per vessel per 30 s


def load_config():
    with open(CONFIG) as f:
        return json.load(f)


def load_static():
    if STATIC_FILE.exists():
        with open(STATIC_FILE) as f:
            return json.load(f)
    return {}


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    tmp.replace(path)


def parse_time(meta_time):
    """aisstream time_utc looks like '2026-11-21 21:04:05.123456789 +0000 UTC'."""
    try:
        base = meta_time[:19]
        dt = datetime.strptime(base, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def vessel_length(static):
    dim = static.get("dimension") or {}
    a, b = dim.get("A") or 0, dim.get("B") or 0
    return a + b if a > 0 and b > 0 else None


def classify(static, towing_seen, vf):
    """Return 'keep', 'drop' or 'unknown' for a vessel."""
    if towing_seen:
        return "keep"
    if not static or static.get("type") is None:
        return "unknown"
    t = static["type"]
    if t in vf["towing_ship_types"]:
        return "keep"
    in_range = any(lo <= t <= hi for lo, hi in vf["ship_type_ranges"])
    if not in_range:
        return "drop"
    length = vessel_length(static)
    if length is None:
        return "unknown"
    return "keep" if length >= vf["min_length_m"] else "drop"


class Snapshot:
    """Accumulates one listening window's messages."""

    def __init__(self, vf, static_cache):
        self.vf = vf
        self.static = static_cache
        self.rows = {}          # mmsi -> list of position rows
        self.last_kept = {}     # mmsi -> datetime of last kept row
        self.towing = set()     # mmsi seen with towing nav status
        self.msg_count = 0

    def handle(self, msg):
        self.msg_count += 1
        mtype = msg.get("MessageType")
        meta = msg.get("MetaData") or {}
        body = (msg.get("Message") or {}).get(mtype) or {}
        mmsi = meta.get("MMSI") or body.get("UserID")
        if not mmsi:
            return
        mmsi = str(mmsi)
        when = parse_time(meta.get("time_utc"))

        if mtype == "ShipStaticData":
            self.static[mmsi] = {
                "type": body.get("Type"),
                "dimension": body.get("Dimension"),
                "name": (body.get("Name") or meta.get("ShipName") or "").strip(),
                "imo": body.get("ImoNumber"),
                "destination": (body.get("Destination") or "").strip(),
                "updated": when.isoformat(),
            }
            return

        if mtype != "PositionReport":
            return
        nav = body.get("NavigationalStatus")
        if nav in self.vf["towing_nav_status"]:
            self.towing.add(mmsi)
        last = self.last_kept.get(mmsi)
        if last is not None and (when - last).total_seconds() < MIN_ROW_SPACING_S:
            return
        lat = body.get("Latitude", meta.get("latitude"))
        lon = body.get("Longitude", meta.get("longitude"))
        if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
            return  # AIS uses 91/181 for "position not available"
        self.last_kept[mmsi] = when
        self.rows.setdefault(mmsi, []).append({
            "time_utc": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "mmsi": mmsi,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "sog": body.get("Sog"),
            "cog": body.get("Cog"),
            "heading": body.get("TrueHeading"),
            "nav_status": nav,
        })

    def kept_rows(self):
        """Rows for vessels that qualify or whose details are still unknown."""
        out, summary = [], {"keep": 0, "unknown": 0, "drop": 0}
        for mmsi, rows in self.rows.items():
            verdict = classify(self.static.get(mmsi), mmsi in self.towing, self.vf)
            summary[verdict] += 1
            if verdict != "drop":
                out.extend(rows)
        out.sort(key=lambda r: (r["time_utc"], r["mmsi"]))
        return out, summary


def append_positions(rows):
    """Append rows to data/positions/YYYY/MM/YYYY-MM-DD.csv (UTC day)."""
    by_day = {}
    for r in rows:
        by_day.setdefault(r["time_utc"][:10], []).append(r)
    for day, day_rows in by_day.items():
        path = DATA / "positions" / day[:4] / day[5:7] / f"{day}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=POSITION_FIELDS)
            if new:
                w.writeheader()
            w.writerows(day_rows)


async def listen(api_key, box, seconds, snap):
    import websockets  # installed in the workflow

    sub = {
        "APIKey": api_key,
        "BoundingBoxes": [[box["south_west"], box["north_east"]]],
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    async with websockets.connect(STREAM_URL, open_timeout=20) as ws:
        await ws.send(json.dumps(sub))
        while True:
            remaining = end - loop.time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if "error" in msg:
                raise RuntimeError(f"aisstream error: {msg['error']}")
            snap.handle(msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=120)
    args = ap.parse_args()

    api_key = os.environ.get("AISSTREAM_API_KEY")
    if not api_key:
        sys.exit("AISSTREAM_API_KEY is not set")

    cfg = load_config()
    snap = Snapshot(cfg["vessel_filter"], load_static())
    started = datetime.now(timezone.utc)
    error = None
    try:
        asyncio.run(listen(api_key, cfg["collection_box"], args.seconds, snap))
    except Exception as e:  # keep whatever was heard before the failure
        error = f"{type(e).__name__}: {e}"

    rows, summary = snap.kept_rows()
    append_positions(rows)
    save_json(STATIC_FILE, snap.static)
    save_json(STATUS_FILE, {
        "last_run_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "listen_seconds": args.seconds,
        "messages": snap.msg_count,
        "rows_saved": len(rows),
        "vessels": summary,
        "error": error,
    })
    print(f"messages={snap.msg_count} rows_saved={len(rows)} vessels={summary} error={error}")
    if error and snap.msg_count == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
