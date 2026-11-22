# Fake Lumix for testing: fakecam.py <bind-ip> <ok|busy> [port] [also-listen-60606]
#
# Phase 5 behaviour: port 80 answers getstate/accctrl like a camera; "busy" answers 503 and listens on :60606.
# Phase 6 additions (environment variables, all optional):
#   FAKE_PHOTO=photo.jpg   served as /DO<anything>.JPG with X-REC_DATE_TIME and every EXIF date rewritten to the time in
#                          the query (?t=2026-11-06T15:30:00, local time) or "a minute ago"
#   FAKE_FRAMES=dir        *.jpg sent by UDP after mode=startstream (each datagram = 32-byte header + one JPEG)
#   FAKE_UDP=127.0.0.1     where the frames go (the emulator's `redir add udp:49199:49199` listens on host loopback)
#   FAKE_CLOCK=compact     which setsetting&type=clock value format is accepted: iso | compact | slash (default none)
# It also tracks cammode (recmode/playmode), logs shutter/zoom/touch/video commands and counts them at /_stats.
#
# Redesign phase 1: --driver <drivers/x.json> [--items N] makes it a whole camera on one port, for the runtimes' tests:
#   POST <media.controlPath>      UPnP Browse of N fake items (every 7th a video; at most 50 per page, like the ZS100)
#   GET /D[OST]<id>.JPG|MP4        fake bytes, X-REC_DATE_TIME (item n: 2026-11-01 12:00 + n minutes), Range
#   getinfo&type=capability        <model> from FAKE_MODEL (default TZ100); /A0CC2B248B00/Server0/ddd: device description
#   Any request the runtimes' denylist would refuse is recorded in /_stats "denied" (the tests expect none).
import sys, os, re, glob, time, socket, threading, json, html
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote_plus

def flag(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name); v = sys.argv[i + 1]; del sys.argv[i:i + 2]; return v
    return default
DRIVER_FILE, N_ITEMS = flag("--driver"), int(flag("--items", "120"))
DRIVER = None
if DRIVER_FILE:   # resolve "extends" from the same folder
    chain = [json.load(open(DRIVER_FILE))]
    while chain[-1].get("extends"): chain.append(json.load(open(os.path.join(os.path.dirname(DRIVER_FILE), chain[-1]["extends"] + ".json"))))
    def _merge(a, b): return {**a, **{k: _merge(a[k], v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v for k, v in b.items()}}
    DRIVER = {}
    for d in reversed(chain): DRIVER = _merge(DRIVER, d)
MODEL = os.environ.get("FAKE_MODEL", "TZ100")
ip, mode = sys.argv[1], sys.argv[2]
port = int(sys.argv[3]) if len(sys.argv) > 3 else 80
PHOTO = open(os.environ["FAKE_PHOTO"], "rb").read() if os.environ.get("FAKE_PHOTO") else None
FRAMES = [open(f, "rb").read() for f in sorted(glob.glob(os.path.join(os.environ["FAKE_FRAMES"], "*.jpg")))] if os.environ.get("FAKE_FRAMES") else []
UDP_HOST = os.environ.get("FAKE_UDP", "127.0.0.1")
CLOCK = os.environ.get("FAKE_CLOCK", "")
state = {"cammode": "play", "stream": None, "sent": 0, "cmds": [], "denied": [], "paired": False}

def denied(target):   # the runtimes' denylist (core/js/lux-camera.js guard), so a test notices anything that slipped through
    words = re.split(r"[^a-z0-9_]+", unquote_plus(target).lower())
    return any(w and (w.startswith(("del", "format", "setsetting", "setinfo")) or "delete" in w) for w in words)

def fake_items():   # newest (highest id) first, like the camera
    out = []
    for n in range(N_ITEMS):
        iid = str(1070000 + n); video = n % 7 == 3
        out.append({"id": iid, "n": n, "ext": "MP4" if video else "JPG", "video": video})
    return out[::-1]
ITEMS = {i["id"]: i for i in fake_items()}

def item_bytes(it, kind):
    size = {"O": 300000 + it["n"] * 17, "S": 20000, "T": 4000}[kind]
    head = b"\xff\xd8\xff\xe1" if it["ext"] == "JPG" or kind != "O" else b"\x00\x00\x00\x18ftypmp42"
    return (head + it["id"].encode() * (size // 7 + 1))[:size]

def item_date(it): return (datetime(2026, 11, 1, 12, 0) + timedelta(minutes=it["n"])).strftime("%Y-%m-%dT%H:%M:%S")

def browse_reply(body):
    g = lambda t: int((re.search(r"<%s>(\d+)</%s>" % (t, t), body) or [0, "0"])[1])
    start, count = g("StartingIndex"), min(g("RequestedCount"), 50)
    page = fake_items()[start:start + count]
    base = "http://%s:%d" % (ip, port)
    def res(it, pn, kind, size, mime, extra=""):
        ext = it["ext"] if kind == "O" else "JPG"
        return '<res protocolInfo="http-get:*:%s:DLNA.ORG_OP=01;PANASONIC.COM_PN=%s" size="%d"%s>%s/D%s%s.%s</res>' % (mime, pn, size, extra, base, kind, it["id"], ext)
    xs = []
    for it in page:
        cls = "object.item.videoItem.movie" if it["video"] else "object.item.imageItem"
        o = res(it, "CAM_ORG", "O", len(item_bytes(it, "O")), "video/mp4" if it["video"] else "image/jpeg", ' duration="0:00:%02d"' % (5 + it["n"] % 50) if it["video"] else "")
        xs.append('<item id="%s" parentID="0" restricted="0"><dc:title>x</dc:title><upnp:class>%s</upnp:class>%s%s%s</item>' % (
            it["id"], cls, o, res(it, "CAM_TN", "T", 4000, "image/jpeg"), res(it, "CAM_LRGTN", "S", 20000, "image/jpeg")))
    didl = '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">%s</DIDL-Lite>' % "".join(xs)
    return ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><u:BrowseResponse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">'
            '<Result>%s</Result><NumberReturned>%d</NumberReturned><TotalMatches>%d</TotalMatches><UpdateID>1</UpdateID></u:BrowseResponse></s:Body></s:Envelope>' % (html.escape(didl, quote=False), len(page), N_ITEMS)).encode()

DDD = ('<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device><friendlyName>ZS100-FAKE</friendlyName><manufacturer>Panasonic</manufacturer>'
       '<modelNumber>DMC-ZS100</modelNumber><X_FirmVersion>1.20</X_FirmVersion><serviceList><service><serviceType>urn:schemas-upnp-org:service:ContentDirectory:1</serviceType>'
       '<controlURL>/Server0/CDS_control</controlURL></service></serviceList></device></root>').encode()

def reply(result="ok"):
    return ('<?xml version="1.0"?><camrply><result>%s</result><state><batt>2/3</batt><cammode>%s</cammode></state></camrply>' % (result, state["cammode"])).encode()

def streamer(udp_port, token):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    n = 0
    while state["stream"] == token and FRAMES:
        pkt = b"\x00" * 30 + b"\x01\x02" + FRAMES[n % len(FRAMES)]   # header without FF D8
        s.sendto(pkt, (UDP_HOST, udp_port))
        state["sent"] += 1
        n += 1
        time.sleep(0.04)   # 25 fps: the app should thin this to 15
    s.close()

def photo_for(q):
    t = (q.get("t") or [None])[0]
    when = datetime.strptime(t, "%Y-%m-%dT%H:%M:%S") if t else datetime.now() - timedelta(seconds=60)
    exif = when.strftime("%Y:%m:%d %H:%M:%S").encode() + b"\x00"
    data = re.sub(rb"\d{4}:\d\d:\d\d \d\d:\d\d:\d\d\x00", exif, PHOTO)
    return data, when.strftime("%Y-%m-%dT%H:%M:%S")

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, f, *a): print("fakecam", ip, self.path, flush=True)
    def send(self, body, ctype="text/xml", extra=None):
        self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(body); self.wfile.flush()
        if len(body) > 100000:   # let the client read everything before the socket closes (the emulator's NAT lost the tail otherwise)
            try:
                self.connection.shutdown(socket.SHUT_WR); self.connection.settimeout(3); self.connection.recv(1)
            except Exception: pass
    def do_HEAD(self): self.do_GET()
    def do_GET(self):
        if mode == "busy":
            self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers(); return
        u = urlparse(self.path); q = parse_qs(u.query)
        g = lambda k: (q.get(k) or [""])[0]
        if u.path == "/_stats": return self.send(json.dumps({"cammode": state["cammode"], "sent": state["sent"], "cmds": state["cmds"], "denied": state["denied"]}).encode(), "application/json")
        if denied(self.path): state["denied"].append(self.command + " " + self.path)
        if DRIVER:
            mm = re.match(r"^/D([OST])(\d+)\.(JPG|MP4)$", u.path)
            if mm and mm.group(2) in ITEMS: return self.send_item(ITEMS[mm.group(2)], mm.group(1))
            if u.path.endswith("/Server0/ddd"): return self.send(DDD)
            if g("mode") == "getinfo" and g("type") == "capability":
                return self.send(('<?xml version="1.0"?><camrply><result>ok</result><comm_proto_ver>1.6</comm_proto_ver><product_info><model>%s</model></product_info></camrply>' % MODEL).encode())
        if PHOTO and re.match(r"^/D[OST].*\.JPG$", u.path):
            data, rec = photo_for(q)
            if self.command == "HEAD": return self.send_head_only(len(data), rec)
            rng = re.match(r"bytes=(\d+)-", self.headers.get("Range", ""))
            if rng and int(rng.group(1)) < len(data):
                a = int(rng.group(1)); part = data[a:]
                self.send_response(206); self.send_header("Content-Type", "image/jpeg"); self.send_header("Content-Length", str(len(part)))
                self.send_header("Content-Range", "bytes %d-%d/%d" % (a, len(data) - 1, len(data))); self.send_header("X-REC_DATE_TIME", rec); self.end_headers()
                self.wfile.write(part); self.wfile.flush(); return
            return self.send(data, "image/jpeg", {"X-REC_DATE_TIME": rec})
        if "accctrl" in self.path: return self.send(b"ok,ZS100-FAKE", "text/plain")
        m, v = g("mode"), g("value")
        if m in ("camcmd", "startstream", "stopstream", "camctrl", "setsetting"): state["cmds"].append(self.path.split("?", 1)[-1])
        if m == "camcmd" and v == "recmode": state["cammode"] = "rec"
        elif m == "camcmd" and v == "playmode": state["cammode"] = "play"
        elif m == "startstream":
            tok = object(); state["stream"] = tok
            threading.Thread(target=streamer, args=(int(v), tok), daemon=True).start()
        elif m == "stopstream": state["stream"] = None
        elif m == "setsetting" and g("type") == "clock":
            val = g("value")
            form = "iso" if re.match(r"^\d{4}-\d\d-\d\dT", val) else "compact" if re.match(r"^\d{14}$", val) else "slash" if "/" in val else ""
            if form != CLOCK: return self.send(reply("err_param"))
        elif m == "camcmd" and v in ("capture", "video_recstart", "video_recstop", "tele-normal", "wide-normal", "zoomstop"): pass
        elif m == "camcmd": return self.send(reply("err_non_support"))
        self.send(reply())
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        action = self.headers.get("SOAPACTION", "")
        if denied(self.path) or not re.search(r'#(Browse|Search)"?$', action): state["denied"].append("POST %s %s" % (self.path, action))
        if mode == "busy":
            self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers(); return
        if DRIVER and urlparse(self.path).path == DRIVER["media"]["controlPath"] and action.endswith('#Browse"'):
            return self.send(browse_reply(body), 'text/xml; charset="utf-8"')
        self.send_response(500); self.send_header("Content-Length", "0"); self.end_headers()
    def send_item(self, it, kind):
        data = item_bytes(it, kind); rec = item_date(it)
        ctype = "video/mp4" if kind == "O" and it["video"] else "image/jpeg"
        rng = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if rng and int(rng.group(1)) < len(data):
            a = int(rng.group(1)); b = min(int(rng.group(2)), len(data) - 1) if rng.group(2) else len(data) - 1
            part = data[a:b + 1] if self.command == "GET" else b""
            self.send_response(206); self.send_header("Content-Range", "bytes %d-%d/%d" % (a, b, len(data)))
        else:
            part = data if self.command == "GET" else b""
            self.send_response(200)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(part) if self.command == "GET" else len(data)))
        self.send_header("Accept-Ranges", "bytes"); self.send_header("X-REC_DATE_TIME", rec); self.send_header("X-FILE_SIZE", str(len(data)))
        self.end_headers(); self.wfile.write(part); self.wfile.flush()
    def send_head_only(self, n, rec):
        self.send_response(200); self.send_header("Content-Type", "image/jpeg"); self.send_header("Content-Length", str(n)); self.send_header("X-REC_DATE_TIME", rec); self.end_headers()

if mode == "busy" or (len(sys.argv) > 4):
    def l():
        s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind((ip, 60606)); s.listen(5)
        while True: s.accept()[0].close()
    threading.Thread(target=l, daemon=True).start()
ThreadingHTTPServer((ip, port), H).serve_forever()
