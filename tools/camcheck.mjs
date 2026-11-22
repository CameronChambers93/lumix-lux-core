// Read-only check of the JS runtime against a real camera: node tools/camcheck.mjs [ip]   (Python twin: camcheck.py)
// Probe, connect (pairs; sends playmode if the camera sits in rec), capability, match a driver, list, dates of the 3 newest
// items, a 1-byte Range read of the newest original. No Remote commands. node has no UDP SSDP here, so no device description.
// Close the PC page / phone app first: the camera serves one app at a time.
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import * as lux from "../js/lux-camera.js";

const CORE = join(dirname(fileURLToPath(import.meta.url)), "..");
const drivers = Object.fromEntries(readdirSync(join(CORE, "drivers")).filter((f) => f.endsWith(".json"))
  .map((f) => JSON.parse(readFileSync(join(CORE, "drivers", f), "utf8"))).map((d) => [d.id, d]));
const ip = process.argv[2] || "10.0.0.220";
const t0 = Date.now();
const step = (name, v) => console.log(`${((Date.now() - t0) / 1000).toFixed(2).padStart(6)}s  ${name}: ${typeof v === "string" ? v : JSON.stringify(v)}`);
const transport = lux.fetchTransport();

let cam = new lux.Camera(lux.resolveDriver("panasonic-camcgi", drivers), ip, { transport, appName: "lux-camcheck" });
const probe = await cam.probe(3000);
step("probe", probe);
if (probe !== "ok") {
  console.error(`camera at ${ip} is ${probe ?? "not answering"}: is it on Wi-Fi (Remote Shooting & View) and free of other apps?`);
  process.exit(1);
}
const { state, ...st } = await cam.connect();
step("connect", st);
const info = await cam.identify();
step("identify", info);
const m = lux.matchDriver(drivers, info);
step("driver", `${m.driver.id} (matched=${m.matched} by ${m.by})`);
cam = new lux.Camera(m.driver, ip, { transport, appName: "lux-camcheck" });
const items = await cam.list();
step("list", `${items.length} items, ${items.filter((i) => i.kind === "video").length} videos, newest ${items[0]?.name ?? "-"}`);
for (const it of items.slice(0, 3)) step(`date ${it.name}`, await cam.date(it));
if (items.length) {
  const r = await transport.get(cam.mediaRequest(items[0]).url, { timeout: 15000, headers: { Range: "bytes=100-100" } });
  step("range", `HTTP ${r.status} ${r.headers["content-range"]}`);
}
console.log(JSON.stringify({ ok: true, driver: m.driver.id, items: items.length }));
