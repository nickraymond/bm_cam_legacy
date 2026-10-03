## 2026-10-03 04:04Z (EM, backend; received/expected; none complete)
| device | clip | received/expected | note |
|---|---|---|---|
| BMCAM_003 | 03:00Z | 175/183 | |
| BMCAM_003 | 02:00Z | 171/184 | |
| BMCAM_003 | 01:00Z | 154/182 | |
| BMCAM_003 | 00:05Z | 45/183 | cut cycle (G4.10 event, not D1) |
| BMCAM_004 | 03:00Z | 146/188 | |
| BMCAM_004 | 02:00Z | 151/186 | |
| BMCAM_004 | 01:00Z | 141/184 | |
| BMCAM_004 | 00:00Z | 177/187 | image |
| BMCAM_004 | 23:04Z | 35/182 | stub clip (G4.10) |
Heal sends since 03:30Z: SPOT-33507C 03:36Z (36 chunks); SPOT-31593C none that hour.
Note: heal auto-send went out at 03:36Z while /remote-config showed send_enabled=False (BM_COMMAND_SEND missing) → heal auto-send does not depend on that flag (to confirm with the EM / backend code).
