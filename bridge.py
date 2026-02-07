import paho.mqtt.client as mqtt
import requests
import json
import telegram
import asyncio
import threading
import os
import sys
import urllib3
from datetime import datetime

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
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Memory to track online sensors for get_status command
sensors_inventory = {}

# --- ASYNCIO SETUP FOR TELEGRAM ---
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

        # 1. STATION HEALTH & HEARTBEAT
        if msg.topic == "seismo/status" or msg.topic == "seismo/heartbeat":
            payload["last_seen"] = datetime.now().strftime("%H:%M:%S")
            sensors_inventory[station_id] = payload
            
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
                except: pass

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
            intensitas = payload.get("intensitas", "N/A") # e.g. "VI (Kuat)"

            event_lat = str(payload.get("lat", "0"))
            event_lon = str(payload.get("lon", "0"))

            intensity_short = intensitas.split(' ')[0] # "VI"
            intensity_desc = intensitas
            if '(' in intensitas:
                # Extract text inside parenthesis e.g. "Kuat"
                try:
                    intensity_desc = intensitas.split('(')[1].replace(')', '') 
                except:
                    intensity_desc = intensitas

            title = f"⚠️ PERINGATAN GEMPA {intensity_desc.upper()} (INTENSITY {intensity_short})"
            
            # Body Format:
            # Station : SEIS-01
            # Lokasi : ...
            message_body = (
                f"Station : {station_id}\n"
                f"Lokasi : {lokasi}\n"
                f"Waktu: {waktu}\n"
                f"Intensitas : {intensitas}"
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
            
            # Also save alert event to DB
            try:
                requests.post(REPORT_ENDPOINT, json=payload, timeout=5)
            except: pass

        # 3. FINAL REPORTS (DB ONLY)
        elif msg.topic == "seismo/report":
            # Save to Database
            try:
                response = requests.post(REPORT_ENDPOINT, json=payload, timeout=10)
                print(f"Report from {station_id} saved to DB. Code: {response.status_code}")
            except Exception as e:
                print(f"Failed to save report to DB: {e}")

        # 4. COMMAND HANDLER
        elif msg.topic == "seismo/command":
            if payload.get("cmd") == "get_status":
                report = {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
