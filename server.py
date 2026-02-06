from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import json
import os

app = Flask(__name__)
CORS(app)

# --- MODIFIKASI: Simpan DB di folder khusus agar persistent ---
# Pastikan folder 'data' ada
if not os.path.exists('data'):
    os.makedirs('data')

DB_FILE = "data/laporan_gempa.db"
# -------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS laporan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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

@app.route('/laporan', methods=['POST'])
def tambah_laporan():
    data = request.json
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO laporan (lokasi, waktu_kejadian, durasi, pga_maks, intensitas_maks, deskripsi) VALUES (?, ?, ?, ?, ?, ?)",
            (data['lokasi'], data['waktu'], data['durasi'], data['pga'], data['intensitas'], data['deskripsi'])
        )
        conn.commit()
        conn.close()
        return jsonify({"status": "sukses"}), 201
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"status": "gagal", "error": str(e)}), 400

@app.route('/laporan', methods=['GET'])
def dapatkan_laporan():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM laporan ORDER BY id DESC")
    laporan_rows = cursor.fetchall()
    conn.close()

    laporan_list = [dict(row) for row in laporan_rows]
    return jsonify(laporan_list)

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000)
