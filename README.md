# linux-t1-touch — Apple T1 Touch Bar display driver for Linux

Target: **MacBookPro13,2** (2016 13" Touch Bar), Apple **T1** iBridge, USB `05ac:8600`.
Not T2. `tiny-dfr`, `appletbdrm` and `hid-appletb-*` are all T2-only and do nothing here.

Status: **WORKING — a usable Touch Bar.** Arbitrary images and 30fps video, plus a full
function row: drawn icons over a gradient sampled from the desktop wallpaper, with
**working buttons** (touch -> uinput key injection).

## Layout

    ibridge-cfg/   kernel module: selects USB configuration 2 at enumeration
    dfr-probe/     kernel module: claims the DFR display interface, speaks the protocol
    scripts/       build.sh, dfr-go.sh (run), dfr-reset.sh (undo), dispon.py (panel enable)
    reference/     imbushuo/DFRDisplayKm — the working Windows driver (MIT). READ THE SOURCE.

## Run

    bash scripts/build.sh
    sudo bash scripts/dfr-up.sh      # bring up the session, leave it running
    python3 scripts/dfr-play.py test        # gradient
    python3 scripts/dfr-play.py bars        # colour bars
    python3 scripts/dfr-play.py pic.png     # a still image
    python3 scripts/dfr-play.py clip.mp4    # video, looping (--once, --fps N)
    sudo bash scripts/dfr-reset.sh   # restore the stock function row, no reboot

`dfr-go.sh` is the original bring-up + red/green/blue self-test.

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
was chasing this one line.

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

> Lesson: fetching *summaries* of the Windows source produced four separate wrong values
> (padding length, padding contents, RequestLength, and two reserved fields). Read the
> vendored source directly.

## Verified

Panel **2170 x 60**, **3 bytes per pixel**, byte order `r,g,b` as written (the "ABGR" name in
the descriptor does not imply a swap). Colours can be changed live:

    echo 255 | sudo tee /sys/module/dfr_probe/parameters/colr

## What actually fixed it

Four values in the framebuffer request were wrong, every one of them invented from *summaries*
of the Windows driver rather than its source. Reading `reference/DFRDisplayKm/` directly fixed
all four at once. See the framebuffer table above — `Reserved1 = 0x09`,
`RequestLength = total - 16`, `Reserved0 = 0x0001`, and the 88-byte patterned padding.

## Pushing pixels

`dfr-probe` exposes **`/dev/dfr0`**. Write exactly one frame: **2170 x 60 RGB888 =
390600 bytes**. Short writes are zero-padded. The driver re-sends the current buffer
continuously, which is also what holds the display session open.

Anything ffmpeg can decode works:

    ffmpeg -i clip.mp4 -vf scale=2170:60 -f rawvideo -pix_fmt rgb24 - > /dev/dfr0

## The bar UI

    sudo bash scripts/dfr-bar-run.sh --flow 30      # start it (background)
    sudo python3 scripts/dfr-bar.py --no-touch      # render only
    sudo python3 scripts/dfr-bar.py --wallpaper X.jpg

`dfr-bar.py` draws esc / brightness / mission-control / launchpad / keyboard-illum /
prev-play-next / mute / volume as **vector icons** (font glyph coverage for emoji and media
symbols is unreliable - they render as tofu), over a gradient built from the dominant colours
of `~/.local/state/omarchy/current/background`. `--flow N` drifts the gradient N px/sec.

The wallpaper is **watched live** (2 s poll on the symlink target + mtime, since a theme
change swaps the link) and the palette re-samples automatically.

Getting the palette to look right took two fixes:
- **Merge near-duplicate colours.** Quantizing alone picks up anti-aliased edge pixels - a
  logo outline yields blend shades that exist nowhere as a real region. Colours within a
  distance of 60 are clustered and only the representative kept.
- **Hold each colour, then blend briefly.** Continuous interpolation between N colours means
  most of the bar shows invented in-between shades. Each sampled colour now occupies ~55%% of
  its segment before a short smoothstep transition.

Touch: the digitizer is a non-standard HID on **config-2 interface 2, EP 0x83**; reports are
~52 bytes with a **little-endian float32 X in [0.5, 1.0]** in the first 4 bytes. Read via
hidraw *alongside* `hid-generic` (no unbinding), mapped to key zones, injected with uinput.
Protocol credit: `xeeban/macbook-t1-linux`.

Two traps worth knowing:
- **Under `sudo`, `~` is `/root`** - resolve the desktop user's home via `SUDO_USER`, or the
  wallpaper silently falls back to a default palette.
- **Never build frames pixel-by-pixel in Python.** 2170x60 per frame took seconds; making a
  1-row image and letting Pillow `resize()` to full height is ~4 ms (~229 fps).

## Panel orientation

The panel is physically **2170x60 landscape** but the framebuffer is **60 wide x 2170 tall,
rotated 90 degrees** — the wire buffer has 60-pixel rows. `dfr-play.py` transposes
(`ffmpeg ...,transpose=1`). Solid fills and column-uniform patterns look correct either way,
which is why the first colour-bar test passed despite the wrong layout.

## Prior art — read these

Two repos cover this same machine and go further:

- **`sunplex07/appletbdrm`** — unified T1/T2 **DRM** driver (`/dev/dri/card*`), `ibridge-switcher`
  + systemd for config 2, backlight via HID reports 3/4/5 on interface 6, DKMS install.
  Note: it does **not** handle `apple-ibridge`, which force-selects config 1 and will fight it.
- **`xeeban/macbook-t1-linux`** (`touch-bar/`) — same machine (MacBookPro13,2). Independently
  found the same config-1 force-select root cause and the same `usb_device_driver` fix. Also has
  **touch input** (multitouch digitizer on HID interrupt EP `0x83`, first 4 bytes = LE float32 X
  in ~[0.5,1.0], read via hidraw alongside hid-generic, keys injected with uinput) and `dfrd/`,
  a full userspace UI with FreeType labels and macOS-style layers.

## Still to do

- draw something useful rather than a solid fill
- persistence across boot
- function row / touch input while in config 2 (currently `apple-ibridge` must stay unloaded)
