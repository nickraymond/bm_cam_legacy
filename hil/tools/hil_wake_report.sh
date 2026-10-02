#!/bin/bash
# hil_wake_report.sh — READ-ONLY report of one production wake on one rig, from the Spotter console log
# that nereus000 records: bus on/off, the Pi's wake→halt (bridge current), START/END (chunks planned /
# sent, burst time), queue-full events, heal commands received, and nereus000's own Wi-Fi link.
# Sends nothing. Rig-guarded.
#
# Inputs:   $1 SPOT-ID, $2 window start UTC (e.g. 2026-10-02T23:00), $3 minutes to cover (default 13)
# Outputs:  $HIL_RUN_DIR/console/wake_<SPOT>_<start>.txt (raw excerpt) and one summary line printed +
#           appended to $HIL_RUN_DIR/wakes.csv:
#           spot,window,bus_on,bus_off,pi_on,pi_off,wake_to_halt_s,start_len,end_sent,burst_s,queue_full,heal_cmds,wifi
# Example:  hil/tools/hil_wake_report.sh SPOT-31593C 2026-10-02T23:00
# Limits:   needs nereus000 reachable and its monitor running; "Pi on/off" = bridge current above 0.025 A
#           (bench measurement 2026-10-02: Pi on ~0.034 A, halted 0.018 A at the bridge); run it after
#           the window has closed (≥ :13).
set -u
SPOT="${1:?SPOT-ID}"; W="${2:?window start UTC}"; MIN="${3:-13}"
. "$(cd "$(dirname "$0")" && pwd)/hil_common.sh"   # hil.env, HIL_RUN_DIR, rig guard
hil_require_spot "$SPOT"
BR=$(_hil_rigs | awk -F= -v s="$SPOT" '$1 == s {print $4}')
RUN="$HIL_RUN_DIR"; mkdir -p "$RUN/console"
END=$(python3 -c "import datetime,sys; t=datetime.datetime.fromisoformat(sys.argv[1]); print((t+datetime.timedelta(minutes=int(sys.argv[2]))).strftime('%Y-%m-%dT%H:%M'))" "$W" "$MIN")
BEG=$(python3 -c "import datetime,sys; t=datetime.datetime.fromisoformat(sys.argv[1]); print((t-datetime.timedelta(minutes=2)).strftime('%Y-%m-%dT%H:%M'))" "$W")   # bus-on lands ~2 s before :00
F="$RUN/console/wake_${SPOT}_${W//:/}.txt"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$HIL_MONITOR" "cat \$(ls $HIL_MONITOR_LOG_ROOT/$SPOT/console_{$(echo $BEG | cut -c1-10 | tr -d -),$(echo $END | cut -c1-10 | tr -d -)}.log 2>/dev/null | sort -u) | awk '\$1 >= \"${BEG}:00Z\" && \$1 <= \"${END}:59Z\"'" < /dev/null > "$F" || { echo "[wake] monitor unreachable"; exit 3; }
WIFI=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$HIL_MONITOR" "nmcli -t -f ACTIVE,SSID,SIGNAL dev wifi 2>/dev/null | grep '^yes' | head -1" < /dev/null)
python3 - "$F" "$BR" "$SPOT" "$W" "${WIFI:-unknown}" "$RUN/wakes.csv" "$HIL_TOOLS_DIR/hil_con_decode.py" <<'PY'
import re, sys, subprocess, os
f, br, spot, w, wifi, csvp, dec = sys.argv[1:]
lines = open(f, errors="replace").read().splitlines()
bus = [(l[:20], m.group(1)) for l in lines for m in [re.search(r"Bridge bus power: (\d)", l)] if m]
bus_on = next((t for t, v in bus if v == "1"), ""); bus_off = next((t for t, v in bus if v == "0"), "")
cur = []
for l in lines:
    if f"{br}, power |" in l:
        t = re.search(r"rtc: (\S+?)\.\d+,", l); c = re.search(r"current: ([0-9.]+)", l)
        if t and c: cur.append((t.group(1), float(c.group(1))))
up = [t for t, c in cur if c > 0.025]
pi_on, pi_off = (up[0], up[-1]) if up else ("", "")
def secs(a, b):
    import datetime
    try: return int((datetime.datetime.fromisoformat(b[:19]) - datetime.datetime.fromisoformat(a[:19])).total_seconds())
    except Exception: return ""
dec_out = subprocess.run([sys.executable, dec], input="\n".join(l for l in lines if ", power |" not in l),
                         capture_output=True, text=True).stdout
start = re.search(r"<START IMG>[^\n]*?length: (\d+)", dec_out)
endm = re.search(r"<END IMG>[^\n]*?uart_duration_sec: ([0-9.]+), sent_buffers: (\d+)", dec_out)
qf = sum("MS_Q_CELLULAR_ONLY is full" in l for l in lines)
heals = len(set(re.findall(r'Remote message received[^\n]*"c":"rsd"[^\n]*?"id":(\d+)|"id":(\d+),"c":"rsd"', "\n".join(lines))))
row = [spot, w, bus_on, bus_off, pi_on, pi_off, str(secs(bus_on.replace("Z", ""), pi_off) if bus_on and pi_off else ""),
       start.group(1) if start else "", endm.group(2) if endm else "", endm.group(1) if endm else "", str(qf), str(heals), wifi]
new = not os.path.exists(csvp)
with open(csvp, "a") as fh:
    if new: fh.write("spot,window,bus_on,bus_off,pi_on,pi_off,wake_to_halt_s,start_len,end_sent,burst_s,queue_full,heal_cmds,wifi\n")
    fh.write(",".join(x.replace(",", ";") for x in row) + "\n")
print(f"WAKE {spot} {w}: bus {bus_on[11:19]}→{bus_off[11:19]} | Pi {pi_on[11:19]}→{pi_off[11:19]} "
      f"(wake→halt {row[6]} s) | START len {row[7] or '-'} END sent {row[8] or '-'} burst {row[9] or '-'} s | "
      f"queue_full {qf} | heal cmds {heals} | nereus000 wifi {wifi}")
PY
