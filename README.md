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
