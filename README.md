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
| `drivers/` | Camera drivers: declarative JSON describing one camera model (ports, commands, quirks, features) |
| `docs/protocols/panasonic-camcgi.md` | The Lumix Wi-Fi protocol (`cam.cgi` + UPnP), as verified on a ZS100 |
| `docs/REDESIGN.md` | The multi-camera redesign plan and its progress log |
| `tools/fakecam.py` | A fake camera for testing the apps without a real one |

Coming (see `docs/REDESIGN.md`): the driver schema, JS and Python camera runtimes, the diagnostics probe list and report
format, the add-a-camera workflow and the driver registry.

History: split from [lumix-zs100-controller](https://github.com/CameronChambers93/lumix-zs100-controller) with history kept.
