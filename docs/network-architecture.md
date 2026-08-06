Network Architecture
PiGuard's connectivity design solves a specific real-world constraint: the two deployment sites such as Sta. Salud (Calabanga) and Palestina, Yabu (Pili), both in 
Camarines Sur, had no WiFi connectivity available on-site. Any architecture depending on local network infrastructure would have failed at the deployment stage before
a single reading was ever taken.

Data Pipeline:
Sensors (Pi, local)
      |   
      |    Ports: I2C / UART / CSI — thermal, radar, camera
      |
Raspberry Pi 4 Model B
      |
      |    Edge Computing ( Flagging if Normal/Warning/Danger )
      |
GSM module
      |
      |    Cellular/GSM Connectivity
      |
Firebase Realtime Database
      |
      |    cloud sync, access-controlled
      |
Flutter mobile app (farm owner's device)
      |
      |    authenticated via whitelisted Google account
      |
Live dashboard + push alerts (Health Anomalies)

Why GSM instead of WiFi:
Rural piggery farms are typically outside reliable broadband coverage, and installing dedicated internet infrastructure for a single monitoring device isn't practical
for a small-scale farm. A GSM module gives the device its own independent uplink — the same approach used by standalone IoT deployments in the field (agriculture, remote
sensing, asset tracking) where the deployment site can't be assumed to have existing network infrastructure.

Access Control:
* The Firebase Realtime Database is restricted to a single whitelisted Google account per deployment — the farm owner's authenticated mobile app instance is the only
  client permitted to read data.
* Sensor classification happens on-device before transmission. The Pi doesn't need a live connection to detect an anomaly — only to report it — so a temporary GSM drop
  doesn't stop the device from doing its core job locally.

Known Limitation & Proposed Fix:
If GSM connectivity drops temporarily, data in transit is lost since there's no local buffering. The documented fix (from the thesis recommendations): add a local offline
buffer on the Pi that queues sensor readings during connectivity gaps and auto-syncs to Firebase once the GSM connection returns, so no health data is lost even during a
signal dropout.


  
