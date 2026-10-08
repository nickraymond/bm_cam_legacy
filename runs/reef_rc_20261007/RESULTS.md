# REEF-RC: RESULTS (bmcam003 / SPOT-33507C, build c8bea4de = development + #143, reef config per BUILD_RECORD.md)

Gate: `hil/gates/REEF_RC.md`. self_heal ON 06:03:51Z (24/day). Contrast `set` commands by the EM from 07:33Z, every ~2 h.
Command wake = a wake whose cycle log shows `[CMD] applied … camera.image_processing.contrast`.

| # | wake (Z) | PDT | cmd? | msgs | first-send loss | gaps | max gap | queue_full | stalls n / longest / rejects | HDR / other rejects | heals sent | START / END | wake→halt | prune (#143) | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 07:00 | 12 AM | no | 165 | **7.27** (12/165) | 34×1, 40×10, 149×1 | 10 | 12 | 13 / 118.5 s / 12 | 0 / 12 | 0 | 07:00:36 / 07:03:25 | 374 s | `pruned 8 … (by key time)` after `[KEY] media key` | runtime c8bea4de, overlay=0, media=still verified in the cycle log |
