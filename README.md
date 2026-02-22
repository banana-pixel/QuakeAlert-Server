# QuakeAlert Server Backend

The official backend infrastructure for the **[QuakeAlert-ESP32](https://github.com/banana-pixel/QuakeAlert-ESP32)**. This repository hosts the Dockerized services that handle MQTT messaging, notification dispatch (Ntfy/Telegram), and earthquake data logging.

## Architecture

The system consists of 4 Docker containers:
1.  **Mosquitto (MQTT):** Receives raw data from ESP32 sensors.
2.  **Bridge Service (Python):** The brain. Listens to MQTT, filters logic, and triggers alerts.
3.  **Report Server (Flask):** Stores earthquake history in a SQLite database.
4.  **Ntfy Server:** Handles push notifications to Android/iOS devices.

## Installation

### 1. Prerequisites
- A VPS (Ubuntu/Debian recommended)
- [Docker](https://docs.docker.com/get-docker/) & Docker Compose

### 2. Setup
Clone the repository and enter the directory:
```bash
git clone [https://github.com/banana-pixel/QuakeAlert-Server.git](https://github.com/banana-pixel/QuakeAlert-Server.git)
cd QuakeAlert-Server

# Copy the example env file and fill in your details
cp .env.example .env
nano .env
```

**Recovery:** If you are rebuilding from a fresh clone or new server, see **[RECOVERY.md](RECOVERY.md)** for required backups (`.env`, nginx config, `limit_req_zone`, etc.) and step-by-step restore instructions.

## License

Apache License 2.0. See [LICENSE](LICENSE).

## Third-Party Attribution

This project uses the following open-source components:

| Component | License | Purpose |
|-----------|---------|---------|
| [paho-mqtt](https://github.com/eclipse/paho.mqtt.python) | Eclipse EPL 2.0 / EDL 1.0 | MQTT client |
| [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) | LGPL v3.0 | Telegram notifications |
| [Flask](https://flask.palletsprojects.com/) | BSD-3-Clause | Report API server |
| [flask-cors](https://github.com/corydolphin/flask-cors) | MIT | CORS support |
| [requests](https://requests.readthedocs.io/) | Apache 2.0 | HTTP client |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | BSD-3-Clause | Environment config |
| [Socket.IO](https://socket.io/) (chat-server) | MIT | Real-time chat |
| [Eclipse Mosquitto](https://mosquitto.org/) (Docker) | EPL 2.0 / EDL 1.0 | MQTT broker |
| [ntfy](https://github.com/binwiederhier/ntfy) (Docker) | Apache 2.0 / GPL v2 | Push notifications |
