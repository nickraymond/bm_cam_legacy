# S4a overnight soak — status (2026-09-28)

**Nothing was changed on either unit.** The planned S4a deploy on bmcam003 was blocked by the
session's permission policy (remote writes on a unit: disarm, deploy, halt) and was not
attempted. The first launcher (watcher only) was stopped while it was still sleeping, before
it touched the unit.

## State (read-only survey 06:00–06:02Z)

| unit | code | config | cron | notes |
|---|---|---|---|---|
| bmcam003 | S3c cabb79f | v2, hash 62e0a1cb (registry v4), supervisor per_boot video transmit | armed | `video.storage.*` names (S3b-era file) |
| bmcam004 | S3b 9491f1f | v2, hash 72a12186 (registry v3), supervisor per_boot | armed | control |

nereus000: spotter-monitor and bm-heal-driver active (both rigs).

## Done read-only

- `pulled/bmcam00{3,4}_camera_config.yaml` (scp read): both load **strictly** under the S4a
  code (registry v5, S4 rules) — hash 580ce986, 3 old-name warnings each, no effective-config
  violations. `tools/config_v2_upgrade.py` dry-run: would rewrite the names, same hash.
- `delivery.sh`: backend delivery per media (received/expected chunks) for both rigs, read
  through nereus000 (the admin token stays there). `delivery_overnight.log`: one check per hour
  at :30 overnight = the baseline for the S4a comparison.

## Ready when approved

1. `watcher_s4a.sh` at a wake (catch, disarm with the ARMED crontab backed up, SIGTERM the
   cycle, back up config/state) → `tools/rc_field_update.sh --ref feature/sprint26-s4-verbs
   --profile bmcam003/live_20260925 --leave-disarmed` → `rearm_s4a.sh` (restore ARMED crontab,
   clean halt) — all inside one 10-min bus window (the deploy took ~1 min in S3c).
2. The next armed wake is the S4a production cycle; compare delivery with bmcam004.
3. Rollback: `rc_field_update.sh --ref feature/sprint26-s3c-save-local` (runtime cabb79f); the
   config file is not rewritten by the deploy (aliases), so no config restore is needed.
