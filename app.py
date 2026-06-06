"""
Reolink battery dashboard – Flask backend.

  * a background thread polls the hub every POLL_INTERVAL seconds and stores a
    snapshot of every channel's battery state into SQLite,
  * a small JSON API exposes the live state, per-camera detail and history,
  * "/" serves the matrix dashboard.
"""

import logging
import os
import sqlite3
import threading
import time
from contextlib import closing

from flask import Flask, Response, jsonify, render_template, request

import reolink
from reolink import client

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("dashboard")

# ---------------- CONFIG ----------------
DB_FILE = os.environ.get("DB_FILE", "/appdata/history.db")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 300))      # seconds
HISTORY_HOURS = int(os.environ.get("HISTORY_HOURS", 24))       # default chart window
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", 30))     # prune older rows
# Snapshots wake sleeping battery cameras, so we cache the last frame per
# channel and only refresh it after this many seconds.
SNAPSHOT_TTL = int(os.environ.get("SNAPSHOT_TTL", 300))

app = Flask(__name__)

# Live snapshot of the last poll, served instantly to the UI.
_latest = {"ts": 0, "cameras": {}}
_latest_lock = threading.Lock()

# channel -> (timestamp, jpeg_bytes)
_snap_cache = {}
_snap_lock = threading.Lock()


# ---------------- DB ----------------
def init_db():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with closing(sqlite3.connect(DB_FILE)) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS battery_history (
                ts            INTEGER NOT NULL,
                channel       INTEGER NOT NULL,
                percent       INTEGER,
                voltage_mv    INTEGER,
                temperature   INTEGER,
                current_ma    INTEGER,
                charge_status TEXT,
                charging      INTEGER,
                online        INTEGER,
                sleep         INTEGER
            )
            """
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_hist_ch_ts ON battery_history(channel, ts)"
        )
        db.commit()


def is_charging(batt):
    """Normalise the many ways Reolink firmwares report charging state."""
    if not batt:
        return None
    cs = str(batt.get("chargeStatus", "")).lower()
    if cs in ("charging", "chargecomplete"):
        return 1
    if cs in ("discharging", "none", "nocharge"):
        return 0
    # Fall back to adapter status / current sign.
    ads = str(batt.get("adapterStatus", "")).lower()
    if ads == "charging":
        return 1
    cur = batt.get("current")
    if isinstance(cur, (int, float)):
        return 1 if cur > 0 else 0
    return None


def store_poll(ts, cameras):
    rows = []
    for ch, cam in cameras.items():
        batt = cam.get("battery") or {}
        rows.append((
            ts, ch,
            batt.get("batteryPercent"),
            batt.get("voltage"),
            batt.get("temperature"),
            batt.get("current"),
            str(batt.get("chargeStatus")) if batt else None,
            is_charging(batt),
            cam.get("online"),
            cam.get("sleep"),
        ))
    if not rows:
        return
    with closing(sqlite3.connect(DB_FILE)) as db:
        db.executemany(
            """INSERT INTO battery_history
               (ts, channel, percent, voltage_mv, temperature, current_ma,
                charge_status, charging, online, sleep)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        db.execute(
            "DELETE FROM battery_history WHERE ts < ?",
            (ts - RETENTION_DAYS * 86400,),
        )
        db.commit()


def poll_loop():
    while True:
        try:
            cameras = client.poll_all()
            if cameras:
                ts = int(time.time())
                # Keep the last known battery reading for any channel that came
                # back empty this round (e.g. a slow/sleeping camera timed out),
                # so the dashboard shows stale-but-useful data instead of blanks.
                with _latest_lock:
                    prev = _latest.get("cameras", {})
                    for ch, cam in cameras.items():
                        if not cam.get("battery") and prev.get(ch, {}).get("battery"):
                            cam["battery"] = dict(prev[ch]["battery"])
                            cam["battery_stale"] = True
                    _latest["ts"] = ts
                    _latest["cameras"] = cameras
                store_poll(ts, cameras)
                got = sum(1 for c in cameras.values() if c.get("battery"))
                logger.info(f"Polled {len(cameras)} channels ({got} with battery)")
            else:
                logger.warning("Poll returned no data")
        except Exception as e:
            logger.exception(f"Poll loop error: {e}")
        time.sleep(POLL_INTERVAL)


# ---------------- API ----------------
def serialize_camera(cam):
    batt = cam.get("battery") or {}
    return {
        "channel": cam.get("channel"),
        "name": cam.get("name"),
        "uid": cam.get("uid"),
        "online": bool(cam.get("online")),
        "sleep": bool(cam.get("sleep")),
        "offline": not bool(cam.get("online")),
        "battery": {
            "percent": batt.get("batteryPercent"),
            "voltage_mv": batt.get("voltage"),
            "voltage_v": round(batt["voltage"] / 1000, 2) if batt.get("voltage") else None,
            "temperature": batt.get("temperature"),
            "current_ma": batt.get("current"),
            "charge_status": batt.get("chargeStatus"),
            "charging": is_charging(batt),
            "low_power": batt.get("lowPower"),
            "stale": cam.get("battery_stale", False),
        } if batt else None,
    }


@app.route("/api/cameras")
def api_cameras():
    with _latest_lock:
        ts = _latest["ts"]
        cams = dict(_latest["cameras"])
    cameras = [serialize_camera(c) for _, c in sorted(cams.items())]
    return jsonify({"updated": ts, "count": len(cameras), "cameras": cameras})


@app.route("/api/camera/<int:channel>")
def api_camera(channel):
    with _latest_lock:
        cam = _latest["cameras"].get(channel)
    if not cam:
        return jsonify({"error": "unknown channel"}), 404
    data = serialize_camera(cam)
    dev = client.get_dev_info(channel)
    data["serial"] = (dev or {}).get("serial") or cam.get("uid")
    data["model"] = (dev or {}).get("model")
    return jsonify(data)


@app.route("/api/camera/<int:channel>/history")
def api_history(channel):
    from flask import request
    hours = int(request.args.get("hours", HISTORY_HOURS))
    since = int(time.time()) - hours * 3600
    with closing(sqlite3.connect(DB_FILE)) as db:
        cur = db.execute(
            """SELECT ts, percent, voltage_mv, temperature, charging
               FROM battery_history
               WHERE channel = ? AND ts >= ?
               ORDER BY ts ASC""",
            (channel, since),
        )
        rows = cur.fetchall()
    return jsonify({
        "channel": channel,
        "hours": hours,
        "points": [
            {"ts": r[0], "percent": r[1], "voltage_mv": r[2],
             "temperature": r[3], "charging": r[4]}
            for r in rows
        ],
    })


@app.route("/api/camera/<int:channel>/snapshot")
def api_snapshot(channel):
    now = time.time()
    with _snap_lock:
        cached = _snap_cache.get(channel)
    # Serve a fresh-enough cached frame without waking the camera again.
    if cached and now - cached[0] < SNAPSHOT_TTL:
        return _img_response(cached[1])

    img = client.snapshot(channel)
    if img is None:
        if cached:
            return _img_response(cached[1])      # stale is better than nothing
        return Response(status=502)
    with _snap_lock:
        _snap_cache[channel] = (now, img)
    return _img_response(img)


def _img_response(data):
    resp = Response(data, mimetype="image/jpeg")
    # Let the browser cache within the TTL so re-renders don't refetch.
    resp.headers["Cache-Control"] = f"private, max-age={SNAPSHOT_TTL}"
    return resp


@app.route("/api/camera/<int:channel>/stream")
def api_stream(channel):
    """Proxy the hub's HTTP-FLV live stream so credentials stay server-side.
    Use ?q=main for the full-res stream (default is the lighter sub-stream)."""
    sub = request.args.get("q", "sub") != "main"
    upstream = client.open_flv(channel, sub=sub)
    if upstream is None:
        return Response(status=502)

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        except GeneratorExit:
            pass
        finally:
            upstream.close()

    return Response(generate(), mimetype="video/x-flv")


@app.route("/api/hdd")
def api_hdd():
    raw = client.get_hdd_info() or []
    disks = []
    for d in raw:
        cap = d.get("capacity") or 0          # total MB
        free = d.get("size") or 0             # remaining MB (Reolink: size = free)
        used = max(cap - free, 0)
        disks.append({
            "number": d.get("number"),
            "mount": d.get("mount"),
            "capacity_mb": cap,
            "free_mb": free,
            "used_mb": used,
            "used_pct": round(used / cap * 100, 1) if cap else 0,
        })
    return jsonify(disks)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/debug")
def api_debug():
    """Diagnostics: raw hub responses. Open in browser and paste the output.
    Does not expose the password. Remove this endpoint once the issue is resolved."""
    out = {"nvr_ip": reolink.NVR_IP, "channel_limit": reolink.CHANNEL_LIMIT}
    try:
        out["token_ok"] = bool(client.get_token())
        channels, statuses = client.discover_channels()
        out["discovered_channels"] = channels
        out["channel_status_raw"] = statuses
        battery_raw = {}
        for ch in channels[:9]:
            battery_raw[ch] = client._post(
                [{"cmd": "GetBatteryInfo", "action": 0, "param": {"channel": ch}}],
                timeout=reolink.POLL_TIMEOUT,
            )
        out["battery_raw"] = battery_raw
        # what the application actually parsed:
        out["parsed"] = {
            ch: (cam.get("battery"))
            for ch, cam in (client.poll_all() or {}).items()
        }
    except Exception as e:
        out["error"] = repr(e)
    return jsonify(out)


def main():
    init_db()
    # Prime an immediate poll so the UI isn't empty on first load.
    threading.Thread(target=poll_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    # Single process, multiple threads on purpose: the poller, snapshot cache
    # and latest-state live in memory, so multi-process WSGI would break them.
    try:
        from waitress import serve
        threads = int(os.environ.get("WAITRESS_THREADS", 16))
        logger.info(f"Serving on :{port} (waitress, {threads} threads)")
        serve(app, host="0.0.0.0", port=port, threads=threads)
    except ImportError:
        logger.warning("waitress missing - falling back to Flask dev server")
        app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    main()
