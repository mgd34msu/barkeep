# Changelog

## v0.1.0 — 2026-08-18

First release. The Apple **T1** Touch Bar works as a real display under Linux —
arbitrary images, 30fps video, and a usable function row on top of it.

Other projects also drive the T1 panel: `sunplex07/appletbdrm` (a DRM driver
covering both T1 and T2) and the kernel driver in `xeeban/macbook-t1-linux`.
This is a separate implementation, with a character device rather than a DRM
card and a userspace UI on top. `tiny-dfr` and `hid-appletb-*` are
T2/Apple-Silicon only and do not work here.

### Display

- `barkeep-cfgsel` selects USB configuration 2 at enumeration via
  `usb_device_driver.choose_configuration` — the only point at which a
  configuration can be chosen on Linux.
- `barkeep-dfr` claims the DFR interface, speaks the protocol, and exposes
  `/dev/dfr0`. One write = one 2170x60 RGB888 frame.
- The panel is 2170x60 landscape but its framebuffer is portrait and rotated
  90°; `dfr-play.py` transposes for you.
- `dfr-play.py` plays stills, video and test patterns through ffmpeg.

### Function row (`dfr-bar.py`)

- Vector icons over a gradient sampled from the screen, the wallpaper or the
  desktop theme, cross-fading as the source changes.
- Touch read from the digitizer over hidraw and injected as real keys via
  uinput.
- Five layers selected by Fn plus a modifier: media, F1–F12, a live system row,
  F13–F24, and a transport strip.
- Live readouts for battery, wifi, bluetooth and keyboard backlight, read
  straight from `/sys` with no daemon.
- Keys may run a command in the desktop session instead of injecting a keycode.
- Touch Bar backlight control, including handing it back to the ambient light
  sensor.
- Keys fade out after 30s idle, leaving the gradient, and fade back on any
  keyboard, pointer or bar activity.
- `--preview out.png` renders any layer to an image with no hardware and no
  root.

### Packaging

- `install.sh` installs both modules through DKMS so they survive kernel
  updates, plus systemd units, a config file and the `barkeep` CLI.
- Disables the older stock-function-row units, which force USB config 1 and
  would otherwise silently blank the bar at boot; `uninstall` puts them back.
- The iBridge is located by USB id rather than a fixed sysfs path.
- Cold boot verified.

### Known issues

- **Suspend hangs the machine.** A lid close can wedge it hard enough to need a
  power cycle. `barkeep-dfr` implements PM callbacks and a systemd sleep hook
  tears the session down before the kernel suspends, but neither is confirmed
  to fix it, and it is **not established that this project is the cause** — no
  successful suspend has been recorded on the development machine with or
  without it. See "Still to do" in the README.
- Only ever run on one machine, a MacBookPro13,2.
- The stock firmware function row is unavailable while in display mode, since
  `apple-ibridge` must stay unloaded.
