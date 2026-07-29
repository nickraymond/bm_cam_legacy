import glob,os,re,csv,datetime,json
import pandas as pd,numpy as np
UTC=datetime.timezone.utc
def isoms(s):
    dt=datetime.datetime.strptime(s[:23],"%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=UTC)
    return int(dt.timestamp()*1000)
def decim(x,y,n=6000):
    if len(x)<=n: return x,y
    idx=np.linspace(0,len(x)-1,n).astype(int); return [x[i] for i in idx],[y[i] for i in idx]
def clean(v): return None if (v is None or (isinstance(v,float) and np.isnan(v))) else round(float(v),4)

# ---- PWR ----
rows=[]
for f in ["log/0000_PWR.csv","log/0001_PWR.csv","log/0002_PWR.csv"]:
    for r in csv.reader(open(f)):
        try:t=float(r[0])
        except:continue
        if t<1e9 or len(r)<10:continue
        rows.append([t]+[float(x) for x in r[1:8]]+[int(r[8]),int(r[9])])
p=pd.DataFrame(rows,columns=["epoch","solarV","battV","busV","solarI","battI","busI","battT","stat","fault"]).sort_values("epoch")
p["ms"]=(p["epoch"]*1000).astype("int64")
p.loc[p["battT"]<-100,"battT"]=np.nan
p["solarP"]=p.solarV*p.solarI; p["busP"]=p.busV*p.busI; p["netP"]=p.solarP-p.busP
X=p["ms"].tolist()
h=pd.read_csv("log/0002_HTU.csv"); h.columns=["epoch","encT","encRH"]; h["ms"]=(h["epoch"]*1000).astype("int64")

# ---- Serial chain (confirmed): Ebox(7c7c, source) -> Temp(86dd) -> Dev kit(57ef) -> Camera(f365, last)
# addr65 = through-current (self + everything downstream). Own draw = reading - node below it.
CHAIN=["7c7c9fd9f087043a","86dd97882ce5cb50","57ef9a36411412f7","f365022b019e29bd"]
LAB={"7c7c9fd9f087043a":"Ebox (7c7c)","86dd97882ce5cb50":"Temp sensor (86dd)",
     "57ef9a36411412f7":"Dev kit (57ef)","f365022b019e29bd":"Camera (f365)"}
COL={"7c7c9fd9f087043a":"#8a8f98","86dd97882ce5cb50":"#54a24b","57ef9a36411412f7":"#4c78a8","f365022b019e29bd":"#e45756"}
def through(nid):
    d={}
    for pf in sorted(glob.glob("bm/"+nid+"/*_power.log")):
        for line in open(pf):
            m=re.match(r'^([\d\-T:.]+)Z.*addr:\s*65,\s*voltage:\s*([\d.\-]+),\s*current:\s*([\d.\-]+)',line)
            if m: d[isoms(m.group(1))]=abs(float(m.group(2))*float(m.group(3)))
    s=pd.Series(d).sort_index(); s.index=pd.to_datetime(s.index,unit='ms'); return s.resample('60s').mean()
thr=pd.DataFrame({nid:through(nid) for nid in CHAIN})
for nid in CHAIN[:-1]: thr[nid]=thr[nid].ffill()
thr[CHAIN[-1]]=thr[CHAIN[-1]].fillna(0.0)          # camera off => 0
thr=thr.dropna(subset=CHAIN[:-1])
node_series={}; node_mean={}
for i,nid in enumerate(CHAIN):
    own=(thr[nid]-thr[CHAIN[i+1]]).clip(lower=0) if i<len(CHAIN)-1 else thr[nid]
    ms=[int(t.value//1_000_000) for t in own.index]
    node_series[nid]=(ms,list(own.values)); node_mean[nid]=float(own.mean())
tot=sum(node_mean.values())
cam=thr[CHAIN[-1]]; cam_on=float(cam[cam>0.2].mean()); cam_duty=float((cam>0.2).mean())

# ---- thermal episodes ----
ev=[]
for f in ["log/0000_ERR.log","log/0001_ERR.log","log/0002_ERR.log"]:
    for line in open(f):
        if "ChargerErrorState" in line:
            ms=isoms(re.match(r'^([\d\-T:.]+)Z',line).group(1))
            if "to THERMAL_FAULT" in line: ev.append((ms,"s"))
            elif "THERMAL_FAULT to" in line: ev.append((ms,"c"))
ev.sort(); episodes=[]; cur=None
for ms,k in ev:
    if k=="s" and cur is None: cur=ms
    elif k=="c" and cur is not None: episodes.append([cur,ms]); cur=None
if cur is not None: episodes.append([cur,int(X[-1])])

def S(name,x,y,panel,color,style="line",dash=0,vis=1,width=1.6):
    x,y=decim(list(x),list(y))
    return {"n":name,"p":panel,"c":color,"s":style,"d":dash,"v":vis,"w":width,
            "x":[int(v) for v in x],"y":[clean(v) for v in y]}

series=[]
series.append(S("Solar voltage",X,p.solarV,0,"#e8a838"))                    # panel 0
series.append(S("Battery voltage",X,p.battV,1,"#159a8a",width=1.7))         # panel 1 (NEW)
# panel 2 power
series.append(S("Solar power in (source)",X,p.solarP,2,"#3fa34d",vis=0,width=1.2))
series.append(S("Buoy load (draw)",X,-p.busP,2,"#8a8f98",vis=0,width=1.2))
series.append(S("Spotter net (solar − load)",X,p.netP,2,"#1a1a1a",width=1.9))
for nid in CHAIN:
    xs,ys=node_series[nid]; series.append(S(LAB[nid]+" own draw",xs,[-v for v in ys],2,COL[nid],width=1.4))
# panel 3 temp
series.append(S("Battery temp",X,p.battT,3,"#4c78a8",width=1.5))
series.append(S("Enclosure temp (HTU)",h["ms"],h.encT,3,"#e45756",dash=1,vis=0,width=1.2))
mask=(p.fault==6)&p.battT.notna()
series.append(S("Charging BLOCKED (thermal)",p["ms"][mask],p.battT[mask],3,"#d62728",style="markers",vis=1))

panels=[{"t":"Solar voltage","u":"V","z":0},
        {"t":"Battery voltage","u":"V","z":0},
        {"t":"Power — Spotter & nodes  (node = own draw, chain-subtracted; Ebox→Temp→Devkit→Camera)","u":"W","z":1},
        {"t":"Temperature — battery (red = charging blocked, thermal fault)","u":"°C","z":0}]
out={"tzoff":-7,"panels":panels,"series":series,"episodes":episodes,"xmin":int(X[0]),"xmax":int(X[-1]),
     "setT":float(p.loc[p.fault==6,"battT"].min()),"chgT":float(p.loc[p.stat==2,"battT"].max()),
     "battTmax":float(p.battT.max()),
     "node_share":{LAB[n]:[round(node_mean[n],3),round(100*node_mean[n]/tot,1)] for n in CHAIN},
     "tot":round(tot,3),"cam_on":round(cam_on,2),"cam_duty":round(100*cam_duty),
     "battVmin":round(float(p.battV.min()),2),"battVmax":round(float(p.battV.max()),2)}
json.dump(out,open("/root/work/data.json","w"),separators=(",",":"))
print("bytes:",os.path.getsize("/root/work/data.json"))
print("TOTAL node draw (net, in-out): %.3f W"%tot)
for n,(w,pc) in out["node_share"].items(): print(f"  {n:22s} {w:.3f} W  {pc:4.1f}%")
print("battV range: %.2f - %.2f V"%(out["battVmin"],out["battVmax"]))
