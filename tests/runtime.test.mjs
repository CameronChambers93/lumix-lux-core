// JS runtime tests: node --test tests/*.test.mjs   (the Python twin is tests/test_runtime.py; both read tests/cases/*.json)
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import * as lux from "../js/lux-camera.js";

const CORE = join(dirname(fileURLToPath(import.meta.url)), "..");
const drivers = Object.fromEntries(readdirSync(join(CORE, "drivers")).filter((f) => f.endsWith(".json"))
  .map((f) => JSON.parse(readFileSync(join(CORE, "drivers", f), "utf8"))).map((d) => [d.id, d]));
const schema = JSON.parse(readFileSync(join(CORE, "schema", "driver.schema.json"), "utf8"));

// expect is partial for objects (only the keys it names), exact for arrays and scalars.
function like(actual, expect, path = "$") {
  if (expect && typeof expect === "object" && !Array.isArray(expect)) {
    assert.ok(actual && typeof actual === "object", `${path}: expected an object, got ${JSON.stringify(actual)}`);
    for (const [k, v] of Object.entries(expect)) like(actual[k], v, `${path}.${k}`);
  } else if (Array.isArray(expect)) {
    assert.ok(Array.isArray(actual), `${path}: expected an array`);
    assert.equal(actual.length, expect.length, `${path}: length`);
    expect.forEach((v, i) => like(actual[i], v, `${path}[${i}]`));
  } else assert.equal(actual ?? null, expect, path);
}

const FNS = {
  denyReason: (url, opts) => ({ denied: lux.denyReason(url, opts) !== null }),
  parseState: lux.parseState, parseCapability: lux.parseCapability, parseDeviceInfo: lux.parseDeviceInfo,
  parseCdsControl: lux.parseCdsControl, parseSsdp: lux.parseSsdp, parseBrowse: lux.parseBrowse, sortItems: lux.sortItems,
  resolveDriver: (id, extra) => lux.resolveDriver(id, { ...drivers, ...extra }),
  matchDriver: (info) => lux.matchDriver(drivers, info),
  validate: (v) => lux.validate(v, schema),
  validateAll: () => { for (const d of Object.values(drivers)) assert.deepEqual(lux.validate(d, schema), [], d.id); return {}; },
};

for (const file of readdirSync(join(CORE, "tests", "cases")).sort()) {
  for (const c of JSON.parse(readFileSync(join(CORE, "tests", "cases", file), "utf8"))) {
    test(`${file}: ${c.name}`, () => {
      const args = c.driver ? [...c.args, lux.resolveDriver(c.driver, drivers)] : c.args;
      if (c.throws) assert.throws(() => FNS[c.fn](...args), (e) => e.message.includes(c.throws));
      else like(FNS[c.fn](...args), c.expect);
    });
  }
}

// ---- end to end against tools/fakecam.py --driver
async function fakecam(ip, mode) {
  const port = 20000 + Math.floor(Math.random() * 20000);
  const p = spawn("python3", [join(CORE, "tools", "fakecam.py"), ip, mode, String(port), "--driver", join(CORE, "drivers", "panasonic-zs100.json"), "--items", "120"],
    { stdio: "ignore" });
  for (let i = 0; i < 50; i++) {
    try { await fetch(`http://${ip}:${port}/_stats`); break; } catch { await new Promise((ok) => setTimeout(ok, 100)); }
  }
  const stats = async () => (await fetch(`http://${ip}:${port}/_stats`)).json();
  return { port, stop: () => p.kill(), stats };
}
const IP = "127.0.0.3";   // the Python suite uses 127.0.0.2, so both can run at once

test("e2e: zs100 against fakecam", async () => {
  const fc = await fakecam(IP, "ok");
  try {
    const transport = lux.fetchTransport();
    const zs100 = lux.resolveDriver("panasonic-zs100", drivers);
    const cam = new lux.Camera(zs100, IP, { transport, appName: "lux-test", portMap: { 80: fc.port, 60606: fc.port }, sleep: async () => {} });

    assert.equal(await cam.probe(), "ok");
    const st = await cam.connect();
    assert.equal(st.ok, true);
    assert.equal(st.mode, "play");

    await cam.control("mode=camcmd&value=recmode");   // the camera drops back to rec by itself
    assert.equal(await cam.ensureMedia(), true);
    assert.equal((await fc.stats()).cammode, "play");
    assert.equal(await cam.ensureMedia(), false);

    const info = await cam.identify(`http://${IP}:${fc.port}/A0CC2B248B00/Server0/ddd`);
    like(info, { capabilityModel: "TZ100", modelNumber: "DMC-ZS100", friendlyName: "ZS100-FAKE" });
    like(lux.matchDriver(drivers, info), { matched: true, driver: { id: "panasonic-zs100" } });

    const items = await cam.list();
    assert.equal(items.length, 120);
    assert.equal(items[0].id, "1070119");
    assert.equal(items.at(-1).id, "1070000");
    assert.equal(items.filter((i) => i.kind === "video").length, 17);
    like(items.find((i) => i.id === "1070003"), { name: "P1070003.MP4", kind: "video", dur: 8 });

    assert.equal(await cam.date(items[0]), "2026-11-01T13:59:00");
    const req = cam.mediaRequest(items[0], "orig", 1000);
    const part = await transport.get(req.url, { headers: req.headers });
    assert.equal(part.status, 206);
    assert.match(part.headers["content-range"], new RegExp(`^bytes 1000-\\d+/${items[0].size}$`));
    assert.equal(cam.dateFromHeaders(part.headers), "2026-11-01T13:59:00");

    like(cam.slice("save"), { keepaliveUrl: `http://${IP}:${fc.port}/cam.cgi?mode=getstate`, dateHeader: "X-REC_DATE_TIME", slots: 2 });
    like(cam.slice("liveView"), { port: 49199, stopUrl: `http://${IP}:${fc.port}/cam.cgi?mode=stopstream` });
    like(lux.discoverSlice(zs100), { probe: { query: "mode=getstate", busyPort: 60606 }, hotspot: { ip: "192.168.54.1" } });

    await cam.remote.start(49199);
    assert.equal((await fc.stats()).cammode, "rec");
    await cam.remote.zoom("tele"); await cam.remote.zoom("stop"); await cam.remote.focus(0.5, 0.25); await cam.remote.capture();
    await cam.remote.stop();
    const s = await fc.stats();
    assert.equal(s.cammode, "play");
    assert.deepEqual(s.cmds.slice(-8), ["mode=camcmd&value=recmode", "mode=startstream&value=49199", "mode=camcmd&value=tele-normal",
      "mode=camcmd&value=zoomstop", "mode=camctrl&type=touch&value=500/250&value2=on", "mode=camcmd&value=capture", "mode=stopstream", "mode=camcmd&value=playmode"]);

    await assert.rejects(cam.control("mode=camcmd&value=delete"), lux.Refused);
    await assert.rejects(cam.get(`http://${IP}:${fc.port}/cam.cgi?mode=setsetting&type=clock&value=1`), lux.Refused);

    const cautious = new lux.Camera(lux.matchDriver(drivers, { capabilityModel: "GH5" }).driver, IP,
      { transport, portMap: { 80: fc.port, 60606: fc.port }, sleep: async () => {} });
    assert.equal((await cautious.list()).length, 120);   // the gallery works
    await assert.rejects(cautious.remote.start(), lux.Unsupported);   // Remote doesn't
    assert.throws(() => cautious.slice("liveView"), lux.Unsupported);

    assert.deepEqual((await fc.stats()).denied, []);
  } finally { fc.stop(); }
});

test("e2e: a camera held by another app is busy", async () => {
  const fc = await fakecam(IP, "busy");
  try {
    const cam = new lux.Camera(lux.resolveDriver("panasonic-zs100", drivers), IP, { transport: lux.fetchTransport(), portMap: { 80: fc.port } });
    assert.equal(await cam.probe(), "busy");
    await assert.rejects(cam.state(), lux.Busy);
  } finally { fc.stop(); }
});
