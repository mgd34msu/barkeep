#!/usr/bin/env python3
"""Push images or video to the T1 Touch Bar via /dev/dfr0.

    dfr-play.py test                 built-in test pattern (no deps)
    dfr-play.py bars                 colour bars
    dfr-play.py IMAGE.png            show a still (needs ffmpeg)
    dfr-play.py VIDEO.mp4            play video, looping (needs ffmpeg)
    dfr-play.py VIDEO.mp4 --once     play once
    dfr-play.py --fps 30 VIDEO.mp4
    dfr-play.py --crop VIDEO.mp4     keep aspect, show a centre band (less aliasing)

Panel is 2170x60 RGB888 = 390600 bytes per frame.
"""
import sys, os, time, subprocess, shutil

# The panel is physically 2170x60 landscape, but the framebuffer is PORTRAIT:
# 60 wide x 2170 tall, rotated 90deg. So the wire buffer is 60-pixel rows.
W, H, BPP = 2170, 60, 3          # logical, as the user sees the bar
PW, PH = H, W                     # panel buffer: 60 wide, 2170 tall
FRAME = W * H * BPP
DEV = "/dev/dfr0"
ROT = 1                           # ffmpeg transpose mode (1=cw, 2=ccw)
FIT = "squash"                    # squash | crop  (crop keeps aspect, shows a centre slice)


def transpose_rgb(buf):
    """logical 2170x60 RGB -> panel 60x2170 RGB"""
    out = bytearray(FRAME)
    for y in range(H):
        row = y * W * BPP
        for x in range(W):
            si = row + x * BPP
            di = (x * PW + y) * BPP
            out[di:di+BPP] = buf[si:si+BPP]
    return bytes(out)


def send(dev, buf):
    dev.write(buf)
    dev.flush()


def gradient():
    """left-to-right gradient along the physical bar = panel's long axis"""
    out = bytearray()
    for ly in range(PH):                 # long axis = physical left..right
        t = ly / (PH - 1)
        px = bytes((int(255 * t), int(255 * (1 - t)), int(128 + 127 * (t * 2 % 1))))
        out += px * PW                   # constant across the short axis
    return bytes(out)


def bars():
    cols = [(255,0,0),(0,255,0),(0,0,255),(255,255,0),(0,255,255),(255,0,255),(255,255,255),(0,0,0)]
    out = bytearray()
    for ly in range(PH):
        out += bytes(cols[ly * len(cols) // PH]) * PW
    return bytes(out)


def ffmpeg_frames(path, fps):
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found - install it, or use 'test'/'bars'")
    if FIT == "crop":
        # keep aspect: fill the width, then take a centre band 60px tall
        vf = f"scale={W}:-1:flags=lanczos,crop={W}:{H}:(iw-{W})/2:(ih-{H})/2,transpose={ROT}"
    else:
        # squash to fit; lanczos + a light blur tames aliasing at 36:1
        vf = f"scale={W}:{H}:flags=lanczos,transpose={ROT}"
    cmd = ["ffmpeg", "-loglevel", "error", "-i", path,
           "-vf", vf, "-r", str(fps),
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    while True:
        buf = p.stdout.read(FRAME)
        if len(buf) < FRAME:
            break
        yield buf
    p.stdout.close(); p.wait()


def main():
    args = [a for a in sys.argv[1:]]
    fps = 30
    once = "--once" in args
    if "--once" in args: args.remove("--once")
    global ROT, FIT
    if "--crop" in args:
        FIT = "crop"; args.remove("--crop")
    if "--rot" in args:
        i = args.index("--rot"); ROT = int(args[i+1]); del args[i:i+2]
    if "--fps" in args:
        i = args.index("--fps"); fps = int(args[i+1]); del args[i:i+2]
    if not args:
        sys.exit(__doc__)
    src = args[0]

    if not os.path.exists(DEV):
        sys.exit(f"{DEV} missing - is dfr-probe loaded and the Touch Bar session up?")

    with open(DEV, "wb", buffering=0) as dev:
        if src in ("test", "gradient"):
            send(dev, gradient()); print("gradient shown"); return
        if src == "bars":
            send(dev, bars()); print("colour bars shown"); return

        delay = 1.0 / fps
        n = 0
        while True:
            for buf in ffmpeg_frames(src, fps):
                send(dev, buf); n += 1
                time.sleep(delay)
            print(f"\r{n} frames", end="", flush=True)
            if once:
                break
        print()


if __name__ == "__main__":
    main()
