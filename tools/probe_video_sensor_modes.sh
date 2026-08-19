#!/bin/bash
# filename: probe_video_sensor_modes.sh
# description: Sprint17 spec probe — which SENSOR MODE does rpicam-vid pick for a
#              given (--roi, --width/--height) pair, and is the result 1:1 or upscaled?
#
# WHY THIS EXISTS (Sprint17 spec question, TODO-BM-013):
#   rpicam-vid selects the sensor mode from the REQUESTED OUTPUT SIZE. `--roi` is a
#   digital zoom applied AFTER that choice (a ScalerCrop on the already-selected mode).
#   So a small output size can select a small binned mode, and a tight ROI on that mode
#   can leave FEWER real sensor pixels than the output has -> the ISP upscales, and the
#   footage is soft no matter what bitrate is set. This probe measures which mode is
#   actually chosen, so the Sprint17 preset table is built on evidence, not on the
#   assumption that --roi and --width are independent.
#
# COORDINATE SYSTEMS (manifesto rule 12):
#   - native px      : 4608x2592 IMX708 sensor-equivalent (what crop_xywh uses)
#   - roi fractions  : 0..1 fractions of the full sensor field (what --roi takes)
#   - sensor-mode px : the readout the pipeline actually delivers (1536x864 / 2304x1296
#                      / 4608x2592) BEFORE the ROI zoom
#   - available px   : sensor-mode px * roi fraction = the real detail inside the ROI
#   - output px      : encoded --width/--height
#   Verdict rule: available_w >= output_w  ->  1:1 or downscale (honest)
#                 available_w <  output_w  ->  UPSCALED (fake resolution)
#
# INPUTS  : none (matrix is inline below); runs on the Pi, needs the camera FREE.
# OUTPUTS : $OUT_DIR/<case>.log (full -v 2 encoder output, incl. mode selection),
#           $OUT_DIR/results.csv, $OUT_DIR/run_manifest.json, $OUT_DIR/<case>.h264
# EXAMPLE : ssh pi@bmcam000 'bash /home/pi/probe_video_sensor_modes.sh'
#
# ASSUMPTIONS:
#   - The recording cycle has ALREADY been stopped (this script refuses to run if
#     rpicam-vid is live — it will not fight the production recorder for the camera).
#   - Clips are short (CLIP_MS) and land in the run dir, NOT in the production videos/.
#
# KNOWN LIMITATIONS:
#   - Measures mode selection + achieved fps + encode wall time only. Image quality is
#     judged from the A/B clips in the sprint proper, not here.
#   - fps is read back from the muxed frame count, so a case that fails to encode
#     reports fps=0 rather than a reason; read the case .log for that.

set -u

CLIP_MS="${CLIP_MS:-6000}"
OUT_DIR="${OUT_DIR:-/home/pi/sprint17_probe_${MATRIX_TAG:-run}_$(date -u +%Y%m%dT%H%M%SZ)}"
EXTRA_ARGS="${EXTRA_ARGS:-}"   # e.g. "--denoise cdn_hq --sharpness 1.5" — applied to EVERY case
NATIVE_W=4608
NATIVE_H=2592

if pgrep -x rpicam-vid >/dev/null 2>&1 || pgrep -x libcamera-vid >/dev/null 2>&1; then
    echo "[PROBE][ERROR] a video encoder is RUNNING — stop the recording cycle first."
    echo "[PROBE][ERROR] $(ps -eo pid,cmd | grep -E '[r]picam-vid|[l]ibcamera-vid')"
    exit 3
fi

mkdir -p "$OUT_DIR" || exit 1
CSV="$OUT_DIR/results.csv"
echo "case,roi,crop_native_intent,output_w,output_h,fps_req,bitrate_mbps,forced_mode,chosen_mode,mode_fov_native,applied_roi_native_xywh,avail_w,avail_h,upscale_factor,verdict,frames,fps_actual,encode_s,bytes,cma_free_kb_during,temp_c_after,rc" > "$CSV"

echo "[PROBE] Sprint17 sensor-mode probe"
echo "[PROBE] host=$(hostname) out_dir=$OUT_DIR clip_ms=$CLIP_MS"
echo "[PROBE] encoder: $(rpicam-vid --version 2>&1 | head -1)"
grep -E "CmaTotal|CmaFree|MemAvailable" /proc/meminfo | sed 's/^/[PROBE] /'

# ---------------------------------------------------------------------------
# Matrix: label | roi (fractions) | crop in NATIVE px (label only) | out_w | out_h
#         | fps | mbps | forced --mode ("" = let rpicam-vid choose)
# One variable at a time where it matters; the "forced" rows isolate whether an
# explicit sensor mode is what buys real 1080p detail.
# ---------------------------------------------------------------------------
# MATRIX=modes     : round 1, mode-selection discovery across the geometry space
# MATRIX=shortlist : round 2, the surviving preset candidates at longer clips, with
#                    the sensor mode FORCED (round 1 proved auto-selection picks the
#                    1536x864 mode, whose field of view is only the CENTER 3072x1728
#                    of the array — so the same --roi string means a different native
#                    box; presets must never leave the mode to chance).
MATRIX="${MATRIX:-modes}"

if [ "$MATRIX" = "modes" ]; then
CASES=(
 "prod_today|0.326389,0.326389,0.347222,0.347222|1504,846,1600,900|1000|562|15|2|"
 "prod_crop_1to1|0.326389,0.326389,0.347222,0.347222|1504,846,1600,900|1600|900|15|4|"
 "prod_crop_1to1_fullmode|0.326389,0.326389,0.347222,0.347222|1504,846,1600,900|1600|900|15|4|4608:2592:10:P"
 "wide_full_1080p|0,0,1,1|0,0,4608,2592|1920|1080|15|8|"
 "wide_full_1080p_binmode|0,0,1,1|0,0,4608,2592|1920|1080|15|8|2304:1296:10:P"
 "tight_1080p_native|0.291667,0.291667,0.416667,0.416667|1344,756,1920,1080|1920|1080|15|8|"
 "tight_1080p_native_fullmode|0.291667,0.291667,0.416667,0.416667|1344,756,1920,1080|1920|1080|15|8|4608:2592:10:P"
 "wide_full_1080p30|0,0,1,1|0,0,4608,2592|1920|1080|30|8|"
 "wide_full_1440_wide|0,0,1,1|0,0,4608,2592|2304|1296|15|10|"
)
else
CASES=(
 "s_wide_1080p15|0,0,1,1|0,0,4608,2592|1920|1080|15|8|2304:1296:10:P"
 "s_wide_1080p30|0,0,1,1|0,0,4608,2592|1920|1080|30|8|2304:1296:10:P"
 "s_wide_720p15|0,0,1,1|0,0,4608,2592|1280|720|15|4|2304:1296:10:P"
 "s_half_1080p15|0.25,0.25,0.5,0.5|1152,648,2304,1296|1920|1080|15|8|2304:1296:10:P"
 "s_tight_1080p12_full|0.291667,0.291667,0.416667,0.416667|1344,756,1920,1080|1920|1080|12|8|4608:2592:10:P"
 "s_tight_1080p14_full|0.291667,0.291667,0.416667,0.416667|1344,756,1920,1080|1920|1080|14|8|4608:2592:10:P"
 "s_stills_roi_1000p|0.326389,0.326389,0.347222,0.347222|1504,846,1600,900|1000|562|15|2|4608:2592:10:P"
 "s_stills_roi_1600p|0.326389,0.326389,0.347222,0.347222|1504,846,1600,900|1600|898|15|6|4608:2592:10:P"
)
fi


for entry in "${CASES[@]}"; do
    IFS='|' read -r label roi crop out_w out_h fps mbps forced <<< "$entry"
    log="$OUT_DIR/$label.log"
    h264="$OUT_DIR/$label.h264"
    mp4="$OUT_DIR/$label.mp4"

    argv=(rpicam-vid -n -v 2 -t "$CLIP_MS" --codec h264 --inline
          --width "$out_w" --height "$out_h" --framerate "$fps"
          --bitrate $((mbps * 1000000)) --roi "$roi" -o "$h264")
    [ -n "$forced" ] && argv+=(--mode "$forced")
    # EXTRA_ARGS is deliberately unquoted: it is an operator-supplied flag string.
    # shellcheck disable=SC2206
    [ -n "$EXTRA_ARGS" ] && argv+=($EXTRA_ARGS)

    echo "[PROBE] === $label: roi=$roi out=${out_w}x${out_h}@${fps}fps ${mbps}Mbps mode=${forced:-auto}"
    printf '%s\n' "COMMAND: ${argv[*]}" > "$log"

    started=$(date +%s.%N)
    "${argv[@]}" >> "$log" 2>&1 &
    pid=$!
    sleep 2
    cma_free=$(grep CmaFree /proc/meminfo | awk '{print $2}')
    wait $pid
    rc=$?
    ended=$(date +%s.%N)
    encode_s=$(awk -v a="$started" -v b="$ended" 'BEGIN{printf "%.1f", b-a}')

    # -v 2 gives us three facts, all verified on bmcam000 2026-08-18:
    #   "Selected sensor format: 1536x864-..."            -> the readout mode chosen
    #   "ScalerCrop : [(768, 432)/128x128..(768, 432)/3072x1728]"
    #                                                     -> that mode's FIELD OF VIEW,
    #                                                        in NATIVE 4608x2592 coords
    #   "Using crop (main) (1770, 996)/1066x599"          -> the ROI actually applied,
    #                                                        also in NATIVE coords
    # The FOV line is the one that matters: --roi fractions are taken against the
    # MODE's field, not the full sensor, so the same --roi string means a different
    # native box on a different mode.
    chosen=$(grep -oE "Selected sensor format: [0-9]+x[0-9]+" "$log" | tail -1 | grep -oE "[0-9]+x[0-9]+")
    [ -z "$chosen" ] && chosen="unknown"
    mode_w=$(echo "$chosen" | cut -dx -f1)
    mode_h=$(echo "$chosen" | cut -dx -f2)

    fov=$(grep -oE "ScalerCrop : \\[.*\\]" "$log" | tail -1 | grep -oE "[0-9]+x[0-9]+" | tail -1)
    [ -z "$fov" ] && fov="unknown"
    fov_w=$(echo "$fov" | cut -dx -f1)
    fov_h=$(echo "$fov" | cut -dx -f2)

    applied=$(grep -oE "Using crop \\(main\\) \\([0-9]+, [0-9]+\\)/[0-9]+x[0-9]+" "$log" | tail -1)
    applied_x=$(echo "$applied" | grep -oE "\\([0-9]+, [0-9]+\\)" | grep -oE "[0-9]+" | head -1)
    applied_y=$(echo "$applied" | grep -oE "\\([0-9]+, [0-9]+\\)" | grep -oE "[0-9]+" | tail -1)
    applied_wh=$(echo "$applied" | grep -oE "[0-9]+x[0-9]+$")
    applied_w=$(echo "$applied_wh" | cut -dx -f1)
    applied_h=$(echo "$applied_wh" | cut -dx -f2)
    [ -z "$applied_w" ] && applied_w=0 && applied_h=0

    # Real detail delivered inside the ROI, in MODE pixels:
    #   applied_native_px * (mode_px / fov_native_px)
    if [ "$chosen" = "unknown" ] || [ "$fov" = "unknown" ] || [ "$applied_w" -eq 0 ]; then
        avail_w=0; avail_h=0; verdict="UNKNOWN"
    else
        avail_w=$(awk -v a="$applied_w" -v m="$mode_w" -v f="$fov_w" 'BEGIN{printf "%d", a*m/f}')
        avail_h=$(awk -v a="$applied_h" -v m="$mode_h" -v f="$fov_h" 'BEGIN{printf "%d", a*m/f}')
        # 2 px tolerance: libcamera rounds the applied crop DOWN (a 1600 px
        # request lands as 1599), which is not upscaling in any real sense.
        if [ "$avail_w" -ge $((out_w - 2)) ]; then verdict="OK_1to1_or_down"; else verdict="UPSCALED"; fi
    fi
    scale=$(awk -v o="$out_w" -v a="$avail_w" 'BEGIN{if(a>0) printf "%.2f", o/a; else print "0"}')

    bytes=0; frames=0; fps_actual=0
    if [ -s "$h264" ]; then
        bytes=$(stat -c %s "$h264")
        ffmpeg -hide_banner -loglevel error -y -framerate "$fps" -i "$h264" -c copy -f mp4 "$mp4" 2>>"$log"
        frames=$(ffprobe -v error -select_streams v:0 -count_frames \
                 -show_entries stream=nb_read_frames -of csv=p=0 "$mp4" 2>>"$log" | tr -d '\r')
        [ -z "$frames" ] && frames=0
        fps_actual=$(awk -v f="$frames" -v ms="$CLIP_MS" 'BEGIN{printf "%.1f", f/(ms/1000)}')
    fi
    temp=$(vcgencmd measure_temp 2>/dev/null | grep -oE "[0-9.]+" | head -1)

    echo "$label,$roi,\"$crop\",$out_w,$out_h,$fps,$mbps,${forced:-auto},$chosen,$fov,\"$applied_x,$applied_y,$applied_w,$applied_h\",$avail_w,$avail_h,$scale,$verdict,$frames,$fps_actual,$encode_s,$bytes,$cma_free,$temp,$rc" >> "$CSV"
    echo "[PROBE]     -> mode=$chosen fov=$fov applied_roi=($applied_x,$applied_y)/${applied_w}x${applied_h} avail=${avail_w}x${avail_h} upscale=${scale}x verdict=$verdict frames=$frames (${fps_actual} fps) rc=$rc bytes=$bytes temp=${temp}C"
done

cat > "$OUT_DIR/run_manifest.json" <<JSON
{
  "tool": "probe_video_sensor_modes.sh",
  "sprint": "Sprint17",
  "purpose": "sensor-mode selection vs --roi/--width: is production video upscaled?",
  "host": "$(hostname)",
  "utc": "$(date -u --iso-8601=seconds)",
  "clip_ms": $CLIP_MS,
  "native_frame": "${NATIVE_W}x${NATIVE_H}",
  "encoder": "$(rpicam-vid --version 2>&1 | head -1)",
  "cma_total_kb": $(grep CmaTotal /proc/meminfo | awk '{print $2}'),
  "out_dir": "$OUT_DIR"
}
JSON

echo "[PROBE] done. results: $CSV"
column -s, -t < "$CSV"
