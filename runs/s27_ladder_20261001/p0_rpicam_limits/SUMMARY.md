# P0 rpicam limits — summary

Source: `probes.csv` (77 probes), logs in `probe_logs/`. Exit 0 with output bytes > 0 = ran;
it does NOT prove the value was applied (libcamera may clamp): cross-check `02_picamera2_controls.json`
and `still_meta/*.json`.

| app | control | ran (exit 0, bytes > 0) | failed (exit / last line) |
|---|---|---|---|
| rpicam-still | baseline | — | — |
| rpicam-still | brightness | -1.21 -1 0 1 1.21 | — |
| rpicam-still | contrast | -3.21 0 1 16 32 35.21 | — |
| rpicam-still | denoise | auto off cdn_off cdn_fast cdn_hq | bogus (rc 255: ERROR: *** Invalid denoise mode bogus ***) |
| rpicam-still | hdr | off auto sensor single-exp | bogus (rc 255: ERROR: *** Invalid HDR option provided: bogus ***) |
| rpicam-still | saturation | -3.21 0 1 16 32 35.21 | — |
| rpicam-still | sharpness | -1.61 0 1 8 16 17.61 | — |
| rpicam-vid | baseline | --mode 2304:1296:10:P --width 1280 --height 720 | — |
| rpicam-vid | brightness | -1.21 -1 0 1 1.21 | — |
| rpicam-vid | contrast | -3.21 0 1 16 32 35.21 | — |
| rpicam-vid | denoise | auto off cdn_off cdn_fast cdn_hq | bogus (rc 255: ERROR: *** Invalid denoise mode bogus ***) |
| rpicam-vid | duplicate --denoise | — | --mode 2304:1296:10:P --width 1280 --height 720 --denoise cdn_off --denoise cdn_fast (rc 255: ERROR: *** option '--denoise' cannot be specified more than ) |
| rpicam-vid | duplicate --sharpness | — | --mode 2304:1296:10:P --width 1280 --height 720 --sharpness 1.0 --sharpness 2.0 (rc 255: ERROR: *** option '--sharpness' cannot be specified more tha) |
| rpicam-vid | hdr @2304:1296:10:P | off auto sensor single-exp | bogus (rc 255: ERROR: *** Invalid HDR option provided: bogus ***) |
| rpicam-vid | hdr @4608:2592:10:P | off auto sensor single-exp | bogus (rc 255: ERROR: *** Invalid HDR option provided: bogus ***) |
| rpicam-vid | saturation | -3.21 0 1 16 32 35.21 | — |
| rpicam-vid | sharpness | -1.61 0 1 8 16 17.61 | — |
