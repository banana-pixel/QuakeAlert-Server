"""
QuakeAlert EEW Bridge — Architecture v2
======================================================
Changes from v1:
  - Telegram integration REMOVED entirely.
  - Spatio-temporal clustering state machine (§3.2).
  - Hybrid Smart Push headers on every ntfy POST (§4.1).
  - WAL-mode SQLite writes to `ongoing_events` table (§3.1).
  - Deadman's switch thread (§3.3) — auto-resolves stale events.
  - Standard UTC timestamps / Unix Epoch throughout (§2.1).
"""

import paho.mqtt.client as mqtt
import requests
import json
import threading
import queue
import os
import sys
import time
import math
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bridge")

# ---------------------------------------------------------------------------
# Configuration — all values read from environment variables
# ---------------------------------------------------------------------------
MQTT_BROKER   = os.getenv("MQTT_BROKER",   "mosquitto")
MQTT_PORT     = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER     = os.getenv("MQTT_USER",     "guest")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "guest")

NTFY_SERVER = os.getenv("NTFY_SERVER", "http://ntfy")
NTFY_TOPIC  = os.getenv("NTFY_TOPIC",  "seismo_alerts")
NTFY_USER   = os.getenv("NTFY_USER",   "guest")
NTFY_PASS   = os.getenv("NTFY_PASS",   "guest")

REPORT_ENDPOINT = os.getenv("REPORT_ENDPOINT", "http://localhost:5000/laporan")
REPORT_API_KEY  = os.getenv("REPORT_API_KEY",  "").strip()
REPORT_HEADERS  = {"X-API-Key": REPORT_API_KEY} if REPORT_API_KEY else {}

# Internal endpoint prefix (e.g. "http://localhost:5000")
_BASE_URL = REPORT_ENDPOINT.rsplit("/", 1)[0]

# DB path mirrors server.py so both processes share the same file
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_FILE  = os.path.join(DATA_DIR, "laporan_gempa.db")

# ---------------------------------------------------------------------------
# Spatio-Temporal Clustering constants (§3.2)
# ---------------------------------------------------------------------------
CLUSTER_BASE_RADIUS_KM    = 50    # Condition A: triggers ≤50 km apart are the same event
CLUSTER_WAVE_RADIUS_KM    = 100   # Condition B: up to 100 km if within wave-propagation window
CLUSTER_WAVE_TIME_SEC     = 15    # Condition B: elapsed time window (seconds)
TIER2_ESCALATION_TIME_SEC = 30    # Escalate to Tier 2 if 2nd sensor joins within 30 s

# Deadman's switch — force-resolve events with no update for 60 seconds (§3.3)
DEADMAN_TIMEOUT_SEC       = 60
DEADMAN_POLL_INTERVAL_SEC = 5

# ---------------------------------------------------------------------------
# Reverse-geocoding helper
# ---------------------------------------------------------------------------
_NOMINATIM_URL     = "https://nominatim.openstreetmap.org/reverse"
_NOMINATIM_HEADERS = {"User-Agent": "QuakeAlert-Server/2.0"}

def reverse_geocode(lat: str, lon: str) -> str:
    """Resolve lat/lon to a human-readable city name. Never raises."""
    try:
        r = requests.get(
            _NOMINATIM_URL,
            params={"lat": lat, "lon": lon, "format": "json"},
            headers=_NOMINATIM_HEADERS,
            timeout=3,
        )
        r.raise_for_status()
        addr = r.json().get("address", {})
        place = (
            addr.get("village") or addr.get("suburb") or
            addr.get("city_district") or addr.get("town") or
            addr.get("city") or addr.get("county")
        )
        state = addr.get("state") or addr.get("province") or ""
        if place and state:
            return f"{place}, {state}"
        if place:
            return place
        display = r.json().get("display_name", "")
        return display[:60] if display else "Unknown Region"
    except Exception as exc:
        logger.warning("reverse_geocode(%s, %s) failed: %s", lat, lon, exc)
    return "Unknown Region"

# ---------------------------------------------------------------------------
# SQLite WAL helper (§3.1)
# ---------------------------------------------------------------------------
def get_db_connection() -> sqlite3.Connection:
    """
    Open a WAL-mode connection with a 5-second busy timeout.
    Every bridge write uses this so concurrent reader-storms never cause SQLITE_BUSY.
    """
    conn = sqlite3.connect(DB_FILE, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

# ---------------------------------------------------------------------------
# Haversine distance (km) — used by the clustering engine
# ---------------------------------------------------------------------------
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# ---------------------------------------------------------------------------
# In-memory event state — protected by a threading.Lock
# ---------------------------------------------------------------------------
_events_lock = threading.Lock()

# Structure:
# {
#   "EVENT_YYYYMMDD_XXXX": {
#       "event_id":      str,
#       "epicenter_lat": float,
#       "epicenter_lon": float,
#       "max_pga":       float,
#       "tier":          int,       # 1 = Sentinel, 2 = Confirmed
#       "sensors":       { station_id: { lat, lon, pga, triggered_at } },
#       "created_at":    int (unix epoch UTC),
#       "updated_at":    int,
#   }
# }
ongoing_events: dict = {}

def _make_event_id() -> str:
    date_tag = datetime.now(timezone.utc).strftime("%Y%m%d")
    rand_tag  = uuid.uuid4().hex[:4].upper()
    return f"EVENT_{date_tag}_{rand_tag}"

def _upsert_ongoing_event(event: dict) -> None:
    """Write/update a row in the `ongoing_events` SQLite table (§3.1)."""
    try:
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO ongoing_events
                (event_id, status, epicenter_lat, epicenter_lon, max_pga, started_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                status        = excluded.status,
                epicenter_lat = excluded.epicenter_lat,
                epicenter_lon = excluded.epicenter_lon,
                max_pga       = excluded.max_pga,
                updated_at    = excluded.updated_at
            """,
            (
                event["event_id"],
                "ACTIVE",
                event["epicenter_lat"],
                event["epicenter_lon"],
                event["max_pga"],
                datetime.fromtimestamp(event["created_at"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                datetime.fromtimestamp(event["updated_at"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.error("_upsert_ongoing_event failed: %s", exc)

def _resolve_event_in_db(event_id: str) -> None:
    """Mark an event as RESOLVED in SQLite and copy summary to laporan."""
    try:
        conn = get_db_connection()
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "UPDATE ongoing_events SET status='RESOLVED', updated_at=? WHERE event_id=?",
            (now_str, event_id),
        )
        conn.commit()
        conn.close()
        logger.info("Event %s marked RESOLVED in DB.", event_id)
    except Exception as exc:
        logger.error("_resolve_event_in_db failed: %s", exc)

# ---------------------------------------------------------------------------
# Hybrid Smart Push dispatch (§4.1)
# ---------------------------------------------------------------------------
INTENSITY_DESC_EN = {
    "lemah": "Weak", "weak": "Weak",
    "sedang": "Moderate", "moderate": "Moderate",
    "kuat": "Strong", "strong": "Strong",
    "sangat kuat": "Very Strong", "very strong": "Very Strong",
    "hebat": "Severe", "severe": "Severe",
    "sangat hebat": "Very Severe", "very severe": "Very Severe",
    "ekstrem": "Extreme", "extreme": "Extreme",
}

def _pga_to_intensity(pga_gal: float) -> str:
    """Convert PGA (gal) to an MMI-like intensity label for the push title."""
    if pga_gal >= 980:
        return "X+ (Extreme)"
    if pga_gal >= 392:
        return "IX (Violent)"
    if pga_gal >= 196:
        return "VIII (Severe)"
    if pga_gal >= 98:
        return "VII (Very Strong)"
    if pga_gal >= 49:
        return "VI (Strong)"
    if pga_gal >= 24.5:
        return "V (Moderate)"
    if pga_gal >= 9.8:
        return "IV (Light)"
    if pga_gal >= 2:
        return "III (Weak)"
    return "II (Micro)"

def _dispatch_push(event: dict, status: str = "ACTIVE") -> None:
    """
    POST a Hybrid Smart Push notification to the Ntfy server.
    All metadata (epicenter, PGA, tier, timestamp) are embedded in custom
    HTTP headers so the Android client can calculate local MMI instantly
    without a round-trip API call (§4.1 — Hybrid Smart Push).
    """
    pga        = event["max_pga"]
    intensity  = _pga_to_intensity(pga)
    tier       = event["tier"]
    event_id   = event["event_id"]
    lat        = event["epicenter_lat"]
    lon        = event["epicenter_lon"]
    ts         = event["updated_at"]  # Unix Epoch UTC

    # Title encodes intensity and tier so the client can show it without parsing body
    tier_label = "WARNING" if tier == 1 else "CONFIRMED EARTHQUAKE"
    title = f"EARTHQUAKE {tier_label} {intensity}"

    body = (
        f"Epicenter : {lat:.4f}, {lon:.4f}\n"
        f"Max PGA   : {pga:.3f} gal\n"
        f"Status    : {status}\n"
        f"Event ID  : {event_id}"
    )

    geo_tag = f"geo:{lat:.4f};{lon:.4f}"

    try:
        resp = requests.post(
            f"{NTFY_SERVER}/{NTFY_TOPIC}",
            auth=(NTFY_USER, NTFY_PASS),
            headers={
                "Title":              title.encode("utf-8"),
                "Priority":           "5",
                "Tags":               f"warning,earthquake,{geo_tag}",
                # --- Hybrid Smart Push custom headers (§4.1) ---
                "X-Event-ID":         event_id,
                "X-Epicenter-Lat":    str(lat),
                "X-Epicenter-Lon":    str(lon),
                "X-Max-PGA":          f"{pga:.4f}",
                "X-Event-Tier":       str(tier),
                "X-Status":           status,
                # UTC Unix Epoch — the Android client uses this to discard
                # out-of-order / replayed packets (§6.2 Step 1)
                "X-Server-Timestamp": str(ts),
            },
            data=body.encode("utf-8"),
            timeout=5,
        )
        if resp.status_code not in (200, 201):
            logger.warning("Ntfy returned HTTP %d for event %s", resp.status_code, event_id)
    except Exception as exc:
        logger.warning("_dispatch_push failed for %s: %s", event_id, exc)

# ---------------------------------------------------------------------------
# Clustering engine — called on every seismo/alert (§3.2)
# ---------------------------------------------------------------------------
def _handle_alert(payload: dict) -> None:
    station_id  = payload.get("id") or payload.get("stationId", "Unknown")
    t_now       = int(time.time())   # Unix Epoch UTC

    try:
        sensor_lat = float(payload.get("lat", 0))
        sensor_lon = float(payload.get("lon", 0))
        sensor_pga = float(payload.get("pga", 0))
    except (TypeError, ValueError):
        logger.warning("Malformed alert payload from %s — skipping", station_id)
        return

    matched_event_id: str | None = None

    with _events_lock:
        # --- Step 1: Try to cluster with an existing event ---
        for eid, ev in ongoing_events.items():
            d  = haversine_km(sensor_lat, sensor_lon, ev["epicenter_lat"], ev["epicenter_lon"])
            dt = t_now - ev["created_at"]

            cond_a = d <= CLUSTER_BASE_RADIUS_KM
            cond_b = d <= CLUSTER_WAVE_RADIUS_KM and dt <= CLUSTER_WAVE_TIME_SEC

            if cond_a or cond_b:
                # Cluster match — update the event
                ev["sensors"][station_id] = {
                    "lat":          sensor_lat,
                    "lon":          sensor_lon,
                    "pga":          sensor_pga,
                    "triggered_at": t_now,
                }
                ev["max_pga"]    = max(ev["max_pga"], sensor_pga)
                ev["updated_at"] = t_now

                # Tier escalation: promote to Tier 2 if second sensor within 30 s
                sensor_count = len(ev["sensors"])
                if ev["tier"] == 1 and sensor_count >= 2 and dt <= TIER2_ESCALATION_TIME_SEC:
                    ev["tier"] = 2
                    logger.info("Event %s escalated to Tier 2 (Confirmed).", eid)

                matched_event_id = eid
                _upsert_ongoing_event(ev)
                event_snapshot = dict(ev)
                break

        # --- Step 2: No match — spawn a new independent event ---
        if matched_event_id is None:
            new_id = _make_event_id()
            new_event = {
                "event_id":      new_id,
                "epicenter_lat": sensor_lat,
                "epicenter_lon": sensor_lon,
                "max_pga":       sensor_pga,
                "tier":          1,
                "sensors": {
                    station_id: {
                        "lat":          sensor_lat,
                        "lon":          sensor_lon,
                        "pga":          sensor_pga,
                        "triggered_at": t_now,
                    }
                },
                "created_at":    t_now,
                "updated_at":    t_now,
            }
            ongoing_events[new_id] = new_event
            event_snapshot = dict(new_event)
            matched_event_id = new_id
            _upsert_ongoing_event(new_event)
            logger.info("New event spawned: %s (Tier 1 Sentinel).", new_id)

    # Dispatch push OUTSIDE the lock to avoid holding it during HTTP I/O
    _dispatch_push(event_snapshot, status="ACTIVE")

# ---------------------------------------------------------------------------
# Report handler — called on seismo/report (§3.3 Active Resolution)
# ---------------------------------------------------------------------------
def _handle_report(payload: dict) -> None:
    station_id = payload.get("id") or payload.get("stationId", "Unknown")

    with _events_lock:
        # Find the event this sensor belongs to
        matched_id = None
        for eid, ev in ongoing_events.items():
            if station_id in ev["sensors"]:
                matched_id = eid
                break

        if matched_id is None:
            logger.info("Report from %s: no matching active event — saving directly.", station_id)
        else:
            # Mark resolved and remove from in-memory cache
            event_snapshot = dict(ongoing_events.pop(matched_id))

    if matched_id:
        _resolve_event_in_db(matched_id)
        event_snapshot["updated_at"] = int(time.time())
        event_snapshot["max_pga"]    = max(
            event_snapshot["max_pga"],
            float(payload.get("pga_max") or payload.get("pga", 0)),
        )
        _dispatch_push(event_snapshot, status="RESOLVED")

    # Forward final report data to Flask /laporan for history storage
    report_body = {
        "stationId":  station_id,
        "lokasi":     payload.get("lokasi", "Unknown"),
        "waktu":      payload.get("waktu_mulai") or payload.get("waktu", ""),
        "durasi":     float(payload.get("durasi", 0)),
        "pga":        float(payload.get("pga_max") or payload.get("pga", 0)),
        "intensitas": payload.get("intensitas_max") or payload.get("intensitas", "N/A"),
        "lat":        payload.get("lat"),
        "lon":        payload.get("lon"),
    }
    try:
        requests.post(REPORT_ENDPOINT, json=report_body, headers=REPORT_HEADERS, timeout=5)
    except Exception as exc:
        logger.warning("Failed to save report to Flask: %s", exc)

# ---------------------------------------------------------------------------
# Deadman's switch thread (§3.3)
# Runs every DEADMAN_POLL_INTERVAL_SEC seconds; force-resolves events that
# have not been updated within DEADMAN_TIMEOUT_SEC.
# ---------------------------------------------------------------------------
def _deadman_worker() -> None:
    logger.info("Deadman's switch thread started.")
    while True:
        time.sleep(DEADMAN_POLL_INTERVAL_SEC)
        now = int(time.time())
        stale_ids: list[str] = []

        with _events_lock:
            for eid, ev in ongoing_events.items():
                if now - ev["updated_at"] > DEADMAN_TIMEOUT_SEC:
                    stale_ids.append(eid)

            for sid in stale_ids:
                ev = ongoing_events.pop(sid)
                logger.warning("Deadman: force-resolving stale event %s (no update for >%ds).", sid, DEADMAN_TIMEOUT_SEC)
                _resolve_event_in_db(sid)
                ev["updated_at"] = now
                _dispatch_push(ev, status="RESOLVED")

# ---------------------------------------------------------------------------
# Heartbeat / status handler — updates station health in Flask
# ---------------------------------------------------------------------------
sensors_inventory: dict = {}

def _handle_heartbeat(payload: dict) -> None:
    station_id = payload.get("id") or payload.get("stationId", "Unknown")

    # Server-side geocoding (only once per station lifetime)
    _generic_labels = {"community node", "unknown", ""}
    raw_lokasi = str(payload.get("lokasi", "")).lower()
    if raw_lokasi in _generic_labels:
        raw_lat = str(payload.get("lat", "0"))
        raw_lon = str(payload.get("lon", "0"))
        if raw_lat not in ("0", "") and raw_lon not in ("0", ""):
            if station_id not in sensors_inventory:
                sensors_inventory[station_id] = {}
            cached = sensors_inventory[station_id].get("resolved_lokasi")
            if cached:
                payload["lokasi"] = cached
            else:
                resolved = reverse_geocode(raw_lat, raw_lon)
                sensors_inventory[station_id]["resolved_lokasi"] = resolved
                payload["lokasi"] = resolved

    payload["last_seen"] = int(time.time())
    existing = sensors_inventory.get(station_id, {})
    existing.update(payload)
    sensors_inventory[station_id] = existing

    # Forward to Flask /heartbeat for the station-status endpoint
    try:
        heartbeat_url = f"{_BASE_URL}/heartbeat"
        r = requests.post(heartbeat_url, json=payload, headers=REPORT_HEADERS, timeout=2)
        if r.status_code != 200:
            logger.warning("Heartbeat forward returned HTTP %d", r.status_code)
    except Exception as exc:
        logger.warning("Failed to forward heartbeat: %s", exc)

# ---------------------------------------------------------------------------
# Memory-leak cleanup for sensors_inventory (unchanged from v1)
# ---------------------------------------------------------------------------
def _cleanup_worker() -> None:
    while True:
        time.sleep(300)
        now = int(time.time())
        stale = [sid for sid, d in sensors_inventory.items() if now - d.get("last_seen", 0) > 7200]
        for sid in stale:
            del sensors_inventory[sid]
        if stale:
            logger.info("Purged %d dead sensors from inventory.", len(stale))

# ---------------------------------------------------------------------------
# MQTT message dispatcher
# ---------------------------------------------------------------------------
message_queue: queue.Queue = queue.Queue(maxsize=2000)

def _message_worker() -> None:
    while True:
        try:
            client, userdata, msg = message_queue.get()
            if msg is None:
                break
            _process_message(client, userdata, msg)
        except Exception as exc:
            logger.error("message_worker error: %s", exc)
        finally:
            message_queue.task_done()

def _process_message(client, userdata, msg) -> None:
    payload_str = msg.payload.decode("utf-8")

    # Ignore raw non-JSON ping strings on command topic
    if msg.topic == "seismo/command" and payload_str.strip() == "ping":
        return

    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError:
        logger.warning("Non-JSON payload on %s — ignoring.", msg.topic)
        return

    topic = msg.topic

    if topic in ("seismo/heartbeat", "seismo/status"):
        _handle_heartbeat(payload)

    elif topic == "seismo/alert":
        _handle_alert(payload)

    elif topic == "seismo/report":
        _handle_report(payload)

    elif topic == "seismo/command":
        cmd = payload.get("cmd")
        if cmd == "get_status":
            report = {
                "timestamp":      int(time.time()),
                "total_sensors":  len(sensors_inventory),
                "active_events":  len(ongoing_events),
                "sensors":        list(sensors_inventory.values()),
            }
            client.publish("seismo/status_report", json.dumps(report))

# ---------------------------------------------------------------------------
# MQTT callbacks
# ---------------------------------------------------------------------------
def on_connect(client, userdata, flags, rc, properties):
    if rc == 0:
        logger.info("Connected to MQTT broker (%s:%d).", MQTT_BROKER, MQTT_PORT)
        client.subscribe([
            ("seismo/alert",     1),
            ("seismo/report",    1),
            ("seismo/status",    1),
            ("seismo/command",   1),
            ("seismo/heartbeat", 0),
        ])
    else:
        logger.error("Failed to connect to MQTT broker, rc=%d.", rc)

def on_message(client, userdata, msg):
    try:
        message_queue.put_nowait((client, userdata, msg))
    except queue.Full:
        logger.warning("Message queue full — dropped message on %s.", msg.topic)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)

    # Start background threads
    threading.Thread(target=_message_worker, daemon=True, name="MsgWorker").start()
    threading.Thread(target=_deadman_worker, daemon=True, name="DeadmanSwitch").start()
    threading.Thread(target=_cleanup_worker, daemon=True, name="CleanupWorker").start()

    # MQTT client (Callback API v2)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message

    if MQTT_USER and MQTT_PASSWORD:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

    logger.info("QuakeAlert Bridge v2 starting...")
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        client.loop_forever()
    except KeyboardInterrupt:
        logger.info("Stopping bridge.")
        client.disconnect()
    except Exception as exc:
        logger.critical("Fatal error: %s", exc)
        sys.exit(1)
