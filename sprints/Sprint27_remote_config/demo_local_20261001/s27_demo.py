"""Local demo: the Sprint27 endpoints on a scratch Postgres (TestClient, fake Sofar). Writes JSON."""
from datetime import datetime, timedelta, timezone
import json, os, sys
from pathlib import Path
sys.path.insert(0, "backend/tests/fixtures/s6a_wire")
import s6a_db
assert s6a_db.setup("s27_demo")
from fastapi.testclient import TestClient
from sqlalchemy import text
from backend.app import models
from backend.app.auth import require_admin, require_view_or_admin
from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.services import sofar_commands as SC
OUT = Path("/Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/magical-edison-3a1195/sprints/Sprint27_remote_config/demo_local_20261001")
DEV = s6a_db.DEVICE
app.dependency_overrides[require_admin] = lambda: None
app.dependency_overrides[require_view_or_admin] = lambda: None
calls = []
app.dependency_overrides[SC.get_transport] = lambda: (lambda url, body, timeout: (calls.append(body) or (202, '{"status":"queued"}')))
SC.requests_transport = None
c = TestClient(app)
s6a_db.fresh_schema()
now = datetime.now(timezone.utc)
with SessionLocal() as db:
    db.execute(text("UPDATE external_gateways SET token_env_var='SOFAR_DEMO'")); db.commit()
    db.add(models.ConfigSnapshot(hash="580ce986", kv={"mode.media": "video", "commands.enabled": "1",
        "camera.controls_enabled": "0", "camera.exposure.enabled": "0", "camera.exposure.ev": "null",
        "video.record.framing": "wide_1080p_lean", "video.record.crop": "null", "video.record.output": "null",
        "video.record.sensor_mode": "null", "video.record.fps": "15", "video.send.size": "480x270",
        "video.send.fps": "10", "video.send.duration_s": "5.0"}, first_seen_at=now, last_seen_at=now, first_device_id=DEV))
    db.add(models.DeviceCommand(device_id=DEV, command_id=1000000, id_range="remote", verb="ping", lane="sofar",
        sender="admin", command_json='{"id":1000000,"c":"ping"}', created_at=now - timedelta(days=3, minutes=5),
        sent_at=now - timedelta(days=3, minutes=5), sent_status=202, ack_ok=True, ack_h="580ce986",
        ack_at=now - timedelta(days=3), external_system_id=s6a_db.SYSTEM))
    db.add(models.DeviceConfigSighting(device_id=DEV, hash="580ce986", via="ack", seen_at=now - timedelta(days=3), ref="1000000", sources={}))
    db.add(models.DeviceConfigSighting(device_id=DEV, hash="580ce986", via="ws", seen_at=now - timedelta(hours=1), ref="", sources={}))
    db.commit()
os.environ.update({"SOFAR_DEMO": "x", "BM_REMOTE_CONFIG": "1", "BM_REMOTE_CONFIG_DEVICES": DEV,
                   "BM_COMMAND_SEND": "1", "BM_COMMAND_SEND_DEVICES": DEV})
V = f"/devices/{DEV}/remote-config"
def save(name, r, trim=None):
    body = r.json()
    if trim: body = trim(body)
    (OUT / f"{name}.json").write_text(json.dumps({"request": f"{r.request.method} {r.request.url.path}",
        "request_body": json.loads(r.request.content or b"null"), "status": r.status_code, "response": body}, indent=1) + "\n")
    print(f"{r.status_code} {name}")
cat = c.get("/remote-config/catalog")
save("01_catalog_excerpt", cat, lambda b: {**{k: v for k, v in b.items() if k != "keys"},
     "keys": [k for k in b["keys"] if k["path"] in ("camera.exposure.ev", "video.record.framing", "commands.enabled")],
     "note": "excerpt: 3 of %d keys" % len(b["keys"])})
save("02_device_view_before", c.get(V), lambda b: {**b, "keys": [k for k in b["keys"] if k["path"].startswith("camera.exposure")]})
save("03_plan_refused_1080p30", c.post(V + "/plan", json={"set": {"video.record.fps": 30}}))
save("04_plan_too_big", c.post(V + "/plan", json={"set": {"camera.controls_enabled": True, "camera.exposure.enabled": True,
     "camera.exposure.ev": -1.0, "camera.exposure.shutter_us": 20000, "camera.exposure.analogue_gain": 2.0,
     "camera.white_balance.enabled": True, "camera.white_balance.mode": "daylight"}}))
save("05_change_not_writable", c.post(V + "/changes", json={"set": {"commands.listen_tail_s": 0.0}}))
ch = c.post(V + "/changes", json={"set": {"camera.controls_enabled": True, "camera.exposure.enabled": True, "camera.exposure.ev": -1.0}})
save("06_change_exposure", ch)
cid = ch.json()["command_id"]
save("07_existing_admin_send", c.post(f"/admin/devices/{DEV}/commands/{cid}/send"))
save("08_device_view_after_send", c.get(V), lambda b: {**b, "keys": [k for k in b["keys"] if k["path"].startswith("camera.exposure")]})
print("sofar fake received:", calls)
