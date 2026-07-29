import json,datetime
data=open("/root/work/data.json").read(); s=json.loads(data)
def fmt(ms): return datetime.datetime.utcfromtimestamp(ms/1000+s["tzoff"]*3600).strftime("%b %d %H:%M")
eprows="".join(f"<tr><td>{fmt(a)}</td><td>{fmt(b)}</td><td>{(b-a)/3600000:.1f} h</td></tr>" for a,b in s["episodes"])
ndrows="".join(f"<tr><td>{k}</td><td>{v[0]:.2f} W</td><td><b>{v[1]:.0f}%</b></td></tr>" for k,v in s["node_share"].items())
span0=fmt(s["xmin"]); span1=fmt(s["xmax"])

HTML = r"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Nereus BM Spotter — Interactive Time Series</title>
<style>
 html,body{margin:0;background:#fafafa;color:#222;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
 .wrap{max-width:1180px;margin:0 auto;padding:14px}
 h1{font-size:18px;margin:4px 0}
 .note{font-size:12px;color:#666;margin:6px 0}
 .cards{display:flex;gap:12px;flex-wrap:wrap;margin:8px 0}
 .card{background:#fff;border:1px solid #e5e5e5;border-radius:8px;padding:9px 13px;flex:1;min-width:250px}
 .card h3{margin:0 0 5px;font-size:12.5px;color:#555}
 table{border-collapse:collapse;width:100%;font-size:12px} td,th{padding:2px 7px;border-bottom:1px solid #eee;text-align:left}
 .pill{display:inline-block;background:#fdecec;color:#c0392b;border-radius:4px;padding:1px 6px;font-weight:600}
 #legend{margin:8px 0;display:flex;flex-wrap:wrap;gap:6px}
 .chip{font-size:11.5px;border:1px solid #ddd;border-radius:14px;padding:2px 10px;cursor:pointer;user-select:none;display:flex;align-items:center;gap:6px;background:#fff}
 .chip .sw{width:11px;height:11px;border-radius:3px;display:inline-block}
 .chip.off{opacity:.38;text-decoration:line-through}
 #cwrap{position:relative;background:#fff;border:1px solid #e5e5e5;border-radius:8px}
 #tt{position:absolute;pointer-events:none;background:rgba(255,255,255,.97);border:1px solid #ccc;border-radius:6px;padding:6px 8px;font-size:11.5px;box-shadow:0 2px 8px rgba(0,0,0,.12);display:none;z-index:5;max-width:280px}
 .btn{font-size:11.5px;border:1px solid #ccc;background:#fff;border-radius:5px;padding:3px 9px;cursor:pointer}
</style></head><body><div class="wrap">
<h1>Nereus BM Spotter — Interactive Time Series</h1>
<div class="note">__SPAN0__ → __SPAN1__ (PDT), ≈1.3 days. <b>Scroll</b> to zoom, <b>drag</b> to pan, <b>double-click</b> to reset. All panels share the time axis. Red band = charger in THERMAL_FAULT. Self-contained (no internet needed).</div>
<div class="cards">
 <div class="card"><h3>Q1 · Battery too hot to charge</h3>
  <table><tr><th>Fault start</th><th>Recovered</th><th>Dur</th></tr>__EPROWS__</table>
  <div class="note">Trips off ≈ <span class="pill">__CHGT__°C</span>, resumes after cooling to ≈ __SETT__°C. Peak __BATTMAX__°C. Battery V sags __BVMIN__–__BVMAX__ V.</div></div>
 <div class="card"><h3>Q2 · Node own draw (chain-subtracted), total __TOT__ W</h3>
  <table><tr><th>Node</th><th>Own draw</th><th>Share</th></tr>__NDROWS__</table>
  <div class="note">Series chain Ebox→Temp→Dev kit→Camera; each node's own draw = its through-reading minus the node below it. Ebox (7c7c) is the 24 V injector (source). Camera (f365) is last, on __CAMDUTY__% of the time (__CAMON__ W when capturing).</div></div>
</div>
<div style="margin:6px 0"><button class="btn" onclick="resetView()">Reset zoom</button></div>
<div id="legend"></div>
<div id="cwrap"><canvas id="cv"></canvas><div id="tt"></div></div>
<div class="note">Buoy rails (solar/batt/bus) ~3.8 V; BM nodes on a separate ~24 V bus. All power in watts. Times America/Los_Angeles (PDT, UTC−7).</div>
</div>
<script>
const DATA=__DATA__;
const TZ=DATA.tzoff*3600*1000, NP=DATA.panels.length;
const cv=document.getElementById('cv'), ctx=cv.getContext('2d'), tt=document.getElementById('tt');
let view=[DATA.xmin,DATA.xmax];
const PAD={l:62,r:16,t:20,b:34}, GAP=26;
let W=0,H=0,DPR=Math.max(1,window.devicePixelRatio||1);
function sizeCanvas(){const wrap=document.getElementById('cwrap');
  W=wrap.clientWidth; H=Math.max(720,Math.min(1040,window.innerHeight-120));
  cv.style.width=W+'px'; cv.style.height=H+'px'; cv.width=Math.round(W*DPR); cv.height=Math.round(H*DPR); ctx.setTransform(DPR,0,0,DPR,0,0);}
function niceStep(r){const S=[60e3,120e3,300e3,600e3,900e3,1800e3,3600e3,7200e3,10800e3,21600e3,43200e3,86400e3,172800e3];const t=r/7;for(const st of S)if(st>=t)return st;return S[S.length-1];}
function fmtTime(ms){const d=new Date(ms+TZ);const p2=n=>String(n).padStart(2,'0');const mon=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getUTCMonth()];return mon+' '+d.getUTCDate()+' '+p2(d.getUTCHours())+':'+p2(d.getUTCMinutes());}
function xToPx(x){return PAD.l+(x-view[0])/(view[1]-view[0])*(W-PAD.l-PAD.r);}
function pxToX(px){return view[0]+(px-PAD.l)/(W-PAD.l-PAD.r)*(view[1]-view[0]);}
function bs(a,v){let lo=0,hi=a.length;while(lo<hi){const m=(lo+hi)>>1;if(a[m]<v)lo=m+1;else hi=m;}return lo;}
function vr(sx){return [Math.max(0,bs(sx,view[0])-1),Math.min(sx.length,bs(sx,view[1])+1)];}
function yrange(p){let mn=Infinity,mx=-Infinity;for(const s of DATA.series){if(s.p!==p||!s.v)continue;const[i0,i1]=vr(s.x);for(let i=i0;i<i1;i++){const y=s.y[i];if(y==null)continue;if(y<mn)mn=y;if(y>mx)mx=y;}}
  if(mn===Infinity){mn=0;mx=1;} if(DATA.panels[p].z){mn=Math.min(mn,0);mx=Math.max(mx,0);} const pd=(mx-mn)*0.08||0.3;return[mn-pd,mx+pd];}
function draw(hx){ctx.clearRect(0,0,W,H);
  const plotW=W-PAD.l-PAD.r, ph=(H-PAD.t-PAD.b-GAP*(NP-1))/NP;
  for(const[a,b] of DATA.episodes){const x0=Math.max(PAD.l,xToPx(a)),x1=Math.min(W-PAD.r,xToPx(b));if(x1<=PAD.l||x0>=W-PAD.r)continue;ctx.fillStyle='rgba(214,39,40,0.07)';ctx.fillRect(x0,PAD.t,x1-x0,H-PAD.t-PAD.b);}
  const step=niceStep(view[1]-view[0]); const start=Math.ceil((view[0]+TZ)/step)*step-TZ;
  for(let p=0;p<NP;p++){const top=PAD.t+p*(ph+GAP),bot=top+ph;const[ymn,ymx]=yrange(p);const yp=y=>bot-(y-ymn)/(ymx-ymn)*ph;
    ctx.strokeStyle='#e8e8e8';ctx.lineWidth=1;ctx.strokeRect(PAD.l,top,plotW,ph);
    ctx.font='11px sans-serif';ctx.textAlign='right';ctx.textBaseline='middle';
    for(let k=0;k<=5;k++){const yv=ymn+(ymx-ymn)*k/5,py=yp(yv);ctx.strokeStyle=(Math.abs(yv)<1e-9&&DATA.panels[p].z)?'#bbb':'#f3f3f3';ctx.beginPath();ctx.moveTo(PAD.l,py);ctx.lineTo(PAD.l+plotW,py);ctx.stroke();ctx.fillStyle='#666';ctx.fillText(yv.toFixed(DATA.panels[p].u==='°C'?0:2),PAD.l-6,py);}
    ctx.textAlign='left';ctx.fillStyle='#333';ctx.font='bold 12px sans-serif';ctx.fillText(DATA.panels[p].t+'  ('+DATA.panels[p].u+')',PAD.l+2,top-8);
    ctx.strokeStyle='#f3f3f3';
    for(let x=start;x<=view[1];x+=step){const px=xToPx(x);if(px<PAD.l||px>W-PAD.r)continue;ctx.beginPath();ctx.moveTo(px,top);ctx.lineTo(px,bot);ctx.stroke();
      if(p===NP-1){ctx.fillStyle='#666';ctx.font='10.5px sans-serif';ctx.textAlign='center';ctx.textBaseline='top';ctx.fillText(fmtTime(x),px,bot+5);}}
    ctx.save();ctx.beginPath();ctx.rect(PAD.l,top,plotW,ph);ctx.clip();
    for(const s of DATA.series){if(s.p!==p||!s.v)continue;const[i0,i1]=vr(s.x);
      if(s.s==='markers'){ctx.fillStyle=s.c;for(let i=i0;i<i1;i++){const y=s.y[i];if(y==null)continue;ctx.beginPath();ctx.arc(xToPx(s.x[i]),yp(y),2.6,0,6.283);ctx.fill();}}
      else{ctx.strokeStyle=s.c;ctx.lineWidth=s.w||1.5;if(s.d)ctx.setLineDash([5,4]);else ctx.setLineDash([]);ctx.beginPath();let pen=false;
        for(let i=i0;i<i1;i++){const y=s.y[i];if(y==null){pen=false;continue;}const px=xToPx(s.x[i]),py=yp(y);if(!pen){ctx.moveTo(px,py);pen=true;}else ctx.lineTo(px,py);}ctx.stroke();ctx.setLineDash([]);}}
    ctx.restore();
    if(hx!=null)for(const s of DATA.series){if(s.p!==p||!s.v)continue;let idx=bs(s.x,hx);if(idx>=s.x.length)idx=s.x.length-1;if(idx>0&&Math.abs(s.x[idx-1]-hx)<Math.abs(s.x[idx]-hx))idx--;const y=s.y[idx];if(y==null)continue;ctx.fillStyle=s.c;ctx.beginPath();ctx.arc(xToPx(s.x[idx]),yp(y),3.4,0,6.283);ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=1;ctx.stroke();}
  }
  if(hx!=null){const px=xToPx(hx);ctx.strokeStyle='#999';ctx.setLineDash([3,3]);ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(px,PAD.t);ctx.lineTo(px,H-PAD.b);ctx.stroke();ctx.setLineDash([]);}
}
function showTip(mx,my,hx){let r='<b>'+fmtTime(hx)+' PDT</b>';
  for(let p=0;p<NP;p++)for(const s of DATA.series){if(s.p!==p||!s.v)continue;let idx=bs(s.x,hx);if(idx>=s.x.length)idx=s.x.length-1;if(idx>0&&Math.abs(s.x[idx-1]-hx)<Math.abs(s.x[idx]-hx))idx--;const y=s.y[idx];if(y==null)continue;
    r+='<br><span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:'+s.c+';margin-right:5px"></span>'+s.n+': <b>'+y.toFixed(2)+' '+DATA.panels[p].u+'</b>';}
  tt.innerHTML=r;tt.style.display='block';let tx=mx+14,ty=my+10;if(tx+tt.offsetWidth>W)tx=mx-tt.offsetWidth-14;if(ty+tt.offsetHeight>H)ty=my-tt.offsetHeight-10;tt.style.left=tx+'px';tt.style.top=ty+'px';}
let dragging=false,lastX=0,raf=null;
function render(hx){if(raf)cancelAnimationFrame(raf);raf=requestAnimationFrame(()=>draw(hx));}
cv.addEventListener('wheel',e=>{e.preventDefault();const xc=pxToX(e.offsetX),f=Math.exp(e.deltaY*0.0012);let r0=xc-(xc-view[0])*f,r1=xc+(view[1]-xc)*f;if(r1-r0<60000){const m=(r0+r1)/2;r0=m-30000;r1=m+30000;}r0=Math.max(DATA.xmin,r0);r1=Math.min(DATA.xmax,r1);if(r1>r0){view=[r0,r1];render(xc);}},{passive:false});
cv.addEventListener('mousedown',e=>{dragging=true;lastX=e.offsetX;});
window.addEventListener('mouseup',()=>dragging=false);
cv.addEventListener('mousemove',e=>{const mx=e.offsetX,my=e.offsetY;
  if(dragging){const dx=(mx-lastX)/(W-PAD.l-PAD.r)*(view[1]-view[0]);lastX=mx;let r0=view[0]-dx,r1=view[1]-dx;if(r0<DATA.xmin){r1+=DATA.xmin-r0;r0=DATA.xmin;}if(r1>DATA.xmax){r0-=r1-DATA.xmax;r1=DATA.xmax;}view=[r0,r1];tt.style.display='none';render(null);return;}
  if(mx<PAD.l||mx>W-PAD.r||my<PAD.t||my>H-PAD.b){tt.style.display='none';render(null);return;}
  const hx=pxToX(mx);render(hx);showTip(mx,my,hx);});
cv.addEventListener('mouseleave',()=>{tt.style.display='none';render(null);});
cv.addEventListener('dblclick',()=>resetView());
function resetView(){view=[DATA.xmin,DATA.xmax];render(null);}
const lg=document.getElementById('legend');
DATA.series.forEach(s=>{const c=document.createElement('div');c.className='chip'+(s.v?'':' off');c.innerHTML='<span class="sw" style="background:'+s.c+'"></span>'+s.n;c.onclick=()=>{s.v=!s.v;c.classList.toggle('off');render(null);};lg.appendChild(c);});
window.addEventListener('resize',()=>{sizeCanvas();render(null);});
sizeCanvas();resetView();
</script></body></html>"""
HTML=(HTML.replace("__DATA__",data).replace("__SPAN0__",span0).replace("__SPAN1__",span1)
      .replace("__EPROWS__",eprows).replace("__NDROWS__",ndrows).replace("__CHGT__",f"{s['chgT']:.0f}")
      .replace("__SETT__",f"{s['setT']:.0f}").replace("__BATTMAX__",f"{s['battTmax']:.0f}")
      .replace("__TOT__",f"{s['tot']:.2f}").replace("__BVMIN__",f"{s['battVmin']:.2f}").replace("__BVMAX__",f"{s['battVmax']:.2f}")
      .replace("__CAMDUTY__",f"{s['cam_duty']:.0f}").replace("__CAMON__",f"{s['cam_on']:.2f}"))
open("/root/work/spotter_timeseries.html","w").write(HTML)
print("wrote",len(HTML),"bytes, panels:",[p["t"] for p in s["panels"]])
