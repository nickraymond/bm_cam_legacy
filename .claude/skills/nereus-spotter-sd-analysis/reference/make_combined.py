import re
dash=open("/root/work/spotter_timeseries.html").read()
temp=open("/root/work/spotter_temp_compare.html").read()
def split(html):
    body=html.split('<body>',1)[1].split('<script>',1)[0]
    script=html.split('<script>',1)[1].rsplit('</script>',1)[0]
    return body,script
bM,sM=split(dash); bT,sT=split(temp)
def ns(t,S):
    for tok in ['cv','cwrap','tt','legend']:
        t=t.replace("getElementById('%s')"%tok,"getElementById('%s%s')"%(tok,S))
        t=t.replace('id="%s"'%tok,'id="%s%s"'%(tok,S))
    t=t.replace('onclick="resetView()"','id="btn%s"'%S)
    return t
bM,sM=ns(bM,'M'),ns(sM,'M'); bT,sT=ns(bT,'T'),ns(sT,'T')

CSS="""
 html,body{margin:0;background:#fafafa;color:#1f2937;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
 .wrap{max-width:1180px;margin:0 auto;padding:14px}
 h1{font-size:18px;margin:2px 0}
 .note,.sub{font-size:12.5px;color:#667085;margin:6px 0 10px}
 .cards,.row{display:flex;gap:14px;flex-wrap:wrap;margin:8px 0;align-items:flex-start}
 .card,.kpi{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:9px 13px;font-size:12.5px}
 .card{flex:1;min-width:250px}.card h3{margin:0 0 5px;font-size:12.5px;color:#555}
 .kpi b{font-size:16px}
 .pill{display:inline-block;background:#fdecec;color:#c0392b;border-radius:4px;padding:1px 6px;font-weight:600}
 table{border-collapse:collapse;font-size:12px} td,th{padding:2px 8px;border-bottom:1px solid #eee;text-align:left}
 #legendM,#legendT{margin:9px 0;display:flex;flex-wrap:wrap;gap:7px}
 .chip{font-size:12px;border:1px solid #e0e0e0;border-radius:14px;padding:3px 11px;cursor:pointer;user-select:none;display:flex;align-items:center;gap:6px;background:#fff}
 .chip .sw{width:12px;height:12px;border-radius:3px;display:inline-block}
 .chip.off{opacity:.4;text-decoration:line-through}
 #cwrapM,#cwrapT{position:relative;background:#fff;border:1px solid #e5e7eb;border-radius:10px}
 #ttM,#ttT{position:absolute;pointer-events:none;background:rgba(255,255,255,.97);border:1px solid #ccc;border-radius:6px;padding:6px 9px;font-size:12px;box-shadow:0 2px 10px rgba(0,0,0,.12);display:none;z-index:5}
 .btn{font-size:12px;border:1px solid #ccc;background:#fff;border-radius:6px;padding:4px 11px;cursor:pointer}
 hr.sep{border:none;border-top:1px solid #e2e4e8;max-width:1180px;margin:28px auto 0}
"""
OUT=("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Nereus BM Spotter — Field Test Analysis</title>"
 "<style>"+CSS+"</style></head><body>"
 +bM+'<hr class="sep">'+bT+
 "<script>\n(function(){\n"+sM+"\n;try{document.getElementById('btnM').onclick=resetView;}catch(e){}\n})();\n"
 "(function(){\n"+sT+"\n;try{document.getElementById('btnT').onclick=resetView;}catch(e){}\n})();\n</script></body></html>")
open("/root/work/spotter_analysis_full.html","w").write(OUT)
print("combined bytes:",len(OUT))
