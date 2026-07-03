"""
QuakeAlert Automated Integration Tests
======================================================
Tests the Flask REST API and the bridge clustering logic.
Uses a temporary database file to avoid modifying development/production data.
Runs completely locally with no external dependencies (mocks requests.post).
"""

import os
import tempfile
import unittest
import sqlite3
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

# Override DB_FILE before importing server/bridge to isolate tests
import server
import bridge

class TestQuakeAlertEEW(unittest.TestCase):

    def setUp(self):
        # 1. Create a temporary SQLite database
        self.db_fd, self.db_path = tempfile.mkstemp()
        server.DB_FILE = self.db_path
        bridge.DB_FILE = self.db_path

        # 2. Set API key env and reload config in server
        self.test_api_key = "test_secret_key"
        server.REPORT_API_KEY = self.test_api_key
        server.app.config['TESTING'] = True
        self.app = server.app.test_client()

        # 3. Initialize test database tables
        with server.app.app_context():
            server.init_db()

        # Clear ongoing events cache in bridge
        bridge.ongoing_events.clear()

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    # ---------------------------------------------------------------------------
    # Flask API Endpoint Tests
    # ---------------------------------------------------------------------------

    def test_flask_authentication(self):
        """Verify that endpoints requiring X-API-Key reject unauthorized requests."""
        # Unauthenticated
        response = self.app.post('/heartbeat', json={})
        self.assertEqual(response.status_code, 401)

        # Authenticated but empty body
        response = self.app.post('/heartbeat', json={}, headers={"X-API-Key": self.test_api_key})
        # May be 400 bad request or 200 depending on body check, but NOT 401
        self.assertNotEqual(response.status_code, 401)

    def test_station_heartbeat_and_retrieval(self):
        """Test POST /heartbeat and GET /stations integration."""
        heartbeat_payload = {
            "id": "TEST_NODE_99",
            "version": "7.0.0",
            "lat": -6.9175,
            "lon": 107.6191,
            "lokasi": "Test Site",
            "pga": "0.0150",
            "rssi": -70,
            "uptime": 500,
            "latency": 45
        }

        # Send heartbeat
        resp = self.app.post('/heartbeat', json=heartbeat_payload, headers={"X-API-Key": self.test_api_key})
        self.assertEqual(resp.status_code, 200)

        # Get stations
        resp = self.app.get('/stations')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['station_id'], "TEST_NODE_99")
        self.assertEqual(data[0]['status'], "online")

    def test_events_api_serialization(self):
        """Test GET /events and GET /events/<id> endpoints with sample database data."""
        # Insert a mock active event directly into ongoing_events table
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO ongoing_events 
            (event_id, status, epicenter_lat, epicenter_lon, max_pga, started_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("EVENT_TEST_001", "ACTIVE", -6.9175, 107.6191, 75.2, "2026-07-03T00:00:00Z", "2026-07-03T00:05:00Z")
        )
        conn.commit()
        conn.close()

        # Query active events
        resp = self.app.get('/events?status=active')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['event_id'], "EVENT_TEST_001")
        self.assertEqual(data[0]['status'], "ACTIVE")
        self.assertEqual(data[0]['max_pga'], 75.2)
        # Ensure timestamp (Unix Epoch) field is generated correctly
        self.assertGreater(data[0]['timestamp'], 0)

        # Query single event status (verification check used by Android app)
        resp = self.app.get('/events/EVENT_TEST_001')
        self.assertEqual(resp.status_code, 200)
        single_data = resp.get_json()
        self.assertEqual(single_data['event_id'], "EVENT_TEST_001")
        self.assertEqual(single_data['status'], "ACTIVE")

        # Query non-existent event status
        resp = self.app.get('/events/EVENT_FAKE_999')
        self.assertEqual(resp.status_code, 404)

    # ---------------------------------------------------------------------------
    # Spatio-Temporal Clustering Tests (bridge.py)
    # ---------------------------------------------------------------------------

    @patch('bridge.requests.post')
    def test_clustering_sentinel_to_confirmed_escalation(self, mock_post):
        """Test that alert triggers form events and escalate to Tier 2 on confirmation."""
        mock_post.return_value = MagicMock(status_code=200)

        # 1. Trigger first Alert (Station A)
        alert_a = {
            "id": "STATION_A",
            "lat": -6.9175,
            "lon": 107.6191,
            "pga": 35.0,
            "waktu": "2026-07-03T00:00:00Z",
            "intensitas": "V"
        }
        bridge._handle_alert(alert_a)

        # Verify a new event is registered in-memory and in SQLite
        self.assertEqual(len(bridge.ongoing_events), 1)
        event_id = list(bridge.ongoing_events.keys())[0]
        event = bridge.ongoing_events[event_id]
        
        self.assertEqual(event['tier'], 1)  # Tier 1 Sentinel
        self.assertEqual(event['max_pga'], 35.0)

        # Verify the Ntfy HTTP push notification was dispatched with Tier 1 headers
        self.assertTrue(mock_post.called)
        last_headers = mock_post.call_args[1]['headers']
        self.assertEqual(last_headers['X-Event-Tier'], "1")
        self.assertEqual(last_headers['X-Status'], "ACTIVE")
        self.assertEqual(last_headers['X-Event-ID'], event_id)

        # 2. Trigger second Alert (Station B) within 10 km (close proximity) and 5 seconds
        mock_post.reset_mock()
        alert_b = {
            "id": "STATION_B",
            "lat": -6.9300,
            "lon": 107.6300,
            "pga": 82.0,
            "waktu": "2026-07-03T00:00:05Z",
            "intensitas": "VI"
        }
        bridge._handle_alert(alert_b)

        # Verify it clusters into the SAME event and escalates to Tier 2 (Confirmed)
        self.assertEqual(len(bridge.ongoing_events), 1)
        updated_event = bridge.ongoing_events[event_id]
        self.assertEqual(updated_event['tier'], 2)  # Escalate to Tier 2
        self.assertEqual(updated_event['max_pga'], 82.0)  # PGA updated to max

        # Verify Ntfy HTTP push updated to Tier 2
        last_headers = mock_post.call_args[1]['headers']
        self.assertEqual(last_headers['X-Event-Tier'], "2")
        self.assertEqual(last_headers['X-Status'], "ACTIVE")

    @patch('bridge.requests.post')
    def test_clustering_spatial_separation(self, mock_post):
        """Test that far-away alerts spawn independent events rather than clustering."""
        mock_post.return_value = MagicMock(status_code=200)

        # Alert A in Bandung
        alert_a = {
            "id": "STATION_A",
            "lat": -6.9175,
            "lon": 107.6191,
            "pga": 20.0
        }
        bridge._handle_alert(alert_a)

        # Alert B in Jakarta (~120 km away - exceeds 50km/100km limits)
        alert_b = {
            "id": "STATION_B",
            "lat": -6.2088,
            "lon": 106.8456,
            "pga": 40.0
        }
        bridge._handle_alert(alert_b)

        # Verify that two independent active events are created
        self.assertEqual(len(bridge.ongoing_events), 2)

    @patch('bridge.requests.post')
    def test_event_resolution_via_report(self, mock_post):
        """Test that receiving a seismo/report resolves the associated ongoing event."""
        mock_post.return_value = MagicMock(status_code=200)

        # Spawn alert event
        alert = {"id": "STATION_A", "lat": -6.9175, "lon": 107.6191, "pga": 50.0}
        bridge._handle_alert(alert)
        event_id = list(bridge.ongoing_events.keys())[0]

        # Send report for STATION_A
        mock_post.reset_mock()
        report = {
            "id": "STATION_A",
            "lokasi": "Bandung",
            "waktu": "2026-07-03T10:00:00Z",
            "durasi": 12.5,
            "pga_max": 65.0,
            "intensitas_max": "VI"
        }
        bridge._handle_report(report)

        # Verify that the event has been resolved and removed from active list
        self.assertEqual(len(bridge.ongoing_events), 0)

        # Check DB status
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM ongoing_events WHERE event_id = ?", (event_id,))
        status = cursor.fetchone()[0]
        conn.close()
        self.assertEqual(status, "RESOLVED")

        # Verify push notification sent with status RESOLVED (this is the first HTTP POST)
        self.assertGreaterEqual(len(mock_post.call_args_list), 1)
        ntfy_headers = mock_post.call_args_list[0][1]['headers']
        self.assertEqual(ntfy_headers['X-Status'], "RESOLVED")

if __name__ == '__main__':
    unittest.main()
