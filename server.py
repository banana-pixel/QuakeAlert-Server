from flask import Flask, request, jsonify
from datetime import datetime, timezone
from flask_cors import CORS
import sqlite3
import os
import re

app = Flask(__name__)
# Restrict CORS to our domain only (Android app is not browser-bound; CORS applies to web clients)
CORS(app, origins=["https://quakealert.bananapixel.my.id"])

# --- SETUP PATHS ---
# Ensure data folder is relative to the script location
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DB_FILE = os.path.join(DATA_DIR, "laporan_gempa.db")
PAGE_SIZE = 20  # reports per page for GET /laporan

# API key for write endpoints (POST /laporan, POST /heartbeat). Set via env REPORT_API_KEY.
REPORT_API_KEY = os.getenv("REPORT_API_KEY", "").strip()

def _normalize_latency(val):
    """Normalize latency to int (ms) for storage. ESP32 may send e.g. '123 ms' or a number."""
    if val is None:
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        m = re.search(r'\d+', val)
        return int(m.group()) if m else None
    return None


def _require_api_key():
    """Return (response, status_code) if request is unauthorized; else None."""
    if not REPORT_API_KEY:
        return jsonify({"error": "Server misconfiguration: REPORT_API_KEY not set"}), 503
    key = request.headers.get("X-API-Key") or request.headers.get("Authorization", "").replace("Bearer ", "")
    if key != REPORT_API_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    return None

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
    print(f"Created data directory at: {DATA_DIR}")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Updated table schema to include latitude and longitude
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS laporan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id TEXT NOT NULL,
            lokasi TEXT NOT NULL,
            waktu_kejadian TEXT NOT NULL,
            durasi REAL NOT NULL,
            pga_maks REAL NOT NULL,
            intensitas_maks INTEGER NOT NULL,
            deskripsi TEXT NOT NULL,
            latitude REAL,
            longitude REAL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stations (
            station_id TEXT PRIMARY KEY,
            last_ping TEXT NOT NULL,
            latency INTEGER,
            RSSI INTEGER,
            status TEXT,
            location TEXT
        )
    ''')
    conn.commit()
    conn.close()
    print("Database initialized.")

# --- ROUTES ---

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "running", "service": "QuakeAlert Database"}), 200

@app.route('/laporan', methods=['POST'])
def tambah_laporan():
    err = _require_api_key()
    if err:
        return err
    if not request.is_json:
        return jsonify({"error": "Invalid JSON"}), 400
    data = request.get_json()

    try:
        # 'deskripsi' is optional; app derives it from intensitas for localization
        required_keys = ['stationId', 'lokasi', 'waktu', 'durasi', 'pga', 'intensitas']
        if not all(key in data for key in required_keys):
            return jsonify({"status": "gagal", "error": "Missing JSON keys"}), 400

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        # Insert latitude and longitude (defaulting to None/NULL if not provided)
        cursor.execute(
            """INSERT INTO laporan (station_id, lokasi, waktu_kejadian, durasi, pga_maks, 
               intensitas_maks, deskripsi, latitude, longitude) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data['stationId'], data['lokasi'], data['waktu'], data['durasi'],
             data['pga'], data['intensitas'], '',
             data.get('lat'), data.get('lon'))
        )
        conn.commit()
        conn.close()
        return jsonify({"status": "sukses"}), 201
    except Exception as e:
        print(f"Error inserting data: {e}")
        return jsonify({"status": "gagal", "error": str(e)}), 400

@app.route('/laporan', methods=['GET'])
def dapatkan_laporan():
    try:
        page = request.args.get('page', 1, type=int)
        if page < 1:
            page = 1
        offset = (page - 1) * PAGE_SIZE

        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM laporan ORDER BY id DESC LIMIT ? OFFSET ?",
            (PAGE_SIZE, offset)
        )
        laporan_rows = cursor.fetchall()
        conn.close()

        laporan_list = [dict(row) for row in laporan_rows]
        return jsonify(laporan_list)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- KEEP ALIVE ROUTES ---

@app.route('/heartbeat', methods=['POST'])
def receive_heartbeat():
    err = _require_api_key()
    if err:
        return err
    if not request.is_json:
        return jsonify({"error": "Invalid JSON"}), 400
    data = request.get_json()
    station_id = data.get('stationId') # Note: matching the JSON key from firmware

    latency = _normalize_latency(data.get('latency'))

    rssi = data.get('rssi')
    
    location = data.get('lokasi', 'Unknown')
    
    if not station_id:
        return jsonify({"error": "Missing stationId"}), 400

    # Use server time for consistency
    current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        # Upsert: Insert or Update if exists
        cursor.execute('''
            INSERT INTO stations (station_id, last_ping, latency, RSSI, location, status)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(station_id) DO UPDATE SET
            last_ping=excluded.last_ping,
            latency=excluded.latency,
            RSSI=excluded.RSSI,
            location=excluded.location,
            status='online'
        ''', (station_id, current_time, latency, rssi, location, 'online'))
        conn.commit()
        conn.close()
        return jsonify({"status": "updated"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/stations', methods=['GET'])
def get_stations_status():
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM stations")
        rows = cursor.fetchall()
        conn.close()

        results = []
        now = datetime.now(timezone.utc)
        # Offline threshold: mark station offline if no heartbeat in this many seconds
        OFFLINE_THRESHOLD_SEC = 120

        for row in rows:
            data = dict(row)
            try:
                # 1. Parse the text string from DB (stored as UTC) back to a datetime object
                last_ping_dt = datetime.strptime(data['last_ping'], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                # 2. Convert to Unix timestamp (integer) for the app
                data['last_ping'] = int(last_ping_dt.timestamp())
                # 3. Mark offline if last heartbeat too old
                diff = now - last_ping_dt
                if diff.total_seconds() > OFFLINE_THRESHOLD_SEC:
                    data['status'] = 'offline'
                # 4. Ensure latency and RSSI are strings for the app (Sensor model expects String?)
                if data.get('latency') is not None and not isinstance(data['latency'], str):
                    data['latency'] = str(data['latency'])
                if data.get('RSSI') is not None and not isinstance(data['RSSI'], str):
                    data['RSSI'] = str(data['RSSI'])
            except Exception as e:
                print(f"Error parsing date: {e}")
                data['status'] = 'unknown'
                data['last_ping'] = 0

            results.append(data)

        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/admin/cleanup-test', methods=['POST'])
def cleanup_test_data():
    """Remove test rows (e.g. station_id='x', lokasi='x') from laporan. Requires API key."""
    err = _require_api_key()
    if err:
        return err
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM laporan WHERE station_id = ? OR lokasi = ?", ('x', 'x'))
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        return jsonify({"status": "ok", "deleted": deleted}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    init_db()
    # Host 0.0.0.0 is required if running in Docker
    app.run(host='0.0.0.0', port=5000)
