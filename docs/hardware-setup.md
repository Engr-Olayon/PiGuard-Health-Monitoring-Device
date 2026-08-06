Hardware Setup

Physical Installation:
* Mounting - device installed at 3 meters above the center of the pigpen enclosure, to maximize sensor coverage across the pen.
* Coverage - designed for a 7×7 meter pigpen, effectively monitoring 10–50 pigs per enclosure depending on stocking density.
* Enclosure - all components housed in a 3D-printed casing for stability and physical protection in a farm environment.

Components:
Component	                                  Role	
Raspberry Pi 4 Model B (4GB)	              Central processing unit reads sensors, runs classification, handles GSM transmission	
MLX90640-BAA (thermal array sensor)	        Non-contact body + environmental temperature measurement
LD2410C (24GHz mmWave radar)	              Motion detection + breathing rate via chest micro-movement
Raspberry Pi Camera Module 2	              Live visual monitoring + automatic snapshot capture on alert
Flash Drive (32GB)	                        OS + application storage
Power Cable (5V AC-DC adapter)	            Continuous power supply
3D-printed casing (×2)	                    Physical housing
Flutter	                                    Mobile app framework	
Firebase	                                  Cloud database + sync	Free

Why These Sensors:
* MLX90640-BAA - chosen for non-contact temperature sensing (no need to physically touch or restrain the animal, which reduces stress
  and disease transmission risk). Wide field of view allows monitoring multiple animals in-frame simultaneously.
* LD2410C — mmWave radar detects chest wall displacement during respiration without contact, used to estimate breathing rate
* Camera Module 2 — catches visible behavioral/health signals that temperature and radar can't (e.g. lethargy, visible injury), and
  auto-captures a snapshot the moment an alert triggers, giving the farmer visual context alongside the numeric alert.

Known Hardware Limitation:
Power outages is common in rural Camarines Sur, were identified as the sole cause of device downtime during the Farm 1 deployment. 
The documented fix is a backup power supply (UPS or solar-powered battery system) for future iterations, plus weatherproofing the casing for long-term outdoor installation.
