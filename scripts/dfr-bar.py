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
    im = Image.open(path).convert("RGB")
    return _palette_from_image(im, n, merge_dist)


def _palette_from_image(im, n=6, merge_dist=60):
    import colorsys
    im = im.copy()
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


THEME = None   # resolved in main()


def theme_colors_path():
    return os.path.join(_user_home(), ".local/state/omarchy/current/theme/colors.toml")


def theme_palette(path=None, n=6):
    """Palette straight from the desktop theme - no capture, no cost.

    Uses the accent/ANSI hues the UI actually draws with, ordered by hue.
    """
    import colorsys, re
    path = path or theme_colors_path()
    vals = {}
    for line in open(path):
        m = re.match(r'\s*([a-z_]+)\s*=\s*"(#[0-9a-fA-F]{6})"', line)
        if m:
            vals[m.group(1)] = m.group(2)
    want = ["accent", "blue", "cyan", "green", "yellow", "orange",
            "red", "magenta", "brown"]
    cols = []
    for k in want:
        v = vals.get(k)
        if not v:
            continue
        c = tuple(int(v[i:i + 2], 16) for i in (1, 3, 5))
        if all(math.dist(c, o) > 40 for o in cols):
            cols.append(c)
    if len(cols) < 2:
        for k in ("background", "foreground"):
            if vals.get(k):
                v = vals[k]
                cols.append(tuple(int(v[i:i + 2], 16) for i in (1, 3, 5)))
    cols.sort(key=lambda c: colorsys.rgb_to_hsv(*[v / 255 for v in c])[0])
    return cols[:n]


def theme_stamp(path):
    try:
        return (os.path.realpath(path), os.stat(path).st_mtime)
    except OSError:
        return None


def watch_theme(state, period=2.0):
    path = theme_colors_path()
    stamp = theme_stamp(path)
    while not state["stop"]:
        time.sleep(period)
        cur = theme_stamp(path)
        if cur and cur != stamp:
            stamp = cur
            try:
                set_palette(state, theme_palette(path), "theme")
            except Exception as e:
                print("theme reload failed:", e)


def _desktop_env():
    """uid / runtime dir / wayland socket of the logged-in desktop user"""
    import pwd
    name = os.environ.get("SUDO_USER") or os.environ.get("DFR_USER")
    try:
        pw = pwd.getpwnam(name) if name else pwd.getpwuid(1000)
    except KeyError:
        pw = pwd.getpwuid(1000)
    rt = f"/run/user/{pw.pw_uid}"
    wd = os.environ.get("WAYLAND_DISPLAY")
    if not wd and os.path.isdir(rt):
        socks = sorted(g for g in os.listdir(rt) if g.startswith("wayland-")
                       and not g.endswith(".lock"))
        wd = socks[0] if socks else "wayland-1"
    return pw.pw_name, pw.pw_uid, rt, wd or "wayland-1"


def screen_palette(n=6, scale=0.05):
    """Dominant colours of what is CURRENTLY ON SCREEN (grim, as the user)."""
    import subprocess, io
    user, uid, rt, wd = _desktop_env()
    cmd = ["grim", "-s", str(scale), "-t", "ppm", "-"]
    if os.geteuid() == 0:
        cmd = ["sudo", "-n", "-u", user, "env",
               f"XDG_RUNTIME_DIR={rt}", f"WAYLAND_DISPLAY={wd}"] + cmd
    r = subprocess.run(cmd, capture_output=True, timeout=8)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError((r.stderr or b"grim failed").decode()[:120])
    im = Image.open(io.BytesIO(r.stdout)).convert("RGB")
    return _palette_from_image(im, n)


def wallpaper_stamp(path):
    """identity of the CURRENT wallpaper: theme changes swap the symlink target"""
    try:
        real = os.path.realpath(path)
        return (real, os.stat(real).st_mtime)
    except OSError:
        return None


def strip_row(cols):
    """gradient as a 1-row RGB image, W wide - the unit we blend and scale"""
    return Image.frombytes("RGB", (W, 1),
                           b"".join(bytes(c) for c in gradient_strip(cols, W)))


def set_palette(state, cols, why):
    """cross-fade the WHOLE bar from the current gradient to the new one"""
    new = strip_row(cols)
    if state.get("row_to") is not None and new.tobytes() == state["row_to"].tobytes():
        return
    state["row_from"] = state.get("row_to") or new
    state["row_to"] = new
    state["fade_t0"] = time.time()
    state["dirty"] = True
    print(f"{why}: " + "  ".join("#%02x%02x%02x" % c for c in cols))


def current_row(state, fade_s):
    """the 1-row gradient for this instant, mid-fade if a change is in flight"""
    a, b = state.get("row_from"), state.get("row_to")
    if a is None:
        return b
    t = (time.time() - state.get("fade_t0", 0)) / max(fade_s, 1e-6)
    if t >= 1.0:
        state["row_from"] = None
        return b
    t = t * t * (3 - 2 * t)                      # ease in/out
    state["fading"] = True
    return Image.blend(a, b, t)


def watch_wallpaper(path, state, period=2.0):
    stamp = wallpaper_stamp(path)
    while not state["stop"]:
        time.sleep(period)
        cur = wallpaper_stamp(path)
        if cur and cur != stamp:
            stamp = cur
            try:
                set_palette(state, wallpaper_palette(path),
                            "wallpaper -> " + os.path.basename(cur[0]))
            except Exception as e:
                print("wallpaper reload failed:", e)


def palette_distance(a, b):
    """rough perceptual gap between two palettes (0 = identical)"""
    if not a or not b:
        return 1e9
    def near(c, pal):
        return min(math.dist(c, o) for o in pal)
    return (sum(near(c, b) for c in a) / len(a) +
            sum(near(c, a) for c in b) / len(b)) / 2


def watch_screen(state, period=3.0, threshold=18.0):
    """Track the colours on screen and cross-fade when they actually shift.

    The threshold stops the bar re-fading on every tiny variation (a blinking
    cursor, a scrolling line) - only a real change in what is on screen.
    """
    last = None
    while not state["stop"]:
        try:
            cols = screen_palette()
            if last is None or palette_distance(cols, last) > threshold:
                last = cols
                set_palette(state, cols, "screen")
        except Exception as e:
            print("screen sample failed:", e)
            time.sleep(10)
        time.sleep(period)


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
    def sun(scale, dx=0):
        r = 7 * scale
        x = cx + dx
        d.ellipse([x - r, cy - r, x + r, cy + r], fill=FG)
        for a in range(0, 360, 45):
            t = math.radians(a)
            d.line([x + math.cos(t) * (r + 3), cy + math.sin(t) * (r + 3),
                    x + math.cos(t) * (r + 7), cy + math.sin(t) * (r + 7)],
                   fill=FG, width=2)

    def tri(ox, flip=False):
        s = 9
        pts = ([(cx + ox + s, cy - s), (cx + ox + s, cy + s), (cx + ox - s, cy)]
               if flip else
               [(cx + ox - s, cy - s), (cx + ox - s, cy + s), (cx + ox + s, cy)])
        d.polygon(pts, fill=FG)

    def speaker(waves, dx=0):
        x = cx + dx
        d.polygon([(x - 12, cy - 5), (x - 6, cy - 5), (x + 1, cy - 12),
                   (x + 1, cy + 12), (x - 6, cy + 5), (x - 12, cy + 5)], fill=FG)
        for i in range(waves):
            r = 6 + i * 5
            d.arc([x + 1 - r, cy - r, x + 1 + r, cy + r], -55, 55, fill=FG, width=2)

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
        # sun spans dx-12..dx+12 (rays); +/- spans ox-6..ox+6 -> ~16px clear
        sun(0.8, dx=-18)
        plusminus(1 if kind.endswith("+") else -1, 20)
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
        d.rounded_rectangle([cx - 30, cy - 8, cx - 10, cy + 8], radius=3,
                            outline=FG, width=2)
        for i in range(3):
            d.line([cx - 26 + i * 6, cy + 4, cx - 26 + i * 6, cy + 4], fill=FG, width=2)
        plusminus(1 if kind.endswith("+") else -1, 16)
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
        speaker(1, dx=-10); plusminus(-1, 22)
    elif kind == "vol+":
        speaker(2, dx=-12); plusminus(1, 26)
    else:
        text(kind)


def render(row, offset, pressed, font):
    """row: a 1-row RGB image W wide. Rotate it, stretch to full height in C."""
    rowb = row.tobytes()
    o = (offset % W) * 3
    rowb = rowb[o:] + rowb[:o]
    img = Image.frombytes("RGB", (W, 1), rowb).resize((W, H), Image.NEAREST)
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
    source, fade_s = "screen", 2.0
    if "--wallpaper" in args:
        i = args.index("--wallpaper"); wall = args[i + 1]; source = "wallpaper"; del args[i:i + 2]
    if "--source" in args:
        i = args.index("--source"); source = args[i + 1]; del args[i:i + 2]
    if "--fade" in args:
        i = args.index("--fade"); fade_s = float(args[i + 1]); del args[i:i + 2]
    if "--no-touch" in args:
        no_touch = True; args.remove("--no-touch")
    if "--flow" in args:
        i = args.index("--flow"); flow = float(args[i + 1]); del args[i:i + 2]
    thresh = 18.0
    if "--threshold" in args:
        i = args.index("--threshold"); thresh = float(args[i + 1]); del args[i:i + 2]
    poll = 3.0
    if "--poll" in args:
        i = args.index("--poll"); poll = float(args[i + 1]); del args[i:i + 2]

    if not os.path.exists(DEV):
        sys.exit(f"{DEV} missing - run scripts/dfr-up.sh first")

    state = {"pressed": -1, "dirty": True, "stop": False,
             "row_from": None, "row_to": None, "fade_t0": 0.0}

    def initial(src):
        if src == "screen":
            return screen_palette(), "screen"
        if src == "wallpaper":
            return wallpaper_palette(wall), "wallpaper " + os.path.basename(
                os.path.realpath(wall))
        return theme_palette(), "theme"

    for src in (source, "theme", "wallpaper"):
        try:
            cols, why = initial(src)
            source = src
            break
        except Exception as e:
            print(f"{src} source unavailable: {e}")
    else:
        cols, why = [(137, 180, 250), (203, 166, 247), (166, 227, 161)], "builtin"
    set_palette(state, cols, why)
    state["row_from"] = None                     # no fade on the very first frame

    font = find_font(26)

    if source == "theme":
        threading.Thread(target=watch_theme, args=(state,), daemon=True).start()
    elif source == "wallpaper":
        threading.Thread(target=watch_wallpaper, args=(wall, state), daemon=True).start()
    elif source == "screen":
        print(f"screen tracking: sampling every {poll}s, fade {fade_s}s, "
              f"threshold {thresh} (in-memory only, nothing written to disk)")
        threading.Thread(target=watch_screen, args=(state, poll, thresh),
                         daemon=True).start()

    node = None if no_touch else find_digitizer()
    if node:
        threading.Thread(target=touch_loop, args=(state, node), daemon=True).start()
    elif not no_touch:
        print("touch: digitizer hidraw not found (need config 2) - display only")

    dev = open(DEV, "wb", buffering=0)
    offset = 0
    period = 1.0 / 30
    try:
        while True:
            state["fading"] = False
            row = current_row(state, fade_s)
            if state["dirty"] or state["fading"] or flow:
                img = render(row, offset, state["pressed"], font)
                dev.write(to_panel(img)); dev.flush()
                state["dirty"] = False
            if flow:
                offset = (offset + max(1, int(flow / 30))) % W
            time.sleep(period)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        state["stop"] = True
        dev.close()


if __name__ == "__main__":
    main()
