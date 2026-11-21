# lumix-lux-core

Shared code for the Lumix Lux apps (Android, iOS, Windows), pulled into each app repo as a git submodule at `core/`.
The plan for the multi-camera redesign, and its progress log, is `docs/REDESIGN.md`: read it before changing anything.

- `ui/index.html`: the mobile page (plain JS, no bundler, `window.Capacitor`). The Android/iOS builds copy it into their `www/`.
  It still has the ZS100 protocol inline until Phase 3 of the redesign moves it onto the JS runtime.
- `drivers/*.json`: camera drivers, data only. Never add anything that deletes from a camera or changes its settings.
- `docs/protocols/panasonic-camcgi.md`: the protocol as verified on the ZS100.
- `tools/fakecam.py`: fake camera (usage in its header).

A change here reaches an app only when that app repo bumps its `core` submodule (`git -C core pull && git add core`).
Commit and push here first, then bump the submodule in each app that needs it.
