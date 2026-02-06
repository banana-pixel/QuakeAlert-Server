import paho.mqtt.client as mqtt
import requests
import json
import telegram
import asyncio
import threading
import os
import sys

# --- KONFIGURASI DARI ENVIRONMENT VARIABLES ---
# Default values are for fallback only
MQTT_BROKER = os.getenv("MQTT_BROKER", "mosquitto") # Connects to docker container name
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER", "guest")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "guest")

NTFY_SERVER = os.getenv("NTFY_SERVER", "http://ntfy")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "test_topic")
NTFY_USER = os.getenv("NTFY_USER", "guest")
NTFY_PASS = os.getenv("NTFY_PASS", "guest")

REPORT_ENDPOINT = os.getenv("REPORT_ENDPOINT", "http://localhost/laporan")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- ASYNCIO SETUP ---
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
        print("===> Berhasil terhubung ke Broker MQTT!")
        client.subscribe([("seismo/alert", 0), ("seismo/report", 0), ("seismo/status", 0)])
    else:
        print(f"===> Gagal terhubung ke Broker, return code: {rc}")

def on_message(client, userdata, msg):
    try:
        print(f"Pesan diterima di topik: {msg.topic}")
        payload_string = msg.payload.decode('utf-8')
        payload = json.loads(payload_string)

        if msg.topic == "seismo/status" and payload.get("event") == "startup":
            station_id = payload.get('stationId', 'N/A')
            lokasi = payload.get('lokasi', 'N/A')
            version = payload.get('version', 'N/A')

            message_text = (
                f"✅ *Sensor Online: {station_id}*\n\n"
                f"Lokasi: {lokasi}\n"
                f"Versi Firmware: {version}"
            )

            coro = bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message_text, parse_mode='Markdown')
            future = asyncio.run_coroutine_threadsafe(coro, asyncio_loop)

            try:
                future.result(timeout=10)
                print(f"Notifikasi startup untuk {station_id} dikirim ke Telegram.")
            except Exception as e:
                print(f"!!! GAGAL MENGIRIM TELEGRAM: {e}")

        elif msg.topic == "seismo/alert":
            lokasi = payload.get("lokasi", "N/A")
            waktu = payload.get("waktu", "N/A")
            intensitas = payload.get("intensitas", "N/A")
            title = f"🚨 PERINGATAN GETARAN ({intensitas})"
            message_body = f"Lokasi: {lokasi}\nWaktu: {waktu}\nIntensitas: {intensitas}"

            # Send to Ntfy
            requests.post(
                f"{NTFY_SERVER}/{NTFY_TOPIC}",
                auth=(NTFY_USER, NTFY_PASS),
                headers={"Title": title.encode('utf-8'), "Priority": "max", "Tags": "warning,earthquake"},
                data=message_body.encode('utf-8'),
                verify=False
            )
            print("Notifikasi peringatan (alert) dikirim ke Ntfy.")

            # Send to Telegram
            tele_msg = f"🚨 *PERINGATAN GEMPA*\n\n{message_body}"
            coro = bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=tele_msg, parse_mode='Markdown')
            asyncio.run_coroutine_threadsafe(coro, asyncio_loop)

        elif msg.topic == "seismo/report":
            try:
                # Send to Report Server (Database)
                response = requests.post(REPORT_ENDPOINT, json=payload)
                print(f"Laporan dikirim ke DB. Status: {response.status_code}")
            except Exception as e:
                print(f"Gagal menyimpan laporan ke DB: {e}")

    except Exception as e:
        print(f"!!! KESALAHAN: {e}")

# --- STARTUP ---
asyncio_thread = threading.Thread(target=start_asyncio_loop, args=(asyncio_loop,), daemon=True)
asyncio_thread.start()

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message
client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

# Wait for container network to be ready
import time
time.sleep(5)
client.connect_async(MQTT_BROKER, MQTT_PORT, 60)

print(">> Skrip jembatan (Containerized) berjalan...")
client.loop_forever()
