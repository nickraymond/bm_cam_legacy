import glob,re,csv,datetime,json,numpy as np,pandas as pd
UTC=datetime.timezone.utc
def isoms(s): return int(datetime.datetime.strptime(s[:23],"%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=UTC).timestamp()*1000)
def decim(x,y,n=5000):
    if len(x)<=n: return list(x),list(y)
    idx=np.linspace(0,len(x)-1,n).astype(int); return [x[i] for i in idx],[y[i] for i in idx]
B="/root/work/20260723_SD Card Upload/"

# --- Sofar temp module (86dd, node 2) reported temp ---
mt=[];mx=[]
for f in glob.glob(B+"bm/86dd97882ce5cb50/*_hum_temp.log"):
    for line in open(f):
        m=re.match(r'^([\d\-T:.]+)Z.*temp:\s*([\d.\-]+)',line)
        if m:
            v=float(m.group(2))
            if v<25: continue     # drop sensor boot-default (~20.9C) readings
            mx.append(isoms(m.group(1))); mt.append(v)
o=np.argsort(mx); mx=[mx[i] for i in o]; mt=[mt[i] for i in o]
mx,mt=decim(mx,mt)

# --- Battery temp + charge fault (PWR.csv) ---
bx=[];bt=[];fault=[]
for f in [B+"log/0000_PWR.csv",B+"log/0001_PWR.csv",B+"log/0002_PWR.csv"]:
    for r in csv.reader(open(f)):
        try:t=float(r[0])
        except:continue
        if t<1e9 or len(r)<10:continue
        temp=float(r[7])
        if temp<-100: temp=None
        bx.append(int(t*1000)); bt.append(temp); fault.append(int(r[9]))
o=np.argsort(bx); bx=[bx[i] for i in o]; bt=[bt[i] for i in o]; fault=[fault[i] for i in o]
# exceedance markers
exx=[bx[i] for i in range(len(bx)) if fault[i]==6 and bt[i] is not None]
ext=[bt[i] for i in range(len(bx)) if fault[i]==6 and bt[i] is not None]

# --- Spotter internal enclosure temp (HTU) ---
hx=[];ht=[]
for f in glob.glob(B+"log/*_HTU.csv"):
    for r in csv.reader(open(f)):
        try:t=float(r[0])
        except:continue
        if t<1e9 or len(r)<2:continue
        hx.append(int(t*1000)); ht.append(float(r[1]))
o=np.argsort(hx); hx=[hx[i] for i in o]; ht=[ht[i] for i in o]
hx,ht=decim(hx,ht)

# --- thermal episodes ---
ev=[]
for f in glob.glob(B+"log/*_ERR.log"):
    for line in open(f):
        if "ChargerErrorState" in line:
            ms=isoms(re.match(r'^([\d\-T:.]+)Z',line).group(1))
            if "to THERMAL_FAULT" in line: ev.append((ms,"s"))
            elif "THERMAL_FAULT to" in line: ev.append((ms,"c"))
ev.sort(); eps=[]; cur=None
for ms,k in ev:
    if k=="s" and cur is None: cur=ms
    elif k=="c" and cur is not None: eps.append([cur,ms]); cur=None
if cur is not None: eps.append([cur,bx[-1]])

LIMIT=43.0  # charge thermal cutoff (charging trips off ~43C)
def clean(v): return None if v is None else round(float(v),3)
DATA={"tzoff":-7,"xmin":min(bx[0],mx[0]),"xmax":max(bx[-1],mx[-1]),"limit":LIMIT,"episodes":eps,
 "series":[
   {"n":"Sofar temp module (node 2, underwater)","c":"#2a7ab0","x":[int(v) for v in mx],"y":[clean(v) for v in mt]},
   {"n":"Battery temp (buoy charger)","c":"#e8843a","x":[int(v) for v in bx],"y":[clean(v) for v in bt]},
   {"n":"Spotter internal temp (enclosure/HTU)","c":"#7b4fa0","x":[int(v) for v in hx],"y":[clean(v) for v in ht]},
 ],
 "marks":{"n":"Battery exceeded thermal rating (charging blocked)","c":"#d62728","x":[int(v) for v in exx],"y":[round(v,2) for v in ext]},
 "battTmax":round(max(v for v in bt if v is not None),1),"modTmax":round(max(mt),1),"intTmax":round(max(ht),1)}
json.dump(DATA,open("/root/work/temp_data.json","w"),separators=(",",":"))
print("module pts",len(mx),"batt pts",len(bx),"exceed pts",len(exx),"episodes",len(eps),"bytes",__import__("os").path.getsize("/root/work/temp_data.json"))
