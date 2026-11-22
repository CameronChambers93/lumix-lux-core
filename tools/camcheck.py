"""Read-only check of the Python runtime against a real camera: python3 tools/camcheck.py [ip]   (JS twin: camcheck.mjs)

Probe, connect (pairs; sends playmode if the camera sits in rec), identify (unicast SSDP + device description +
capability), match a driver, list, dates of the 3 newest items, a 1-byte Range read of the newest original.
No Remote commands. Close the PC page / phone app first: the camera serves one app at a time."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import luxcamera as lux  # noqa: E402

ip = sys.argv[1] if len(sys.argv) > 1 else "10.0.0.220"
drivers = lux.load_drivers()
base = lux.resolve_driver("panasonic-camcgi", drivers)
t0 = time.time()
step = lambda name, v: print(f"{time.time() - t0:6.2f}s  {name}: {v}", flush=True)

cam = lux.Camera(base, ip, app_name="lux-camcheck")
probe = cam.probe(3)
step("probe", probe)
if probe != "ok":
    sys.exit(f"camera at {ip} is {probe or 'not answering'}: is it on Wi-Fi (Remote Shooting & View) and free of other apps?")
step("connect", {k: v for k, v in cam.connect().items() if k != "state"})
location = None
for sender, reply in lux.ssdp_search(lux.ssdp_request(base, multicast=False), (ip, base["discovery"]["ssdp"]["port"]), 2):
    s = lux.parse_ssdp(reply, base)
    if s["matches"]:
        location = s["location"]
        break
step("ssdp location", location)
info = cam.identify(location)
step("identify", info)
m = lux.match_driver(drivers, info)
step("driver", f"{m['driver']['id']} (matched={m['matched']} by {m['by']})")
cds = cam.cds_url
cam = lux.Camera(m["driver"], ip, app_name="lux-camcheck")
cam.cds_url = cds
items = cam.list()
step("list", f"{len(items)} items, {sum(i['kind'] == 'video' for i in items)} videos, newest {items[0]['name'] if items else '-'}")
for it in items[:3]:
    step(f"date {it['name']}", cam.date(it))
if items:
    req = cam.media_request(items[0], "orig")
    with cam.t.open(req["url"], 15, {"Range": "bytes=100-100"}) as r:
        step("range", f"HTTP {r.status} {r.headers.get('Content-Range')}")
print(json.dumps({"ok": True, "driver": m["driver"]["id"], "items": len(items)}))
