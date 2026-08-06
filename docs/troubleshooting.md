Troubleshooting & Lessons Learned

Real issues identified during the 5-day field deployment across two piggery farms, from the professional evaluation and thesis findings, not hypothetical scenarios.

1. GSM Connectivity Stability
   
Root cause: GSM signal quality varies in rural field conditions. Both deployment sites (Sta. Salud, Calabanga and Palestina, Yabu, Pili) had no WiFi available,
so GSM was the only viable uplink, but cellular signal strength isn't as consistent as a wired or WiFi connection would be.

Fix identified: Add a local offline data buffer on the Raspberry Pi. When GSM connectivity drops, sensor readings queue locally instead of being lost, then 
auto-sync to Firebase once the connection is restored.

2. Power Outages Causing Downtime
Root cause: Power outage is identified as the sole cause of downtime during that deployment window. Rural Camarines Sur experiences power interruptions that a
wall-powered device has no resilience against.

Fix identified: Backup power supply, either a UPS or a solar-powered battery system to keep the device running through outages without manual intervention.

3. Threshold Calibration (Surface vs. Core Temperature)
Not a bug, but a design decision worth documenting: veterinary literature commonly cites normal pig core (rectal) temperature at 38.0–39.5°C. But the MLX90640 measures
surface skin temperature, which runs consistently lower, field testing showed 35–37°C as the normal surface range, roughly a 4°C differential consistent with published
research on surface-vs-rectal temperature profiles in swine.
