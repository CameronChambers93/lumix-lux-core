# Lumix Lux: multi-camera redesign and repo split

**Status:** plan, not started (2026-11-21). Decisions made with Cameron:

- **Four repos.** `lumix-lux-core` (new, shared) plus the three app repos. Each app repo pulls core in as a git submodule at `core/`.
- **Drivers are declarative JSON**, read by every app. No downloaded code, so a driver can also be downloaded on iOS
  (Apple doesn't allow apps to download code) and is safe to load from the internet.
- **iOS:** Claude writes the scaffold and the Swift plugin; Cameron builds and tests it on a Mac.
- **Windows** stays the Python PC web app (+ `lumix.bat` zip). It also becomes the inbox for camera diagnostics.

Run it phase by phase, like `mobile/PLAN.md`: do only your phase, test it, append to the Progress log, stop.

## 1. Where things go

| Repo | Contents (from this repo) | New |
|---|---|---|
| `lumix-lux-core` | `PROTOCOL.md` → `docs/protocols/panasonic-camcgi.md`, `drivers/`, `mobile/tools/fakecam.py` → `tools/fakecam.py`, `docs/media/` | driver schema, JS + Python camera runtimes, the shared mobile UI, diagnostics probe list and report schema, the add-a-camera workflow, the driver registry |
| `lumix-lux-android` | `mobile/` (minus `www/index.html`, which moves to core), `mobile/tools/` (phone.sh, cdp.mjs, TESTING.md, udprelay.py, media.sh), `.devcontainer/` (Android toolchain) | `core/` submodule; `build.sh` copies `core/ui/` + `core/js/` + `core/drivers/` into `www/` |
| `lumix-lux-windows` | `app/`, `pc/`, `.devcontainer/` (Python only, no SDKs) | `core/` submodule; diagnostics inbox; release zip bundles `core/drivers/` |
| `lumix-lux-ios` | nothing | Capacitor iOS project, Swift port of the native plugin, `core/` submodule |

History is kept: each repo is made with `git filter-repo --path ...` from a clone of this one, so `git log` still shows
where every file came from. This repo (`lumix-zs100-controller`) is then archived with a README pointing to the four new ones.

## 2. The camera abstraction

### 2.1 Layers

```
 UI (gallery, viewer, Remote, Settings)      knows only: items, features, state
        │
 Camera runtime (core/js, core/py)           Camera { connect, state, ensureMedia, list, date, open, remote.* }
        │      picks a protocol family from driver.protocol
 Protocol family  "panasonic-camcgi"         the code: cam.cgi control + UPnP ContentDirectory + UDP live view
        │      all strings/ports/tags come from the driver
 Driver JSON  drivers/zs100.json             data: ports, paths, commands, tags, quirks, features
        │
 Transport (native)                          httpGet/httpPost/udp/ssdp, pinned to Wi-Fi; knows nothing about cameras
```

- **Protocol families are code, drivers are data.** Nearly every Wi-Fi Lumix of the last decade (GH3–GH5, G7–G9, GX80/85,
  GX9, LX100, FZ1000/2000/300, TZ/ZS70–ZS200) speaks the same `cam.cgi` + UPnP family with different details, so one family
  plus many drivers covers them. A camera that speaks something else (e.g. newer S-series/GH6 that need Bluetooth pairing,
  or PTP-IP) needs a new family, which is a code change in core and an app release; the diagnostics report says which case it is.
- **The native layers become camera-agnostic.** Today `LumixNativePlugin`, `SaveQueue` and `app/server.py` hard-code
  `cam.cgi`, `:60606`, `X-REC_DATE_TIME`, `playmode` etc. After the redesign the JS runtime passes the slice of the driver
  that native code needs (e.g. `enqueue({..., driver: {keepalive, mediaMode, pair, dateHeader, slots}})`,
  `discover({ssdp, probe, hotspot})`, `startLiveView({port, start, stop, jpegScan})`). Swift on iOS gets the same contract.
- **Safety guard in the runtime, not the driver.** Every runtime refuses to send a request whose query matches a denylist
  (`delete`, `format`, `setsetting`, `setinfo`, anything with `del`), whatever a driver says. Drivers can't widen it.
  "Never deletes from the camera" stays true for any downloaded driver.

### 2.2 Driver format

Built from the draft in `drivers/panasonic-lumix.json`, split into a family base and thin model files with `extends`:

```jsonc
// drivers/panasonic-camcgi.json  (base: everything the ZS100 does today)
{ "id": "panasonic-camcgi", "schema": 1, "protocol": "panasonic-camcgi", "abstract": true,
  "control": { ... }, "media": { ... }, "discovery": { ... }, "remote": { ... } }

// drivers/panasonic-zs100.json
{ "id": "panasonic-zs100", "schema": 1, "extends": "panasonic-camcgi", "version": 3,
  "name": "Lumix ZS100 / TZ100 / TZ101",
  "match": { "modelNumber": "^DMC-(ZS100|TZ100|TZ101)$", "capabilityModel": "^TZ100$" },
  "hotspot": { "ip": "192.168.54.1", "ssidPrefix": "ZS100-" },
  "features": { "remote": true, "liveView": true, "video": true, "touchFocus": true, "zoom": "normal",
                "dates": "header", "range": true, "gps": "phone" },
  "verified": { "firmware": "D2.90", "date": "2026-11-06", "by": "diagnostics/zs100-2026-11-06.json" } }
```

- `match` is checked against the UPnP device description (`modelNumber`, `friendlyName`) and `getinfo&type=capability`
  (`<model>`); the first driver that matches wins, else the family base runs in a **cautious mode** (gallery and saving only,
  Remote hidden) and the app offers "Help add this camera" (the diagnostics tool).
- `features` drives the UI: no Remote tab, no video button, no touch focus etc. where the camera lacks them.
- `core/schema/driver.schema.json` (JSON Schema) validates every driver in CI and before an app accepts a download.

### 2.3 Runtimes

- `core/js/lux-camera.js`: an ES module with no dependencies, injected with a transport (`{get, post, udp, ssdp}`), used by
  the Android and iOS WebViews. Replaces the `cam.cgi` strings in today's `mobile/www/index.html` (`probe`, `ensurePlay`, `pair`,
  `listCamera`, `camCmd`, Remote).
- `core/py/luxcamera/`: the same API in standard-library Python, used by `app/server.py` (replaces `Camera`, `parse_didl`,
  `probe_camera`, `ssdp_search`).
- Both pass one shared test suite: `core/tests/cases/*.json` (request → expected parse) run by `node --test` and `python -m unittest`,
  plus end-to-end runs against `tools/fakecam.py`.
- `tools/fakecam.py` grows a `--driver` and a `--replay <diagnostics report>` mode: it pretends to be any camera we have a
  report for. That is how a new driver is tested without the camera in hand.

## 3. Diagnostics: adding a camera

The loop Cameron described: borrow or find a camera → connect it to the phone → run diagnostics → report lands on the PC →
Claude writes the driver → it ships as a download (or, for a new protocol family, an app release).

### 3.1 On the phone: "Add a camera" (Android first, iOS later)

Settings → Camera support → **Add a camera**. Plain steps on screen ("Put the camera on Wi-Fi → Remote Shooting & View",
join its hotspot or home Wi-Fi), then it runs a probe list from `core/diagnostics/probes.json` in tiers, with consent per tier:

1. **Look only (always):** SSDP (multicast + unicast) and the device description XML; port scan of a short list (80, 1900, 8080,
   49199, 50001, 60606, 15740 for PTP-IP); `getstate` before and after pairing; `accctrl` pairing reply (also with other
   known app-id prefixes when the first one is refused); `getinfo` capability / allmenu / curmenu; one `Browse` page
   (and its `TotalMatches`); for the 3 newest items a `HEAD`/`Range 0-0` on every `res` URL (all headers recorded: date header,
   sizes, Range support); the first 128 KB of one JPEG (EXIF only) and response timings.
2. **Mode switch (ask):** `recmode` → `getstate` → `playmode`, and how long each takes.
3. **Live view (ask):** `startstream`, capture ~2 s of UDP (packet sizes, header length before `FF D8`, one decoded frame), `stopstream`.
4. **Remote commands (ask, off by default):** zoom wide/tele/stop, touch focus. Never the shutter or video unless Cameron
   ticks "take one test shot".

Never sent in any tier: the runtime denylist above. The report is one JSON file (`core/schema/diagnostics.schema.json`):
app version, phone, network mode, every request and raw reply (bodies cut at 256 KB, thumbnails kept as base64 so the
replay camera can serve them), timings, errors. Personal data is limited to the camera's own names and 3 thumbnails;
the screen shows what is collected before it runs.

**Send:** to the PC (`POST http://<pc>:8420/api/diagnostics`, the app finds the PC with the same /24 sweep it uses for cameras,
or the address is typed), or with the share sheet (email, Drive) when the PC isn't reachable, e.g. at a shop.

### 3.2 On the PC: inbox and Claude

- `lumix-lux-windows` stores reports in `data/diagnostics/inbox/<model>-<date>.json` and lists them on a small page.
- In `lumix-lux-core`, `docs/ADDING-A-CAMERA.md` plus a Claude Code skill `add-camera` hold the procedure Claude follows:
  1. Validate the report; classify it: known driver works / same family with differences / different family.
  2. Same family: write `drivers/<vendor>-<model>.json` (`extends` the base, only what differs), with `verified` pointing at the report.
  3. Test: schema check, the shared test cases, and a full app flow (list, thumbnails, dates, one download with resume, live
     view if offered) against `fakecam.py --replay <report>`.
  4. Commit on a branch and open a PR in `lumix-lux-core`; merging publishes it (3.3).
  5. Different family: write up what the camera speaks, a family design, and stop for Cameron (code change + app release).
- Optional later: the Windows server starts `claude -p "/add-camera <report>"` in the core checkout when a report arrives, and
  shows the resulting PR link.
- Acceptance test for the whole loop: run the diagnostics on the ZS100, delete `panasonic-zs100.json`, have the skill write it
  from the report alone, and compare it with the hand-written one.

### 3.3 Downloadable drivers

- `lumix-lux-core` publishes `registry/index.json` (id, version, match, sha256, url) and the driver files on GitHub Pages
  (or `raw.githubusercontent.com` of a release tag). CI builds the index from `drivers/` on merge to `main`.
- Apps ship with every driver that existed at build time and check the registry on launch (at most daily) and from
  Settings → Camera support → "Check for new cameras". A download is accepted only if the sha256 matches the index, the
  schema validates, `schema` is a version the app understands and the `protocol` family exists in this app version
  (else: "This camera needs an app update").
- Stored in app storage (Android: `filesDir/drivers/`, iOS: Application Support, Windows: `data/drivers/`); a downloaded driver
  with a higher `version` overrides the bundled one.
- Integrity: HTTPS + sha256 from an index served from the same repo is enough while Cameron is the only publisher, and
  the denylist means a bad driver can't delete anything. If drivers ever come from other people: sign the index (Ed25519).

## 4. iOS notes (for Cameron's Mac build)

- Capacitor iOS project with the same `core/ui` + `core/js`. Swift `LuxNativePlugin` implements the transport contract with
  `URLSession`, `NWConnection` (UDP live view), `NEHotspotConfiguration` (join the camera hotspot), `PHPhotoLibrary` (save).
- Needs `NSLocalNetworkUsageDescription`; SSDP multicast needs Apple's **multicast networking entitlement**
  (`com.apple.developer.networking.multicast`, a request form). Without it, discovery falls back to the /24 sweep.
- No equivalent of Android's foreground `SaveService`: saving continues for ~30 s in the background
  (`beginBackgroundTask`), so "keep the app open while saving" applies. Background `URLSession` can't reach a LAN camera reliably.
- GPS tagging: Core Location with "While using" works only while the app is open; always-on logging needs the "Always"
  permission and a good reason in review. Start with "while open".
- Installing on a phone needs an Apple developer account (free: re-sign every 7 days; paid: TestFlight).

## 5. Phases

0. **Repos** (Cameron + Claude). Cameron creates `lumix-lux-core` and gives the `dc` GitHub token access to all four
   (`dc github-login`). Claude splits with `git filter-repo`, pushes, sets up submodules, updates each repo's `CLAUDE.md`/README,
   archives notice here. Nothing changes in behaviour.
1. **Core runtimes.** Driver schema, base + ZS100 drivers (from the draft), denylist, `lux-camera.js`, `luxcamera` (Python),
   shared tests, `fakecam.py --driver`. Both runtimes pass the tests against fakecam and (read-only) against the real ZS100.
2. **Windows onto core.** `server.py` uses `luxcamera`; drivers picked by `match`; diagnostics inbox endpoint + page;
   release zip bundles drivers. Regression: sync, thumbnails, dates, EXIF, find camera.
3. **Android onto core.** `www` from `core/ui`; native methods take driver slices; UI follows `features`; Settings shows the
   matched driver. Full `TESTING.md` checklist on the emulator against the real ZS100. Bump to v3.0.
4. **Diagnostics in Android.** `probes.json`, tiers and consent, report schema, send to PC / share. Run it on the ZS100.
5. **Add-a-camera workflow.** `fakecam.py --replay`, `ADDING-A-CAMERA.md`, the `add-camera` skill; the ZS100 acceptance test (3.2).
6. **Driver registry and downloads.** CI-built `registry/index.json`, Pages; download/verify/store/override in Android and Windows.
7. **iOS scaffold.** Capacitor iOS, Swift plugin, diagnostics included; build notes for Cameron. Untested by Claude.
8. **Docs.** Per-repo `CLAUDE.md`, README, vault note (cross-repo layout, add-a-camera loop).

## Progress log

(append here)
