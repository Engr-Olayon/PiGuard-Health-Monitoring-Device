import io
import time
import threading
import logging
import cloudinary
import cloudinary.uploader
from flask import Flask, Response
from picamera2 import Picamera2

from firebase_publisher import (
    init_firebase,
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
# CLOUDINARY CONFIG
# ─────────────────────────────────────────
# Replace with your actual Cloudinary credentials
CLOUDINARY_CLOUD_NAME = "your_cloud_name"
CLOUDINARY_API_KEY    = "your_api_key"
CLOUDINARY_API_SECRET = "your_api_secret"

cloudinary.config(
    cloud_name = CLOUDINARY_CLOUD_NAME,
    api_key    = CLOUDINARY_API_KEY,
    api_secret = CLOUDINARY_API_SECRET,
)


# ─────────────────────────────────────────
# CAMERA CONFIG
# ─────────────────────────────────────────
STREAM_PORT     = 5000
STREAM_HOST     = "0.0.0.0"   # Accessible on local network
FRAME_WIDTH     = 640
FRAME_HEIGHT    = 480
MJPEG_QUALITY   = 85           # JPEG quality 0-100


# ─────────────────────────────────────────
# CAMERA SINGLETON
# ─────────────────────────────────────────
class CameraManager:
    """
    Singleton camera manager.
    Handles frame capture for both MJPEG stream and snapshots.
    Thread-safe using a lock.
    """

    def __init__(self):
        self.camera      = None
        self.lock        = threading.Lock()
        self.latest_frame = None
        self.running     = False

    def start(self):
        """Initialize and start the camera."""
        try:
            self.camera = Picamera2()
            config = self.camera.create_video_configuration(
                main={"size": (FRAME_WIDTH, FRAME_HEIGHT), "format": "RGB888"}
            )
            self.camera.configure(config)
            self.camera.start()
            time.sleep(1)  # Warm up
            self.running = True
            logger.info(f"Camera started at {FRAME_WIDTH}x{FRAME_HEIGHT}")

            # Start background capture thread
            thread = threading.Thread(target=self._capture_loop, daemon=True)
            thread.start()

        except Exception as e:
            logger.error(f"Camera failed to start: {e}")
            raise

    def _capture_loop(self):
        """Continuously capture frames in background thread."""
        while self.running:
            try:
                frame = self._capture_jpeg()
                with self.lock:
                    self.latest_frame = frame
                time.sleep(0.033)  # ~30fps
            except Exception as e:
                logger.error(f"Frame capture error: {e}")
                time.sleep(0.5)

    def _capture_jpeg(self) -> bytes:
        """Capture a single JPEG frame."""
        from PIL import Image
        array = self.camera.capture_array()
        img   = Image.fromarray(array)
        buf   = io.BytesIO()
        img.save(buf, format="JPEG", quality=MJPEG_QUALITY)
        return buf.getvalue()

    def get_frame(self) -> bytes | None:
        """Get the latest JPEG frame (thread-safe)."""
        with self.lock:
            return self.latest_frame

    def capture_snapshot(self) -> bytes | None:
        """Capture a high-quality snapshot for Cloudinary upload."""
        try:
            from PIL import Image
            array = self.camera.capture_array()
            img   = Image.fromarray(array)
            buf   = io.BytesIO()
            img.save(buf, format="JPEG", quality=95)
            return buf.getvalue()
        except Exception as e:
            logger.error(f"Snapshot capture failed: {e}")
            return None

    def stop(self):
        """Stop the camera."""
        self.running = False
        if self.camera:
            self.camera.stop()
            logger.info("Camera stopped.")


# Global camera instance
camera_manager = CameraManager()


# ─────────────────────────────────────────
# FLASK MJPEG STREAM
# ─────────────────────────────────────────
app = Flask(__name__)


def generate_mjpeg():
    """Generator function for MJPEG stream."""
    while True:
        frame = camera_manager.get_frame()
        if frame:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" +
                frame +
                b"\r\n"
            )
        time.sleep(0.033)  # ~30fps


@app.route("/stream")
def video_stream():
    """MJPEG stream endpoint. Access via http://<pi-ip>:5000/stream"""
    return Response(
        generate_mjpeg(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/snapshot")
def snapshot():
    """Single JPEG snapshot endpoint."""
    frame = camera_manager.get_frame()
    if frame:
        return Response(frame, mimetype="image/jpeg")
    return "No frame available", 503


@app.route("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok", "camera": camera_manager.running}, 200


# ─────────────────────────────────────────
# CLOUDINARY UPLOAD
# ─────────────────────────────────────────
def upload_snapshot_to_cloudinary(reason: str = "alert") -> str | None:
    """
    Capture a snapshot and upload it to Cloudinary.

    Args:
        reason : Tag for the upload (e.g. 'alert', 'manual')

    Returns:
        Cloudinary URL string, or None on failure.
    """
    try:
        snapshot = camera_manager.capture_snapshot()
        if not snapshot:
            return None

        timestamp = int(time.time())
        public_id = f"piguard/snapshots/{reason}_{timestamp}"

        result = cloudinary.uploader.upload(
            snapshot,
            public_id   = public_id,
            resource_type = "image",
            tags        = ["piguard", reason],
        )

        url = result.get("secure_url")
        logger.info(f"Snapshot uploaded → {url}")

        # Publish URL to Firebase
        publish_camera_snapshot(url)

        return url

    except Exception as e:
        logger.error(f"Cloudinary upload failed: {e}")
        return None


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
def main():
    logger.info("=== PiGuard Camera Stream Starting ===")

    init_firebase()
    camera_manager.start()

    logger.info(f"MJPEG stream available at http://<your-pi-ip>:{STREAM_PORT}/stream")
    logger.info("Press Ctrl+C to stop.")

    try:
        # Run Flask in main thread
        app.run(
            host  = STREAM_HOST,
            port  = STREAM_PORT,
            debug = False,
            threaded = True,
        )
    except KeyboardInterrupt:
        logger.info("Shutting down camera stream...")
        camera_manager.stop()
        logger.info("Goodbye!")


if __name__ == "__main__":
    main()
