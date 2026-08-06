# System Architecture: QuakeAlert Real-Time EEW

## 1. System Overview

QuakeAlert is a low-latency, community-driven Earthquake Early Warning (EEW) network. It relies on edge-device vibration analysis (ESP32 + MPU6050) communicating via a persistent MQTT TCP connection to a central Python detection service (`bridge.py`).

To ensure maximum speed and privacy, the mobile application acts as a "Thin Client," performing local impact calculations without transmitting user coordinates to the server.

## 2. Real-Time Alert Engine (Server Logic)

The server does **not** queue or delay alerts to wait for corroboration, as every millisecond is critical. Instead, it utilizes an in-memory **Spatial Clustering Engine** that dynamically upgrades event confidence in real-time.

When a sensor publishes an alert to `seismo/alert`, the server executes the following sequence:

### A. Spatial Clustering (Handling Simultaneous Quakes)

To support multiple earthquakes happening globally at the same time, the server maintains an active dictionary of `ongoing_events`.

1. The server checks the new trigger's coordinates against all `ongoing_events`.
2. **Cluster Match:** If the trigger is within a 50 km radius of an existing active event, the server groups this trigger into that specific event.
3. **New Event Creation:** If the trigger is 500 km away from any existing event (e.g., a simultaneous but separate earthquake in a different province), the server instantly spawns a completely new, independent `Event ID`.

### B. The Tiered Escalation Protocol

Events escalate based on network corroboration without ever delaying the initial warning.

* **Tier 1 (Unconfirmed / Sentinel Mode):** The instant a *new* event is created by a single sensor, the server broadcasts a Tier 1 Warning. The epicenter is anchored to this first responding sensor. This ensures rapid alerting even if only one community sensor is deployed in a region.
* **Tier 2 (Confirmed Event):** If the Spatial Clustering Engine adds a second (or third) sensor to an existing event within a 30-second window, the server upgrades the event to Tier 2. It broadcasts a "Confirmed Earthquake" status and updates the event's Maximum Peak Ground Acceleration (PGA) if the subsequent sensors report stronger shaking.

## 3. Event Resolution & Fail-Safes

Robust EEW systems must gracefully handle sensor destruction or power loss during catastrophic events.

* **Standard Resolution:** When local shaking drops below the STA/LTA threshold, sensors publish a final `seismo/report`. The server closes the active event, instructs the mobile clients to clear their emergency screens, and commits the finalized data to the SQLite database (`laporan_gempa.db`).
* **60-Second Deadman's Switch:** A background thread continuously monitors the lifespan of all `ongoing_events`. If a sensor loses power and fails to send a stop signal, the server forcefully terminates the event exactly 60 seconds after the initial trigger. This guarantees mobile clients are never locked into a false emergency state.
* **Last Will & Testament (LWT):** If a sensor goes offline unexpectedly (network drop/power loss), the MQTT broker immediately notifies the server via a retained LWT message on the `seismo/status` topic.

## 4. Thin-Client Mobile Application (Smart Filtering)

The mobile application relies on a **Distance vs. Intensity Matrix** to filter out irrelevant alerts and prevent alert fatigue.

1. **Passive Reception:** The app receives the real-time event stream from the server containing the epicenter's coordinates and Maximum PGA.
2. **Local Haversine Calculation:** The app calculates the distance between the device's current GPS location and the event's epicenter.
3. **Logarithmic Attenuation:** The app applies an attenuation formula to estimate the *Local Intensity* (e.g., subtracting intensity values based on the calculated distance).
4. **User-Defined Thresholds:** The app evaluates the estimated Local Intensity against the user's settings. A Magnitude 7.0 at 200 km might trigger a moderate local warning, while a Magnitude 3.0 at 200 km will be silently filtered out.
