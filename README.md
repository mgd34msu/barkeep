# linux-t1-touch — Apple T1 Touch Bar display driver for Linux

Target: **MacBookPro13,2** (2016 13" Touch Bar), Apple **T1** iBridge, USB `05ac:8600`.
Not T2. `tiny-dfr`, `appletbdrm` and `hid-appletb-*` are all T2-only and do nothing here.

Status: **working, and it survives a reboot.** The bar is a real display — arbitrary images
and 30fps video — plus a full function row on top of it:

- drawn icons over a gradient sampled from what is on screen (or the wallpaper, or the theme)
- **buttons that work** — touch is read from the digitizer and injected as real keys
- **hold Fn** for F1–F12, release to return to the media strip
- **keys fade away after 30s idle**, leaving just the gradient, and fade back on any input
- DKMS modules + systemd units, so it comes up on every boot

## Layout

    ibridge-cfg/   kernel module: selects USB configuration 2 at enumeration
    dfr-probe/     kernel module: claims the DFR display interface, speaks the protocol,
                   exposes /dev/dfr0
    scripts/       dfr-bar.py    the function-row UI (icons, touch, Fn, idle fade)
                   dfr-play.py   stills, video and test patterns
                   ibridge-common.sh  locates the iBridge in sysfs by USB id
                   dfr-up.sh     bring the display session up
                   dfr-reset.sh  put the stock firmware row back, no reboot
                   dispon.py     panel enable on its own
                   build.sh      build both modules out of tree
                   dfr-go.sh     original bring-up + red/green/blue self-test
                   dfr-bar-run.sh  run the UI from the source tree
    systemd/       display + bar units
    etc/config     installed to /etc/t1-touchbar/config
    install.sh     install | uninstall | status
    reference/     imbushuo/DFRDisplayKm — the working Windows driver (MIT). READ THE SOURCE.

## Requirements

    dkms gcc linux-headers-$(uname -r) python-pillow python-evdev

`grim` is optional and only used by `--source screen`; without it that source falls back to
the theme palette. `install.sh` checks all of this before touching anything.

## Install

    sudo ./install.sh              # DKMS modules + scripts + systemd, enabled at boot
    ./install.sh status
    sudo ./install.sh uninstall    # removes everything, restores the stock function row

Config lives in `/etc/t1-touchbar/config`:

    DFR_ARGS="--source screen --flow 30 --fade 2 --poll 3 --threshold 18 --idle 30 --idle-out 2 --idle-in 1"

    sudo systemctl restart t1-touchbar-bar     # after editing
    t1-touchbar {start|stop|status|play <file|test|bars|flow>}

Units:

- `t1-touchbar-display.service` — enters USB config 2 and loads the modules
- `t1-touchbar-bar.service` — the UI (requires the display unit)

Modules are DKMS, so they rebuild on kernel updates.

**In display mode `apple-ibridge` is unloaded**, so the stock firmware function row is
replaced by this one. `uninstall` puts it back.

### If you previously set up the stock function row, read this

An earlier `touchbar.service` / `touchbar-resume.service` (running
`/usr/local/bin/touchbar-rebind`) is **mutually exclusive** with display mode. Those units
load `apple-ibridge`, which force-selects USB config 1 — see "What was hard" below — and
`touchbar.service` is `After=multi-user.target`, so at boot it lands *after* the display unit
and silently undoes it.

`install.sh` disables them and records what it disabled in `/etc/t1-touchbar/legacy-disabled`;
`uninstall` re-enables them. The display unit also declares `Conflicts=` on both, so starting
one by hand cannot quietly blank the bar. `./install.sh status` warns if either is enabled.

## Run from the source tree

    bash scripts/build.sh
    sudo bash scripts/dfr-up.sh             # bring up the session, leave it running
    python3 scripts/dfr-play.py test        # gradient
    python3 scripts/dfr-play.py bars        # colour bars
    python3 scripts/dfr-play.py pic.png     # a still image
    python3 scripts/dfr-play.py clip.mp4    # video, looping (--once, --fps N)
    sudo bash scripts/dfr-bar-run.sh --flow 30   # the function-row UI
    sudo bash scripts/dfr-reset.sh          # restore the stock function row, no reboot

## Troubleshooting

**The bar is black but both units say `active`.** The display session was torn down under a
UI that is happily still rendering into it. Check the bar's log:

    journalctl -u t1-touchbar-bar -b | grep -v Deprecat

These three lines together are the signature:

    panel on showing black (0 report(s))        <- 0, not 1: something took the hidraw
    touch: digitizer hidraw not found (need config 2)
    $ cat /sys/bus/usb/devices/1-3/bConfigurationValue   -> 1

Almost always `apple-ibridge` got loaded by something — see the legacy-units note above.
`lsmod | grep apple_ib` should print nothing while the bar is running.

**Nothing at all, `/dev/dfr0` missing.** The display unit failed to reach config 2:
`journalctl -u t1-touchbar-display -b`. It prints `config=N (want 2)`.

**The display unit is skipped, not failed.** Its `ExecCondition` found no `05ac:8600` in
`/sys/bus/usb/devices/*`. The device is located by USB id, not by a fixed port, so this means
the hardware genuinely is not there. `./install.sh status` prints the path it found.

**The machine hangs on suspend.** Fixed — but if you see it, the cause is a USB driver that
keeps submitting URBs into the PM transition. `dfr-probe` implements `.suspend`/`.resume`/
`.reset_resume`: suspend sets the stop flag, waits on the URB anchor and kills what is left;
resume redoes the whole handshake, because the panel loses its session. A log that ends at
`PM: suspend entry (deep)` with driver traffic right before it is the signature.

**"another dfr-bar is already running".** Two writers on `/dev/dfr0` fight and the bar
flickers, so the UI takes an exclusive lock on `/run/dfr-bar.lock`. Stop the service before
running it by hand.

## What was hard, and why

**1. The DFR display lives only in USB configuration 2.** Config 1 (the Linux default) has
*no bulk OUT endpoint at all*. Config 2 interface 3 is `class 0x10 / sub 0 / proto 0`,
bulk OUT `0x02` + IN `0x85`.

**2. Configuration can only be chosen at enumeration.** Userspace `SET_CONFIGURATION`
(sysfs or libusb) never works — Microsoft documents the same rule for `usbccgp`. Linux's
hook is `usb_device_driver.choose_configuration` (+ `generic_subclass = 1`), which is what
`ibridge-cfg` uses. Equivalent to macOS `kUSBPreferredConfiguration` and Windows
`OriginalConfigurationValue`.

**3. `apple-ibridge` actively fights this.** `appleib_hid_probe()` does:

    if (udev->actconfig->desc.bConfigurationValue != APPLEIB_BASIC_CONFIG)
            usb_driver_set_configuration(udev, APPLEIB_BASIC_CONFIG);   /* = 1 */

so in config 2 it forces the device straight back to config 1, ~30ms after entry. Found with
ftrace on `usb_driver_set_configuration`. **You must `rmmod apple_ib_tb apple_ib_als
apple_ibridge` first.** Every earlier theory (cdc_ncm, packet rate, URB pools, frame format)
was chasing this one line. It is also why the legacy rebind units break everything.

## Protocol

Generic request, 32 bytes, little-endian:

| off | field | value |
|----|-------|-------|
| 0x00 | RequestHeader | `0x15120002` |
| 0x0C | RequestLength | `0x10` |
| 0x10 | RequestKey | FourCC |
| 0x1C | End | `0x10` |

Keys: `GINF` get info, `REDY` host ready, `CLDR` clear screen, `UDCL` framebuffer updated.
Responses use header `0x01140000`. A response echoes the request header with **bit 31 set**
(`0x15120002` -> `0x95120002`) — that is an ACK, not an error.

Bring-up order (from `Device.c` `EvtDeviceD0Entry`): reset both bulk pipes -> `GINF`
(retry up to 100, the first reply is a short UDCL) -> validate -> `REDY` -> `CLDR`.

GINF reply gives **2170 x 60**, pixel format **ABGR**, plus width/height in inches as floats.

Framebuffer update — ONE bulk write of `[60-byte request][pixels][88-byte padding]`:

| off | field | value |
|----|-------|-------|
| 0x00 | RequestHeader | `0x00120002` |
| 0x04 | Reserved1 | **`0x00000009`** |
| 0x0C | RequestLength | **total - 16** |
| 0x10 | Reserved0 (u16) | **`0x0001`** |
| 0x12 | FrameId (u8) | increments, wraps at 0xff |
| 0x30 | BeginX/BeginY/Width/Height | u16 each |
| 0x38 | BufferSize | W*H*3 |

`DFR_FRAMEBUFFER_PIXEL_BYTES = 3`. The padding is **88 bytes and not zeros** — see
`dfr_update_padding[]`, copied verbatim from `reference/.../DfrDisplay.c`.

Those four bolded values are what made it work, and every one of them was wrong at first
because it came from *summaries* of the Windows driver rather than its source — including an
invented padding length (96, actually 88) and invented contents (zeros, actually a pattern).

> Lesson: read the vendored source directly. Never trust a summary of a binary protocol.

Panel **2170 x 60**, **3 bytes per pixel**, byte order `r,g,b` as written (the "ABGR" name in
the descriptor does not imply a swap). Colours can be changed live:

    echo 255 | sudo tee /sys/module/dfr_probe/parameters/colr

## Pushing pixels

`dfr-probe` exposes **`/dev/dfr0`**. Write exactly one frame: **2170 x 60 RGB888 =
390600 bytes**. Short writes are zero-padded. The driver re-sends the current buffer
continuously, which is also what holds the display session open.

Anything ffmpeg can decode works:

    ffmpeg -i clip.mp4 -vf scale=2170:60 -f rawvideo -pix_fmt rgb24 - > /dev/dfr0

### Panel orientation

The panel is physically **2170x60 landscape** but the framebuffer is **60 wide x 2170 tall,
rotated 90 degrees** — the wire buffer has 60-pixel rows. `dfr-play.py` transposes
(`ffmpeg ...,transpose=1`). Solid fills and column-uniform patterns look correct either way,
which is why the first colour-bar test passed despite the wrong layout.

## The bar UI

    sudo bash scripts/dfr-bar-run.sh --flow 30      # start it
    sudo python3 scripts/dfr-bar.py --no-touch      # render only
    sudo python3 scripts/dfr-bar.py --wallpaper X.jpg

`dfr-bar.py` draws esc / brightness / mission-control / launchpad / keyboard-illum /
prev-play-next / mute / volume as **vector icons** (font glyph coverage for emoji and media
symbols is unreliable — they render as tofu). `--flow N` drifts the gradient N px/sec.

### Colour sources

    --source screen      (default) colours currently ON SCREEN, via grim
    --source theme       the desktop's own palette (colors.toml) — zero cost, no capture
    --source wallpaper   the backdrop image
    --fade 2 --poll 3 --threshold 18

`screen` samples with `grim` at 5% scale (~0.45 s) straight into memory — **nothing is ever
written to disk**. A palette-distance threshold stops it re-fading on noise (cursor blink, a
scrolling line); only a real shift in what is on screen triggers a change. Be aware it is
capturing your desktop on a timer; `theme` gives a similar effect with no capture at all.
`theme` and `wallpaper` watch mtime/symlink target and re-sample automatically.

On Wayland there is no readable screen buffer — any pixel access goes through a capture
protocol (grim uses `wlr-screencopy`) or the portal. There is no cheaper way to do this.

Getting the palette to look right took two fixes:

- **Merge near-duplicate colours.** Quantizing alone picks up anti-aliased edge pixels — a
  logo outline yields blend shades that exist nowhere as a real region. Colours within a
  distance of 60 are clustered and only the representative kept.
- **Hold each colour, then blend briefly.** Continuous interpolation between N colours means
  most of the bar shows invented in-between shades. Each sampled colour now occupies ~55% of
  its segment before a short smoothstep transition.

### Transitions

Every transition uses one shared `ease()` (smoothstep), so overlapping ones move on the same
curve and read as a single motion:

- **palette change** — cross-fades the whole bar at once (an eased blend of the two 1-row
  gradients), not a left-to-right wipe
- **startup** — the panel is lit while still showing black, then the first frame rises out of
  it, so there is no pop
- **idle auto-hide** — the key overlay fades out and back

A cross-fade starts from *what is on the panel right now*, not from the previous fade's
destination. Screen sampling polls every 3s while a fade runs 2s, so interruptions are
routine; starting from the old target snapped a channel 85/255 in a single frame. Now the
worst frame is 5/255, and it falls at the curve's natural steepest point.

### Idle auto-hide

    --idle 30        hide the keys after 30s with no activity (0 disables)
    --idle-out 2     seconds to fade them away
    --idle-in 1      seconds to bring them back

With the keys hidden only the gradient shows. Activity means a real keyboard or pointer: a
device with a letter key, `EV_REL`, or `EV_ABS` plus a touch/click button — that last case is
the bar's own digitizer, so touching the bar wakes it too. Power/Sleep/Lid/Video Bus carry
`EV_KEY` but are not typing, and the UI's own uinput node is skipped by name so injected keys
cannot wake the bar through a second path.

The opacity ramp advances a phase 0..1 *linearly* by elapsed time and eases the phase, rather
than easing from a fixed start time. That is what lets an interrupted fade reverse from
exactly where it is — type halfway through the fade-out and the keys come back from half
opacity with no jump.

`render()` takes a `buttons` opacity and draws the keys onto a copy of the gradient, then
blends the two. One C-speed composite, and the plates, outlines and glyphs fade together as a
single layer.

### Fn layer

Hold Fn and the bar switches to F1–F12 and injects those keycodes; release and it returns to
the media strip. The keyboard node is found by capability (it reports `KEY_FN`), not by a
fixed `/dev/input/eventN` — the number moves between boots. The Apple SPI keyboard
**autorepeats Fn while held** (value 2), so treat 1 and 2 as down and 0 as up. If the layer
flips while a finger is down, the old layer's key is released first so it cannot stick.
Reading the keyboard needs no root if you are in the `input` group.

Note that most F-keys do nothing at the desktop level until an app has focus — that is
correct behaviour, not a broken injection. To check the keys really work, watch the uinput
device (`Apple T1 Touch Bar`) with `python-evdev`, or use `wev`, or `cat -v` in a terminal
(`^[OP` = F1).

### Touch

The digitizer is a non-standard HID on **config-2 interface 2, EP 0x83**; reports are ~52
bytes with a **little-endian float32 X in [0.5, 1.0]** in the first 4 bytes. Read via hidraw
*alongside* `hid-generic` (no unbinding), mapped to key zones, injected with uinput. Release
is inferred from a ~120ms gap in reports. Protocol credit: `xeeban/macbook-t1-linux`.

### Two traps worth knowing

- **Under `sudo`, `~` is `/root`** — resolve the desktop user's home via `SUDO_USER`, or the
  wallpaper silently falls back to a default palette.
- **Never build frames pixel-by-pixel in Python.** 2170x60 per frame took *seconds*; making a
  1-row image and letting Pillow `resize()` stretch it to full height is ~4 ms (~229 fps).

## Credits and licensing

**`imbushuo/DFRDisplayKm`** (MIT) — the Windows Touch Bar driver, vendored in full under
`reference/DFRDisplayKm` with its original LICENSE. The DFR protocol in `dfr-probe.c` is
**derived from it**: the request/response envelope, the framebuffer update layout and field
values, the FourCC keys, the bring-up order, and `dfr_update_padding[]`, which is copied
byte-for-byte from their `DfrUpdatePadding[]`. MIT is GPL-compatible, so the kernel modules
ship as GPL-2.0 with the MIT notice retained. Without this driver none of this would exist.

**`xeeban/macbook-t1-linux`** — no code taken, but the touch digitizer protocol came from
reading it: interface 2 / EP 0x83, reports whose first 4 bytes are a little-endian float32 X
in [0.5, 1.0]. They also independently found the same `apple-ibridge` config-1 root cause.

**`sunplex07/appletbdrm`** — nothing used here; listed below as prior art worth adopting.

This project is **GPL-2.0** (see `LICENSE`) — the kernel modules are already marked as such
via SPDX and `MODULE_LICENSE("GPL")`, and `scripts/` follows for consistency. MIT is
GPL-compatible, so the derivation above is fine with the notice retained.

## Prior art — read these

Two repos cover this same machine and go further in places:

- **`sunplex07/appletbdrm`** — unified T1/T2 **DRM** driver (`/dev/dri/card*`), `ibridge-switcher`
  + systemd for config 2, backlight via HID reports 3/4/5 on interface 6, DKMS install.
  Note: it does **not** handle `apple-ibridge`, which force-selects config 1 and will fight it.
- **`xeeban/macbook-t1-linux`** (`touch-bar/`) — same machine (MacBookPro13,2). Independently
  found the same config-1 force-select root cause and the same `usb_device_driver` fix. Also has
  touch input (the digitizer protocol above) and `dfrd/`, a full userspace UI with FreeType
  labels and macOS-style layers. Also fixed a heap out-of-bounds read in `apple_ibridge`
  (upstream `t2linux/apple-ib-drv#11`).

## Still to do

- **Suspend/resume is fixed but only tested via `/sys/power/pm_test`**, which exercises the
  full device suspend/resume path without actually cutting power. A real lid-close cycle has
  not been re-tested since the fix.
- The stock firmware function row is unavailable while in config 2 (`apple-ibridge` must stay
  unloaded). `dfr-bar.py` replaces it with a working one, so this matters less than it did.
- Adopting `appletbdrm` would make the bar a real DRM device rather than a character device.
