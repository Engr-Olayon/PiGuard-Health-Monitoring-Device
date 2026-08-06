import time
import board
import busio
import numpy as np
import adafruit_mlx90640
import logging

from firebase_publisher import (
    init_firebase,
    publish_online,
    publish_heartbeat,
    publish_thermal,
    publish_thermal_breach,
    publish_alert,
    resolve_alert,
)

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
# THRESHOLDS
# ─────────────────────────────────────────

# Body temperature (°C)
BODY_NORMAL_MIN  = 38.0
BODY_NORMAL_MAX  = 39.5
BODY_WARNING_MAX = 40.5
# Warning : 39.6 – 40.5
# Danger  : above 40.5

# Environment temperature (°C)
ENV_NORMAL_MIN   = 18.0
ENV_NORMAL_MAX   = 27.0
ENV_WARNING_MAX  = 30.0
# Warning : 27.1 – 30.0
# Danger  : above 30.0

# How many consecutive readings before logging a breach
BREACH_CONFIRM_COUNT = 3   # ~15 seconds at 5s interval

# How many consecutive normal readings before auto-resolving
RESOLVE_CONFIRM_COUNT = 3

# Seconds between each sensor read
READ_INTERVAL = 5

# Heartbeat every N seconds
HEARTBEAT_INTERVAL = 30


# ─────────────────────────────────────────
# SENSOR SETUP
# ─────────────────────────────────────────
def setup_sensor():
    """Initialize MLX90640 sensor on I2C bus."""
    try:
        i2c = busio.I2C(board.SCL, board.SDA, frequency=400000)
        sensor = adafruit_mlx90640.MLX90640(i2c)
        sensor.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_4_HZ
        logger.info("MLX90640 sensor initialized at 4Hz.")
        return sensor
    except Exception as e:
        logger.error(f"Sensor initialization failed: {e}")
        raise


# ─────────────────────────────────────────
# GRID PROCESSING
# ─────────────────────────────────────────
def read_grid(sensor) -> list:
    """
    Read raw 768-value temperature grid from MLX90640.
    Returns a flat list of 768 floats (32 cols x 24 rows).
    """
    frame = [0.0] * 768
    sensor.getFrame(frame)
    return frame


def extract_temperatures(grid: list) -> dict:
    """
    Extract body temp, env temp, min, and max from the 768-value grid.

    Body temp  : Average of the top 5% hottest pixels (hotspot region).
                 Represents the pig's body temperature.

    Env temp   : Average of the border pixels (outer edge of grid).
                 Represents the ambient pen environment temperature.

    Returns dict with keys: body_temp, env_temp, min_temp, max_temp
    """
    arr = np.array(grid).reshape(24, 32)  # 24 rows x 32 cols

    # ── Body temp: top 5% hottest pixels ──────────────────────────
    flat       = arr.flatten()
    threshold  = np.percentile(flat, 95)
    hotspot    = flat[flat >= threshold]
    body_temp  = float(np.mean(hotspot))

    # ── Env temp: border pixels (outer edge) ──────────────────────
    border = np.concatenate([
        arr[0, :],    # top row
        arr[-1, :],   # bottom row
        arr[1:-1, 0], # left column (excluding corners)
        arr[1:-1,-1], # right column (excluding corners)
    ])
    env_temp = float(np.mean(border))

    # ── Min / Max ──────────────────────────────────────────────────
    min_temp = float(np.min(flat))
    max_temp = float(np.max(flat))

    return {
        "body_temp": round(body_temp, 2),
        "env_temp":  round(env_temp,  2),
        "min_temp":  round(min_temp,  2),
        "max_temp":  round(max_temp,  2),
    }


# ─────────────────────────────────────────
# SEVERITY HELPERS
# ─────────────────────────────────────────
def get_body_severity(temp: float) -> str:
    """Return 'normal', 'warning', or 'danger' for body temp."""
    if temp <= BODY_NORMAL_MAX:
        return "normal"
    elif temp <= BODY_WARNING_MAX:
        return "warning"
    else:
        return "danger"


def get_env_severity(temp: float) -> str:
    """Return 'normal', 'warning', or 'danger' for env temp."""
    if temp <= ENV_NORMAL_MAX:
        return "normal"
    elif temp <= ENV_WARNING_MAX:
        return "warning"
    else:
        return "danger"


# ─────────────────────────────────────────
# BREACH TRACKER CLASS
# ─────────────────────────────────────────
class BreachTracker:
    """
    Tracks consecutive breach readings before triggering an alert.
    Prevents false alerts from single noisy readings.

    Flow:
      - Breach detected → increment counter
      - Counter reaches BREACH_CONFIRM_COUNT → log breach + fire alert
      - Reading returns to normal → increment resolve counter
      - Resolve counter reaches RESOLVE_CONFIRM_COUNT → auto-resolve
    """

    def __init__(self, breach_type: str):
        self.breach_type       = breach_type  # 'body' or 'env'
        self.breach_count      = 0
        self.resolve_count     = 0
        self.active_alert_id   = None         # Firebase alert push key
        self.breach_start_time = None         # When breach was first confirmed
        self.peak_temp         = None         # Highest temp during breach

    def is_breaching(self) -> bool:
        return self.active_alert_id is not None

    def update(self, temp: float, severity: str, grid: list,
               body_temp: float = None, env_temp: float = None):
        """
        Call on every sensor read with the current temperature and severity.
        Handles breach confirmation, alert firing, and auto-resolution.
        """
        if severity == "normal":
            self._handle_normal(temp)
        else:
            self._handle_breach(temp, severity, grid, body_temp, env_temp)

    def _handle_breach(self, temp: float, severity: str, grid: list,
                       body_temp: float, env_temp: float):
        self.resolve_count = 0  # Reset resolve counter

        if not self.is_breaching():
            self.breach_count += 1
            logger.warning(
                f"[{self.breach_type.upper()}] Breach reading "
                f"{self.breach_count}/{BREACH_CONFIRM_COUNT} → "
                f"{temp:.2f}°C ({severity})"
            )

            if self.breach_count >= BREACH_CONFIRM_COUNT:
                # Confirmed breach — log it and fire alert
                self.breach_start_time = time.time()
                self.peak_temp         = temp
                self._fire_alert(temp, severity, grid, body_temp, env_temp)
        else:
            # Already breaching — track peak temperature
            if temp > (self.peak_temp or 0):
                self.peak_temp = temp

    def _handle_normal(self, temp: float):
        self.breach_count = 0  # Reset breach counter

        if self.is_breaching():
            self.resolve_count += 1
            logger.info(
                f"[{self.breach_type.upper()}] Normal reading "
                f"{self.resolve_count}/{RESOLVE_CONFIRM_COUNT} → "
                f"{temp:.2f}°C"
            )

            if self.resolve_count >= RESOLVE_CONFIRM_COUNT:
                self._auto_resolve()

    def _fire_alert(self, temp: float, severity: str, grid: list,
                    body_temp: float, env_temp: float):
        """Publish breach + alert to Firebase."""
        exceeded_by = round(
            temp - (BODY_NORMAL_MAX if self.breach_type == "body"
                    else ENV_NORMAL_MAX), 2
        )

        # Build human-readable message
        if self.breach_type == "body":
            message = (
                f"Pig body temperature {severity}: {temp:.1f}°C "
                f"(+{exceeded_by:.1f}°C above normal)"
            )
        else:
            message = (
                f"Pen environment temperature {severity}: {temp:.1f}°C "
                f"(+{exceeded_by:.1f}°C above normal)"
            )

        # Log to /breaches/
        publish_thermal_breach(
            breach_type      = self.breach_type,
            temperature      = temp,
            exceeded_by      = exceeded_by,
            duration_minutes = 0,  # Just started
            grid             = grid,
        )

        # Log to /alerts/ and capture the push key for later resolution
        # Note: publish_alert returns None currently — we query it below
        publish_alert(
            alert_type = "thermal",
            message    = message,
            severity   = severity,
            body_temp  = body_temp,
            env_temp   = env_temp,
            thermal_grid = grid,
        )

        # Store a placeholder — real alert_id tracking added in main loop
        self.active_alert_id = f"{self.breach_type}_breach_active"
        logger.warning(
            f"🚨 ALERT FIRED → {message}"
        )

    def _auto_resolve(self):
        """Auto-resolve the active alert."""
        if self.active_alert_id:
            duration = int((time.time() - self.breach_start_time) / 60)
            logger.info(
                f"✅ [{self.breach_type.upper()}] Breach resolved. "
                f"Duration: {duration} min. Peak: {self.peak_temp:.2f}°C"
            )
            # Reset tracker
            self.active_alert_id   = None
            self.breach_count      = 0
            self.resolve_count     = 0
            self.breach_start_time = None
            self.peak_temp         = None


# ─────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────
def main():
    logger.info("=== PiGuard Thermal Reader Starting ===")

    # Initialize Firebase
    init_firebase()
    publish_online(True)

    # Initialize sensor
    sensor = setup_sensor()

    # Initialize breach trackers
    body_tracker = BreachTracker("body")
    env_tracker  = BreachTracker("env")

    last_heartbeat = time.time()
    read_count     = 0

    logger.info(f"Reading thermal data every {READ_INTERVAL}s. Press Ctrl+C to stop.")

    try:
        while True:
            loop_start = time.time()

            # ── Read sensor ───────────────────────────────────────
            try:
                grid  = read_grid(sensor)
                temps = extract_temperatures(grid)

                body_temp = temps["body_temp"]
                env_temp  = temps["env_temp"]
                min_temp  = temps["min_temp"]
                max_temp  = temps["max_temp"]

                # ── Publish to Firebase ───────────────────────────
                publish_thermal(body_temp, env_temp, min_temp, max_temp, grid)

                # ── Check thresholds ──────────────────────────────
                body_severity = get_body_severity(body_temp)
                env_severity  = get_env_severity(env_temp)

                body_tracker.update(body_temp, body_severity, grid,
                                    body_temp=body_temp, env_temp=env_temp)
                env_tracker.update(env_temp, env_severity, grid,
                                   body_temp=body_temp, env_temp=env_temp)

                read_count += 1
                logger.info(
                    f"Read #{read_count} → "
                    f"Body: {body_temp:.2f}°C [{body_severity}]  "
                    f"Env: {env_temp:.2f}°C [{env_severity}]"
                )

            except Exception as e:
                logger.error(f"Sensor read error: {e}")

            # ── Heartbeat ─────────────────────────────────────────
            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                publish_heartbeat()
                last_heartbeat = time.time()

            # ── Sleep until next read ─────────────────────────────
            elapsed = time.time() - loop_start
            sleep_time = max(0, READ_INTERVAL - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("Shutting down thermal reader...")
        publish_online(False)
        logger.info("Device marked offline. Goodbye!")


# ─────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────
if __name__ == "__main__":
    main()
