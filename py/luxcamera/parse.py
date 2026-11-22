"""Pure parsers for the panasonic-camcgi family (the shared test cases call these). Regex-based like the JS runtime,
so both read the same replies the same way."""
from __future__ import annotations

import html
import re
from urllib.parse import urljoin

ENT = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}


def xml_unescape(s: str) -> str:
    def one(m):
        e = m.group(1)
        if e[0] == "#":
            return chr(int(e[2:], 16) if e[1].lower() == "x" else int(e[1:]))
        return ENT.get(e, m.group(0))
    return re.sub(r"&(#x[0-9a-fA-F]+|#\d+|\w+);", one, str(s))


def _inner(xml: str, tag: str):
    t = re.escape(tag)
    m = re.search(rf"<(?:[\w-]+:)?{t}(?:\s[^>]*)?>([\s\S]*?)</(?:[\w-]+:)?{t}\s*>", xml)
    return m.group(1) if m else None


def _text(xml: str, tag: str):
    v = _inner(xml, tag)
    if v is None:
        return None
    cdata = re.fullmatch(r"\s*<!\[CDATA\[([\s\S]*)\]\]>\s*", v)
    return cdata.group(1) if cdata else xml_unescape(v).strip()


def _attrs(s: str) -> dict:
    return {m.group(1): xml_unescape(m.group(3) if m.group(3) is not None else m.group(4))
            for m in re.finditer(r"""([\w:-]+)\s*=\s*("([^"]*)"|'([^']*)')""", s)}


def _elements(xml: str, tag: str) -> list[dict]:
    t = re.escape(tag)
    return [{"attrs": _attrs(m.group(1) or ""), "inner": m.group(2)}
            for m in re.finditer(rf"<(?:[\w-]+:)?{t}(\s[^>]*)?>([\s\S]*?)</(?:[\w-]+:)?{t}\s*>", xml)]


def parse_state(body: str, driver: dict) -> dict:
    s = driver["control"]["state"]
    res = _text(body, s["resultTag"])
    state = {}
    st = _inner(body, "state")
    if st is not None:
        for m in re.finditer(r"<([\w-]+)>([^<]*)</\1>", st):
            state[m.group(1)] = xml_unescape(m.group(2))
    return {"result": res, "ok": res == s["okValue"], "busy": driver["control"]["busy"] in body,
            "battery": state.get(s["batteryTag"]), "mode": state.get(s["modeTag"]), "state": state}


def parse_capability(body: str, driver: dict) -> dict:
    return {"model": _text(body, driver["control"]["capability"]["modelTag"])}


def parse_device_info(body: str, driver: dict) -> dict:
    t = driver["discovery"]["info"]
    return {"name": _text(body, t["nameTag"]), "model": _text(body, t["modelTag"]),
            "firmware": _text(body, t["firmwareTag"]), "manufacturer": _text(body, t["manufacturerTag"])}


def parse_ssdp(reply: str, driver: dict) -> dict:
    def h(name):
        m = re.search(rf"^{name}:\s*(.*?)\s*$", reply, re.I | re.M)
        return m.group(1) if m else None
    location, server = h("LOCATION"), h("SERVER")
    ip = re.match(r"\w+://([^/:]+)", location or "")
    lower = reply.lower()
    return {"location": location, "server": server, "ip": ip.group(1) if ip else None,
            "matches": any(s.lower() in lower for s in driver["discovery"]["ssdp"]["replyContains"])}


def ssdp_request(driver: dict, multicast: bool = True, target: str | None = None, mx: int = 1) -> str:
    host = "239.255.255.250:1900" if multicast else "{ip}:%d" % driver["discovery"]["ssdp"]["port"]
    return (f'M-SEARCH * HTTP/1.1\r\nHOST: {host}\r\nMAN: "ssdp:discover"\r\nMX: {mx}\r\n'
            f'ST: {target or driver["discovery"]["ssdp"]["target"]}\r\n\r\n')


def parse_cds_control(body: str, location: str, driver: dict):
    for svc in _elements(body, "service"):
        if (_text(svc["inner"], "serviceType") or "") == driver["discovery"]["ssdp"]["cdsTarget"]:
            u = _text(svc["inner"], "controlURL")
            return urljoin(location, u) if u else None
    return None


def browse_body(driver: dict, start: int, count: int) -> str:
    return ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><u:Browse '
            'xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1"><ObjectID>%s</ObjectID>'
            '<BrowseFlag>BrowseDirectChildren</BrowseFlag><Filter>*</Filter><StartingIndex>%d</StartingIndex>'
            '<RequestedCount>%d</RequestedCount><SortCriteria></SortCriteria></u:Browse></s:Body></s:Envelope>'
            % (html.escape(driver["media"]["objectId"]), start, count))


def _dur(s) -> int:
    m = re.match(r"(\d+):(\d+):(\d+)", s or "")
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) if m else 0


def _int(s) -> int:
    s = (s or "").strip()
    return int(s) if s.isdigit() else 0


def parse_browse(body: str, driver: dict) -> dict:
    """A Browse SOAP response -> {total, returned, items: [{id, name, size, kind, orig, thumb, preview, dur}]}."""
    m = driver["media"]
    didl = _text(body, "Result") or ""
    profile, id_ok = re.compile(m["resourceProfile"]), re.compile(m["idPattern"])
    role_of = {pn: role for role, pn in m["roles"].items()}
    items = []
    for it in _elements(didl, "item"):
        iid, res, dur = it["attrs"].get("id", ""), {}, 0
        for r in _elements(it["inner"], "res"):
            pn = profile.search(r["attrs"].get("protocolInfo", ""))
            role = role_of.get(pn.group(1)) if pn else None
            if not role or role in res:
                continue
            res[role] = {"url": xml_unescape(r["inner"]).strip(), "size": _int(r["attrs"].get("size"))}
            if role == "orig":
                dur = _dur(r["attrs"].get("duration"))
        if not id_ok.search(iid) or "orig" not in res:
            continue
        ext = res["orig"]["url"].rsplit(".", 1)[-1].upper()
        items.append({
            "id": iid, "name": m["nameTemplate"].replace("{id}", iid).replace("{ext}", ext), "size": res["orig"]["size"],
            "kind": "video" if "video" in (_text(it["inner"], "class") or "") else "photo",
            "orig": res["orig"]["url"], "thumb": res.get("thumb", {}).get("url"), "preview": res.get("preview", {}).get("url"),
            "dur": dur,
        })
    return {"total": _int(_text(body, "TotalMatches")), "returned": _int(_text(body, "NumberReturned")), "items": items}


def sort_items(items: list) -> list:
    """Newest first: numeric ids by value, others by string."""
    if all(i["id"].isdigit() for i in items):
        return sorted(items, key=lambda i: int(i["id"]), reverse=True)
    return sorted(items, key=lambda i: i["id"], reverse=True)
