"""Scratch: exact stall lengths S (hand-off gap >= 2 s, no LEGACY report within 60 s) and the
arrival->hand-off lag that starts each stall; predicts chunks lost per stall at pacing T:
arrivals land at k*T - lag after the stall starts; 2 queue slots -> loss iff 3*T - lag < S."""
import re, sys, statistics as st
from datetime import datetime
P=lambda s: datetime.strptime(s[:19],"%Y-%m-%dT%H:%M:%S").timestamp()+float("0"+s[19:23])
TS=re.compile(r"^\.?(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3})Z \[MS\]")
lab,t0,t1,files=sys.argv[1],P(sys.argv[2]),P(sys.argv[3]),sys.argv[4:]
ev=[]
for f in files:
    for l in open(f,errors="replace"):
        m=TS.match(l)
        if not m: continue
        t=P(m.group(1))
        if not t0<=t<=t1: continue
        if "Queuing message" in l: ev.append((t,"d"))
        elif "to queue MS_Q_CELLULAR_ONLY" in l and "len: 6124" not in l: ev.append((t,"a"))
        elif "MS_Q_LEGACY" in l: ev.append((t,"L"))
ev.sort(); leg=[t for t,k in ev if k=="L"]
d=[t for t,k in ev if k=="d"]; a=[t for t,k in ev if k=="a"]
stalls=[]; lags=[]
import bisect
for x,y in zip(d,d[1:]):
    g=y-x
    if not 2<=g<15 or any(-5<=y-L<=60 for L in leg): continue
    i=bisect.bisect_right(a,x)-1          # last chunk arrival before the stall-starting hand-off
    if i<0 or x-a[i]>1.0 or a[i+1:i+2]==[] or a[i+1]-a[i]>1.3: continue   # only mid-burst stalls
    stalls.append(g); lags.append(x-a[i])
lag=st.median(lags) if lags else 0.1
if not stalls: print(lab, "no stalls"); sys.exit()
q=lambda p: sorted(stalls)[int(p*(len(stalls)-1))]
out=f"{lab}: n={len(stalls)} S min/med/p90/max = {min(stalls):.2f}/{st.median(stalls):.2f}/{q(.9):.2f}/{max(stalls):.2f}  lag med {lag:.2f}"
for T in (1.02,1.1,1.2,1.25,1.3,1.4):
    out+=f" | T={T}: {sum(s>3*T-lag for s in stalls)}"
print(out)
