#!/usr/bin/env python3
"""dfr-bar — a working Touch Bar for the Apple T1.

Draws the config-1 style function row on top of a gradient sampled from the
desktop wallpaper, and makes the buttons actually work: reads the digitizer
over hidraw and injects real keys via uinput.

    sudo python3 dfr-bar.py                     # wallpaper gradient + buttons
    sudo python3 dfr-bar.py --wallpaper X.jpg   # explicit image
    sudo python3 dfr-bar.py --no-touch          # render only
    sudo python3 dfr-bar.py --flow 40           # drift the gradient, px/sec

Requires the display session to be up (scripts/dfr-up.sh) so /dev/dfr0 exists.

Touch protocol (T1, USB config 2): the digitizer is a non-standard HID on
interface 2, EP 0x83. Reports are ~52 bytes; the first 4 are a little-endian
float32 X in [0.5, 1.0] across the bar. hid-generic keeps the interface and
hidraw hands us raw reports, so this coexists with the display stack.
Release is inferred from a gap in reports.
"""
import os, sys, glob, time, math, struct, threading

from PIL import Image, ImageDraw, ImageFont

W, H, BPP = 2170, 60, 3          # bar as the user sees it (landscape)
PW, PH = H, W                    # panel buffer is portrait: 60 wide, 2170 tall
FRAME = W * H * BPP
DEV = "/dev/dfr0"
def _user_home():
    """under sudo, ~ is /root - resolve the DESKTOP user's home instead"""
    import pwd
    for name in (os.environ.get("SUDO_USER"), os.environ.get("DFR_USER")):
        if name:
            try:
                return pwd.getpwnam(name).pw_dir
            except KeyError:
                pass
    try:
        return pwd.getpwuid(1000).pw_dir
    except KeyError:
        return os.path.expanduser("~")


WALL = os.path.join(_user_home(), ".local/state/omarchy/current/background")

RELEASE_S = 0.12                 # no report for this long => finger up

# icon kind, evdev keycode, relative width. Icons are DRAWN, not glyphs -
# font coverage for emoji/media symbols is unreliable (tofu boxes).
KEYS = [
    ("esc",    1,   1.6),   # KEY_ESC
    ("bri-",   224, 1.0),   # KEY_BRIGHTNESSDOWN
    ("bri+",   225, 1.0),   # KEY_BRIGHTNESSUP
    ("grid",   120, 1.0),   # KEY_SCALE      (mission control)
    ("apps",   204, 1.0),   # KEY_DASHBOARD  (launchpad)
    ("kb-",    229, 1.0),   # KEY_KBDILLUMDOWN
    ("kb+",    230, 1.0),   # KEY_KBDILLUMUP
    ("prev",   165, 1.0),   # KEY_PREVIOUSSONG
    ("play",   164, 1.0),   # KEY_PLAYPAUSE
    ("next",   163, 1.0),   # KEY_NEXTSONG
    ("mute",   113, 1.0),   # KEY_MUTE
    ("vol-",   114, 1.0),   # KEY_VOLUMEDOWN
    ("vol+",   115, 1.0),   # KEY_VOLUMEUP
]


# ---------------------------------------------------------------- palette

def find_font(size):
    for pat in ("/usr/share/fonts/**/JetBrainsMono*.ttf",
                "/usr/share/fonts/**/DejaVuSans.ttf",
                "/usr/share/fonts/**/*Nerd*.ttf",
                "/usr/share/fonts/**/*.ttf"):
        for p in sorted(glob.glob(pat, recursive=True)):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def wallpaper_palette(path, n=6, merge_dist=60):
    """Dominant colours actually PRESENT in the wallpaper.

    Quantizing alone is not enough: anti-aliased edges (e.g. a logo outline)
    produce blend colours that exist nowhere as a real region. So we quantize,
    then merge entries that are perceptually close, keeping the most common
    member of each cluster and its total share.
    """
    import colorsys
    im = Image.open(path).convert("RGB")
    im.thumbnail((200, 200))
    q = im.quantize(colors=n * 2, method=Image.MEDIANCUT).convert("RGB")
    counts = {}
    for px in q.getdata():
        counts[px] = counts.get(px, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])

    clusters = []                       # [representative, total_weight]
    for col, wgt in ordered:
        for cl in clusters:
            r = cl[0]
            if math.dist(col, r) < merge_dist:
                cl[1] += wgt
                break
        else:
            clusters.append([col, wgt])

    total = sum(c[1] for c in clusters) or 1
    keep = [c for c in clusters if c[1] / total >= 0.04][:n] or clusters[:n]
    if len(keep) < 2:
        keep = clusters[:2] if len(clusters) >= 2 else keep
    keep.sort(key=lambda c: colorsys.rgb_to_hsv(*[v / 255 for v in c[0]])[0])
    return [tuple(c[0]) for c in keep]


def gradient_strip(cols, length, hold=0.55):
    """Cyclic ramp that HOLDS each sampled colour, then blends briefly.

    `hold` is the fraction of each segment showing the real colour, so most of
    the bar is a colour that genuinely appears in the wallpaper rather than an
    invented in-between shade.
    """
    out = []
    k = len(cols)
    for i in range(length):
        t = (i / length) * k
        idx = int(t)
        f = t - idx
        a = cols[idx % k]
        b = cols[(idx + 1) % k]
        if f <= hold:
            out.append(tuple(a))
        else:
            g = (f - hold) / (1.0 - hold)
            g = g * g * (3 - 2 * g)                 # smoothstep the short blend
            out.append(tuple(int(a[j] + (b[j] - a[j]) * g) for j in range(3)))
    return out


def wallpaper_stamp(path):
    """identity of the CURRENT wallpaper: theme changes swap the symlink target"""
    try:
        real = os.path.realpath(path)
        return (real, os.stat(real).st_mtime)
    except OSError:
        return None


def watch_wallpaper(path, state, period=2.0):
    """re-sample the palette whenever the wallpaper changes"""
    stamp = wallpaper_stamp(path)
    while not state["stop"]:
        time.sleep(period)
        cur = wallpaper_stamp(path)
        if cur and cur != stamp:
            stamp = cur
            try:
                cols = wallpaper_palette(path)
            except Exception as e:
                print("wallpaper reload failed:", e)
                continue
            state["strip"] = gradient_strip(cols, W)
            state["dirty"] = True
            print("wallpaper changed ->", os.path.basename(cur[0]))
            print("  " + "  ".join("#%02x%02x%02x" % c for c in cols))


# ---------------------------------------------------------------- drawing

def key_extents():
    total = sum(k[2] for k in KEYS)
    acc, out = 0.0, []
    for _, _, wgt in KEYS:
        x0 = int(acc / total * W + 0.5)
        acc += wgt
        x1 = int(acc / total * W + 0.5)
        out.append((x0, x1))
    return out


FG = (255, 255, 255, 255)


def draw_icon(d, kind, cx, cy, font):
    """vector icons - no font coverage worries"""
    def sun(scale):
        r = 7 * scale
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=FG)
        for a in range(0, 360, 45):
            t = math.radians(a)
            d.line([cx + math.cos(t) * (r + 3), cy + math.sin(t) * (r + 3),
                    cx + math.cos(t) * (r + 7), cy + math.sin(t) * (r + 7)],
                   fill=FG, width=2)

    def tri(ox, flip=False):
        s = 9
        pts = ([(cx + ox + s, cy - s), (cx + ox + s, cy + s), (cx + ox - s, cy)]
               if flip else
               [(cx + ox - s, cy - s), (cx + ox - s, cy + s), (cx + ox + s, cy)])
        d.polygon(pts, fill=FG)

    def speaker(waves):
        d.polygon([(cx - 12, cy - 5), (cx - 6, cy - 5), (cx + 1, cy - 12),
                   (cx + 1, cy + 12), (cx - 6, cy + 5), (cx - 12, cy + 5)], fill=FG)
        for i in range(waves):
            r = 6 + i * 5
            d.arc([cx + 1 - r, cy - r, cx + 1 + r, cy + r], -55, 55, fill=FG, width=2)

    def text(label, size=22):
        f = font
        try:
            bb = d.textbbox((0, 0), label, font=f)
            d.text((cx - (bb[2] - bb[0]) / 2 - bb[0], cy - (bb[3] - bb[1]) / 2 - bb[1]),
                   label, font=f, fill=FG)
        except Exception:
            pass

    def plusminus(sign, ox):
        d.line([cx + ox - 6, cy, cx + ox + 6, cy], fill=FG, width=2)
        if sign > 0:
            d.line([cx + ox, cy - 6, cx + ox, cy + 6], fill=FG, width=2)

    if kind == "esc":
        text("esc")
    elif kind in ("bri-", "bri+"):
        cx -= 8; sun(0.8); cx += 8; plusminus(1 if kind.endswith("+") else -1, 14)
    elif kind == "grid":
        for r in range(2):
            for c in range(3):
                x = cx - 15 + c * 11; y = cy - 9 + r * 11
                d.rectangle([x, y, x + 8, y + 8], fill=FG)
    elif kind == "apps":
        for r in range(3):
            for c in range(3):
                x = cx - 13 + c * 10; y = cy - 13 + r * 10
                d.ellipse([x, y, x + 6, y + 6], fill=FG)
    elif kind in ("kb-", "kb+"):
        d.rounded_rectangle([cx - 20, cy - 8, cx - 2, cy + 8], radius=3,
                            outline=FG, width=2)
        for i in range(3):
            d.line([cx - 17 + i * 5, cy + 4, cx - 17 + i * 5, cy + 4], fill=FG, width=2)
        plusminus(1 if kind.endswith("+") else -1, 12)
    elif kind == "prev":
        d.rectangle([cx - 12, cy - 9, cx - 9, cy + 9], fill=FG); tri(4, flip=True)
    elif kind == "next":
        tri(-4); d.rectangle([cx + 9, cy - 9, cx + 12, cy + 9], fill=FG)
    elif kind == "play":
        d.rectangle([cx - 8, cy - 9, cx - 4, cy + 9], fill=FG)
        d.rectangle([cx + 4, cy - 9, cx + 8, cy + 9], fill=FG)
    elif kind == "mute":
        speaker(0)
        d.line([cx + 6, cy - 8, cx + 16, cy + 8], fill=FG, width=2)
        d.line([cx + 16, cy - 8, cx + 6, cy + 8], fill=FG, width=2)
    elif kind == "vol-":
        speaker(1); plusminus(-1, 20)
    elif kind == "vol+":
        speaker(2); plusminus(1, 22)
    else:
        text(kind)


def render(strip, offset, pressed, font, _cache={}):
    """Build the frame without per-pixel Python: make a 1-row image from the
    gradient bytes and let Pillow scale it to full height in C."""
    n = len(strip)
    if "row" not in _cache or _cache.get("n") != n:
        _cache["row"] = b"".join(bytes(c) for c in strip)
        _cache["n"] = n
    rowb = _cache["row"]
    o = (offset % n) * 3
    rowb = rowb[o:] + rowb[:o]                      # cheap byte rotation
    base = Image.frombytes("RGB", (W, 1), rowb).resize((W, H), Image.NEAREST)

    img = base.convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")
    for i, (x0, x1) in enumerate(key_extents()):
        d.rounded_rectangle([x0 + 4, 5, x1 - 4, H - 6], radius=10,
                            fill=(0, 0, 0, 205 if i != pressed else 70),
                            outline=(255, 255, 255, 60), width=1)
        draw_icon(d, KEYS[i][0], (x0 + x1) / 2, H / 2, font)
    return img


def to_panel(img):
    """landscape 2170x60 -> panel's portrait 60x2170 buffer"""
    return img.transpose(Image.ROTATE_270).tobytes()


# ---------------------------------------------------------------- touch

def find_digitizer():
    """hidraw node on USB interface 2 of 05ac:8600 while in config 2"""
    for hr in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        dev = os.path.realpath(os.path.join(hr, "device"))
        # walk up to the USB interface directory
        p = dev
        for _ in range(6):
            inum = os.path.join(p, "bInterfaceNumber")
            if os.path.exists(inum):
                try:
                    if int(open(inum).read().strip(), 16) != 2:
                        break
                except Exception:
                    break
                usbdev = os.path.dirname(p)
                try:
                    vid = open(os.path.join(usbdev, "idVendor")).read().strip()
                    pid = open(os.path.join(usbdev, "idProduct")).read().strip()
                    cfg = open(os.path.join(usbdev, "bConfigurationValue")).read().strip()
                except Exception:
                    break
                if vid == "05ac" and pid == "8600" and cfg == "2":
                    return "/dev/" + os.path.basename(hr)
                break
            p = os.path.dirname(p)
    return None


def make_uinput():
    import evdev
    from evdev import UInput, ecodes
    caps = {ecodes.EV_KEY: [k[1] for k in KEYS]}
    return UInput(caps, name="Apple T1 Touch Bar")


def touch_loop(state, node):
    import evdev
    from evdev import ecodes
    ui = make_uinput()
    fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
    extents = key_extents()
    last = 0.0
    cur = None
    print(f"touch: reading {node}")
    try:
        while not state["stop"]:
            try:
                data = os.read(fd, 64)
            except BlockingIOError:
                data = b""
            now = time.time()
            if data and len(data) >= 4:
                x = struct.unpack("<f", data[:4])[0]
                if x >= 0.45:
                    nx = max(0.0, min(0.99999, (x - 0.5) * 2.0))
                    idx = min(int(nx * W) , W - 1)
                    zone = 0
                    for i, (a, b) in enumerate(extents):
                        if a <= idx < b:
                            zone = i
                            break
                    last = now
                    if cur != zone:
                        if cur is not None:
                            ui.write(ecodes.EV_KEY, KEYS[cur][1], 0)
                        ui.write(ecodes.EV_KEY, KEYS[zone][1], 1)
                        ui.syn()
                        cur = zone
                        state["pressed"] = zone
                        state["dirty"] = True
            elif cur is not None and now - last > RELEASE_S:
                ui.write(ecodes.EV_KEY, KEYS[cur][1], 0)
                ui.syn()
                cur = None
                state["pressed"] = -1
                state["dirty"] = True
            time.sleep(0.004)
    finally:
        os.close(fd)
        ui.close()


# ---------------------------------------------------------------- main

def main():
    args = sys.argv[1:]
    wall, no_touch, flow = WALL, False, 0.0
    if "--wallpaper" in args:
        i = args.index("--wallpaper"); wall = args[i + 1]; del args[i:i + 2]
    if "--no-touch" in args:
        no_touch = True; args.remove("--no-touch")
    if "--flow" in args:
        i = args.index("--flow"); flow = float(args[i + 1]); del args[i:i + 2]

    if not os.path.exists(DEV):
        sys.exit(f"{DEV} missing - run scripts/dfr-up.sh first")

    src = wall if os.path.exists(wall) else None
    if src:
        cols = wallpaper_palette(src)
        print("palette from", src)
        print("  " + "  ".join("#%02x%02x%02x" % c for c in cols))
    else:
        cols = [(122, 162, 247), (187, 154, 247), (247, 118, 142), (158, 206, 106)]
        print("wallpaper not found, using theme-ish default palette")

    font = find_font(26)
    state = {"pressed": -1, "dirty": True, "stop": False,
             "strip": gradient_strip(cols, W)}

    if src:
        threading.Thread(target=watch_wallpaper, args=(wall, state),
                         daemon=True).start()

    node = None if no_touch else find_digitizer()
    if node:
        threading.Thread(target=touch_loop, args=(state, node), daemon=True).start()
    elif not no_touch:
        print("touch: digitizer hidraw not found (need config 2) - display only")

    dev = open(DEV, "wb", buffering=0)
    offset, last_draw = 0, 0.0
    fps = 30 if flow else 0
    try:
        while True:
            now = time.time()
            if state["dirty"] or (flow and now - last_draw >= 1.0 / 30):
                img = render(state["strip"], offset, state["pressed"], font)
                dev.write(to_panel(img)); dev.flush()
                state["dirty"] = False
                last_draw = now
            if flow:
                offset = (offset + max(1, int(flow / 30))) % W
                time.sleep(1.0 / 30)
            else:
                time.sleep(0.02)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        state["stop"] = True
        dev.close()


if __name__ == "__main__":
    main()
