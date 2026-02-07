import paho.mqtt.client as mqtt
import requests
import json
import telegram
import asyncio
import threading
import os
import sys
import urllib3

# Suppress InsecureRequestWarning if using self-signed certs/local http
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
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- ASYNCIO SETUP ---
# Create a dedicated loop for Asyncio (Telegram)
asyncio_loop = asyncio.new_event_loop()

def start_asyncio_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

if not TELEGRAM_BOT_TOKEN:
    print("Error: TELEGRAM_BOT_TOKEN is missing!")
    sys.exit(1)

bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"===> Connected to MQTT Broker ({MQTT_BROKER})!")
        client.subscribe([("seismo/alert", 0), ("seismo/report", 0), ("seismo/status", 0)])
    else:
        print(f"===> Failed to connect to Broker, return code: {rc}")

def on_message(client, userdata, msg):
    station_id = "Unknown" # Initialize here to prevent UnboundLocalError
    try:
        print(f"Msg received on: {msg.topic}")
        payload_string = msg.payload.decode('utf-8')
        payload = json.loads(payload_string)

        station_id = payload.get('stationId', 'Unknown')

        # 1. STATION HEALTH
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
                print(f"Error posting status to NTFY: {e}")

            if payload.get("event") == "startup":
                lokasi = payload.get('lokasi', 'N/A')
                version = payload.get('version', 'N/A')
                message_text = (
                    f"✅ *Sensor Online: {station_id}*\n\n"
                    f"Lokasi: {lokasi}\n"
                    f"Versi Firmware: {version}"
                )
                # Thread-safe async call
                asyncio.run_coroutine_threadsafe(
                    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message_text, parse_mode='Markdown'),
                    asyncio_loop
                )

        # 2. EARTHQUAKE ALERTS
        elif msg.topic == "seismo/alert":
            lokasi = payload.get("lokasi", "N/A")
            waktu = payload.get("waktu", "N/A")
            intensitas = payload.get("intensitas", "N/A")

            title = f"🚨 PERINGATAN ({intensitas}) - {station_id}"
            message_body = f"Station: {station_id}\nLokasi: {lokasi}\nWaktu: {waktu}\nIntensitas: {intensitas}"

            # Ntfy
            try:
                requests.post(
                    f"{NTFY_SERVER}/{NTFY_TOPIC}",
                    auth=(NTFY_USER, NTFY_PASS),
                    headers={"Title": title.encode('utf-8'), "Priority": "max", "Tags": "warning,earthquake"},
                    data=message_body.encode('utf-8'),
                    verify=False,
                    timeout=5
                )
            except Exception as e:
                print(f"Error sending Alert to NTFY: {e}")

            # Telegram
            tele_msg = f"🚨 *PERINGATAN GEMPA*\n\n{message_body}"
            asyncio.run_coroutine_threadsafe(
                bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=tele_msg, parse_mode='Markdown'),
                asyncio_loop
            )

        # 3. DATA REPORTS
        elif msg.topic == "seismo/report":
            try:
                response = requests.post(REPORT_ENDPOINT, json=payload, timeout=10)
                print(f"Report from {station_id} saved to DB. Code: {response.status_code}")
            except Exception as e:
                print(f"Failed to save report to DB: {e}")

    except json.JSONDecodeError:
        print(f"!!! Error: Could not decode JSON from {msg.topic}")
    except Exception as e:
        print(f"!!! CRITICAL ERROR processing message from {station_id}: {e}")

# --- STARTUP ---
# Start the asyncio loop in a separate thread for Telegram
asyncio_thread = threading.Thread(target=start_asyncio_loop, args=(asyncio_loop,), daemon=True)
asyncio_thread.start()

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message
if MQTT_USER and MQTT_PASSWORD:
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

print(">> Bridge Script Running...")
print(f">> Connecting to {MQTT_BROKER}:{MQTT_PORT}...")

# Blocking loop for MQTT (runs on main thread)
try:
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_forever()
except KeyboardInterrupt:
    print("Stopping...")
    client.disconnect()
except Exception as e:
    print(f"Fatal Error: {e}")
