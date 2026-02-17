# QuakeAlert Server – Recovery Guide

Use this guide to **rebuild the server from scratch** (e.g. after a fresh clone or new VPS). These files are not in the repo and must be restored from your backup.

---

## 1. Files you must back up (keep them safe)

Back these up **before** you lose the server. Store them in a secure place (password manager, encrypted disk, or private backup repo).

| File | Purpose |
|------|--------|
| **`.env`** | MQTT, Ntfy, Telegram secrets, and **REPORT_API_KEY** (for write endpoints). Copy from `.env.example` and fill in real values. |
| **`config/pwfile`** | Mosquitto user/password. Copy from `config/pwfile.example` and replace with your hashed passwords. |
| **`config/firebase-key.json`** | Firebase service account JSON for Ntfy (Play Store push). Download from Firebase Console. |
| **`nginx_quakealert.conf`** | Nginx server block for your domain. Use `nginx_quakealert.conf.example` in this repo as a template if you lost the original. |
| **Database (SQLite)** | Earthquake reports and station status. Back up regularly (see section 1b). |

### 1b. Back up the database (quake history + stations)

The report server stores data in a Docker volume. Back it up so you can restore history after a rebuild.

**One-time backup (run on the server):**

```bash
cd ~/QuakeAlert-Server   # or your repo path

mkdir -p backup
docker cp quake-report:/app/data/laporan_gempa.db backup/laporan_gempa_$(date +%Y%m%d).db
```

Copy `backup/laporan_gempa_YYYYMMDD.db` to a safe place (another server, cloud storage, or local machine).

**Optional: schedule weekly backups (cron):**

```bash
crontab -e
# Add this single line (runs every Sunday at 3 AM). Use your actual repo path.
0 3 * * 0 mkdir -p /root/QuakeAlert-Server/backup && docker cp quake-report:/app/data/laporan_gempa.db /root/QuakeAlert-Server/backup/laporan_gempa_$(date +\%Y\%m\%d).db
```

Then copy the backup off the server to your laptop or cloud (see section 1c).

### 1c. Copy backup to your laptop

Backups on the server are lost if the server or disk fails. Copy them to your laptop (or another safe place) regularly.

**From your laptop** (Linux, macOS, or Windows with OpenSSH/WSL), open a terminal and run:

```bash
# Create a folder on your laptop
mkdir -p ~/quakealert-backups

# Copy all backup files from the server (replace YOUR_SERVER_IP with the server’s IP or hostname)
scp root@YOUR_SERVER_IP:/root/QuakeAlert-Server/backup/*.db ~/quakealert-backups/
```

Example: if your server IP is `47.123.45.67`:

```bash
scp root@47.123.45.67:/root/QuakeAlert-Server/backup/*.db ~/quakealert-backups/
```

You will be asked for the server’s `root` password. The `.db` files will appear in `~/quakealert-backups/` on your laptop.

**Copy the whole backup folder:**

```bash
scp -r root@YOUR_SERVER_IP:/root/QuakeAlert-Server/backup ~/quakealert-backups
```

**Using a GUI (no terminal):**

- **FileZilla** (Windows/macOS/Linux): Protocol **SFTP**, host = server IP, user `root`, password = server password. Browse to `/root/QuakeAlert-Server/backup/`, select the `.db` files, and drag them to a folder on your laptop.
- **WinSCP** (Windows): Same idea — connect via SFTP, go to `/root/QuakeAlert-Server/backup/`, download the files.

Do this whenever you want a safe copy (e.g. once a month after the weekly cron has run).

---

## 2. Rate-limit zones in nginx (required)

The QuakeAlert server block uses two rate-limit zones. Define both **once** inside the `http { ... }` block in your main nginx config (e.g. `/etc/nginx/nginx.conf`), not inside a `server` block:

```nginx
# Chat (Socket.IO): 1 req/s per IP, burst 5
limit_req_zone $binary_remote_addr zone=chat_limit:10m rate=1r/s;

# Report API (/laporan, /stations): 10 req/s per IP, burst 20
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;
```

Then reload nginx: `sudo nginx -t && sudo systemctl reload nginx`.

- **chat_limit** is used in the `location /socket.io/` block (see `nginx_quakealert.conf.example`).
- **api_limit** is used in the `location /laporan` and `location /stations` blocks to protect the report server from abuse.

If either zone is missing, nginx may fail to start or fail to load the QuakeAlert server block.

---

## 3. Recovery steps (fresh clone or new server)

### 3.1 Clone and enter the repo

```bash
git clone https://github.com/banana-pixel/QuakeAlert-Server.git
cd QuakeAlert-Server
```

### 3.2 Restore secrets and config

- Copy your backed-up **`.env`** into the repo root. If you don’t have it, copy `.env.example` to `.env` and fill in MQTT, Ntfy, Telegram, and **REPORT_API_KEY** (required for POST /laporan and POST /heartbeat; generate with `openssl rand -hex 32`).
- Copy **`config/pwfile`** (or create from `config/pwfile.example` and set Mosquitto passwords).
- Place **`config/firebase-key.json`** in the `config/` folder (required for Ntfy Firebase push).

### 3.3 Nginx

- Copy your backed-up **`nginx_quakealert.conf`** to your nginx sites (e.g. `/etc/nginx/sites-available/` and symlink in `sites-enabled/`), **or** copy `nginx_quakealert.conf.example` to that path and rename it, then adjust `server_name` and SSL paths if your domain or Certbot paths differ.
- Ensure both **`limit_req_zone`** (chat_limit and api_limit) are defined in your main nginx config (see section 2).
- Run: `sudo nginx -t && sudo systemctl reload nginx`.

### 3.4 SSL (if new server)

If this is a new hostname or VPS, get a certificate:

```bash
sudo certbot --nginx -d quakealert.bananapixel.my.id
```

(Use your actual domain.) Certbot will update the SSL paths in your server block if you use its managed config.

### 3.5 Start the stack

```bash
docker compose up -d --build
```

Check that containers are up: `docker compose ps`. The report server should show `(healthy)` after the healthcheck passes (see section 5).

### 3.6 Restore the database (optional)

If you have a backup of `laporan_gempa.db` and want to restore quake history and station data:

```bash
# Stop the report server so the DB isn’t in use
docker compose stop report-server

# Copy your backup into the running container (start it once for the copy)
docker compose start report-server
docker cp /path/to/your/laporan_gempa_YYYYMMDD.db quake-report:/app/data/laporan_gempa.db

# Restart so the app picks up the file
docker compose restart report-server
```

If the stack is not running yet, start it once, then run the `docker cp` line above (with your backup path), then `docker compose restart report-server`.

---

## 4. Quick checklist

- [ ] `.env` in repo root with **REPORT_API_KEY** set (from backup or `.env.example` filled in)
- [ ] `config/pwfile` (from backup or from `config/pwfile.example`)
- [ ] `config/firebase-key.json` in `config/`
- [ ] Nginx server block in place (from backup or `nginx_quakealert.conf.example`)
- [ ] Both `limit_req_zone` (chat_limit and api_limit) defined in nginx `http` block (section 2)
- [ ] SSL certificate (Certbot) if new server
- [ ] `docker compose up -d --build` run successfully
- [ ] `docker compose ps` shows quake-report as `(healthy)`
- [ ] (Optional) Database restored from backup (section 3.6) if you had one

---

## 5. Report-server healthcheck

The **report-server** service in `docker-compose.yml` has a healthcheck that hits `http://127.0.0.1:5000/` (the Flask root route) from inside the container. Docker uses it to mark the container as healthy or unhealthy.

- **Command:** Python one-liner using `urllib.request.urlopen` (no `curl` required; the image is Python-only).
- **Settings:** `interval: 30s`, `timeout: 10s`, `retries: 3`, `start_period: 40s`.
- **Result:** After startup, `docker compose ps` will show `(healthy)` for quake-report when the check passes, or `(unhealthy)` if the app is not responding. This allows orchestrators or restart policies to react to a stuck Flask process.

If you change the healthcheck (e.g. different interval or command), document it here or in the README.

---

## 6. Optional: Back up the live nginx config into the repo

If you want the **exact** live config in the repo (e.g. for a different branch or backup), copy it to a name that is **not** in `.gitignore`, for example:

```bash
cp nginx_quakealert.conf nginx_quakealert.conf.backup
# Then commit nginx_quakealert.conf.backup (not ignored)
```

Do **not** commit `.env`, `config/pwfile`, or `config/firebase-key.json`; they contain secrets.

---

## Clean test data (dummy "x" rows)

If you inserted test rows (e.g. with `station_id` or `lokasi` = `x`) and want to remove them:

```bash
# On the server (use your real REPORT_API_KEY from .env)
curl -X POST http://127.0.0.1:5000/admin/cleanup-test \
  -H "X-API-Key: YOUR_REPORT_API_KEY"
```

Response: `{"status":"ok","deleted":N}`. This only deletes rows where `station_id = 'x'` or `lokasi = 'x'`.

---

## Sensor shows "offline" in the app

Stations are marked **offline** when the server has not received a heartbeat for **2 minutes** (120 seconds).

1. **ESP32** sends a heartbeat every **60 seconds** on `seismo/heartbeat` (when MQTT is connected). Payload: `stationId`, `lokasi`, `rssi`, `latency`, `status`.
2. **Bridge** forwards each heartbeat to `POST /heartbeat` (with API key). If the bridge gets 401, heartbeats are not stored — check that the bridge has the same `REPORT_API_KEY` as the server.
3. **Check:** `docker logs quake-bridge` — you should see no "Failed to forward heartbeat" errors. If the ESP32 is powered and on WiFi, heartbeats should appear every ~60s and the app should show the sensor **online** after the next refresh.

If the sensor stays offline: ensure the ESP32 is connected to WiFi and MQTT (see [QuakeAlert-ESP32](https://github.com/banana-pixel/QuakeAlert-ESP32) for LED and Serial output).

---

## Alerts not working / geo shows 0,0

**Geo 0,0:** The ESP32 gets lat/lon from geolocation (ip-api.com or ipinfo.io). If both fail or the fallback didn’t set coordinates, alerts and reports can have lat/lon 0,0. Ensure the ESP32 has run `getLokasi()` successfully at least once (check Serial for "Lokasi Updated"). The firmware now sets lat/lon from ipinfo.io’s `loc` field in the fallback so geo is not 0,0 when ip-api.com fails.

**Alerts not received on the app:** (1) In the app, subscribe to the same **ntfy topic** as in `NTFY_TOPIC` in the server `.env`. (2) For Play flavor, ensure `config/firebase-key.json` is valid and Ntfy is configured to use it. (3) On the server, check `docker logs quake-bridge` when an event happens — you should see no "Error sending Alert to NTFY". (4) Test with a manual MQTT publish to `seismo/alert` and confirm the app receives the notification.

---

## Reference: nginx.conf snippet

If you are setting up nginx from scratch, add these two lines inside the `http { }` block of `/etc/nginx/nginx.conf` (e.g. under "Logging Settings" or before `include /etc/nginx/sites-enabled/*;`):

```nginx
limit_req_zone $binary_remote_addr zone=chat_limit:10m rate=1r/s;
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;
```
