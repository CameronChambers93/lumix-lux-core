"""Python runtime tests: python3 -m unittest discover -s tests   (the JS twin is tests/runtime.test.mjs; both read tests/cases/*.json)"""
import json
import random
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "py"))
import luxcamera as lux  # noqa: E402

DRIVERS = lux.load_drivers()
SCHEMA = lux.load_schema()


def like(actual, expect, path="$"):
    """expect is partial for objects (only the keys it names), exact for lists and scalars."""
    if isinstance(expect, dict):
        assert isinstance(actual, dict), f"{path}: expected an object, got {actual!r}"
        for k, v in expect.items():
            like(actual.get(k), v, f"{path}.{k}")
    elif isinstance(expect, list):
        assert isinstance(actual, list), f"{path}: expected a list, got {actual!r}"
        assert len(actual) == len(expect), f"{path}: length {len(actual)} != {len(expect)}"
        for i, v in enumerate(expect):
            like(actual[i], v, f"{path}[{i}]")
    else:
        assert actual == expect, f"{path}: {actual!r} != {expect!r}"


def _validate_all():
    for d in DRIVERS.values():
        assert lux.validate(d, SCHEMA) == [], d["id"]
    return {}


FNS = {
    "denyReason": lambda url, opts=None: {"denied": lux.deny_reason(url, **{k: v for k, v in (opts or {}).items()}) is not None},
    "parseState": lux.parse_state, "parseCapability": lux.parse_capability, "parseDeviceInfo": lux.parse_device_info,
    "parseCdsControl": lux.parse_cds_control, "parseSsdp": lux.parse_ssdp, "parseBrowse": lux.parse_browse,
    "sortItems": lux.sort_items,
    "resolveDriver": lambda i, extra=None: lux.resolve_driver(i, {**DRIVERS, **(extra or {})}),
    "matchDriver": lambda info: lux.match_driver(DRIVERS, info),
    "validate": lambda v: lux.validate(v, SCHEMA),
    "validateAll": _validate_all,
}


class Cases(unittest.TestCase):
    def test_cases(self):
        n = 0
        for f in sorted((CORE / "tests" / "cases").glob("*.json")):
            for c in json.loads(f.read_text(encoding="utf-8")):
                with self.subTest(f"{f.name}: {c['name']}"):
                    args = c["args"] + ([lux.resolve_driver(c["driver"], DRIVERS)] if c.get("driver") else [])
                    if c.get("throws"):
                        with self.assertRaises(Exception) as cm:
                            FNS[c["fn"]](*args)
                        self.assertIn(c["throws"], str(cm.exception))
                    else:
                        like(FNS[c["fn"]](*args), c["expect"])
                n += 1
        self.assertGreater(n, 30)


IP = "127.0.0.2"  # the JS suite uses 127.0.0.3, so both can run at once


class Fakecam:
    def __init__(self, mode):
        self.port = random.randint(20000, 40000)
        self.p = subprocess.Popen([sys.executable, str(CORE / "tools" / "fakecam.py"), IP, mode, str(self.port),
                                   "--driver", str(CORE / "drivers" / "panasonic-zs100.json"), "--items", "120"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                self.stats()
                break
            except urllib.error.HTTPError:  # busy mode answers 503 to everything: it's up
                break
            except OSError:
                time.sleep(0.1)

    def stats(self):
        with urllib.request.urlopen(f"http://{IP}:{self.port}/_stats", timeout=2) as r:
            return json.loads(r.read())

    def stop(self):
        self.p.kill()
        self.p.wait()


class EndToEnd(unittest.TestCase):
    def test_zs100(self):
        fc = Fakecam("ok")
        self.addCleanup(fc.stop)
        pm = {80: fc.port, 60606: fc.port}
        zs100 = lux.resolve_driver("panasonic-zs100", DRIVERS)
        cam = lux.Camera(zs100, IP, app_name="lux-test", port_map=pm, sleep=lambda s: None)

        self.assertEqual(cam.probe(), "ok")
        st = cam.connect()
        self.assertTrue(st["ok"])
        self.assertEqual(st["mode"], "play")

        cam.control("mode=camcmd&value=recmode")  # the camera drops back to rec by itself
        self.assertTrue(cam.ensure_media())
        self.assertEqual(fc.stats()["cammode"], "play")
        self.assertFalse(cam.ensure_media())

        info = cam.identify(f"http://{IP}:{fc.port}/A0CC2B248B00/Server0/ddd")
        like(info, {"capabilityModel": "TZ100", "modelNumber": "DMC-ZS100", "friendlyName": "ZS100-FAKE"})
        like(lux.match_driver(DRIVERS, info), {"matched": True, "driver": {"id": "panasonic-zs100"}})

        items = cam.list()
        self.assertEqual(len(items), 120)
        self.assertEqual(items[0]["id"], "1070119")
        self.assertEqual(items[-1]["id"], "1070000")
        self.assertEqual(sum(i["kind"] == "video" for i in items), 17)
        like(next(i for i in items if i["id"] == "1070003"), {"name": "P1070003.MP4", "kind": "video", "dur": 8})

        self.assertEqual(cam.date(items[0]), "2026-11-01T13:59:00")
        with cam.open(items[0], "orig", 1000) as r:
            self.assertEqual(r.status, 206)
            self.assertRegex(r.headers["Content-Range"], rf"^bytes 1000-\d+/{items[0]['size']}$")
            self.assertEqual(cam.date_from_headers(r.headers), "2026-11-01T13:59:00")
            self.assertEqual(len(r.read()), items[0]["size"] - 1000)

        like(cam.slice("save"), {"keepaliveUrl": f"http://{IP}:{fc.port}/cam.cgi?mode=getstate", "dateHeader": "X-REC_DATE_TIME", "slots": 2})
        like(cam.slice("liveView"), {"port": 49199, "stopUrl": f"http://{IP}:{fc.port}/cam.cgi?mode=stopstream"})
        like(lux.discover_slice(zs100), {"probe": {"query": "mode=getstate", "busyPort": 60606}, "hotspot": {"ip": "192.168.54.1"}})

        cam.remote.start(49199)
        self.assertEqual(fc.stats()["cammode"], "rec")
        cam.remote.zoom("tele")
        cam.remote.zoom("stop")
        cam.remote.focus(0.5, 0.25)
        cam.remote.capture()
        cam.remote.stop()
        s = fc.stats()
        self.assertEqual(s["cammode"], "play")
        self.assertEqual(s["cmds"][-8:], ["mode=camcmd&value=recmode", "mode=startstream&value=49199", "mode=camcmd&value=tele-normal",
                                          "mode=camcmd&value=zoomstop", "mode=camctrl&type=touch&value=500/250&value2=on",
                                          "mode=camcmd&value=capture", "mode=stopstream", "mode=camcmd&value=playmode"])

        with self.assertRaises(lux.Refused):
            cam.control("mode=camcmd&value=delete")
        with self.assertRaises(lux.Refused):
            cam.get(f"http://{IP}:{fc.port}/cam.cgi?mode=setsetting&type=clock&value=1")

        cautious = lux.Camera(lux.match_driver(DRIVERS, {"capabilityModel": "GH5"})["driver"], IP, port_map=pm, sleep=lambda s: None)
        self.assertEqual(len(cautious.list()), 120)  # the gallery works
        with self.assertRaises(lux.Unsupported):  # Remote doesn't
            cautious.remote.start()
        with self.assertRaises(lux.Unsupported):
            cautious.slice("liveView")

        self.assertEqual(fc.stats()["denied"], [])

    def test_busy(self):
        fc = Fakecam("busy")
        self.addCleanup(fc.stop)
        cam = lux.Camera(lux.resolve_driver("panasonic-zs100", DRIVERS), IP, port_map={80: fc.port, 60606: fc.port})
        self.assertEqual(cam.probe(), "busy")
        with self.assertRaises(lux.Busy):
            cam.state()


if __name__ == "__main__":
    unittest.main()
