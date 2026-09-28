#!/bin/bash
# ladder_bmcam004.sh — S5 ladder on bmcam004 (SPOT-31593C, bus HELD), the bmcam003 recipe
# (RESULTS.md) in one script: ids 61xxx console, 100000030+ signed service. Evidence goes to
# ladder.log (cmd.sh / pistate.sh) and gate.log. Preconditions: bmcam004 on 7d30fea, armed,
# config 580ce986, HALTED after its production wake, SPOT-31593C bus held on.
# Every stage prints a PHASE line; a failed wait stops the script (set -e on waits only).
set -u
H=bmcam004; SPOT=SPOT-31593C; BR=0e582dd12c1e1480
HERE="$(cd "$(dirname "$0")" && pwd)"; cd "$HERE"
REPO="$(cd ../.. && pwd)"
c() { ./cmd.sh "$1" "$2" "${3:-5}" "$SPOT" | grep -E '^(CON|CELL|SPOT|SUMMARY)' | grep -v 'bm pub' | cut -c1-230; }
st() { ./pistate.sh "$1" "$H" | grep -E 'overlay|guarded|boot|sha256 [0-9a-f]+ bm_command' | cut -c1-220; }
w() { ./waitlog.sh "$1" "$2" "${3:-300}" "$H" || { echo "WAIT FAILED: $1 / $2"; exit 1; }; }
phase() { echo "##### $(date -u +%T) PHASE $*" | tee -a gate.log; }
dark() { until ! ssh -o BatchMode=yes -o ConnectTimeout=3 pi@$H true </dev/null 2>/dev/null; do sleep 4; done; echo "dark $(date -u +%T)"; }
halt_now() {   # stop the running cycle (no halt), then a clean halt
  ssh -o BatchMode=yes pi@$H 'pkill -TERM -f "[r]c_run_capture_cycle.sh|[r]c_progressive_jp[e]g.py"; for i in $(seq 1 60); do pgrep -f "[r]c_progressive_jp[e]g.py" >/dev/null || break; sleep 1; done; sync; nohup sudo -n /bin/bash /home/pi/BM_Devel_Pi/tuned_halt.sh >/dev/null 2>&1 </dev/null & echo halt-issued' </dev/null
  dark; sleep 8
}
wake_console() {   # bus cycle; commands after subscribe (~21 s) and before the drain (~48 s)
  ./console.sh $SPOT "bridge cfg commit $BR s" 4 | grep -E "Reboot info|power on for"
}

phase "A: wake, close the window at the boot drain, stage bus_always_on"
if [ -z "${SKIP_WAKE_A:-}" ]; then
dark
wake_console
sleep 27; fi
c L4.set-window-closed '{"id":61010,"c":"set","kv":{"schedule.window.start":"03:00","schedule.window.end":"04:00"}}'
sleep 6;  c L4.set-window-closed-repeat '{"id":61010,"c":"set","kv":{"schedule.window.start":"03:00","schedule.window.end":"04:00"}}'
w 'id=61010' 'applied id=61010' 240
c L8.hld-clamped '{"id":61020,"c":"hld","v":30}'
st L9.before-stage
c L9.stage-bus-always-on '{"id":61021,"c":"set","kv":{"power.bus_always_on":true}}'
st L9.after-stage
c L9.cfm-bus-always-on '{"id":61022,"c":"cfm","ref":61021}'
c L1.ping '{"id":61001,"c":"ping"}'
c L1.help '{"id":61002,"c":"help"}' 8
c L1.get-mode '{"id":61003,"c":"get","k":["mode","r"]}' 6
c L1.get-journal '{"id":61005,"c":"get","k":["journal"]}' 6
st L2.before
c L2.dup-get '{"id":61003,"c":"get","k":["mode","r"]}'
c L2.dup-set '{"id":61010,"c":"set","kv":{"schedule.window.start":"03:00","schedule.window.end":"04:00"}}'
st L2.after

phase "L3 rejections"
st L3.before
c L3.nan '{"id":61040,"c":"set","kv":{"e":NaN}}'
c L3.crosskey '{"id":61043,"c":"set","kv":{"b":"manual"}}'
c L3.unknown-key '{"id":61044,"c":"set","kv":{"nope.key":1}}'
c L3.locked '{"id":61045,"c":"set","kv":{"commands.runtime":"legacy"}}'
c L3.service-unsigned '{"id":61046,"c":"set","kv":{"uplink.chunk_chars":320}}'
c L3.v8-verb '{"id":61047,"c":"roi","v":2}'
c L3.range '{"id":61048,"c":"set","kv":{"e":9.5}}'
c L3.cap-floor '{"id":61049,"c":"set","kv":{"video.send.message_cap":40}}'
c L3.bool-as-int '{"id":61059,"c":"set","kv":{"power.halt.dry_run":1}}'
c L3.dup-json-key '{"id":61060,"c":"set","kv":{"e":1.0,"e":-1.0}}'
c L3.space-escaped "$(python3 -c 'print("{\"id\":61061,\"c\":\"set\",\"kv\":{\"schedule.timezone\":\"America/New" + chr(92) + "u0020York\"}}")')"
st L3.after

phase "L11 console line length"
c L11.len-256 "$(sed -n 4p probe_lines.txt | cut -d' ' -f2- | sed 's/"id":5105[0-9]/"id":61053/')"
c L11.len-257 "$(sed -n 5p probe_lines.txt | cut -d' ' -f2- | sed 's/"id":5105[0-9]/"id":61054/')"

phase "L4 set/reset/CAS"
c L4.set-e '{"id":61070,"c":"set","kv":{"e":-1.0}}'
c L4.reset-e '{"id":61071,"c":"reset","k":["e"]}'
c L2.dup-set-after-reset '{"id":61070,"c":"set","kv":{"e":-1.0}}'
c L4.cas-mismatch '{"id":61072,"c":"set","kv":{"e":0.5},"b":"deadbeef"}'
st L4.after

phase "B: next boot with bus_always_on in effect; hld 120"
halt_now
wake_console
sleep 45; c L8.hld-120 '{"id":61090,"c":"hld","v":120}'
sleep 20; c L8.hld-120-repeat '{"id":61090,"c":"hld","v":120}'

phase "L5/L6 WB + trg kv both media"
c L5.set-wb-manual '{"id":61080,"c":"set","kv":{"b":"manual","camera.white_balance.gains":[1.62,1.91],"camera.white_balance.enabled":true,"camera.controls_enabled":true}}'
c L6.trg-still-med '{"id":61081,"c":"trg","v":2,"kv":{"med":"still","m":30}}'
w 'id=61081' 'staying awake' 400
c L6.trg-video-kv '{"id":61092,"c":"trg","v":2,"kv":{"d":3,"m":80}}'
w 'id=61092' 'staying awake' 400

phase "L7 per_boot save_local one-shots (F7: fire while held)"
c L7.P-video-SL '{"id":61100,"c":"trg","v":2,"kv":{"o":"save_local","d":3}}'
w 'id=61100' 'staying awake' 300
c L7.P-still-SL '{"id":61101,"c":"trg","v":2,"kv":{"med":"still","o":"save_local"}}'
w 'id=61101' 'staying awake' 300

phase "L12 signed service"
S1=$(cd "$REPO" && python3 tools/bm_service_sign.py --json-only $H '{"id":100000030,"c":"set","kv":{"uplink.chunk_chars":360}}')
SOLD=$(cd "$REPO" && python3 tools/bm_service_sign.py --json-only $H '{"id":100000025,"c":"ping"}')
SRST=$(cd "$REPO" && python3 tools/bm_service_sign.py --json-only $H '{"id":100000032,"c":"reset","k":["uplink.chunk_chars"]}')
BAD=$(cd "$REPO" && python3 tools/bm_service_sign.py --json-only $H '{"id":100000031,"c":"set","kv":{"uplink.chunk_chars":320}}' | python3 -c 'import sys,json; d=json.loads(sys.stdin.read()); s=d["sig"]; d["sig"]=("0" if s[0]!="0" else "1")+s[1:]; print(json.dumps(d,separators=(",",":")))')
c L12.signed-set "$S1" 8
c L12.replay "$S1" 6
c L12.bad-sig "$BAD" 6
c L12.older-id "$SOLD" 6
c L12.signed-reset "$SRST" 8
st L12.after

phase "L10 wap"
c L10.wap-2 '{"id":61150,"c":"wap","v":2}' 8
c L10.wap-2-dup '{"id":61150,"c":"wap","v":2}'
c L10.wap-1 '{"id":61151,"c":"wap","v":1}'
sleep 40
ssh -o BatchMode=yes -o ConnectTimeout=5 pi@$H true </dev/null 2>/dev/null && echo "L10 ssh STILL UP (unexpected)" || echo "L10 ssh down in AP mode (expected)"
system_profiler SPAirPortDataType 2>/dev/null | grep -c "$H:" | sed 's/^/L10 SSID seen: /'
c L10.wap-0 '{"id":61152,"c":"wap","v":0}'
until ssh -o BatchMode=yes -o ConnectTimeout=4 pi@$H true </dev/null 2>/dev/null; do sleep 5; done; echo "L10 ssh back $(date -u +%T)"

phase "L7 stay_on x4 + L9 revert across bus cycles"
c L7.set-stay_on '{"id":61102,"c":"set","kv":{"mode.run":"stay_on"}}'
halt_now
wake_console
w 'RUNTIME' 'stay_on boot time read' 180
c L7.S-video-T '{"id":61110,"c":"trg","v":2,"kv":{"d":3,"m":80}}'
w 'id=61110' 'action [0-9]+ done' 400
c L7.S-set-save_local '{"id":61111,"c":"set","kv":{"o":"save_local"}}'
w 'id=61111' 'stay_on boot time read' 120
c L7.S-video-SL '{"id":61112,"c":"trg","v":2,"kv":{"d":3}}'
w 'id=61112' 'action [0-9]+ done' 300
c L7.S-set-med-still '{"id":61113,"c":"set","kv":{"med":"still"}}'
w 'id=61113' 'stay_on boot time read' 120
c L7.S-still-SL '{"id":61114,"c":"trg","v":2}'
w 'id=61114' 'action [0-9]+ done' 300
st L9.revert-before-cycles
halt_now; wake_console; w 'RUNTIME' 'stay_on boot time read' 180
halt_now; wake_console; w 'RUNTIME' 'stay_on boot time read' 180
./waitlog.sh 'GUARD' 'stay_on: media=' 60 $H | grep -E 'GUARD|CF'
st L9.revert-after
c L7.S-still-T '{"id":61120,"c":"trg","v":2,"kv":{"m":30}}'
w 'id=61120' 'action [0-9]+ done' 300

phase "L9 stage across a bus cycle"
c L9.halt-false '{"id":61130,"c":"set","kv":{"power.halt.enabled":false}}'
c L9.halt-true-staged '{"id":61131,"c":"set","kv":{"power.halt.enabled":true}}'
st L9.staged
halt_now; wake_console; w 'RUNTIME' 'stay_on boot time read' 180
st L9.staged-after-cycle
c L9.cfm '{"id":61132,"c":"cfm","ref":61131}'
st L9.confirmed

phase "restore: reset all -> per_boot baseline wake"
c R.reset-all '{"id":61210,"c":"reset","all":1}' 8
w 'id=61210' 'per_boot: media=' 120
st R.after-reset-all
dark
phase "ladder done; next: restore_schedule.sh bmcam004 $BR $SPOT"
