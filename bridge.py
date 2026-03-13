import paho.mqtt.client as mqtt
import requests
import json
import telegram
import asyncio
import threading
import os
import sys
import urllib3
import time
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Suppress InsecureRequestWarning
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- CONFIGURATION ---
MQTT_BROKER = os.getenv("MQTT_BROKER", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER", "guest")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "guest")

NTFY_SERVER = os.getenv("NTFY_SERVER", "http://ntfy")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "test_topic")
NTFY_USER = os.getenv("NTFY_USER", "guest")
NTFY_PASS = os.getenv("NTFY_PASS", "guest")

REPORT_ENDPOINT = os.getenv("REPORT_ENDPOINT", "http://localhost:5000/laporan")
REPORT_API_KEY = os.getenv("REPORT_API_KEY", "").strip()
REPORT_HEADERS = {"X-API-Key": REPORT_API_KEY} if REPORT_API_KEY else {}

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Memory to track online sensors for get_status command
sensors_inventory = {}

# --- REVERSE GEOCODING HELPER ---
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_NOMINATIM_HEADERS = {"User-Agent": "QuakeAlert-Server/1.0"}

def reverse_geocode(lat: str, lon: str) -> str:
    """Resolve lat/lon to a human-readable city name via Nominatim.

    Returns a formatted string like "Depok, Jawa Barat" or
    "Unknown Region" on any failure (timeout, bad response, missing fields).
    Never raises — callers must not crash on geocode failures.
    """
    try:
        r = requests.get(
            _NOMINATIM_URL,
            params={"lat": lat, "lon": lon, "format": "json"},
            headers=_NOMINATIM_HEADERS,
            timeout=3,
        )
        r.raise_for_status()
        data = r.json()
        addr = data.get("address", {})
        # Prefer the most specific populated-place name available, cascade
        # from village → suburb → city_district → town → city → county.
        place = (
            addr.get("village")
            or addr.get("suburb")
            or addr.get("city_district")
            or addr.get("town")
            or addr.get("city")
            or addr.get("county")
        )
        state = addr.get("state") or addr.get("province") or ""
        if place and state:
            return f"{place}, {state}"
        if place:
            return place
        # Last resort: fall back to the raw display name truncated to 60 chars
        display = data.get("display_name", "")
        if display:
            return display[:60]
    except Exception as exc:
        logger.warning("reverse_geocode(%s, %s) failed: %s", lat, lon, exc)
    return "Unknown Region"


asyncio_loop = asyncio.new_event_loop()

def start_asyncio_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

if not TELEGRAM_BOT_TOKEN:
    print("Error: TELEGRAM_BOT_TOKEN is missing!")
    sys.exit(1)

bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)

# --- MQTT CALLBACKS (UPDATED TO V2) ---
def on_connect(client, userdata, flags, rc, properties):
    if rc == 0:
        print(f"===> Connected to MQTT Broker ({MQTT_BROKER})!")
        # Subscribe to ALL topics
        client.subscribe([
            ("seismo/alert", 0), 
            ("seismo/report", 0), 
            ("seismo/status", 0),
            ("seismo/command", 0),
            ("seismo/heartbeat", 0)
        ])
    else:
        print(f"===> Failed to connect to Broker, return code: {rc}")

def on_message(client, userdata, msg):
    global sensors_inventory
    station_id = "Unknown"
    payload_string = msg.payload.decode('utf-8')

    # Ignore raw string commands like 'ping'
    if msg.topic == "seismo/command" and payload_string == "ping":
        return

    try:
        payload = json.loads(payload_string)
        station_id = payload.get('stationId', payload.get('id', 'Unknown'))

        # --- THICK CLOUD: SERVER-SIDE LOCATION ENRICHMENT ---
        # The ESP32 sends lokasi="Community Node" to protect privacy.
        # The server intercepts masked coordinates here ONCE per station and
        # resolves them to a real city via Nominatim. The result is cached in
        # sensors_inventory so we call the API exactly once per station
        # lifetime, making us immune to Nominatim rate-limit bans.
        _generic_labels = {"community node", "unknown", ""}
        raw_lokasi = str(payload.get("lokasi", "")).lower()
        if raw_lokasi in _generic_labels:
            raw_lat = str(payload.get("lat", "0"))
            raw_lon = str(payload.get("lon", "0"))
            if raw_lat != "0" and raw_lon != "0":
                # Ensure an inventory slot exists before reading/writing cache
                if station_id not in sensors_inventory:
                    sensors_inventory[station_id] = {}
                cached = sensors_inventory[station_id].get("resolved_lokasi")
                if cached:
                    payload["lokasi"] = cached
                else:
                    resolved = reverse_geocode(raw_lat, raw_lon)
                    sensors_inventory[station_id]["resolved_lokasi"] = resolved
                    payload["lokasi"] = resolved
                    logger.info(
                        "Geocoded station %s → %s", station_id, resolved
                    )
        # payload["lokasi"] is now enriched for all downstream consumers
        # (Telegram, NTFY, SQLite) without any further changes required.

        if msg.topic == "seismo/status" or msg.topic == "seismo/heartbeat":
            payload["last_seen"] = int(time.time())
            # IMPORTANT: Merge rather than overwrite so that the geocode cache
            # key "resolved_lokasi" (written above) is never silently wiped by
            # an incoming heartbeat replacing the entire inventory slot.
            existing = sensors_inventory.get(station_id, {})
            existing.update(payload)
            sensors_inventory[station_id] = existing
            try:
                # Construct URL (assumes report server is on port 5000)
                # If REPORT_ENDPOINT is "http://localhost:5000/laporan", we want "http://localhost:5000/heartbeat"
                base_url = REPORT_ENDPOINT.rsplit('/', 1)[0]
                heartbeat_url = f"{base_url}/heartbeat"
                
                # Forward the payload directly (with API key if set)
                r = requests.post(heartbeat_url, json=payload, headers=REPORT_HEADERS, timeout=2)
                if r.status_code != 200:
                    print(f"Failed to forward heartbeat: HTTP {r.status_code} — check REPORT_API_KEY if 401")
            except Exception as e:
                print(f"Failed to forward heartbeat: {e}")
            
            # Forward status if needed
            if msg.topic == "seismo/status":
                try:
                    requests.post(
                        f"{NTFY_SERVER}/seismo_status",
                        auth=(NTFY_USER, NTFY_PASS),
                        data=payload_string.encode('utf-8'),
                        verify=False,
                        timeout=5
                    )
                except Exception as e:
                    logger.warning("Ntfy forward failed for %s: %s", station_id, e)

            if payload.get("event") == "startup":
                lokasi = payload.get('lokasi', 'N/A')
                version = payload.get('version', 'N/A')
                message_text = (
                    f"✅ *Sensor Online: {station_id}*\n\n"
                    f"Lokasi: {lokasi}\n"
                    f"Versi Firmware: {version}"
                )
                asyncio.run_coroutine_threadsafe(
                    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message_text, parse_mode='Markdown'),
                    asyncio_loop
                )

        # 2. EARTHQUAKE ALERTS (IMMEDIATE WARNING)
        elif msg.topic == "seismo/alert":
            lokasi = payload.get("lokasi", "N/A")
            waktu = payload.get("waktu", "N/A")
            intensitas_raw = payload.get("intensitas", "N/A")  # e.g. "VI (Kuat)" or "IX (Hebat)"

            event_lat = str(payload.get("lat", "0"))
            event_lon = str(payload.get("lon", "0"))

            intensity_short = intensitas_raw.split(' ')[0]  # "VI" or "X+"
            intensity_desc_id = intensitas_raw
            if '(' in intensitas_raw:
                try:
                    intensity_desc_id = intensitas_raw.split('(')[1].replace(')', '').strip()
                except Exception:
                    pass

            # Map Indonesian descriptors to English
            INTENSITY_DESC_EN = {
                "lemah": "Weak", "weak": "Weak",
                "sedang": "Moderate", "moderate": "Moderate",
                "kuat": "Strong", "strong": "Strong",
                "sangat kuat": "Very Strong", "very strong": "Very Strong",
                "hebat": "Severe", "severe": "Severe",
                "sangat hebat": "Very Severe", "very severe": "Very Severe",
                "ekstrem": "Extreme", "extreme": "Extreme",
            }
            intensity_desc_en = INTENSITY_DESC_EN.get(intensity_desc_id.lower(), intensity_desc_id.title())

            # Title: no emoji (ntfy "warning" tag adds one); English only
            title = f"EARTHQUAKE WARNING {intensity_desc_en.upper()} (INTENSITY {intensity_short})"

            # Body: English labels and English intensity descriptor
            intensitas_en = f"{intensity_short} ({intensity_desc_en})"
            message_body = (
                f"Station : {station_id}\n"
                f"Location : {lokasi}\n"
                f"Time: {waktu} UTC\n"
                f"Intensity : {intensitas_en}"
            )

            # Ntfy Warning (High Priority)
            try:
                geo_tag = f"geo:{event_lat};{event_lon}"

                requests.post(
                    f"{NTFY_SERVER}/{NTFY_TOPIC}",
                    auth=(NTFY_USER, NTFY_PASS),
                    headers={
                        "Title": title.encode('utf-8'),
                        "Priority": "5",
                        "Tags": f"warning,earthquake,{geo_tag}"
                    },
                    data=message_body.encode('utf-8'),
                    verify=False,
                    timeout=5
                )
            except Exception as e:
                print(f"Error sending Alert to NTFY: {e}")

            # Telegram
            tele_msg = f"*{title}*\n\n{message_body}"
            asyncio.run_coroutine_threadsafe(
                bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=tele_msg, parse_mode='Markdown'),
                asyncio_loop
            )
            
            try:
                requests.post(REPORT_ENDPOINT, json=payload, headers=REPORT_HEADERS, timeout=5)
            except Exception as e:
                print(f"Failed to save alert to DB: {e}")

        # 3. FINAL REPORTS (DB ONLY)
        elif msg.topic == "seismo/report":
            # Save to Database
            try:
                response = requests.post(REPORT_ENDPOINT, json=payload, headers=REPORT_HEADERS, timeout=10)
                print(f"Report from {station_id} saved to DB. Code: {response.status_code}")
            except Exception as e:
                print(f"Failed to save report to DB: {e}")

        # 4. COMMAND HANDLER
        elif msg.topic == "seismo/command":
            if payload.get("cmd") == "get_status":
                report = {
                    "timestamp": int(time.time()),
                    "total_sensors": len(sensors_inventory),
                    "sensors": list(sensors_inventory.values())
                }
                client.publish("seismo/status_report", json.dumps(report))

    except json.JSONDecodeError:
        print(f"!!! Error: Could not decode JSON from {msg.topic}")
    except Exception as e:
        print(f"!!! CRITICAL ERROR processing message from {station_id}: {e}")

# --- STARTUP ---
asyncio_thread = threading.Thread(target=start_asyncio_loop, args=(asyncio_loop,), daemon=True)
asyncio_thread.start()

# Initialize with Callback API V2
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message

if MQTT_USER and MQTT_PASSWORD:
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

print(">> Bridge Script Running (V2 Ready)...")
try:
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_forever()
except KeyboardInterrupt:
    print("Stopping...")
    client.disconnect()
except Exception as e:
    print(f"Fatal Error: {e}")
