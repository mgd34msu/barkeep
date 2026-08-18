# linux-t1-touch — Apple T1 Touch Bar display driver for Linux

Target: **MacBookPro13,2** (2016 13" Touch Bar), Apple **T1** iBridge, USB `05ac:8600`.
Not T2. `tiny-dfr`, `appletbdrm` and `hid-appletb-*` are all T2-only and do nothing here.

Status: **protocol works, panel does not yet render.** Config 2 holds indefinitely,
frames are accepted and ACKed, panel descriptor reads correctly. See "Open" below.

## Layout

    ibridge-cfg/   kernel module: selects USB configuration 2 at enumeration
    dfr-probe/     kernel module: claims the DFR display interface, speaks the protocol
    scripts/       build.sh, dfr-go.sh (run), dfr-reset.sh (undo), dispon.py (panel enable)
    reference/     imbushuo/DFRDisplayKm — the working Windows driver (MIT). READ THE SOURCE.

## Run

    bash scripts/build.sh
    sudo bash scripts/dfr-go.sh      # bring up + colour cycle
    sudo bash scripts/dfr-reset.sh   # restore the stock function row, no reboot

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

## Open

Frames ACK but the panel stays dark. The HID feature report (Apple usage `0xff120021`,
report id 2, `[rid, aux=1, disp=1]` via `HIDIOCSFEATURE` — see `scripts/dispon.py`) lights
the backlight white, but no framebuffer content has ever appeared. The byte-exact format
above is implemented and built but has **not yet been tested on hardware**.

Note DFRDisplayKm contains no backlight command at all, implying the panel should light on a
valid frame — so the enable may be a red herring and the frame may still be subtly wrong.
