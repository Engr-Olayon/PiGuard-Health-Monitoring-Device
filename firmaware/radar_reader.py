import serial
import time
import logging
from collections import deque

from firebase_publisher import (
    init_firebase,
    publish_online,
    publish_heartbeat,
    publish_radar,
    publish_radar_breach,
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
# SERIAL CONFIG
# ─────────────────────────────────────────
SERIAL_PORT = "/dev/ttyS0"
BAUD_RATE   = 256000
TIMEOUT     = 1  # seconds

# LD2410-C frame header and footer
FRAME_HEADER = b'\xf4\xf3\xf2\xf1'
FRAME_FOOTER = b'\xf8\xf7\xf6\xf5'


# ─────────────────────────────────────────
# THRESHOLDS
# ─────────────────────────────────────────

# Breathing rate (bpm)
BPM_NORMAL_MIN  = 15
BPM_NORMAL_MAX  = 25
BPM_WARNING_MIN = 10
BPM_WARNING_MAX = 30
# Warning : 10–14 or 26–30 bpm
# Danger  : below 10 or above 30 bpm

# Breach confirmation counts
BREACH_CONFIRM_COUNT  = 3
RESOLVE_CONFIRM_COUNT = 3

# Publish interval (seconds)
PUBLISH_INTERVAL   = 2
HEARTBEAT_INTERVAL = 30

# Radar history — rolling 1440-value list (24hrs at 1 per minute)
HISTORY_LENGTH = 1440


# ─────────────────────────────────────────
# SERIAL SETUP
# ─────────────────────────────────────────
def setup_serial() -> serial.Serial:
    """Initialize serial connection to LD2410-C."""
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=TIMEOUT)
        logger.info(f"Serial port opened: {SERIAL_PORT} @ {BAUD_RATE} baud")
        return ser
    except Exception as e:
        logger.error(f"Serial port failed to open: {e}")
        raise


# ─────────────────────────────────────────
# FRAME PARSER
# ─────────────────────────────────────────
def parse_frame(data: bytes) -> dict | None:
    """
    Parse a raw LD2410-C UART frame.

    LD2410-C reporting frame structure (basic target data):
      Byte 0-3  : Frame header (F4 F3 F2 F1)
      Byte 4-5  : Data length (little-endian)
      Byte 6    : Frame type (0x02 = basic target data)
      Byte 7    : Head (0xAA)
      Byte 8    : Target state
                    0x00 = No target
                    0x01 = Moving target
                    0x02 = Stationary target
                    0x03 = Moving + stationary
      Byte 9-10 : Moving target distance (little-endian, cm)
      Byte 11   : Moving target energy (0-100)
      Byte 12-13: Stationary target distance (little-endian, cm)
      Byte 14   : Stationary target energy (0-100)
      Byte 15-16: Detection distance (little-endian, cm)
      Byte 17   : Tail (0x55)
      Byte 18   : Check (0x00)
      Byte 19-22: Frame footer (F8 F7 F6 F5)

    Breathing rate is derived from stationary target energy
    fluctuations over time (LD2410-C does not output raw BPM).
    We use movement pattern + energy to classify movement level.

    Returns dict or None if frame is invalid.
    """
    try:
        # Find frame header
        header_pos = data.find(FRAME_HEADER)
        if header_pos == -1:
            return None

        frame = data[header_pos:]
        if len(frame) < 20:
            return None

        # Validate frame type (basic target data = 0x02)
        frame_type = frame[6]
        if frame_type != 0x02:
            return None

        # Validate head byte
        if frame[7] != 0xAA:
            return None

        target_state = frame[8]

        # Moving target
        moving_distance = frame[9] + frame[10] * 256
        moving_energy   = frame[11]

        # Stationary target
        stationary_distance = frame[12] + frame[13] * 256
        stationary_energy   = frame[14]

        # Detection distance
        detection_distance = frame[15] + frame[16] * 256

        # ── Presence ──────────────────────────────────────────────
        presence = target_state != 0x00

        # ── Movement level ────────────────────────────────────────
        # High  : Moving target detected (target_state 0x01 or 0x03)
        # Low   : Only stationary target detected (target_state 0x02)
        # None  : No target detected (target_state 0x00)
        if target_state in (0x01, 0x03):
            movement_level = "High"
        elif target_state == 0x02:
            movement_level = "Low"
        else:
            movement_level = "None"

        return {
            "presence":             presence,
            "movement_level":       movement_level,
            "moving_distance":      moving_distance,
            "moving_energy":        moving_energy,
            "stationary_distance":  stationary_distance,
            "stationary_energy":    stationary_energy,
            "detection_distance":   detection_distance,
            "target_state":         target_state,
        }

    except Exception as e:
        logger.debug(f"Frame parse error: {e}")
        return None


# ─────────────────────────────────────────
# BREATHING RATE ESTIMATOR
# ─────────────────────────────────────────
class BreathingEstimator:
    """
    Estimates breathing rate from LD2410-C stationary energy fluctuations.

    The LD2410-C does not output raw BPM. However, stationary energy
    fluctuates as the animal breathes (chest rise/fall causes micro-movement).
    We count energy oscillation cycles over a rolling window to estimate BPM.

    This is an approximation — accuracy is best for single-animal monitoring
    at close range (< 2m). Documented limitation in thesis.
    """

    def __init__(self, window_seconds: int = 30):
        # Rolling buffer of (timestamp, energy) tuples
        self.window_seconds = window_seconds
        self.energy_buffer  = deque()
        self.last_energy    = None
        self.last_direction = None  # 'up' or 'down'
        self.cycle_times    = deque()  # timestamps of detected peaks

    def update(self, stationary_energy: int) -> int:
        """
        Feed a new stationary energy reading.
        Returns estimated BPM (0 if insufficient data).
        """
        now = time.time()

        # Store energy reading
        self.energy_buffer.append((now, stationary_energy))

        # Remove readings older than window
        while self.energy_buffer and \
              now - self.energy_buffer[0][0] > self.window_seconds:
            self.energy_buffer.popleft()

        # Detect direction change (peak = one breath cycle)
        if self.last_energy is not None:
            if stationary_energy > self.last_energy:
                direction = "up"
            elif stationary_energy < self.last_energy:
                direction = "down"
            else:
                direction = self.last_direction

            # Peak detected when direction changes from up to down
            if self.last_direction == "up" and direction == "down":
                self.cycle_times.append(now)

            self.last_direction = direction

        self.last_energy = stationary_energy

        # Remove cycle times outside window
        while self.cycle_times and \
              now - self.cycle_times[0] > self.window_seconds:
            self.cycle_times.popleft()

        # Calculate BPM from cycle count in window
        if len(self.cycle_times) >= 2:
            cycles_per_second = len(self.cycle_times) / self.window_seconds
            bpm = int(cycles_per_second * 60)
            # Clamp to physiologically plausible range
            bpm = max(0, min(bpm, 60))
            return bpm

        return 0  # Not enough data yet


# ─────────────────────────────────────────
# SEVERITY HELPER
# ─────────────────────────────────────────
def get_bpm_severity(bpm: int) -> str:
    """Return 'normal', 'warning', or 'danger' for breathing rate."""
    if bpm == 0:
        return "normal"  # No data yet — don't trigger false alerts
    if BPM_NORMAL_MIN <= bpm <= BPM_NORMAL_MAX:
        return "normal"
    elif BPM_WARNING_MIN <= bpm <= BPM_WARNING_MAX:
        return "warning"
    else:
        return "danger"


def get_bpm_direction(bpm: int) -> str:
    """Return 'high' or 'low' for breach direction."""
    return "high" if bpm > BPM_NORMAL_MAX else "low"


# ─────────────────────────────────────────
# RADAR HISTORY TRACKER
# ─────────────────────────────────────────
class RadarHistoryTracker:
    """
    Maintains rolling 1440-value BPM and movement history (24hrs).
    Publishes one entry per minute via firebase_publisher.
    """

    def __init__(self):
        self.bpm_history      = deque([0.0] * HISTORY_LENGTH,
                                       maxlen=HISTORY_LENGTH)
        self.movement_history = deque(["None"] * HISTORY_LENGTH,
                                       maxlen=HISTORY_LENGTH)
        self.minute_bpm_sum   = 0
        self.minute_bpm_count = 0
        self.minute_movements = []
        self.last_minute_push = time.time()

    def update(self, bpm: int, movement_level: str):
        """Call on every sensor read."""
        self.minute_bpm_sum   += bpm
        self.minute_bpm_count += 1
        self.minute_movements.append(movement_level)

        # Push one entry per minute
        if time.time() - self.last_minute_push >= 60:
            avg_bpm = (self.minute_bpm_sum / self.minute_bpm_count
                       if self.minute_bpm_count > 0 else 0.0)

            # Dominant movement this minute
            dominant = max(
                set(self.minute_movements),
                key=self.minute_movements.count
            ) if self.minute_movements else "None"

            self.bpm_history.append(round(avg_bpm, 1))
            self.movement_history.append(dominant)

            # Reset accumulators
            self.minute_bpm_sum   = 0
            self.minute_bpm_count = 0
            self.minute_movements = []
            self.last_minute_push = time.time()

            return list(self.bpm_history), list(self.movement_history)

        return None, None


# ─────────────────────────────────────────
# BPM BREACH TRACKER
# ─────────────────────────────────────────
class BpmBreachTracker:
    """
    Tracks consecutive BPM breach readings before firing an alert.
    Mirrors the BreachTracker pattern from thermal_reader.py.
    """

    def __init__(self):
        self.breach_count      = 0
        self.resolve_count     = 0
        self.active_alert_id   = None
        self.breach_start_time = None
        self.breach_bpm        = None

    def is_breaching(self) -> bool:
        return self.active_alert_id is not None

    def update(self, bpm: int, severity: str):
        if severity == "normal":
            self._handle_normal(bpm)
        else:
            self._handle_breach(bpm, severity)

    def _handle_breach(self, bpm: int, severity: str):
        self.resolve_count = 0

        if not self.is_breaching():
            self.breach_count += 1
            logger.warning(
                f"[BPM] Breach reading "
                f"{self.breach_count}/{BREACH_CONFIRM_COUNT} → "
                f"{bpm} bpm ({severity})"
            )

            if self.breach_count >= BREACH_CONFIRM_COUNT:
                self.breach_start_time = time.time()
                self.breach_bpm        = bpm
                self._fire_alert(bpm, severity)

    def _handle_normal(self, bpm: int):
        self.breach_count = 0

        if self.is_breaching():
            self.resolve_count += 1
            logger.info(
                f"[BPM] Normal reading "
                f"{self.resolve_count}/{RESOLVE_CONFIRM_COUNT} → "
                f"{bpm} bpm"
            )

            if self.resolve_count >= RESOLVE_CONFIRM_COUNT:
                self._auto_resolve()

    def _fire_alert(self, bpm: int, severity: str):
        direction = get_bpm_direction(bpm)

        if direction == "high":
            message = (
                f"Abnormal breathing rate {severity}: {bpm} bpm "
                f"(above normal range of {BPM_NORMAL_MAX} bpm)"
            )
        else:
            message = (
                f"Abnormal breathing rate {severity}: {bpm} bpm "
                f"(below normal range of {BPM_NORMAL_MIN} bpm)"
            )

        publish_radar_breach(
            breathing_rate   = bpm,
            direction        = direction,
            duration_minutes = 0,
        )

        publish_alert(
            alert_type     = "radar",
            message        = message,
            severity       = severity,
            breathing_rate = bpm,
        )

        self.active_alert_id = f"bpm_breach_active"
        logger.warning(f"🚨 ALERT FIRED → {message}")

    def _auto_resolve(self):
        if self.active_alert_id:
            duration = int((time.time() - self.breach_start_time) / 60)
            logger.info(
                f"✅ [BPM] Breach resolved. "
                f"Duration: {duration} min. BPM was: {self.breach_bpm}"
            )
            self.active_alert_id   = None
            self.breach_count      = 0
            self.resolve_count     = 0
            self.breach_start_time = None
            self.breach_bpm        = None


# ─────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────
def main():
    logger.info("=== PiGuard Radar Reader Starting ===")

    init_firebase()
    publish_online(True)

    ser             = setup_serial()
    breath_est      = BreathingEstimator(window_seconds=30)
    history_tracker = RadarHistoryTracker()
    bpm_tracker     = BpmBreachTracker()

    last_publish   = time.time()
    last_heartbeat = time.time()
    read_count     = 0

    # Latest values (updated on every frame, published on interval)
    latest = {
        "presence":       False,
        "breathing_rate": 0,
        "movement_level": "None",
    }

    logger.info("Reading radar data. Press Ctrl+C to stop.")

    try:
        while True:
            # ── Read serial data ──────────────────────────────────
            if ser.in_waiting > 0:
                data   = ser.read(ser.in_waiting)
                parsed = parse_frame(data)

                if parsed:
                    bpm = breath_est.update(parsed["stationary_energy"])

                    latest["presence"]       = parsed["presence"]
                    latest["breathing_rate"] = bpm
                    latest["movement_level"] = parsed["movement_level"]

                    read_count += 1

            # ── Publish on interval ───────────────────────────────
            now = time.time()
            if now - last_publish >= PUBLISH_INTERVAL:
                bpm      = latest["breathing_rate"]
                movement = latest["movement_level"]
                presence = latest["presence"]

                publish_radar(
                    presence       = presence,
                    breathing_rate = bpm,
                    movement_level = movement,
                )

                # Check BPM thresholds
                severity = get_bpm_severity(bpm)
                bpm_tracker.update(bpm, severity)

                # Update history
                bpm_hist, mov_hist = history_tracker.update(bpm, movement)
                if bpm_hist is not None:
                    publish_radar_history(bpm_hist, mov_hist)

                logger.info(
                    f"Radar → presence={presence}  "
                    f"bpm={bpm}  "
                    f"movement={movement}  "
                    f"[{severity}]"
                )

                last_publish = now

            # ── Heartbeat ─────────────────────────────────────────
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                publish_heartbeat()
                last_heartbeat = now

            time.sleep(0.05)  # 50ms loop — responsive but not CPU-hungry

    except KeyboardInterrupt:
        logger.info("Shutting down radar reader...")
        ser.close()
        publish_online(False)
        logger.info("Serial closed. Device marked offline. Goodbye!")


# ─────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────
if __name__ == "__main__":
    main()
