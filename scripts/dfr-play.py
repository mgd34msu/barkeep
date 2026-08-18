#!/usr/bin/env python3
"""Push images or video to the T1 Touch Bar via /dev/dfr0.

    dfr-play.py test                 built-in test pattern (no deps)
    dfr-play.py bars                 colour bars
    dfr-play.py IMAGE.png            show a still (needs ffmpeg)
    dfr-play.py VIDEO.mp4            play video, looping (needs ffmpeg)
    dfr-play.py VIDEO.mp4 --once     play once
    dfr-play.py --fps 30 VIDEO.mp4

Panel is 2170x60 RGB888 = 390600 bytes per frame.
"""
import sys, os, time, subprocess, shutil

W, H, BPP = 2170, 60, 3
FRAME = W * H * BPP
DEV = "/dev/dfr0"


def send(dev, buf):
    dev.write(buf)
    dev.flush()


def gradient():
    row = bytearray()
    for x in range(W):
        t = x / (W - 1)
        row += bytes((int(255 * t), int(255 * (1 - t)), int(128 + 127 * (t * 2 % 1))))
    return bytes(row) * H


def bars():
    cols = [(255,0,0),(0,255,0),(0,0,255),(255,255,0),(0,255,255),(255,0,255),(255,255,255),(0,0,0)]
    row = bytearray()
    for x in range(W):
        row += bytes(cols[x * len(cols) // W])
    return bytes(row) * H


def ffmpeg_frames(path, fps):
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found - install it, or use 'test'/'bars'")
    cmd = ["ffmpeg", "-loglevel", "error", "-i", path,
           "-vf", f"scale={W}:{H}:flags=bilinear", "-r", str(fps),
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
