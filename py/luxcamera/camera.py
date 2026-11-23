"""The camera, for the panasonic-camcgi family. Same API as js/lux-camera.js (snake_case):

    cam = Camera(resolve_driver("panasonic-zs100", load_drivers()), "10.0.0.220", app_id=...)
    cam.connect(); items = cam.list(); when = cam.date(items[0])

The transport (default: UrllibTransport) has get(url, timeout, headers) and post(url, body, timeout, headers), both
returning Response(status, headers {lowercase: value}, body str), and optionally connect(host, port, timeout).
Every request goes through guard() first."""
from __future__ import annotations

import re
import threading
import time
from urllib.parse import quote

from .guard import Refused, guard
from .parse import (browse_body, parse_browse, parse_capability, parse_cds_control, parse_device_info, parse_state,
                    sort_items, ssdp_request)
from .transport import UrllibTransport


class Busy(Exception):
    def __init__(self):
        super().__init__("The camera is connected to another device. Close it there and try again.")


class Unsupported(Exception):
    def __init__(self, what: str):
        super().__init__(f"This camera's driver doesn't support {what}")


def _fill(q: str, **vars) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: quote(str(vars[m.group(1)]), safe="-_.!~*'()") if m.group(1) in vars else m.group(0), q)


class Camera:
    def __init__(self, driver: dict, ip: str, transport=None, app_id: str | None = None, app_name: str = "lumix-lux",
                 port_map: dict | None = None, sleep=time.sleep):
        if driver.get("protocol") != "panasonic-camcgi":
            raise ValueError(f'No runtime for protocol "{driver.get("protocol")}"')
        self.driver = driver
        self.ip = ip
        self.t = transport or UrllibTransport()
        self.app_id = app_id or driver["appIdPrefix"] + "000000000000"
        self.app_name = app_name
        self.port_map = {int(k): v for k, v in (port_map or {}).items()}
        self.sleep = sleep
        self.features = driver["features"]
        self.last_state: dict | None = None
        self.cds_url: str | None = None
        self.lock = threading.Lock()  # control commands go one at a time: the camera garbles overlapping requests
        self.remote = Remote(self)

    def url(self, port: int, path: str, query: str = "") -> str:
        p = self.port_map.get(port, port)
        return f"http://{self.ip}{'' if p == 80 else ':%d' % p}{path}{'?' + query if query else ''}"

    def control_url(self, query: str) -> str:
        c = self.driver["control"]
        return self.url(c["port"], c["path"], query)

    def get(self, url: str, timeout: float | None = None, headers: dict | None = None):
        guard(url)
        return self.t.get(url, timeout if timeout is not None else self.driver["control"]["timeoutMs"] / 1000, headers or {})

    def post(self, url: str, body: str, timeout: float = 30, headers: dict | None = None):
        guard(url, "POST", body, headers or {})
        return self.t.post(url, body, timeout, headers or {})

    def control(self, query: str, timeout: float | None = None) -> str:
        """One cam.cgi request -> body. Raises Busy for the "another app has it" answers, RuntimeError for other non-200s."""
        c = self.driver["control"]
        with self.lock:
            r = self.get(self.control_url(query), timeout)
        if r.status == c["busyStatus"] or c["busy"] in r.body:
            raise Busy()
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        return r.body

    def probe(self, timeout: float = 1.5):
        """Is there a camera of this family at this address? "ok", "busy" or None. Never raises."""
        c = self.driver["control"]
        try:
            r = self.get(self.control_url(c["probe"]["query"]), timeout)
            if r.status == 200:
                return ("busy" if c["busy"] in r.body else "ok") if c["probe"]["match"] in r.body else None
            if r.status != c["busyStatus"]:
                return None
        except Exception:
            return None
        if not hasattr(self.t, "connect"):
            return "busy"
        try:
            bp = self.driver["discovery"]["busyPort"]
            self.t.connect(self.ip, self.port_map.get(bp, bp), timeout)
            return "busy"
        except OSError:
            return None

    def state(self) -> dict:
        st = parse_state(self.control(self.driver["control"]["state"]["query"]), self.driver)
        if st["busy"]:
            raise Busy()
        self.last_state = st
        return st

    def pair(self) -> str:
        p = self.driver["control"]["pair"]
        r = self.control(_fill(p["query"], appId=self.app_id, appName=self.app_name), p["timeoutMs"] / 1000).strip()
        if not r.startswith(p["okPrefix"]):
            raise RuntimeError(f"Camera refused the connection ({r})")
        return r

    def connect(self) -> dict:
        """getstate, pair, getstate again if it wasn't ok, then media mode. Returns the state."""
        st = self.state()
        self.pair()
        if not st["ok"]:
            st = self.state()
        self.ensure_media(st)
        return self.last_state

    def ensure_media(self, st: dict | None = None) -> bool:
        """Media access wants the camera in its media (playback) mode; it drops back to rec by itself. True if it switched."""
        st = st or self.state()
        m = self.driver["control"]["modes"]["media"]
        if not st.get("mode") or st["mode"] == m["value"]:
            return False
        self.control(m["query"], 8)
        self.sleep(self.driver["control"]["settleMs"] / 1000)
        if self.last_state:
            self.last_state["mode"] = m["value"]
        return True

    def capability(self) -> dict:
        return parse_capability(self.control(self.driver["control"]["capability"]["query"], 8), self.driver)

    def identify(self, location: str | None = None) -> dict:
        """What the camera says it is: {capabilityModel, modelNumber, friendlyName, firmware, manufacturer} as far as known."""
        info = {}
        try:
            info["capabilityModel"] = self.capability()["model"]
        except Exception:
            pass
        if location:
            r = self.get(location, 8)
            if r.status == 200:
                d = parse_device_info(r.body, self.driver)
                info.update(modelNumber=d["model"], friendlyName=d["name"], firmware=d["firmware"], manufacturer=d["manufacturer"])
                self.cds_url = parse_cds_control(r.body, location, self.driver) or self.cds_url
        return info

    def cds(self) -> str:
        m = self.driver["media"]
        return self.cds_url or self.url(m["controlPort"], m["controlPath"])

    def list(self) -> list[dict]:
        """Every item, newest first. A weak link can cut a response short: retry a page asking for fewer items each time."""
        m, out, start, total = self.driver["media"], [], 0, None
        headers = {"Content-Type": 'text/xml; charset="utf-8"',
                   "SOAPACTION": '"urn:schemas-upnp-org:service:ContentDirectory:1#Browse"'}
        while total is None or start < total:
            page, last_err = None, None
            for count in [m["pageSize"]] + [c for c in m["retryCounts"] if c < m["pageSize"]]:
                try:
                    r = self.post(self.cds(), browse_body(self.driver, start, count), headers=headers)
                    if r.status != 200:
                        raise RuntimeError(f"Listing failed (HTTP {r.status})")
                    page = parse_browse(r.body, self.driver)
                    break
                except Refused:
                    raise
                except Exception as e:
                    last_err = e
                    self.sleep(0.4)
            if page is None:
                raise last_err
            out += page["items"]
            total = page["total"]
            if not page["returned"]:
                break
            start += page["returned"]
        return sort_items(out)

    def date(self, item: dict):
        """The capture date as the camera has it (local wall time, no zone), from one tiny request, or None."""
        m = self.driver["media"]
        r = self.get(item.get(m["dateFrom"]) or item["orig"], 8, {"Range": "bytes=0-0"} if m["range"] else {})
        return r.headers.get(m["dateHeader"].lower())

    def date_from_headers(self, headers) -> str | None:
        """The date header from any media response's headers (a dict or an http.client message)."""
        return headers.get(self.driver["media"]["dateHeader"]) if headers is not None else None

    def media_request(self, item: dict, role: str = "orig", offset: int = 0) -> dict:
        """What to fetch for an item (role: orig | thumb | preview), resuming at offset when the camera supports Range."""
        url = item.get(role)
        if not url:
            raise Unsupported(f"a {role} for {item['name']}")
        guard(url)
        return {"url": url, "headers": {"Range": f"bytes={offset}-"} if offset and self.driver["media"]["range"] else {}}

    def open(self, item: dict, role: str = "orig", offset: int = 0, timeout: float = 30):
        """A streaming response for an item (urllib transport only): read() it in chunks, check .status for 206."""
        req = self.media_request(item, role, offset)
        return self.t.open(req["url"], timeout, req["headers"])

    def slice(self, name: str) -> dict:
        """Slices of the driver for native code / helpers. URLs are built and guard-checked here."""
        d, c = self.driver, self.driver["control"]

        def checked(o):
            for v in o.values():
                if isinstance(v, str) and v.startswith(("http:", "https:")):
                    guard(v)
                elif isinstance(v, dict):
                    checked(v)
            return o

        if name == "save":
            return checked({"keepaliveUrl": self.control_url(c["state"]["query"]), "keepaliveMs": c["keepaliveMs"],
                            "mediaMode": {"value": c["modes"]["media"]["value"], "url": self.control_url(c["modes"]["media"]["query"]),
                                          "modeTag": c["state"]["modeTag"], "settleMs": c["settleMs"]},
                            "pairUrl": self.control_url(_fill(c["pair"]["query"], appId=self.app_id, appName=self.app_name)),
                            "pairOkPrefix": c["pair"]["okPrefix"],
                            "okMatch": f'<{c["state"]["resultTag"]}>{c["state"]["okValue"]}</{c["state"]["resultTag"]}>',
                            "busy": c["busy"], "busyStatus": c["busyStatus"],
                            "dateHeader": d["media"]["dateHeader"], "slots": d["media"]["slots"], "range": d["media"]["range"]})
        if name == "discover":
            return discover_slice(d)
        if name == "liveView":
            if not d["features"]["liveView"]:
                raise Unsupported("live view")
            lv = d["remote"]["liveView"]
            return checked({"port": lv["port"], "startUrl": self.control_url(_fill(lv["start"], port=lv["port"])),
                            "stopUrl": self.control_url(lv["stop"]), "jpegScan": lv["jpegScan"]})
        raise ValueError(f'Unknown slice "{name}"')


def discover_slice(driver: dict) -> dict:
    c, s = driver["control"], driver["discovery"]["ssdp"]
    return {"ssdp": {"port": s["port"], "target": s["target"], "request": ssdp_request(driver), "replyContains": s["replyContains"]},
            "probe": {"port": c["port"], "path": c["path"], "query": c["probe"]["query"], "match": c["probe"]["match"],
                      "busyStatus": c["busyStatus"], "busyPort": driver["discovery"]["busyPort"]},
            "hotspot": driver.get("hotspot")}


class Remote:
    def __init__(self, cam: Camera):
        self.cam = cam

    def need(self, feature: str):
        if not self.cam.features.get(feature):
            raise Unsupported(feature)

    def cmd(self, name: str, timeout: float = 6, **vars) -> dict:
        q = self.cam.driver["remote"]["commands"].get(name)
        if not q:
            raise Unsupported(name)
        st = parse_state(self.cam.control(_fill(q, **vars), timeout), self.cam.driver)
        if not st["ok"]:
            raise RuntimeError(f"The camera answered {st['result']}")
        return st

    def start(self, udp_port: int | None = None):
        """Rec mode, then the stream to udp_port (the caller receives the UDP frames)."""
        self.need("remote")
        d = self.cam.driver
        rec = d["control"]["modes"]["rec"]
        self.cam.control(rec["query"], 8)
        for _ in range(10):
            if self.cam.state()["mode"] == rec["value"]:
                break
            self.cam.sleep(0.3)
        if d["features"]["liveView"]:
            lv = d["remote"]["liveView"]
            self.cam.control(_fill(lv["start"], port=udp_port or lv["port"]), 6)

    def stop(self):
        d = self.cam.driver
        if d["features"]["liveView"]:
            try:
                self.cam.control(d["remote"]["liveView"]["stop"], 4)
            except Exception:
                pass
        self.cam.control(d["control"]["modes"]["media"]["query"], 8)

    def capture(self):
        self.need("remote")
        return self.cmd("capture", 12)

    def video_start(self):
        self.need("video")
        self.need("remote")
        return self.cmd("videoStart", 8)

    def video_stop(self):
        self.need("remote")
        return self.cmd("videoStop", 8)

    def zoom(self, direction: str):  # "wide" | "tele" | "stop"
        if self.cam.features.get("zoom", "none") == "none":
            raise Unsupported("zoom")
        return self.cmd({"wide": "zoomWide", "tele": "zoomTele", "stop": "zoomStop"}[direction], 4)

    def focus(self, fx: float, fy: float):  # 0..1 across the live view
        self.need("touchFocus")
        s = self.cam.driver["remote"]["focusScale"]
        return self.cmd("focus", x=round(fx * s), y=round(fy * s))
