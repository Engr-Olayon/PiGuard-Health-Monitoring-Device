import firebase_admin
from firebase_admin import credentials, db
import time
import logging
import json
import requests
import google.auth.transport.requests
from google.oauth2 import service_account
import socket

# ─────────────────────────────────────────
# LOGGING SETUP
# ─────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────
SERVICE_ACCOUNT_PATH = "/home/piguard/piguard1/piguard-firebase.json"
DATABASE_URL         = "https://piguard-hmd-default-rtdb.firebaseio.com/"
DEVICE_ID            = "pi_001"


# ─────────────────────────────────────────
# FIREBASE INITIALIZATION
# ─────────────────────────────────────────
def init_firebase():
    """
    Initialize Firebase Admin SDK.
    Safe to call multiple times — skips if already initialized.
    """
    if not firebase_admin._apps:
        try:
            cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
            firebase_admin.initialize_app(cred, {
                "databaseURL": DATABASE_URL
            })
            logger.info("Firebase initialized successfully.")
        except Exception as e:
            logger.error(f"Firebase initialization failed: {e}")
            raise


# ─────────────────────────────────────────
# HELPER — CURRENT TIMESTAMP
# ─────────────────────────────────────────
def _now_ms() -> int:
    """Returns current UTC time in milliseconds."""
    return int(time.time() * 1000)


# ─────────────────────────────────────────
# DEVICE STATUS
# ─────────────────────────────────────────
def publish_online(online: bool):
    """
    Update device online status and last_seen timestamp.
    Called on startup (True) and shutdown (False).
    """
    try:
        ref = db.reference(f"/devices/{DEVICE_ID}/status")
        ref.update({
            "online":    online,
            "last_seen": _now_ms(),
        })
        logger.info(f"Device status → online={online}")
    except Exception as e:
        logger.error(f"Failed to publish device status: {e}")


def publish_heartbeat():
    """
    Update last_seen timestamp only.
    Call this every ~30 seconds from main loop.
    """
    try:
        ref = db.reference(f"/devices/{DEVICE_ID}/status")
        ref.update({"last_seen": _now_ms()})
        logger.debug("Heartbeat sent.")
    except Exception as e:
        logger.error(f"Failed to publish heartbeat: {e}")


# ─────────────────────────────────────────
# LOCAL IP PUBLISHER
# ─────────────────────────────────────────
def get_local_ip() -> str:
    """Get Pi's local Wi-Fi IP address."""
    try:
        import subprocess
        result = subprocess.run(
            ['hostname', '-I'],
            capture_output=True,
            text=True
        )
        ip = result.stdout.strip().split()[0]
        return ip
    except Exception as e:
        logger.error(f"Could not get local IP: {e}")
        return "0.0.0.0"


def publish_local_ip():
    """Publish Pi's local IP to Firebase status node."""
    try:
        ip  = get_local_ip()
        ref = db.reference(f"/devices/{DEVICE_ID}/status")
        ref.update({"local_ip": ip})
        logger.info(f"Local IP published → {ip}")
    except Exception as e:
        logger.error(f"Failed to publish local IP: {e}")


# ─────────────────────────────────────────
# THERMAL DATA
# ─────────────────────────────────────────
def publish_thermal(body_temp: float, env_temp: float,
                    min_temp: float, max_temp: float,
                    grid: list):
    """Push latest thermal sensor data to Firebase."""
    try:
        ref = db.reference(f"/devices/{DEVICE_ID}/thermal")
        ref.set({
            "body_temp": round(body_temp, 2),
            "env_temp":  round(env_temp,  2),
            "min_temp":  round(min_temp,  2),
            "max_temp":  round(max_temp,  2),
            "grid":      [round(v, 2) for v in grid],
            "timestamp": _now_ms(),
        })
        logger.info(
            f"Thermal → body={body_temp:.2f}°C  "
            f"env={env_temp:.2f}°C  "
            f"min={min_temp:.2f}°C  "
            f"max={max_temp:.2f}°C"
        )
    except Exception as e:
        logger.error(f"Failed to publish thermal data: {e}")


# ─────────────────────────────────────────
# THERMAL BREACH
# ─────────────────────────────────────────
def publish_thermal_breach(breach_type: str, temperature: float,
                           exceeded_by: float, duration_minutes: int,
                           grid: list):
    """Log a thermal breach event to /breaches/pi_001/."""
    try:
        ref = db.reference(f"/breaches/{DEVICE_ID}")
        ref.push({
            "type":             breach_type,
            "temperature":      round(temperature, 2),
            "exceeded_by":      round(exceeded_by, 2),
            "duration_minutes": duration_minutes,
            "timestamp":        _now_ms(),
            "grid":             [round(v, 2) for v in grid],
        })
        logger.info(
            f"Thermal breach logged → "
            f"type={breach_type}  "
            f"temp={temperature:.2f}°C  "
            f"exceeded_by={exceeded_by:.2f}°C"
        )
    except Exception as e:
        logger.error(f"Failed to publish thermal breach: {e}")


# ─────────────────────────────────────────
# RADAR DATA
# ─────────────────────────────────────────
def publish_radar(presence: bool, breathing_rate: int, movement_level: str):
    """Push latest radar sensor data to Firebase."""
    try:
        ref = db.reference(f"/devices/{DEVICE_ID}/radar")
        ref.set({
            "presence":       presence,
            "breathing_rate": breathing_rate,
            "movement_level": movement_level,
            "timestamp":      _now_ms(),
        })
        logger.info(
            f"Radar → presence={presence}  "
            f"bpm={breathing_rate}  "
            f"movement={movement_level}"
        )
    except Exception as e:
        logger.error(f"Failed to publish radar data: {e}")


def publish_radar_history(bpm_list: list, movement_list: list):
    """Push rolling 24hr BPM and movement history to Firebase."""
    try:
        ref = db.reference(f"/devices/{DEVICE_ID}/radar_history")
        ref.set({
            "bpm":      bpm_list,
            "movement": movement_list,
        })
        logger.debug("Radar history updated.")
    except Exception as e:
        logger.error(f"Failed to publish radar history: {e}")


# ─────────────────────────────────────────
# RADAR BREACH
# ─────────────────────────────────────────
def publish_radar_breach(breathing_rate: int, direction: str,
                         duration_minutes: int):
    """Log a radar breach event to /radar_breaches/pi_001/."""
    try:
        ref = db.reference(f"/radar_breaches/{DEVICE_ID}")
        ref.push({
            "breathing_rate":   breathing_rate,
            "direction":        direction,
            "duration_minutes": duration_minutes,
            "timestamp":        _now_ms(),
        })
        logger.info(
            f"Radar breach logged → "
            f"bpm={breathing_rate}  direction={direction}"
        )
    except Exception as e:
        logger.error(f"Failed to publish radar breach: {e}")


# ─────────────────────────────────────────
# ALERT
# ─────────────────────────────────────────
def publish_alert(alert_type: str, message: str, severity: str,
                  body_temp: float = None, env_temp: float = None,
                  breathing_rate: int = None, movement_level: str = None,
                  thermal_grid: list = None, camera_snapshot_url: str = None,
                  bpm_chart: list = None) -> str | None:
    """
    Push a new health alert to /alerts/pi_001/.
    Returns the Firebase push key (alert ID) for later use.
    """
    try:
        ref     = db.reference(f"/alerts/{DEVICE_ID}")
        new_ref = ref.push({
            "type":                alert_type,
            "message":             message,
            "severity":            severity,
            "resolved":            False,
            "resolved_by":         "",
            "timestamp":           _now_ms(),
            "body_temp":           round(body_temp, 2) if body_temp is not None else None,
            "env_temp":            round(env_temp,  2) if env_temp  is not None else None,
            "breathing_rate":      breathing_rate,
            "movement_level":      movement_level,
            "thermal_grid":        [round(v, 2) for v in thermal_grid] if thermal_grid else None,
            "camera_snapshot_url": camera_snapshot_url,
            "bpm_chart":           bpm_chart,
        })

        # Send FCM push notification
        send_fcm_notification(
            title = f"PiGuard Alert — {severity.upper()}",
            body  = message,
            data  = {
                "type":     alert_type,
                "severity": severity,
            },
        )

        logger.info(
            f"Alert published → "
            f"type={alert_type}  "
            f"severity={severity}  "
            f"id={new_ref.key}"
        )
        return new_ref.key  # ← return Firebase push key

    except Exception as e:
        logger.error(f"Failed to publish alert: {e}")
        return None


def resolve_alert(alert_id: str):
    """Mark an alert as auto-resolved (Pi side)."""
    try:
        ref = db.reference(f"/alerts/{DEVICE_ID}/{alert_id}")
        ref.update({
            "resolved":    True,
            "resolved_by": "auto",
        })
        logger.info(f"Alert auto-resolved → id={alert_id}")
    except Exception as e:
        logger.error(f"Failed to resolve alert {alert_id}: {e}")


def update_alert_snapshot(alert_id: str, url: str):
    """
    Update an existing alert with a camera snapshot URL.
    Called after Cloudinary upload completes on breach confirmation.

    Args:
        alert_id : Firebase push key returned by publish_alert()
        url      : Cloudinary URL of the captured snapshot
    """
    try:
        ref = db.reference(f"/alerts/{DEVICE_ID}/{alert_id}")
        ref.update({"camera_snapshot_url": url})
        logger.info(f"Alert snapshot URL updated → id={alert_id}")
    except Exception as e:
        logger.error(f"Failed to update alert snapshot: {e}")


# ─────────────────────────────────────────
# CAMERA SNAPSHOT
# ─────────────────────────────────────────
def publish_camera_snapshot(url: str):
    """Publish latest camera snapshot URL to Firebase camera node."""
    try:
        ref = db.reference(f"/devices/{DEVICE_ID}/camera")  # fixed typo: was db.references
        ref.set({
            "url":       url,
            "timestamp": _now_ms(),
        })
        logger.info(f"Camera snapshot URL published → {url}")
    except Exception as e:
        logger.error(f"Failed to publish camera snapshot: {e}")


# ─────────────────────────────────────────
# FCM PUSH NOTIFICATION
# ─────────────────────────────────────────
def send_fcm_notification(title: str, body: str, data: dict = None):
    """
    Send FCM push notification to piguard_alerts topic.
    Uses OAuth2 token from serviceAccountKey.json.
    """
    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_PATH,
            scopes=["https://www.googleapis.com/auth/firebase.messaging"],
        )
        creds.refresh(google.auth.transport.requests.Request())
        access_token = creds.token

        url = "https://fcm.googleapis.com/v1/projects/piguard-hmd/messages:send"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type":  "application/json",
        }

        payload = {
            "message": {
                "topic": "piguard_alerts",
                "notification": {
                    "title": title,
                    "body":  body,
                },
                "android": {
                    "priority": "high",
                    "notification": {
                        "channel_id": "piguard_alerts",
                        "sound":      "default",
                    },
                },
                "data": {k: str(v) for k, v in (data or {}).items()},
            }
        }

        response = requests.post(url, headers=headers, json=payload)

        if response.status_code == 200:
            logger.info(f"FCM notification sent → {title}")
        else:
            logger.error(
                f"FCM send failed → "
                f"status={response.status_code}  "
                f"body={response.text}"
            )

    except Exception as e:
        logger.error(f"FCM notification error: {e}")


# ─────────────────────────────────────────
# QUICK CONNECTION TEST
# ─────────────────────────────────────────
if __name__ == "__main__":
    logger.info("Running firebase_publisher connection test...")
    init_firebase()
    publish_online(True)
    logger.info("✅ Connection test passed! Check Firebase Console for updated status.")
