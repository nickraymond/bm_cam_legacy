---
name: nereus-spotter-sd-analysis
description: >
  Parse a Sofar Spotter "SD Card Upload" folder from a Nereus BM field test and
  build interactive power / voltage / temperature time-series plots. Covers the
  log file formats, how buoy power and per-node power are computed (chain
  subtraction from the bristlemouth series bus), how node order is determined,
  the charging / thermal-fault conventions, and how to render the self-contained
  HTML dashboards. Use when asked to analyze, parse, or plot Spotter SD-card
  data, power logs, battery/thermal behavior, or to extend that analysis.
  For bench bmcam000/bmcam003 energy-per-cycle comparisons, use the fast
  path in §9 (repo tool tools/sd_bridge_ab_coplot.py) instead of rebuilding
  the pipeline.
---

# Nereus BM Spotter — SD-card data analysis

This skill reproduces the power + thermal analysis done on the Nereus BM camera
field test (Jul 2026) and explains the conventions well enough to extend it to
new SD-card dumps or additional power logs.

Everything below was validated against a real upload
(`20260723_SD Card Upload/`). Times in the raw logs are **UTC**; the deployment
was California in July, so the plots convert to **PDT (UTC−7)**.

---

## 1. Input: the SD-card folder layout

A Spotter SD upload looks like this:

```
<upload>/
  README.txt                      # points to sofarocean.com data-format docs
  log/                            # buoy (Spotter) system logs
    0000_PWR.csv 0001_PWR.csv 0002_PWR.csv    # power/battery/solar telemetry
    0000_HTU.csv 0002_HTU.csv ...             # enclosure humidity+temp sensor
    0000_ERR.log 0001_ERR.log 0002_ERR.log    # charger state changes, faults
    0000_GPS.log, *_LOC.csv, *_BARO.csv, ...  # other sensors (not used here)
  bm/                             # Bristlemouth node logs, one dir per node
    7c7c9fd9f087043a/  0000_power.log 0002_power.log ...
    86dd97882ce5cb50/  0000_power.log 0000_hum_temp.log 0000_pressure.log ...
    57ef9a36411412f7/  0000_power.log ...
    f365022b019e29bd/  0000_power.log ...
  msgdata/ outbox/ sent/          # comms; not used here
```

The numeric prefix (`0000_`, `0001_`, `0002_`) is a **file-rotation / power-cycle
index**, not a node id. The same stream is split across those files, so **always
read all prefixes for a given stream and concatenate**, then sort by timestamp.

Node directory names under `bm/` are the 16-hex-char **node addresses**.

---

## 2. Buoy-level power (`log/*_PWR.csv`)

Header (present as the first row):

```
timestamp(ticks/UTC), solar(V), batt(V), bus(V), solar(I), batt(I), bus(I),
batt_temp(C), chg_stat, chg_fault
```

Columns, 0-indexed:

| idx | field    | meaning                                                |
|-----|----------|--------------------------------------------------------|
| 0   | epoch    | Unix seconds (UTC). Skip rows where `epoch < 1e9`.      |
| 1   | solarV   | solar input voltage (V)                                |
| 2   | battV    | battery voltage (V)                                    |
| 3   | busV     | system bus voltage (V)                                 |
| 4   | solarI   | solar input current (A)                                |
| 5   | battI    | battery current (A); **negative = discharging**        |
| 6   | busI     | bus/load current (A)                                   |
| 7   | battT    | battery temperature (°C). `< -100` is a sentinel → NaN |
| 8   | chg_stat | charge status (see §4)                                 |
| 9   | chg_fault| charge fault code (see §4)                             |

**Power convention (buoy):**

```python
solarP = solarV * solarI          # power INTO the system (source, +)
busP   = busV   * busI            # power drawn by the load (draw)
netP   = solarP - busP            # net; >0 charging surplus, <0 running on battery
```

In the plots we show **source as positive, draw as negative**: solar power and
`netP` are plotted as-is; the buoy load is plotted as `-busP`. So on the power
panel, anything above zero is energy coming in, anything below zero is energy
going out. This same +source / −draw sign rule is used for the node draws.

Parsing recipe:

```python
rows=[]
for f in ["log/0000_PWR.csv","log/0001_PWR.csv","log/0002_PWR.csv"]:
    for r in csv.reader(open(f)):
        try: t=float(r[0])
        except: continue                 # skips the header + junk
        if t<1e9 or len(r)<10: continue
        rows.append([t]+[float(x) for x in r[1:8]]+[int(r[8]),int(r[9])])
p=pd.DataFrame(rows,columns=["epoch","solarV","battV","busV","solarI","battI",
                             "busI","battT","stat","fault"]).sort_values("epoch")
p.loc[p["battT"]<-100,"battT"]=np.nan
p["solarP"]=p.solarV*p.solarI; p["busP"]=p.busV*p.busI; p["netP"]=p.solarP-p.busP
```

---

## 3. Per-node power and **chain subtraction** (`bm/<node>/*_power.log`)

Each Bristlemouth node logs power readings from its onboard current sensors. Line
format:

```
2026-07-22T14:59:13.585Z | tick: 6331, rtc: ..., addr: 65, voltage: 23.838400, current: 0.065750
2026-07-22T14:59:13.582Z | tick: 6329, rtc: 0,   addr: 67, voltage: 23.840000, current: 0.000000
```

Key fact: the nodes are wired as a **series (daisy-chained) power bus** off a
single 24 V injector. Each node measures the current **passing through it**,
which includes itself **plus everything downstream**. That "through" reading is
**`addr: 65`**. (Other `addr` values like 67 are auxiliary rails; for the chain
analysis use **only addr 65**.)

Power per reading = `abs(voltage * current)` (abs because sign depends on sensor
orientation and we only care about magnitude on a 24 V rail).

### Determining node ORDER (source → last)

Because addr-65 is cumulative downstream current, the **mean through-power ranks
the nodes in bus order**: the injector/source node sees the most (itself + all
others), the last node sees the least (just itself). Compute the mean addr-65
power per node and sort **descending**:

```
7c7c9fd9f087043a  through-power ≈ 1.001 W   → source (Ebox / 24V injector)
86dd97882ce5cb50  through-power ≈ 0.983 W   → Temp sensor
57ef9a36411412f7  through-power ≈ 0.818 W   → Dev kit
f365022b019e29bd  through-power ≈ 0.373 W   → Camera (last, duty-cycled)
```

Confirm the ranking is monotonic and the gaps make physical sense (a big drop at
the end = the last, intermittent node). If a new dump adds/removes nodes, re-run
this ranking rather than hard-coding addresses.

For this dataset the confirmed chain and labels are:

```python
CHAIN=["7c7c9fd9f087043a","86dd97882ce5cb50","57ef9a36411412f7","f365022b019e29bd"]
LAB={"7c7c9fd9f087043a":"Ebox (7c7c)","86dd97882ce5cb50":"Temp sensor (86dd)",
     "57ef9a36411412f7":"Dev kit (57ef)","f365022b019e29bd":"Camera (f365)"}
```

### Computing each node's OWN draw (chain subtraction)

A node's own consumption = its through-reading minus the through-reading of the
node directly **below** it in the chain. The last node's own draw = its reading.

```python
def through(nid):                       # mean addr-65 power, resampled to 60 s
    d={}
    for pf in sorted(glob.glob("bm/"+nid+"/*_power.log")):
        for line in open(pf):
            m=re.match(r'^([\d\-T:.]+)Z.*addr:\s*65,\s*voltage:\s*([\d.\-]+),\s*current:\s*([\d.\-]+)',line)
            if m: d[isoms(m.group(1))]=abs(float(m.group(2))*float(m.group(3)))
    s=pd.Series(d).sort_index(); s.index=pd.to_datetime(s.index,unit='ms')
    return s.resample('60s').mean()

thr=pd.DataFrame({nid:through(nid) for nid in CHAIN})
for nid in CHAIN[:-1]: thr[nid]=thr[nid].ffill()   # upstream nodes always on
thr[CHAIN[-1]]=thr[CHAIN[-1]].fillna(0.0)          # camera off => 0 W
thr=thr.dropna(subset=CHAIN[:-1])

node_mean={}
for i,nid in enumerate(CHAIN):
    own = (thr[nid]-thr[CHAIN[i+1]]).clip(lower=0) if i<len(CHAIN)-1 else thr[nid]
    node_mean[nid]=float(own.mean())   # W
```

Notes / gotchas:
- Resample to a common grid (60 s) **before** subtracting, or the two series
  won't line up in time.
- `.clip(lower=0)`: measurement noise can make a downstream reading momentarily
  exceed an upstream one, giving a tiny negative "own draw"; clip it to 0.
- The last node (camera) is **duty-cycled**. Report both its average and its
  "on" draw: `cam_on = cam[cam>0.2].mean()`, `duty = (cam>0.2).mean()`.
- Total system node draw = sum of the own-draws (equals the source node's
  through-power, as a sanity check).

---

## 4. Charging & thermal conventions

From `*_PWR.csv`:

- **`chg_stat`**: `2` = actively charging, `0` = not charging (idle/full).
- **`chg_fault`**: `0` = OK, `6` = **THERMAL_FAULT** (battery too hot, charging
  blocked), `10` = other fault. Treat `fault==6` as "charging blocked, thermal."

Battery charge **thermal cutoff ≈ 43 °C**: charging trips off around 43 °C and
resumes after the battery cools to ≈ 40 °C (hysteresis). Derive these empirically:

```python
setT = p.loc[p.fault==6, "battT"].min()   # coolest temp seen while in thermal fault ≈ cutoff
chgT = p.loc[p.stat==2,  "battT"].max()   # hottest temp still charging
```

**Thermal episodes** (shaded regions on the plots) come from the charger state
transitions in `log/*_ERR.log`, not from the temperature threshold:

```
... ChargerErrorState changed from N/A to OK
... ChargerErrorState changed from OK to THERMAL_FAULT      # episode START
... ChargerErrorState changed from THERMAL_FAULT to OK      # episode END
```

Pair each `to THERMAL_FAULT` (set) with the next `THERMAL_FAULT to …` (clear).
If the log ends while still in fault, close the episode at the last data point.

```python
ev=[]
for f in glob.glob("log/*_ERR.log"):
    for line in open(f):
        if "ChargerErrorState" in line:
            ms=isoms(re.match(r'^([\d\-T:.]+)Z',line).group(1))
            if "to THERMAL_FAULT" in line:   ev.append((ms,"s"))
            elif "THERMAL_FAULT to" in line: ev.append((ms,"c"))
ev.sort(); episodes=[]; cur=None
for ms,k in ev:
    if k=="s" and cur is None: cur=ms
    elif k=="c" and cur is not None: episodes.append([cur,ms]); cur=None
if cur is not None: episodes.append([cur, last_ms])
```

Red dot markers on the temperature plot = every PWR sample with `fault==6`, which
shows exactly when charging was actually blocked (a subset of the shaded windows).

---

## 5. Temperature sources (three of them — keep them straight)

| Source | Where it comes from | What it means |
|--------|--------------------|----------------|
| **Battery temp** | `PWR.csv` col 7 (`batt_temp`) | temperature at the buoy charger/battery |
| **Enclosure / internal** | `log/*_HTU.csv` (cols: epoch, temp°C, RH%) | sealed electronics enclosure (HTU sensor); runs **hottest** — sun on the buoy |
| **Sofar module (underwater)** | `bm/86dd…/*_hum_temp.log`, `temp:` field | in-water node temperature; runs **coolest** |

`*_hum_temp.log` line: `... temp: 34.12 ...` (regex `temp:\s*([\d.\-]+)`). **Drop
readings below ~25 °C** — the sensor reports a boot-default (~20.9 °C) before it
settles, which otherwise creates fake spikes.

Physical takeaway from this dataset: the enclosure (surface heat), not the water,
is what drives the battery past its charge limit. Ordering: enclosure > battery >
underwater module.

---

## 6. Time handling

- All log timestamps are **UTC**. `PWR.csv`/`HTU.csv` use Unix epoch **seconds**;
  `.log` files use ISO `2026-07-22T14:59:13.585Z`.
- Convert to a single ms epoch for plotting: `int(epoch*1000)` or parse the ISO
  string. `isoms(s)` used everywhere:
  ```python
  def isoms(s): return int(datetime.datetime.strptime(s[:23],
      "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc).timestamp()*1000)
  ```
- Display in **PDT (UTC−7)**. The HTML engines store `tzoff:-7` and add
  `tzoff*3600*1000` ms before formatting tick labels; they never rely on the
  viewer's local zone.

---

## 7. Rendering the plots

The deliverables are **self-contained single-file HTML** dashboards (no internet,
no libraries) — a hand-rolled `<canvas>` engine with scroll-zoom, drag-pan,
double-click reset, a clickable legend, and a crosshair tooltip. Two builders:

- **Main dashboard** (`emit_data.py` → `data.json`, `make_html.py` → HTML): four
  stacked panels sharing one time axis — Solar V, Battery V, Power (buoy net +
  per-node own draws), Battery temp. Thermal episodes shaded red across all
  panels; red dots where charging blocked.
- **Temperature comparison** (`make_temp.py` → `temp_data.json`,
  `make_temp_html.py` → HTML): the three temperature sources overlaid on one
  panel, with a **dual axis (°C left, °F right)**, the ~43 °C limit line, and the
  over-limit markers.
- **Combined** (`make_combined.py`): splices the two standalone HTML files into
  one page (dashboard on top, temperature comparison at the bottom). It wraps
  each chart's script in its own IIFE and **namespaces the DOM ids** (`cvM/cvT`,
  `cwrapM/cwrapT`, etc.) so the two engines don't collide. Reuse this pattern to
  append more charts.

Rendering conventions that matter:
- **Decimate** long series before embedding (`np.linspace` index pick to ≈5–6k
  points/series) — keeps the file small and the canvas smooth. Preserve NaN gaps
  (emit `null`) so the line breaks instead of interpolating across dropouts.
- Right axis °F = `°C * 9/5 + 32` drawn at the same gridline y-positions; widen
  the right margin (~50 px) to fit the labels.
- Colors are consistent across panels (solar amber, battery teal, net black,
  fault red). Follow the `dataviz` skill if restyling.

To regenerate from a new upload: point the `B=` base path at the new folder, run
`make_temp.py` then `emit_data.py` (they write the two `*.json`), then the two
`make_*_html.py`, then `make_combined.py`. Screenshot with a headless Chromium
(Playwright) and check the browser console for JS errors before shipping.

---

## 8. Extending to power logs / new data

- **More nodes / a different chain:** re-run the through-power ranking in §3 to
  get the order; don't assume the old addresses. Rebuild `CHAIN`/`LAB`/`COL`.
- **Longer deployments:** raise the decimation target or switch to min/max
  envelope decimation so short spikes survive. Consider daily aggregates.
- **New metrics from PWR.csv:** battery current (col 5) gives charge/discharge
  directly; `battV` sag under load and solar-harvest totals (integrate `solarP`
  over time) are natural additions as extra panels.
- **Energy budget:** integrate `netP` (W·s → Wh) to show state-of-charge trend;
  cross-check against `battV`.
- **Other sensors:** `*_BARO.csv`, `*_GPS.log`, `*_pressure.log`,
  `*_SPC.csv`(spectra) follow the same "read all prefixes, epoch-seconds UTC,
  sort" pattern. Sofar's format docs: the URL in the SD `README.txt`
  (sofarocean.com/posts/spotter-data-access).

---

## 9. Bench bmcam A/B — fast path (use the repo tool)

For the common 2026-07+ bench question — "compare bmcam000 vs bmcam003 power /
energy per cycle from SD dumps" — do NOT rebuild the pipeline from the recipes
above. The repo `bm_cam_legacy` has a validated tool that does the whole job:

```bash
python3 tools/sd_bridge_ab_coplot.py \
    --unit "bmcam003:<dump003>/bm/c3c564b91856226c:#2563eb" \
    --unit "bmcam000:<dump000>/bm/0e582dd12c1e1480:#dc2626" \
    --hours 10 --out-dir runs/power_review_<YYYYMMDD>
```

It parses the bridge addr-65 traces, finds complete bus-ON windows from the
trace itself (threshold 0.05 W, ≤90 s gaps; edge partials dropped, not
averaged), trapezoid-integrates Wh/cycle, and emits a self-contained
interactive coplot HTML + per-cycle CSV + summary JSON + the 60 s-mean data
JSON. See its docstring for conventions.

Known bench topology (each Spotter chain is just bridge → camera unit; the
full 4-node chain subtraction in §3 is for the field buoy, not the bench):

| Spotter | bridge node (log the addr-65 of THIS one) | camera unit |
|---|---|---|
| SPOT-31593C | `0e582dd12c1e1480` | bmcam000 |
| SPOT-33507C | `c3c564b91856226c` | bmcam003 |

Why the bridge, not the camera node: the camera-side mote logs sparsely on
SPOT-31593C (`transmitAggregations` differs between the Spotters), while the
bridge trace is dense (10 s) on both, reads the downstream camera load, and
drops to ~0 W in every bus-off window. Full rationale: design D9 in
`tools/bridge_energy_per_cycle.py` — that sibling tool computes the same
quantity from a live USB-console capture (no SD pull; agrees with SD to ~3 %).

Sanity anchors (reproduce these before trusting new numbers): halted-Pi
baseline reads 0.42–0.44 W at ~23.9 V; bmcam000 on 20/10 measured
0.2256–0.2275 Wh/cycle across independent runs. Prior results:
`runs/sprint10_overnight_20260729/` (pacing A/B) and
`runs/power_review_20260730/` (schedule comparison, 15/15 vs 20/10).

## 10. Quick reference — the pipeline in order

1. `PWR.csv` (all prefixes) → buoy solar/batt/bus V·I, `solarP/busP/netP`, battT,
   `stat`/`fault`.
2. `bm/<node>/*_power.log` addr-65 → per-node through-power → rank to get order →
   chain-subtract → per-node own draw.
3. `ERR.log` charger transitions → thermal episodes (shaded).
4. `HTU.csv` + `bm/86dd/*_hum_temp.log` → enclosure & underwater temps.
5. Convert UTC→PDT (−7), decimate, emit compact JSON.
6. Render self-contained canvas HTML; combine; screenshot-verify.

Sign rule to remember: **+ = power in (source), − = power out (draw)**.
Charging blocks at **fault==6** (~43 °C); resumes ~40 °C.
