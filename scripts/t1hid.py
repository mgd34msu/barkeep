"""T1 iBridge HID feature reports: panel power and Touch Bar brightness.

The bar's backlight is driven by HID feature reports on **USB interface 6**
(usage page 0xFF12), reachable from userspace through hidraw once apple-ibridge
is out of the way. Protocol credit: sunplex07/appletbdrm, which implements the
same reports in-kernel for both T1 and T2.

    report 5 (116 bytes)  AutoBrightness + capability block
                          byte[3]    1 = manual, 2 = ALS-driven
                          byte[4:8]  MinNits, u32 LE
                          byte[8:12] MaxNits, u32 LE
    report 4 (14 bytes)   absolute brightness
                          byte[1] = 2, byte[2:6] = nits, u32 LE
    report 3 (15 bytes)   display state, byte[?] 1 = off, 2 = on

Manual brightness only takes effect once AutoBrightness is switched off, so
set_nits() clears it first.

Nits are in thousandths: the firmware default floor is 11899, i.e. ~11.9 nits.
"""
import fcntl, glob, os, struct

IFACE_BRIGHTNESS = 6

REPORT5_ID, REPORT5_LEN = 5, 116        # autobrightness + caps
REPORT4_ID, REPORT4_LEN = 4, 14         # absolute nits
REPORT3_ID, REPORT3_LEN = 3, 15         # display on/off

AUTO_OFF, AUTO_ON = 1, 2
MIN_NITS_DEFAULT, MAX_NITS_DEFAULT = 11899, 500000


def _ioc(direction, length):
    # _IOC(dir, 'H', nr, len) - HIDIOCGFEATURE is nr 7, HIDIOCSFEATURE is nr 6
    nr = 7 if direction == "r" else 6
    return (3 << 30) | (length << 16) | (ord("H") << 8) | nr


def hidraw_for_interface(want, vid="05ac", pid="8600"):
    """The /dev/hidrawN backed by a given USB interface of the iBridge."""
    for hr in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        p = os.path.realpath(os.path.join(hr, "device"))
        for _ in range(6):
            inum = os.path.join(p, "bInterfaceNumber")
            if os.path.exists(inum):
                try:
                    if int(open(inum).read().strip(), 16) != want:
                        break
                    usb = os.path.dirname(p)
                    if (open(os.path.join(usb, "idVendor")).read().strip() == vid
                            and open(os.path.join(usb, "idProduct")).read().strip() == pid):
                        return "/dev/" + os.path.basename(hr)
                except Exception:
                    pass
                break
            p = os.path.dirname(p)
    return None


def get_feature(node, report_id, length):
    buf = bytearray(length)
    buf[0] = report_id
    fd = os.open(node, os.O_RDWR)
    try:
        fcntl.ioctl(fd, _ioc("r", length), buf, True)
    finally:
        os.close(fd)
    return bytes(buf)


def set_feature(node, buf):
    fd = os.open(node, os.O_RDWR)
    try:
        fcntl.ioctl(fd, _ioc("w", len(buf)), bytes(buf))
    finally:
        os.close(fd)


def caps(node=None):
    """(min_nits, max_nits, auto_on, ok).

    `ok` is False when the panel could not actually be read - no device, or no
    permission on the hidraw node (it needs root). The first three values are
    then firmware defaults, which are NOT this panel's real range, so callers
    must not present them as measured.
    """
    node = node or hidraw_for_interface(IFACE_BRIGHTNESS)
    if not node:
        return MIN_NITS_DEFAULT, MAX_NITS_DEFAULT, False, False
    try:
        r = get_feature(node, REPORT5_ID, REPORT5_LEN)
        lo = struct.unpack_from("<I", r, 4)[0]
        hi = struct.unpack_from("<I", r, 8)[0]
        if not (0 < lo < hi):                 # implausible - use defaults
            return MIN_NITS_DEFAULT, MAX_NITS_DEFAULT, r[3] == AUTO_ON, False
        return lo, hi, r[3] == AUTO_ON, True
    except PermissionError:
        return MIN_NITS_DEFAULT, MAX_NITS_DEFAULT, False, False
    except Exception:
        return MIN_NITS_DEFAULT, MAX_NITS_DEFAULT, False, False


def set_auto(on, node=None):
    """ALS-driven brightness on/off. The T1 does the tracking itself."""
    node = node or hidraw_for_interface(IFACE_BRIGHTNESS)
    if not node:
        return False
    try:
        r = bytearray(get_feature(node, REPORT5_ID, REPORT5_LEN))
    except Exception:
        r = bytearray(REPORT5_LEN)
        r[0] = REPORT5_ID
    r[3] = AUTO_ON if on else AUTO_OFF
    try:
        set_feature(node, r)
        return True
    except Exception:
        return False


def set_nits(nits, node=None):
    node = node or hidraw_for_interface(IFACE_BRIGHTNESS)
    if not node:
        return False
    set_auto(False, node)                    # manual needs auto off first
    buf = bytearray(REPORT4_LEN)
    buf[0] = REPORT4_ID
    buf[1] = 2
    struct.pack_into("<I", buf, 2, int(nits))
    try:
        set_feature(node, buf)
        return True
    except Exception:
        return False


def set_percent(pct, node=None):
    """0-100 across the panel's own reported nits range."""
    node = node or hidraw_for_interface(IFACE_BRIGHTNESS)
    lo, hi, _, _ = caps(node)
    pct = max(0.0, min(100.0, float(pct)))
    return set_nits(lo + (hi - lo) * pct / 100.0, node)
