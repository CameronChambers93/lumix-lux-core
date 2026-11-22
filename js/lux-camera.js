// Lumix Lux camera runtime (JS). An ES module with no dependencies, for the Android/iOS WebViews and node.
// The same API, in standard-library Python, is core/py/luxcamera; both pass core/tests/cases/*.json.
//
//   import { resolveDriver, matchDriver, Camera } from "./lux-camera.js";
//   const cam = new Camera(resolveDriver("panasonic-zs100", drivers), "10.0.0.220", { transport, appId });
//   await cam.connect(); const items = await cam.list(); const when = await cam.date(items[0]);
//
// The transport is injected (native on phones, fetch in node):
//   get(url, { timeout, headers })        -> { status, headers: { lowercase-name: value }, body: string }
//   post(url, body, { timeout, headers }) -> the same
// It throws on network errors. Every request goes through guard() first, whatever the driver says.

export const SCHEMA_VERSIONS = [1];
export const PROTOCOLS = ["panasonic-camcgi"];

export class Busy extends Error {
  constructor() { super("The camera is connected to another device. Close it there and try again."); this.name = "Busy"; }
}
export class Refused extends Error {
  constructor(reason) { super(`Refused to send: ${reason}`); this.name = "Refused"; this.reason = reason; }
}
export class Unsupported extends Error {
  constructor(what) { super(`This camera's driver doesn't support ${what}`); this.name = "Unsupported"; }
}

// ---- safety guard: nothing that deletes from a camera or changes its settings is ever sent.
// A request is refused when a word of its path or query (split on anything but letters, digits and _) starts with "del" or
// contains "delete", or starts with "format", "setsetting" or "setinfo". POSTs must be a SOAP Browse or Search.
const DENY = [/^del/, /delete/, /^format/, /^setsetting/, /^setinfo/];
const SOAP_ALLOWED = /#(Browse|Search)"?$/;
const SOAP_DENIED_BODY = /<\s*(?:[\w-]+:)?(Destroy|Delete|Update|Create|Import|Export|Move)\w*[\s/>]/i;

export function denyReason(url, { method = "GET", body = "", headers = {} } = {}) {
  let target = String(url).replace(/^[a-z]+:\/\/[^/]*/i, "");
  try { target = decodeURIComponent(target.replace(/\+/g, " ")); } catch { /* keep it encoded */ }
  for (const word of target.toLowerCase().split(/[^a-z0-9_]+/)) {
    if (word && DENY.some((re) => re.test(word))) return `"${word}" is on the denylist`;
  }
  if (method !== "GET" && method !== "HEAD") {
    const action = Object.entries(headers).find(([k]) => k.toLowerCase() === "soapaction")?.[1] || "";
    if (!SOAP_ALLOWED.test(action)) return `${method} is only allowed for a SOAP Browse or Search`;
    if (SOAP_DENIED_BODY.test(body)) return "the SOAP body has a write action";
  }
  return null;
}
export function guard(url, opts) {
  const why = denyReason(url, opts);
  if (why) throw new Refused(why);
}

// ---- drivers
const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);
function merge(base, over) {
  const out = { ...base };
  for (const [k, v] of Object.entries(over)) out[k] = isObj(v) && isObj(base[k]) ? merge(base[k], v) : v;
  return out;
}

// drivers: { id: raw driver JSON }. Returns the driver with its "extends" chain merged in (objects merge, the rest replaces).
export function resolveDriver(id, drivers) {
  const chain = [];
  for (let cur = id; cur; cur = drivers[cur].extends) {
    if (!drivers[cur]) throw new Error(`Unknown driver "${cur}"`);
    if (chain.includes(cur)) throw new Error(`Driver "${id}" extends itself`);
    chain.push(cur);
  }
  let out = {};
  for (const cur of [...chain].reverse()) out = merge(out, drivers[cur]);
  out.abstract = !!drivers[id].abstract;
  delete out.extends;
  out.chain = chain;
  if (!SCHEMA_VERSIONS.includes(out.schema)) throw new Error(`Driver "${id}" has schema ${out.schema}; this app understands ${SCHEMA_VERSIONS}`);
  if (!PROTOCOLS.includes(out.protocol)) throw new Error(`Driver "${id}" needs protocol "${out.protocol}"; this app needs an update`);
  return out;
}

// info: { modelNumber, friendlyName, capabilityModel } (any may be missing). The first concrete driver with a match key whose
// pattern matches the known value wins. Else the family base runs in cautious mode (its features: gallery and saving only).
export function matchDriver(drivers, info, protocol = "panasonic-camcgi") {
  const ids = Object.keys(drivers).sort();
  for (const id of ids) {
    const d = drivers[id];
    if (d.abstract || !d.match) continue;
    for (const [key, pattern] of Object.entries(d.match)) {
      if (info[key] != null && new RegExp(pattern).test(info[key])) return { driver: resolveDriver(id, drivers), matched: true, by: key };
    }
  }
  const base = ids.find((id) => drivers[id].abstract && drivers[id].protocol === protocol);
  if (!base) throw new Error(`No driver for protocol "${protocol}"`);
  return { driver: resolveDriver(base, drivers), matched: false, by: null };
}

// The subset of JSON Schema that schema/driver.schema.json uses. Returns a list of "path: problem" strings, empty when valid.
export function validate(value, schema, root = schema, path = "$") {
  if (schema.$ref) return validate(value, root.$defs[schema.$ref.replace("#/$defs/", "")], root, path);
  const errs = [];
  const type = Array.isArray(value) ? "array" : value === null ? "null" : Number.isInteger(value) ? "integer" : typeof value;
  if (schema.type && !(schema.type === type || (schema.type === "number" && type === "integer"))) return [`${path}: should be ${schema.type}`];
  if (schema.enum && !schema.enum.includes(value)) errs.push(`${path}: should be one of ${JSON.stringify(schema.enum)}`);
  if (schema.pattern && type === "string" && !new RegExp(schema.pattern).test(value)) errs.push(`${path}: doesn't match ${schema.pattern}`);
  if (schema.minimum != null && typeof value === "number" && value < schema.minimum) errs.push(`${path}: should be >= ${schema.minimum}`);
  if (type === "array") {
    if (schema.minItems != null && value.length < schema.minItems) errs.push(`${path}: should have at least ${schema.minItems} items`);
    if (schema.items) value.forEach((v, i) => errs.push(...validate(v, schema.items, root, `${path}[${i}]`)));
  }
  if (type === "object") {
    for (const k of schema.required || []) if (!(k in value)) errs.push(`${path}: missing "${k}"`);
    for (const [k, v] of Object.entries(value)) {
      const sub = schema.properties?.[k];
      if (sub) errs.push(...validate(v, sub, root, `${path}.${k}`));
      else if (schema.additionalProperties === false) errs.push(`${path}: unknown key "${k}"`);
      else if (isObj(schema.additionalProperties)) errs.push(...validate(v, schema.additionalProperties, root, `${path}.${k}`));
    }
  }
  return errs;
}

// ---- tiny XML reading (camera replies are small and flat; no DOMParser in node)
const ENT = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };
export function xmlUnescape(s) {
  return String(s).replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (m, e) =>
    e[0] === "#" ? String.fromCodePoint(e[1].toLowerCase() === "x" ? parseInt(e.slice(2), 16) : +e.slice(1)) : ENT[e] ?? m);
}
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
function inner(xml, tag) {   // first <tag> or <ns:tag>, raw inner text, or null
  const m = new RegExp(`<(?:[\\w-]+:)?${esc(tag)}(?:\\s[^>]*)?>([\\s\\S]*?)</(?:[\\w-]+:)?${esc(tag)}\\s*>`).exec(xml);
  return m ? m[1] : null;
}
function text(xml, tag) {
  const v = inner(xml, tag);
  if (v == null) return null;
  const cdata = /^\s*<!\[CDATA\[([\s\S]*)\]\]>\s*$/.exec(v);
  return cdata ? cdata[1] : xmlUnescape(v).trim();
}
function elements(xml, tag) {   // every <tag ...>inner</tag> -> { attrs, inner }
  const re = new RegExp(`<(?:[\\w-]+:)?${esc(tag)}(\\s[^>]*)?>([\\s\\S]*?)</(?:[\\w-]+:)?${esc(tag)}\\s*>`, "g");
  const out = [];
  for (let m; (m = re.exec(xml));) out.push({ attrs: attrs(m[1] || ""), inner: m[2] });
  return out;
}
function attrs(s) {
  const out = {};
  for (const m of s.matchAll(/([\w:-]+)\s*=\s*("([^"]*)"|'([^']*)')/g)) out[m[1]] = xmlUnescape(m[3] ?? m[4]);
  return out;
}

// ---- parsers (pure; the shared test cases call these)
export function parseState(body, driver) {
  const s = driver.control.state;
  const res = text(body, s.resultTag);
  const state = {};
  const st = inner(body, "state");
  if (st != null) for (const m of st.matchAll(/<([\w-]+)>([^<]*)<\/\1>/g)) state[m[1]] = xmlUnescape(m[2]);
  return {
    result: res, ok: res === s.okValue, busy: body.includes(driver.control.busy),
    battery: state[s.batteryTag] ?? null, mode: state[s.modeTag] ?? null, state,
  };
}

export function parseCapability(body, driver) {
  return { model: text(body, driver.control.capability.modelTag) };
}

export function parseDeviceInfo(body, driver) {
  const t = driver.discovery.info;
  return { name: text(body, t.nameTag), model: text(body, t.modelTag), firmware: text(body, t.firmwareTag), manufacturer: text(body, t.manufacturerTag) };
}

// An SSDP reply -> { location, server, ip, matches } (matches: it looks like this family's camera).
export function parseSsdp(reply, driver) {
  const h = (name) => new RegExp(`^${name}:\\s*(.*?)\\s*$`, "im").exec(reply)?.[1] ?? null;
  const location = h("LOCATION"), server = h("SERVER");
  const lower = reply.toLowerCase();
  return {
    location, server, ip: /^\w+:\/\/([^/:]+)/.exec(location || "")?.[1] ?? null,
    matches: driver.discovery.ssdp.replyContains.some((s) => lower.includes(s.toLowerCase())),
  };
}

export function ssdpRequest(driver, { multicast = true, target, mx = 1 } = {}) {
  const host = multicast ? "239.255.255.250:1900" : `{ip}:${driver.discovery.ssdp.port}`;
  return `M-SEARCH * HTTP/1.1\r\nHOST: ${host}\r\nMAN: "ssdp:discover"\r\nMX: ${mx}\r\nST: ${target || driver.discovery.ssdp.target}\r\n\r\n`;
}

// The ContentDirectory controlURL from a device description, made absolute against its LOCATION.
export function parseCdsControl(body, location, driver) {
  for (const svc of elements(body, "service")) {
    if ((text(svc.inner, "serviceType") || "") === driver.discovery.ssdp.cdsTarget) {
      const u = text(svc.inner, "controlURL");
      return u ? new URL(u, location).href : null;
    }
  }
  return null;
}

export function browseBody(driver, start, count) {
  return `<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><u:Browse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1"><ObjectID>${driver.media.objectId}</ObjectID><BrowseFlag>BrowseDirectChildren</BrowseFlag><Filter>*</Filter><StartingIndex>${start}</StartingIndex><RequestedCount>${count}</RequestedCount><SortCriteria></SortCriteria></u:Browse></s:Body></s:Envelope>`;
}

const int = (s) => (/^\d+$/.test(String(s ?? "").trim()) ? +String(s).trim() : 0);
const parseDur = (s) => { const m = /^(\d+):(\d+):(\d+)/.exec(s || ""); return m ? +m[1] * 3600 + +m[2] * 60 + +m[3] : 0; };

// A Browse SOAP response -> { total, returned, items: [{ id, name, size, kind, orig, thumb, preview, dur }] }.
export function parseBrowse(body, driver) {
  const m = driver.media;
  const total = int(text(body, "TotalMatches")), returned = int(text(body, "NumberReturned"));
  const didl = text(body, "Result") || "";
  const profile = new RegExp(m.resourceProfile), idOk = new RegExp(m.idPattern);
  const roleOf = Object.fromEntries(Object.entries(m.roles).map(([role, pn]) => [pn, role]));
  const items = [];
  for (const it of elements(didl, "item")) {
    const id = it.attrs.id || "", res = {};
    let dur = 0;
    for (const r of elements(it.inner, "res")) {
      const pn = profile.exec(r.attrs.protocolInfo || "")?.[1];
      const role = roleOf[pn];
      if (!role || res[role]) continue;
      res[role] = { url: xmlUnescape(r.inner).trim(), size: int(r.attrs.size) };
      if (role === "orig") dur = parseDur(r.attrs.duration);
    }
    if (!idOk.test(id) || !res.orig) continue;
    const ext = res.orig.url.split(".").pop().toUpperCase();
    items.push({
      id, name: m.nameTemplate.replace("{id}", id).replace("{ext}", ext), size: res.orig.size,
      kind: (text(it.inner, "class") || "").includes("video") ? "video" : "photo",
      orig: res.orig.url, thumb: res.thumb?.url ?? null, preview: res.preview?.url ?? null, dur,
    });
  }
  return { total, returned, items };
}

// Newest first: numeric ids by value, others by string.
export function sortItems(items) {
  if (items.every((i) => /^\d+$/.test(i.id))) return items.sort((a, b) => b.id - a.id);
  return items.sort((a, b) => (b.id < a.id ? -1 : b.id > a.id ? 1 : 0));
}

const fill = (q, vars) => q.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? encodeURIComponent(vars[k]) : m));
const header = (r, name) => r.headers?.[name.toLowerCase()] ?? null;

// ---- the camera
export class Camera {
  // opts: transport (required), appId, appName, portMap ({ 80: 18080 } for tests against fakecam), sleep(ms)
  constructor(driver, ip, opts) {
    if (driver.protocol !== "panasonic-camcgi") throw new Error(`No runtime for protocol "${driver.protocol}"`);
    this.driver = driver;
    this.ip = ip;
    this.t = opts.transport;
    this.appId = opts.appId || driver.appIdPrefix + "000000000000";
    this.appName = opts.appName || "lumix-lux";
    this.portMap = opts.portMap || {};
    this.sleep = opts.sleep || ((ms) => new Promise((ok) => setTimeout(ok, ms)));
    this.features = driver.features;
    this.lastState = null;
    this.cdsUrl = null;
    this.queue = Promise.resolve();   // control commands go one at a time: the camera garbles overlapping requests
    this.remote = new Remote(this);
  }

  url(port, path, query) {
    const p = this.portMap[port] ?? port;
    return `http://${this.ip}${p === 80 ? "" : ":" + p}${path}${query ? "?" + query : ""}`;
  }
  controlUrl(query) { return this.url(this.driver.control.port, this.driver.control.path, query); }

  async get(url, opts = {}) {
    guard(url);
    return this.t.get(url, { timeout: opts.timeout ?? this.driver.control.timeoutMs, headers: opts.headers || {} });
  }
  async post(url, body, opts = {}) {
    guard(url, { method: "POST", body, headers: opts.headers || {} });
    return this.t.post(url, body, { timeout: opts.timeout ?? 30000, headers: opts.headers || {} });
  }

  // One cam.cgi request -> body. Throws Busy for the "another app has it" answers, Error for any other non-200.
  control(query, timeout) {
    const run = async () => {
      const c = this.driver.control;
      const r = await this.get(this.controlUrl(query), { timeout });
      if (r.status === c.busyStatus || r.body.includes(c.busy)) throw new Busy();
      if (r.status !== 200) throw new Error(`HTTP ${r.status}`);
      return r.body;
    };
    const p = this.queue.then(run, run);
    this.queue = p.catch(() => {});
    return p;
  }

  // Is there a camera of this family at this address? "ok", "busy" or null. Never throws.
  async probe(timeout = 1500) {
    const c = this.driver.control;
    try {
      const r = await this.get(this.controlUrl(c.probe.query), { timeout });
      if (r.status === 200) return r.body.includes(c.probe.match) ? (r.body.includes(c.busy) ? "busy" : "ok") : null;
      if (r.status !== c.busyStatus) return null;
    } catch { return null; }
    if (!this.t.connect) return "busy";   // a 503 from a host we can't check further
    try { await this.t.connect(this.ip, this.portMap[this.driver.discovery.busyPort] ?? this.driver.discovery.busyPort, timeout); return "busy"; }
    catch { return null; }
  }

  async state() {
    const st = parseState(await this.control(this.driver.control.state.query), this.driver);
    if (st.busy) throw new Busy();
    this.lastState = st;
    return st;
  }

  async pair() {
    const p = this.driver.control.pair;
    const r = (await this.control(fill(p.query, { appId: this.appId, appName: this.appName }), p.timeoutMs)).trim();
    if (!r.startsWith(p.okPrefix)) throw new Error(`Camera refused the connection (${r})`);
    return r;
  }

  // getstate, pair, getstate again if it wasn't ok, then media mode. Returns the state.
  async connect() {
    let st = await this.state();
    await this.pair();
    if (!st.ok) st = await this.state();
    await this.ensureMedia(st);
    return this.lastState;
  }

  // Media access wants the camera in its media (playback) mode; it drops back to rec by itself. True if it switched.
  async ensureMedia(st) {
    st = st || (await this.state());
    const m = this.driver.control.modes.media;
    if (!st.mode || st.mode === m.value) return false;
    await this.control(m.query, 8000);
    await this.sleep(this.driver.control.settleMs);
    if (this.lastState) this.lastState.mode = m.value;
    return true;
  }

  async capability() { return parseCapability(await this.control(this.driver.control.capability.query, 8000), this.driver); }

  // What the camera says it is: { capabilityModel, modelNumber, friendlyName, firmware, manufacturer } as far as known.
  // The device description needs its LOCATION (from SSDP); pass it when the transport found one.
  async identify(location) {
    const info = {};
    try { info.capabilityModel = (await this.capability()).model; } catch { /* older models may not answer */ }
    if (location) {
      const r = await this.get(location, { timeout: 8000 });
      if (r.status === 200) {
        const d = parseDeviceInfo(r.body, this.driver);
        Object.assign(info, { modelNumber: d.model, friendlyName: d.name, firmware: d.firmware, manufacturer: d.manufacturer });
        this.cdsUrl = parseCdsControl(r.body, location, this.driver) || this.cdsUrl;
      }
    }
    return info;
  }

  cds() { return this.cdsUrl || this.url(this.driver.media.controlPort, this.driver.media.controlPath); }

  // Every item, newest first. A weak link can cut a response short: retry a page asking for fewer items each time.
  async list() {
    const m = this.driver.media, out = [];
    const headers = { "Content-Type": 'text/xml; charset="utf-8"', SOAPACTION: '"urn:schemas-upnp-org:service:ContentDirectory:1#Browse"' };
    for (let start = 0, total = Infinity; start < total;) {
      let page, lastErr;
      for (const count of [m.pageSize, ...m.retryCounts.filter((c) => c < m.pageSize)]) {
        try {
          const r = await this.post(this.cds(), browseBody(this.driver, start, count), { headers });
          if (r.status !== 200) throw new Error(`Listing failed (HTTP ${r.status})`);
          page = parseBrowse(r.body, this.driver);
          break;
        } catch (e) {
          if (e instanceof Refused) throw e;
          lastErr = e;
          await this.sleep(400);
        }
      }
      if (!page) throw lastErr;
      out.push(...page.items);
      total = page.total;
      if (!page.returned) break;
      start += page.returned;
    }
    return sortItems(out);
  }

  // The capture date as the camera has it (local wall time, no zone), from one tiny request, or null.
  async date(item) {
    const m = this.driver.media;
    const url = item[m.dateFrom] || item.orig;
    const r = await this.get(url, { timeout: 8000, headers: m.range ? { Range: "bytes=0-0" } : {} });
    return header(r, m.dateHeader);
  }
  dateFromHeaders(headers) { return headers?.[this.driver.media.dateHeader.toLowerCase()] ?? null; }

  // What to fetch for an item (role: orig | thumb | preview), resuming at offset when the camera supports Range.
  mediaRequest(item, role = "orig", offset = 0) {
    const url = item[role];
    if (!url) throw new Unsupported(`a ${role} for ${item.name}`);
    guard(url);
    return { url, headers: offset && this.driver.media.range ? { Range: `bytes=${offset}-` } : {} };
  }

  // Slices of the driver for native code (save queue, discovery, live view). URLs are built and guard-checked here.
  slice(name) {
    const d = this.driver, c = d.control;
    if (name === "save") return {
      keepaliveUrl: this.controlUrl(c.state.query), keepaliveMs: c.keepaliveMs,
      mediaMode: { value: c.modes.media.value, url: this.controlUrl(c.modes.media.query), modeTag: c.state.modeTag, settleMs: c.settleMs },
      pairUrl: this.controlUrl(fill(c.pair.query, { appId: this.appId, appName: this.appName })),
      busy: c.busy, dateHeader: d.media.dateHeader, slots: d.media.slots, range: d.media.range,
    };
    if (name === "discover") return discoverSlice(d);
    if (name === "liveView") {
      if (!d.features.liveView) throw new Unsupported("live view");
      const lv = d.remote.liveView;
      return { port: lv.port, startUrl: this.controlUrl(fill(lv.start, { port: lv.port })), stopUrl: this.controlUrl(lv.stop), jpegScan: lv.jpegScan };
    }
    throw new Error(`Unknown slice "${name}"`);
  }
}

// What native discovery needs (before any camera is known): SSDP, the probe and the hotspot.
export function discoverSlice(driver) {
  const c = driver.control, s = driver.discovery.ssdp;
  return {
    ssdp: { port: s.port, target: s.target, request: ssdpRequest(driver), replyContains: s.replyContains },
    probe: { port: c.port, path: c.path, query: c.probe.query, match: c.probe.match, busyStatus: c.busyStatus, busyPort: driver.discovery.busyPort },
    hotspot: driver.hotspot || null,
  };
}

class Remote {
  constructor(cam) { this.cam = cam; }
  need(feature) { if (!this.cam.features[feature]) throw new Unsupported(feature); }
  async cmd(name, vars, timeout) {
    const q = this.cam.driver.remote.commands[name];
    if (!q) throw new Unsupported(name);
    const st = parseState(await this.cam.control(fill(q, vars || {}), timeout), this.cam.driver);
    if (!st.ok) throw new Error(`The camera answered ${st.result}`);
    return st;
  }
  // Rec mode, then the stream to udpPort. Native code receives the UDP frames (slice("liveView")).
  async start(udpPort) {
    this.need("remote");
    const d = this.cam.driver, rec = d.control.modes.rec;
    await this.cam.control(rec.query, 8000);
    for (let i = 0; i < 10; i++) {
      const st = await this.cam.state();
      if (st.mode === rec.value) break;
      await this.cam.sleep(300);
    }
    if (d.features.liveView) {
      const lv = d.remote.liveView;
      await this.cam.control(fill(lv.start, { port: udpPort ?? lv.port }), 6000);
    }
  }
  async stop() {
    const d = this.cam.driver;
    if (d.features.liveView) await this.cam.control(d.remote.liveView.stop, 4000).catch(() => {});
    await this.cam.control(d.control.modes.media.query, 8000);
  }
  capture() { this.need("remote"); return this.cmd("capture", null, 12000); }
  videoStart() { this.need("video"); this.need("remote"); return this.cmd("videoStart", null, 8000); }
  videoStop() { this.need("remote"); return this.cmd("videoStop", null, 8000); }
  zoom(dir) {   // "wide" | "tele" | "stop"
    if (this.cam.features.zoom === "none") throw new Unsupported("zoom");
    return this.cmd({ wide: "zoomWide", tele: "zoomTele", stop: "zoomStop" }[dir], null, 4000);
  }
  focus(fx, fy) {   // 0..1 across the live view
    this.need("touchFocus");
    const s = this.cam.driver.remote.focusScale;
    return this.cmd("focus", { x: Math.round(fx * s), y: Math.round(fy * s) });
  }
}

// A transport for node (tests, tools): fetch with a timeout.
export function fetchTransport() {
  const go = async (url, init, timeout) => {
    const r = await fetch(url, { ...init, signal: AbortSignal.timeout(timeout) });
    const headers = {};
    r.headers.forEach((v, k) => { headers[k.toLowerCase()] = v; });
    return { status: r.status, headers, body: await r.text() };
  };
  return {
    get: (url, { timeout = 4000, headers = {} } = {}) => go(url, { headers }, timeout),
    post: (url, body, { timeout = 30000, headers = {} } = {}) => go(url, { method: "POST", body, headers }, timeout),
  };
}
