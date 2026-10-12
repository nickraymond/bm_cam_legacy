# Weekend soak scoring (TE2), Fri 10/9 4 PM → Mon 10/12 3 AM PDT

For the Monday read-out at 7:35 AM PDT. Units: bmcam003 / SPOT-33507C and bmcam004 / SPOT-31593C.
Build: RC2 runtime 9ec4cb7 on both, nrjxl B3a stills, low-gain overlay. Times are PDT unless marked Z.

Sources:
- `commands.log`: the ledger, rows 1–19.
- `wakes.csv`: Spotter console, per wake.
- `summary.csv` and `pulled/`: unit cycle logs and sidecars.
- `analysis/media_*_2026-10-12T10.csv`: backend media table at 3 AM.
- `gate.log`: events.

## 1. Commands: 18 sent, 18 confirmed, 1 lost on first send (recovered)

| | bmcam003 | bmcam004 |
|---|---|---|
| Sent | 9: 1 trial + 8 contrast slots | 9: 1 low-gain + 1 trial + 7 contrast slots |
| Confirmed (unit OK / ack within 3 syncs) | **9 / 9** | **8 / 9** on first send; 9 / 9 after the re-send |
| Lost | 0 | **1** (1000079, Sun 3:03 AM) |
| In effect at the next wake (cfg hash = ack hash) | all checked so far ✔ | all checked so far ✔ |

- **The loss (1000079) came from the SPOT-31593C dead zone.** The command reached the Spotter between bmcam004's halt and bus-off (:08–:10). Nothing was listening, so it was dropped rather than held.
  - Recovery: the same id was re-sent at 10:08 AM, timed to ride the :15 report sync after bus-off. The Spotter held it, and the unit applied it at the next boot.
- **The timed :15 send is now 3 / 3 on bmcam004:** 1000079 (re-send), 1000080 and 1000081.
  - For 1000081 the carrier sync is inferred, not observed, because the console was dark (see §4).
- **Unit-side fix proposal (for Monday):**
  - keep the Pi listening until bus-off; or
  - have the mote replay the command at the next boot.
  - Either one removes the need to time sends.
- **The last two commands confirmed from the cycle logs only, at the 4 AM wake:**
  - 1000177: bmcam003 contrast to 1.1, ack hash c013f614;
  - 1000081: bmcam004 contrast to 1.0, ack hash 9c980489.
  - Backend acks were not checked by the TE.

## 2. Stop rules: no triggers on either unit, all weekend

| Rule | bmcam003 | bmcam004 |
|---|---|---|
| SSH needed | 0 | 0 |
| 2 commands lost | 0 lost | 1 lost (rule not hit) |
| Hard cut at bus-off, 2 in a row | 0 | 0 (blind from 1 AM on, see §4) |
| Budget skip / `[PHASE][WARN] skipping` | 0 | 0 |
| `[CFG][ERR]` / LKG fallback | 0 | 0 |
| Wakes (hourly, Fri 4 PM → Mon 3 AM) | 60 / 60 booted | 60 / 60 booted |
| Wake→halt (console, n = 57) | 440–506 s, median 498 | 335–506 s, median 495 |

Every halt landed well before bus-off at 600 s.

## 3. Delivery and heals

| | bmcam003 | bmcam004 |
|---|---|---|
| Spotter first-send loss per wake (n = 57) | median 8.9 %, mean 11.0 %, max 36.6 %; 9 wakes > 20 % | median 4.0 %, mean 5.9 %, max 20.4 %; 2 wakes > 20 % |
| queue_full rejects (total) | 1354 | 582 |
| START / END missing on the console | 7 / 11 | 1 / 5 |
| Heal chunks sent | 1805, in 54 wakes | 890, in 41 wakes |
| Weekend images complete at the backend (3 AM) | **56 / 60** | **57 / 60** |
| Backlog: start → peak → 3 AM | 10 images / 407 chunks → 469 → **4 / 131** | 5 / 211 → 220 → **3 / 110** |

- **Spotter-side loss is not a stop reason** (run-book §3). bmcam003's higher loss comes from the SPOT-33507C health check at about :02:45, which lands mid-burst (open item for Nick).
- **The two Spotters can give two images the same media key** in the same hour. Both units produced key 0emmcv at the 2 AM wake.
  - The backend keeps them apart: 58391 is bmcam003's and 58392 is bmcam004's. So this is harmless today.
  - It only stays harmless as long as heals are matched by (device, key).

### Finding: the heal ask policy is starving older images

| Media | Unit | Captured | Age at 5 AM | Missing | Asks seen in the unit logs |
|---|---|---|---|---|---|
| 58283 (0ejud1) | 003 | Sat 2 PM | 39 h | 55 → 15? | 1 ask (40 chunks), first at 3 AM Mon, 37 h after capture |
| 58331 (0el2t1) | 003 | Sun 6 AM | 23 h | 66 | none* |
| 58313 (0ekm4w) | 004 | Sun 12 AM | 29 h | 12 | none |

\* bmcam003's cycle-log tails are truncated on some wakes, so post-transmit asks can be missing from its list. bmcam004's logs are complete.

- The other open images are the 2 AM and 3 AM captures on each unit, which are inside the normal 2–3-wake heal pipeline.
- **JXL-RC bar 1 (every image complete within 48 h) is at risk for these three.** Their 48-h deadlines:
  - 58283: **today 2 PM**;
  - 58313: Tue 12 AM;
  - 58331: Tue 6 AM.
- Two causes are possible, and the unit logs can't tell them apart:
  - the backend did not pick these keys under the 24 asks/day cap;
  - its asks went to SPOT-31593C and were dropped in the dead zone.
- **Ask to the EM:** check the backend rsd log for keys 0ekm4w and 0el2t1 since Sun 12 AM. Sent and dropped means it is the dead zone; never sent means it is the lane policy.

## 4. Events

- **nereus000 USB hub dropped out at Mon 12:14:53 AM.** It took both Spotter consoles, both Spotter USB disks and eth0, and it is physical: no sysfs node. Nick needs to re-seat the hub.
  - It caused the 1:10 AM rig-health "did not wake" CRIT. That was an artifact: both units woke, as the cycle logs show.
  - **Gaps from 1 AM on:**
    - no console evidence (START/END, first-send loss, Spotter receipts, node current);
    - so no hard-cut detection;
    - and no :15-carrier observation for 1000081.
  - Imaging, heals and commands were unaffected, and the mirror to nereus000 still works over Wi-Fi.
- **SPOT-31593C's report sync re-anchored** at Sat 11:03 PM PDT (10/11 06:03Z) with a corrupted RTC stamp "2072-00-07T06:03:07". It drifted +2 min/h for a few hours, then settled back to :15:01.
- **Collector:** bmcam003 cycle-log tails are truncated on some wakes (ssh reads failed after ~:03). Backfill from the units' `cron_logs/` on Monday.

## 5. Monday follow-ups (TE)

1. Backfill the truncated bmcam003 cycle logs from `cron_logs/` (read-only).
2. Write up the dead-zone fix proposal: listen to bus-off, or mote replay at the next boot.
3. Heal-starvation check once the EM has the backend rsd log; re-score 58283 at 2 PM.
4. Collector stops Mon 9 AM and the mirror at 9:30 AM (their until-times); after that, a final summary commit.
