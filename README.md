# Lumix Lux core

Shared code for the Lumix Lux apps, which copy photos and videos off Panasonic Lumix cameras over Wi-Fi, browse them and
shoot remotely. Each app repo pulls this one in as a git submodule at `core/`:

- [lumix-lux-android](https://github.com/CameronChambers93/lumix-lux-android): Android app (Capacitor)
- [lumix-lux-ios](https://github.com/CameronChambers93/lumix-lux-ios): iOS app (Capacitor, in progress)
- [lumix-lux-windows](https://github.com/CameronChambers93/lumix-lux-windows): PC web app (standard-library Python)

## What's here

| Path | What |
|---|---|
| `ui/index.html` | The mobile app's page (gallery, viewer, Remote, Settings), shared by Android and iOS |
| `drivers/` | Camera drivers: declarative JSON. `panasonic-camcgi.json` is the family base, model files `extends` it |
| `schema/driver.schema.json` | The driver format; both runtimes validate drivers against it |
| `js/lux-camera.js` | Camera runtime for the WebView apps (ES module, no dependencies, transport injected) |
| `py/luxcamera/` | The same runtime in standard-library Python, for the PC app |
| `tests/` | Shared test cases (`cases/*.json`) and end-to-end tests against `fakecam.py`, run by both runtimes |
| `docs/protocols/panasonic-camcgi.md` | The Lumix Wi-Fi protocol (`cam.cgi` + UPnP), as verified on a ZS100 |
| `docs/REDESIGN.md` | The multi-camera redesign plan and its progress log |
| `tools/fakecam.py` | A fake camera for testing the apps without a real one (`--driver` makes it a whole camera on one port) |
| `tools/camcheck.py`, `tools/camcheck.mjs` | Read-only check of each runtime against a real camera |

Tests: `node --test tests/*.test.mjs` and `python3 -m unittest discover -s tests` (Node 22, Python 3.9+).

Coming (see `docs/REDESIGN.md`): the apps moving onto the runtimes, the diagnostics probe list and report format, the
add-a-camera workflow and the driver registry.

History: split from [lumix-zs100-controller](https://github.com/CameronChambers93/lumix-zs100-controller) with history kept.
