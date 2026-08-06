PiGuard — IoT-Based Real-Time Livestock Health Monitoring

PiGuard is a non-invasive, IoT-based pig health monitoring device built for small to medium-scale rural piggery farms. Traditional livestock monitoring relies on manual 
observation, which means early signs of disease are often missed until the animal is already seriously ill. PiGuard closes that gap by continuously detecting body temperature,
environmental temperature, and breathing rate, combined with live pen footage and automated real-time alerts, giving farmers the information they need to catch problems early 
and reduce preventable livestock deaths.

The device was field-deployed across two piggery farms in Camarines Sur (Sta. Salud, Calabanga and Palestina, Yabu, Pili), monitoring 20 pigs over a five-day testing period.

This repository documents the full system such as hardware, embedded firmware, cloud sync, and mobile app, with a focus on the network architecture that ties it all together.

My Role:
I designed and built the entire system solo: hardware integration, embedded firmware, cloud backend, and mobile application.

System Architecture: 
[Pigpen — sensor unit mounted 3m above a 7×7m enclosure]
   MLX90640-BAA thermal array  → body temperature
   LD2410C mmWave radar        → breathing rate / movement
   Pi Camera Module 2          → live visual monitoring
        │
        ▼
[Raspberry Pi 4 Model B] — Python firmware
   - reads all sensor data locally
   - classifies readings against predefined health thresholds
     (Normal / Warning / Danger)
   - transmits processed data → Firebase, using GSM connectivity
        │
        ▼ 
[Firebase Realtime Database]
   - cloud-based real-time sync of sensor data + alert states
   - access restricted to an authenticated, whitelisted Google account
        │
        ▼
[Mobile App — Flutter]
   - authenticates via allowed Google account
   - subscribes to Firebase for live data + push alerts
   - lets the farm owner monitor pig health remotely,
     without needing to be physically present at the pigpen

Networking Details:
* GSM over WiFi - Rural piggery farms often don't have reliable on-site internet. Routing the Pi's connectivity through a GSM/cellular module, instead of depending on farm WiFi,
  meant the device could be deployed and monitored remotely regardless of what network infrastructure existed on-site.
* Edge Computing - All sensor classification (Normal/Warning/Danger thresholds) happens on the Pi itself before anything is sent out, the device doesn't depend on a live connection
  to function moment-to-moment, only to report.
* Access control - The Firebase Realtime Database is locked to a specific whitelisted Google account, so only the authorized mobile app instance can read the farm's data.

Tech Stack:
Layer	          Technology
Sensors	        MLX90640-BAA (thermal), LD2410C (mmWave radar), Pi Camera Module 2
Embedded	      Raspberry Pi 4 Model B, Python
Connectivity	  GSM module
Cloud	          Firebase Realtime Database
Mobile	        Flutter (Dart)
Auth	          Firebase Auth (Google account)

Background:
PiGuard was completed and defended as an undergraduate thesis project at Naga College Foundation, Inc. Full thesis documentation available in docs/.





