import json,datetime
D=open("/root/work/temp_data.json").read(); s=json.loads(D)
def fmt(ms): return datetime.datetime.utcfromtimestamp(ms/1000+s["tzoff"]*3600).strftime("%b %d %H:%M")
span0=fmt(s["xmin"]); span1=fmt(s["xmax"])
eprows="".join(f"<tr><td>{fmt(a)}</td><td>{fmt(b)}</td><td>{(b-a)/3600000:.1f} h</td></tr>" for a,b in s["episodes"])
HTML=r"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Nereus BM — Three Temperature Sources</title>
<style>
 html,body{margin:0;background:#fafafa;color:#1f2937;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
 .wrap{max-width:1060px;margin:0 auto;padding:16px}
 h1{font-size:18px;margin:2px 0}
 .sub{font-size:12.5px;color:#667085;margin:6px 0 10px}
 .row{display:flex;gap:14px;flex-wrap:wrap;align-items:flex-start}
 .kpi{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:8px 13px;font-size:12.5px}
 .kpi b{font-size:16px}
 table{border-collapse:collapse;font-size:12px} td,th{padding:2px 9px;border-bottom:1px solid #eee;text-align:left}
 #legend{margin:10px 0;display:flex;flex-wrap:wrap;gap:8px}
 .chip{font-size:12px;border:1px solid #e0e0e0;border-radius:14px;padding:3px 11px;cursor:pointer;user-select:none;display:flex;align-items:center;gap:6px;background:#fff}
 .chip .sw{width:12px;height:12px;border-radius:3px;display:inline-block}
 .chip.off{opacity:.4;text-decoration:line-through}
 #cwrap{position:relative;background:#fff;border:1px solid #e5e7eb;border-radius:10px}
 #tt{position:absolute;pointer-events:none;background:rgba(255,255,255,.97);border:1px solid #ccc;border-radius:6px;padding:6px 9px;font-size:12px;box-shadow:0 2px 10px rgba(0,0,0,.12);display:none;z-index:5}
 .btn{font-size:12px;border:1px solid #ccc;background:#fff;border-radius:6px;padding:4px 11px;cursor:pointer}
</style></head><body><div class="wrap">
<h1>Three temperature sources — underwater module · battery · Spotter enclosure</h1>
<div class="sub">__SPAN0__ → __SPAN1__ (PDT). <b>Scroll</b> to zoom · <b>drag</b> to pan · <b>double-click</b> to reset. Red dots = battery over its charge thermal rating (charging blocked). Dashed line = ~__LIMIT__ °C cutoff. Shaded = THERMAL_FAULT. Left axis °C, right axis °F. Self-contained.</div>
<div class="row">
 <div class="kpi">Enclosure peak<br><b>__IMAX__ °C</b></div>
 <div class="kpi">Battery peak<br><b>__BMAX__ °C</b></div>
 <div class="kpi">Module peak<br><b>__MMAX__ °C</b></div>
 <div class="kpi">Battery over limit<br><table><tr><th>Start</th><th>End</th><th>Dur</th></tr>__EPROWS__</table></div>
</div>
<div style="margin:10px 0"><button class="btn" onclick="resetView()">Reset zoom</button></div>
<div id="legend"></div>
<div id="cwrap"><canvas id="cv"></canvas><div id="tt"></div></div>
<div class="sub">The sealed electronics enclosure runs hottest (sun on the buoy), the battery next, and the in-water Sofar module stays coolest — the surface heat, not the water, is what drives the battery past its charge limit.</div>
</div>
<script>
const D=__DATA__;const TZ=D.tzoff*3600*1000;const NS=D.series.length;
const cv=document.getElementById('cv'),ctx=cv.getContext('2d'),tt=document.getElementById('tt');
let view=[D.xmin,D.xmax];const P={l:52,r:50,t:16,b:34};let W=0,H=0,DPR=Math.max(1,devicePixelRatio||1);
const vis={m:true};for(let i=0;i<NS;i++)vis[i]=true;
function size(){const w=document.getElementById('cwrap');W=w.clientWidth;H=Math.max(430,Math.min(640,innerHeight-260));cv.style.width=W+'px';cv.style.height=H+'px';cv.width=W*DPR|0;cv.height=H*DPR|0;ctx.setTransform(DPR,0,0,DPR,0,0);}
function step(r){const S=[60e3,300e3,600e3,900e3,1800e3,3600e3,7200e3,10800e3,21600e3,43200e3,86400e3];const t=r/7;for(const x of S)if(x>=t)return x;return S[S.length-1];}
function ft(ms){const d=new Date(ms+TZ),p=n=>String(n).padStart(2,'0');const M=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getUTCMonth()];return M+' '+d.getUTCDate()+' '+p(d.getUTCHours())+':'+p(d.getUTCMinutes());}
function xP(x){return P.l+(x-view[0])/(view[1]-view[0])*(W-P.l-P.r);}
function xV(px){return view[0]+(px-P.l)/(W-P.l-P.r)*(view[1]-view[0]);}
function bs(a,v){let lo=0,hi=a.length;while(lo<hi){const m=(lo+hi)>>1;if(a[m]<v)lo=m+1;else hi=m;}return lo;}
function yr(){let mn=Infinity,mx=-Infinity;for(let si=0;si<NS;si++){if(!vis[si])continue;const S=D.series[si];let i0=Math.max(0,bs(S.x,view[0])-1),i1=Math.min(S.x.length,bs(S.x,view[1])+1);for(let i=i0;i<i1;i++){const y=S.y[i];if(y==null)continue;if(y<mn)mn=y;if(y>mx)mx=y;}}
 if(mn===Infinity){mn=20;mx=60;}mn=Math.min(mn,D.limit);mx=Math.max(mx,D.limit);const pd=(mx-mn)*0.08||1;return[mn-pd,mx+pd];}
function draw(hx){ctx.clearRect(0,0,W,H);const pw=W-P.l-P.r,ph=H-P.t-P.b,[ymn,ymx]=yr();const yP=y=>P.t+ph-(y-ymn)/(ymx-ymn)*ph;
 for(const[a,b]of D.episodes){const x0=Math.max(P.l,xP(a)),x1=Math.min(W-P.r,xP(b));if(x1>x0)ctx.fillStyle='rgba(214,39,40,.07)',ctx.fillRect(x0,P.t,x1-x0,ph);}
 ctx.strokeStyle='#e8e8e8';ctx.strokeRect(P.l,P.t,pw,ph);
 ctx.font='11px sans-serif';ctx.textAlign='right';ctx.textBaseline='middle';
 for(let k=0;k<=5;k++){const yv=ymn+(ymx-ymn)*k/5,py=yP(yv);ctx.strokeStyle='#f3f3f3';ctx.beginPath();ctx.moveTo(P.l,py);ctx.lineTo(P.l+pw,py);ctx.stroke();
   ctx.textAlign='right';ctx.fillStyle='#667085';ctx.fillText(yv.toFixed(0)+'°',P.l-6,py);
   ctx.textAlign='left';ctx.fillStyle='#98929c';ctx.fillText((yv*9/5+32).toFixed(0)+'°',P.l+pw+7,py);}
 const st=step(view[1]-view[0]),s0=Math.ceil((view[0]+TZ)/st)*st-TZ;ctx.strokeStyle='#f3f3f3';
 for(let x=s0;x<=view[1];x+=st){const px=xP(x);if(px<P.l||px>W-P.r)continue;ctx.beginPath();ctx.moveTo(px,P.t);ctx.lineTo(px,P.t+ph);ctx.stroke();ctx.fillStyle='#667085';ctx.font='10.5px sans-serif';ctx.textAlign='center';ctx.textBaseline='top';ctx.fillText(ft(x),px,P.t+ph+5);}
 const ly=yP(D.limit);ctx.strokeStyle='#d62728';ctx.setLineDash([6,4]);ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(P.l,ly);ctx.lineTo(P.l+pw,ly);ctx.stroke();ctx.setLineDash([]);
 ctx.fillStyle='#d62728';ctx.font='10.5px sans-serif';ctx.textAlign='left';ctx.textBaseline='bottom';ctx.fillText('charge thermal limit ~'+D.limit+'°C',P.l+4,ly-2);
 ctx.save();ctx.beginPath();ctx.rect(P.l,P.t,pw,ph);ctx.clip();
 for(let si=0;si<NS;si++){if(!vis[si])continue;const S=D.series[si];let i0=Math.max(0,bs(S.x,view[0])-1),i1=Math.min(S.x.length,bs(S.x,view[1])+1);ctx.strokeStyle=S.c;ctx.lineWidth=1.7;ctx.beginPath();let pen=false;for(let i=i0;i<i1;i++){const y=S.y[i];if(y==null){pen=false;continue;}const px=xP(S.x[i]),py=yP(y);if(!pen){ctx.moveTo(px,py);pen=true;}else ctx.lineTo(px,py);}ctx.stroke();}
 if(vis.m){const M=D.marks;let i0=Math.max(0,bs(M.x,view[0])),i1=Math.min(M.x.length,bs(M.x,view[1])+1);ctx.fillStyle=M.c;for(let i=i0;i<i1;i++){ctx.beginPath();ctx.arc(xP(M.x[i]),yP(M.y[i]),2.6,0,6.283);ctx.fill();}}
 ctx.restore();
 if(hx!=null){const px=xP(hx);ctx.strokeStyle='#aaa';ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(px,P.t);ctx.lineTo(px,P.t+ph);ctx.stroke();ctx.setLineDash([]);
  for(let si=0;si<NS;si++){if(!vis[si])continue;const S=D.series[si];let idx=bs(S.x,hx);if(idx>=S.x.length)idx=S.x.length-1;if(idx>0&&Math.abs(S.x[idx-1]-hx)<Math.abs(S.x[idx]-hx))idx--;const y=S.y[idx];if(y==null)continue;ctx.fillStyle=S.c;ctx.beginPath();ctx.arc(xP(S.x[idx]),yP(y),3.6,0,6.283);ctx.fill();ctx.strokeStyle='#fff';ctx.stroke();}}
}
function tip(mx,my,hx){let r='<b>'+ft(hx)+' PDT</b>';for(let si=0;si<NS;si++){if(!vis[si])continue;const S=D.series[si];let idx=bs(S.x,hx);if(idx>=S.x.length)idx=S.x.length-1;if(idx>0&&Math.abs(S.x[idx-1]-hx)<Math.abs(S.x[idx]-hx))idx--;const y=S.y[idx];if(y==null)continue;r+='<br><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:'+S.c+';margin-right:5px"></span>'+S.n+': <b>'+y.toFixed(1)+' °C</b>';}tt.innerHTML=r;tt.style.display='block';let tx=mx+14,ty=my+10;if(tx+tt.offsetWidth>W)tx=mx-tt.offsetWidth-14;tt.style.left=tx+'px';tt.style.top=ty+'px';}
let drag=false,lx=0,raf=null;function render(hx){if(raf)cancelAnimationFrame(raf);raf=requestAnimationFrame(()=>draw(hx));}
cv.addEventListener('wheel',e=>{e.preventDefault();const xc=xV(e.offsetX),f=Math.exp(e.deltaY*.0012);let a=xc-(xc-view[0])*f,b=xc+(view[1]-xc)*f;if(b-a<60000){const m=(a+b)/2;a=m-3e4;b=m+3e4;}a=Math.max(D.xmin,a);b=Math.min(D.xmax,b);if(b>a){view=[a,b];render(xc);}},{passive:false});
cv.addEventListener('mousedown',e=>{drag=true;lx=e.offsetX;});addEventListener('mouseup',()=>drag=false);
cv.addEventListener('mousemove',e=>{const mx=e.offsetX,my=e.offsetY;if(drag){const dx=(mx-lx)/(W-P.l-P.r)*(view[1]-view[0]);lx=mx;let a=view[0]-dx,b=view[1]-dx;if(a<D.xmin){b+=D.xmin-a;a=D.xmin;}if(b>D.xmax){a-=b-D.xmax;b=D.xmax;}view=[a,b];tt.style.display='none';render(null);return;}if(mx<P.l||mx>W-P.r||my<P.t||my>H-P.b){tt.style.display='none';render(null);return;}const hx=xV(mx);render(hx);tip(mx,my,hx);});
cv.addEventListener('mouseleave',()=>{tt.style.display='none';render(null);});cv.addEventListener('dblclick',()=>resetView());
function resetView(){view=[D.xmin,D.xmax];render(null);}
const lg=document.getElementById('legend');const items=D.series.map((S,i)=>({k:i,n:S.n,c:S.c}));items.push({k:'m',n:D.marks.n,c:D.marks.c});
items.forEach(it=>{const c=document.createElement('div');c.className='chip';c.innerHTML='<span class="sw" style="background:'+it.c+'"></span>'+it.n;c.onclick=()=>{vis[it.k]=!vis[it.k];c.classList.toggle('off');render(null);};lg.appendChild(c);});
addEventListener('resize',()=>{size();render(null);});size();resetView();
</script></body></html>"""
HTML=(HTML.replace("__DATA__",D).replace("__SPAN0__",span0).replace("__SPAN1__",span1)
 .replace("__LIMIT__",f"{s['limit']:.0f}").replace("__BMAX__",f"{s['battTmax']:.1f}").replace("__MMAX__",f"{s['modTmax']:.1f}")
 .replace("__IMAX__",f"{s['intTmax']:.1f}").replace("__EPROWS__",eprows))
open("/root/work/spotter_temp_compare.html","w").write(HTML)
print("wrote",len(HTML),"bytes; sources:",[x["n"] for x in s["series"]])
