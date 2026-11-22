# lumix-lux-core

Shared code for the Lumix Lux apps (Android, iOS, Windows), pulled into each app repo as a git submodule at `core/`.
The plan for the multi-camera redesign, and its progress log, is `docs/REDESIGN.md`: read it before changing anything.

- `ui/index.html`: the mobile page (plain JS, no bundler, `window.Capacitor`). The Android/iOS builds copy it into their `www/`.
  It still has the ZS100 protocol inline until Phase 3 of the redesign moves it onto the JS runtime.
- `drivers/*.json`: camera drivers, data only, validated by `schema/driver.schema.json`. `panasonic-camcgi.json` is the abstract
  family base (its `features` are the cautious mode for unknown cameras); model files `extends` it and list only what differs.
  Never add anything that deletes from a camera or changes its settings (the runtimes' guard refuses it anyway).
- **Runtimes:** `js/lux-camera.js` (WebViews; transport injected) and `py/luxcamera/` (PC app) have the same API and must stay in
  step: a change to one goes into the other, with a case in `tests/cases/*.json` that both run. The denylist guard (`denyReason`
  / `deny_reason`) is in both and in `fakecam.py`'s `denied()`; never loosen it to make a driver work.
- **Tests:** `node --test tests/*.test.mjs` and `python3 -m unittest discover -s tests` (both start fakecam on 127.0.0.x, CI runs
  them). Against the real camera, read-only: `python3 tools/camcheck.py` / `node tools/camcheck.mjs` (close the PC page first).
- `docs/protocols/panasonic-camcgi.md`: the protocol as verified on the ZS100.
- `tools/fakecam.py`: fake camera (usage in its header); `--driver <file>` serves control, Browse and media on one port.

A change here reaches an app only when that app repo bumps its `core` submodule (`git -C core pull && git add core`).
Commit and push here first, then bump the submodule in each app that needs it.
