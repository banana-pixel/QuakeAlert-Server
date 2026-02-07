from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import json
import os
import sys

app = Flask(__name__)
CORS(app)

# --- SETUP PATHS ---
# Ensure data folder is relative to the script location
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DB_FILE = os.path.join(DATA_DIR, "laporan_gempa.db")

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
    print(f"Created data directory at: {DATA_DIR}")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS laporan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id TEXT NOT NULL,
            lokasi TEXT NOT NULL,
            waktu_kejadian TEXT NOT NULL,
            durasi REAL NOT NULL,
            pga_maks TEXT NOT NULL,
            intensitas_maks TEXT NOT NULL,
            deskripsi TEXT NOT NULL
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
    data = request.json
    try:
        # Validate required keys exists to prevent internal server error
        required_keys = ['stationId', 'lokasi', 'waktu', 'durasi', 'pga', 'intensitas', 'deskripsi']
        if not all(key in data for key in required_keys):
            return jsonify({"status": "gagal", "error": "Missing JSON keys"}), 400

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO laporan (station_id, lokasi, waktu_kejadian, durasi, pga_maks, intensitas_maks, deskripsi) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (data['stationId'], data['lokasi'], data['waktu'], data['durasi'], data['pga'], data['intensitas'], data['deskripsi'])
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
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM laporan ORDER BY id DESC LIMIT 50") # Added LIMIT for safety
        laporan_rows = cursor.fetchall()
        conn.close()

        laporan_list = [dict(row) for row in laporan_rows]
        return jsonify(laporan_list)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    init_db()
    # Host 0.0.0.0 is required if running in Docker
    app.run(host='0.0.0.0', port=5000)
