import time
import threading
import logging
import signal
import sys

from firebase_publisher import (
    init_firebase,
    publish_online,
    publish_heartbeat,
    publish_thermal,
    publish_thermal_breach,
    publish_radar,
    publish_radar_history,
    publish_radar_breach,
    publish_alert,
    publish_camera_snapshot,
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
# GLOBAL SHUTDOWN FLAG
# ─────────────────────────────────────────
shutdown_event = threading.Event()


# ─────────────────────────────────────────
# THERMAL THREAD
# ─────────────────────────────────────────
def thermal_thread(camera_mgr):
    """
    Runs thermal reader in its own thread.
    Imports sensor logic from thermal_reader.py.
    Triggers camera snapshot on confirmed breach.
    """
    from thermal_reader import (
        setup_sensor,
        read_grid,
        extract_temperatures,
        get_body_severity,
        get_env_severity,
        BreachTracker,
        READ_INTERVAL,
        HEARTBEAT_INTERVAL,
    )

    logger.info("[THERMAL] Thread started.")

    try:
        sensor       = setup_sensor()
        body_tracker = BreachTracker("body")
        env_tracker  = BreachTracker("env")

        last_heartbeat = time.time()
        read_count     = 0

        while not shutdown_event.is_set():
            loop_start = time.time()

            try:
                grid  = read_grid(sensor)
                temps = extract_temperatures(grid)

                body_temp = temps["body_temp"]
                env_temp  = temps["env_temp"]
                min_temp  = temps["min_temp"]
                max_temp  = temps["max_temp"]

                publish_thermal(body_temp, env_temp, min_temp, max_temp, grid)

                body_severity = get_body_severity(body_temp)
                env_severity  = get_env_severity(env_temp)

                # Check if breach just triggered — capture snapshot
                body_was_breaching = body_tracker.is_breaching()
                env_was_breaching  = env_tracker.is_breaching()

                body_tracker.update(body_temp, body_severity, grid,
                                    body_temp=body_temp, env_temp=env_temp)
                env_tracker.update(env_temp, env_severity, grid,
                                   body_temp=body_temp, env_temp=env_temp)

                # Snapshot on new breach detection
                if not body_was_breaching and body_tracker.is_breaching():
                    logger.info("[THERMAL] Body breach confirmed — capturing snapshot.")
                    _capture_and_upload_snapshot(camera_mgr, reason="body_temp_alert")

                if not env_was_breaching and env_tracker.is_breaching():
                    logger.info("[THERMAL] Env breach confirmed — capturing snapshot.")
                    _capture_and_upload_snapshot(camera_mgr, reason="env_temp_alert")

                read_count += 1
                logger.info(
                    f"[THERMAL] Read #{read_count} → "
                    f"Body: {body_temp:.2f}°C [{body_severity}]  "
                    f"Env: {env_temp:.2f}°C [{env_severity}]"
                )

            except Exception as e:
                logger.error(f"[THERMAL] Sensor read error: {e}")

            # Heartbeat
            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                publish_heartbeat()
                last_heartbeat = time.time()

            elapsed    = time.time() - loop_start
            sleep_time = max(0, READ_INTERVAL - elapsed)
            shutdown_event.wait(timeout=sleep_time)

    except Exception as e:
        logger.error(f"[THERMAL] Thread crashed: {e}")

    logger.info("[THERMAL] Thread stopped.")


# ─────────────────────────────────────────
# RADAR THREAD
# ─────────────────────────────────────────
def radar_thread(camera_mgr):
    """
    Runs radar reader in its own thread.
    Triggers camera snapshot on confirmed BPM breach.
    """
    from radar_reader import (
        setup_serial,
        parse_frame,
        BreathingEstimator,
        RadarHistoryTracker,
        BpmBreachTracker,
        get_bpm_severity,
        PUBLISH_INTERVAL,
        HEARTBEAT_INTERVAL,
    )

    logger.info("[RADAR] Thread started.")

    try:
        ser             = setup_serial()
        breath_est      = BreathingEstimator(window_seconds=30)
        history_tracker = RadarHistoryTracker()
        bpm_tracker     = BpmBreachTracker()

        last_publish   = time.time()
        last_heartbeat = time.time()

        latest = {
            "presence":       False,
            "breathing_rate": 0,
            "movement_level": "None",
        }

        while not shutdown_event.is_set():
            if ser.in_waiting > 0:
                data   = ser.read(ser.in_waiting)
                parsed = parse_frame(data)

                if parsed:
                    bpm = breath_est.update(parsed["stationary_energy"])
                    latest["presence"]       = parsed["presence"]
                    latest["breathing_rate"] = bpm
                    latest["movement_level"] = parsed["movement_level"]

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

                severity = get_bpm_severity(bpm)

                # Snapshot on new BPM breach
                was_breaching = bpm_tracker.is_breaching()
                bpm_tracker.update(bpm, severity)
                if not was_breaching and bpm_tracker.is_breaching():
                    logger.info("[RADAR] BPM breach confirmed — capturing snapshot.")
                    _capture_and_upload_snapshot(camera_mgr, reason="bpm_alert")

                bpm_hist, mov_hist = history_tracker.update(bpm, movement)
                if bpm_hist is not None:
                    publish_radar_history(bpm_hist, mov_hist)

                logger.info(
                    f"[RADAR] presence={presence}  "
                    f"bpm={bpm}  "
                    f"movement={movement}  "
                    f"[{severity}]"
                )

                last_publish = now

            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                publish_heartbeat()
                last_heartbeat = now

            time.sleep(0.05)

    except Exception as e:
        logger.error(f"[RADAR] Thread crashed: {e}")

    logger.info("[RADAR] Thread stopped.")


# ─────────────────────────────────────────
# CAMERA THREAD
# ─────────────────────────────────────────
def camera_thread(camera_mgr):
    """
    Runs Flask MJPEG stream in its own thread.
    """
    from camera_stream import app, STREAM_HOST, STREAM_PORT
    logger.info("[CAMERA] Thread started.")
    logger.info(f"[CAMERA] Stream at http://<pi-ip>:{STREAM_PORT}/stream")

    try:
        app.run(
            host     = STREAM_HOST,
            port     = STREAM_PORT,
            debug    = False,
            threaded = True,
            use_reloader = False,  # Must be False inside a thread
        )
    except Exception as e:
        logger.error(f"[CAMERA] Thread crashed: {e}")

    logger.info("[CAMERA] Thread stopped.")


# ─────────────────────────────────────────
# SNAPSHOT HELPER
# ─────────────────────────────────────────
def _capture_and_upload_snapshot(camera_mgr, reason: str):
    """
    Capture a snapshot and upload to Cloudinary.
    Publishes URL to Firebase camera node.
    Called on breach confirmation.
    """
    try:
        from camera_stream import upload_snapshot_to_cloudinary
        url = upload_snapshot_to_cloudinary(reason=reason)
        if url:
            publish_camera_snapshot(url)
            logger.info(f"Snapshot uploaded → {url}")
        else:
            logger.warning("Snapshot upload returned no URL.")
    except Exception as e:
        logger.error(f"Snapshot capture/upload failed: {e}")


# ─────────────────────────────────────────
# HEARTBEAT THREAD
# ─────────────────────────────────────────
def heartbeat_thread():
    """
    Dedicated heartbeat thread.
    Updates device last_seen every 30 seconds.
    """
    logger.info("[HEARTBEAT] Thread started.")
    while not shutdown_event.is_set():
        publish_heartbeat()
        shutdown_event.wait(timeout=30)
    logger.info("[HEARTBEAT] Thread stopped.")


# ─────────────────────────────────────────
# GRACEFUL SHUTDOWN
# ─────────────────────────────────────────
def handle_shutdown(signum, frame):
    """Handle Ctrl+C and system signals gracefully."""
    logger.info("Shutdown signal received. Stopping all threads...")
    shutdown_event.set()


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
def main():
    logger.info("╔══════════════════════════════════╗")
    logger.info("║     PiGuard HMD Starting...      ║")
    logger.info("╚══════════════════════════════════╝")

    # Register shutdown signals
    signal.signal(signal.SIGINT,  handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Initialize Firebase
    init_firebase()
    publish_online(True)
    logger.info("Firebase connected. Device marked online.")

    # Initialize camera manager (shared across threads)
    from camera_stream import camera_manager
    camera_manager.start()

    # Start all threads
    threads = [
        threading.Thread(target=thermal_thread,   args=(camera_manager,), name="ThermalThread",   daemon=True),
        threading.Thread(target=radar_thread,     args=(camera_manager,), name="RadarThread",     daemon=True),
        threading.Thread(target=camera_thread,    args=(camera_manager,), name="CameraThread",    daemon=True),
        threading.Thread(target=heartbeat_thread,                         name="HeartbeatThread", daemon=True),
    ]

    for t in threads:
        t.start()
        logger.info(f"Started: {t.name}")

    logger.info("All systems running. Press Ctrl+C to stop.")

    # Keep main thread alive until shutdown
    while not shutdown_event.is_set():
        time.sleep(1)

    # Graceful shutdown
    logger.info("Waiting for threads to finish...")
    for t in threads:
        t.join(timeout=5)

    publish_online(False)
    camera_manager.stop()

    logger.info("╔══════════════════════════════════╗")
    logger.info("║     PiGuard HMD Stopped.         ║")
    logger.info("╚══════════════════════════════════╝")


# ─────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────
if __name__ == "__main__":
    main()
