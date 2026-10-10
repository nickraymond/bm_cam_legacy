# rsd lane audit, SPOT-33507C (bmcam003), last 14 rsds on the console, 2026-10-10 02:11Z–15:11Z (EM ask)

Source: `pulled/rsd_tail_20261010.txt` (console "Remote message received … rsd"). Key ages via `rc_media_key.key_time()` at the
receipt time.

| received (Z) | rsd id | fill | keys > 24 h | keys [capture, age at receipt]: chunks |
|---|---|---|---|---|
| 02:11 | 100261 | 40/40 | 0 | 0ei80v[10-10T00Z, 2 h]:40 |
| 02:12 | 100262 | 34/40 | 0 | 0eiasu[01Z, 1 h]:32, 0ei2gw[10-09T22Z, 4 h]:2 |
| 03:11 | 100263 | 40/40 | **1** | **0eg28w[10-08T20Z, 31 h]:40** (= media 58137, the deploy-wake pjpg, 45/184) |
| 04:11 | 100264 | 39/40 | 0 | 0eigct[03Z, 1 h]:39 |
| 06:11 | 100265 | 38/40 | 0 | 0eij52[04Z]:8, 0eidkt[02Z]:22, 0eiasu[01Z]:2, 0ehu4y[10-09T19Z, 11 h]:6 |
| 06:11 | 100266 | 32/40 | **1** | **0eg28w[10-08T20Z, 34 h]:32** (58137 again) |
| 08:03 | 100267 | 38/40 | 0 | 0eiop1[06Z]:17, 0eilx2[05Z]:19, 0eiasu[01Z]:2 |
| 08:11 | 100268 | 40/40 | 0 | 0eirh0[07Z]:12, 0eidkt[02Z]:22, 0ehu4y[19Z, 13 h]:6 |
| 10:11 | 100269 | 35/40 | **1** | **0egloy[10-09T03Z, 31 h]:35** (= 58158) |
| 11:10 | 100270 | 40/40 | 0 | 0eix0z[09Z]:40 |
| 12:11 | 100271 | 37/40 | 0 | 0eizsy[10Z]:27, 0eilx2[05Z]:10 |
| 13:11 | 100272 | 37/40 | **1** | **0eh7wt[10-09T11Z, 26 h]:37** (= 58183) |
| 14:11 | 100273 | 37/40 | 0 | 0ej84x[13Z]:3, 0ej5cx[12Z]:4, 0ej2ky[11Z]:10, 0eix0z[09Z]:20 |
| 15:11 | 100274 | 26/40 | 0 | 0ejaww[14Z]:2, 0ehzox[10-09T21Z, 18 h]:24 |

Findings:
- **The backfill lane fires.** 4 of 14 asks (~1 in 3.5) carry a > 24 h key; each of those is a single-key ask filled 32–40.
- **Asks are nearly full:** 26–40 of 40, mean ≈ 37, so under-filling is not the issue.
- **The deploy-wake leftover takes half the backfill.** "Soonest-to-expire" picks media 58137 (the 2026-10-08 20Z pjpg cut short by the RC2 deploy, 139 chunks): 2 of 4 backfill asks (72 chunks).
- **58158 got one backfill round** (35 chunks asked 10:11Z → applied 11Z → sent 12Z). It moved 156 → 175/191; the remaining 16 most likely rejected in the 12Z queue stalls. It needs another backfill round before its deadline, Sun 03Z (Sat 8 PM PDT).
- **58183 got its round** at 13:11Z (applied 14Z, sent 15Z); expect progress in the 15Z/16Z media table.
- **No backfill ask yet** for 58188 / 58191.
