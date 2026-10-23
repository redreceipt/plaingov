#!/usr/bin/env python3
"""Render brand assets from mark.svg with headless Chrome into ../static (site) and . (X profile images).

Usage: python3 brand/build.py   (macOS; needs Google Chrome)
"""
import os, struct, subprocess, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "..", "static")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
MARK = open(os.path.join(HERE, "mark.svg")).read()
FONT = 'font-family:-apple-system,"SF Pro Display","Segoe UI",system-ui,sans-serif'
INK, BLUE, BG = "#111827", "#1d4ed8", "#fbfaf7"


def shot(html, w, h, out, transparent=False):
    with tempfile.TemporaryDirectory() as tmp:
        page = os.path.join(tmp, "p.html")
        open(page, "w").write(f"<!doctype html><html><body style='margin:0;width:{w}px;height:{h}px;overflow:hidden'>{html}</body></html>")
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={tmp}/profile",
                        "--force-device-scale-factor=1", f"--window-size={w},{h}", f"--screenshot={out}",
                        *(["--default-background-color=00000000"] if transparent else []), f"file://{page}"],
                       check=True, capture_output=True)


def mark(size):
    return f"<div style='width:{size}px;height:{size}px'>{MARK.replace('<svg ', f'<svg style=\"display:block\" width=\"{size}\" height=\"{size}\" ')}</div>"


def card(w, h, mark_px, title_px, sub_px, pad):
    return (f"<div style='width:{w}px;height:{h}px;background:{BG};display:flex;align-items:center;gap:{pad // 2}px;padding:0 {pad}px;box-sizing:border-box;{FONT}'>"
            f"{mark(mark_px)}<div><div style='font-size:{title_px}px;font-weight:800;letter-spacing:-0.02em;color:{INK}'>Plain<span style='color:{BLUE}'>Gov</span></div>"
            f"<div style='font-size:{sub_px}px;color:#374151;margin-top:{sub_px // 3}px'>Government, in plain English.</div>"
            f"<div style='font-size:{int(sub_px * .7)}px;color:#6b7280;margin-top:{sub_px // 2}px'>Congress and all 50 states, explained simply.</div></div></div>")


os.makedirs(STATIC, exist_ok=True)
open(os.path.join(STATIC, "favicon.svg"), "w").write(MARK)
shot(mark(32), 32, 32, os.path.join(STATIC, "favicon-32.png"), transparent=True)
for size, name in ((180, "apple-touch-icon.png"), (512, "icon-512.png")):  # full-bleed: iOS/Android round the corners
    shot(f"<div style='width:{size}px;height:{size}px;background:{BLUE}'>{mark(size)}</div>", size, size, os.path.join(STATIC, name))
shot(card(1200, 630, 220, 96, 40, 90), 1200, 630, os.path.join(STATIC, "og.png"))
shot(f"<div style='width:400px;height:400px;background:{BLUE}'>{mark(400)}</div>", 400, 400, os.path.join(HERE, "x-avatar.png"))
shot(f"<div style='width:1500px;height:500px;background:{BG};display:flex;align-items:center;justify-content:center;{FONT}'>"
     f"<div style='text-align:center'><div style='font-size:84px;font-weight:800;letter-spacing:-0.02em;color:{INK}'>Government, in <span style='color:{BLUE}'>plain English</span>.</div>"
     f"<div style='font-size:36px;color:#4b5563;margin-top:18px'>New bills and laws from Congress and all 50 states, explained simply.</div></div></div>",
     1500, 500, os.path.join(HERE, "x-banner.png"))

# favicon.ico wrapping the 32px PNG (PNG-in-ICO works in every current browser)
png = open(os.path.join(STATIC, "favicon-32.png"), "rb").read()
open(os.path.join(STATIC, "favicon.ico"), "wb").write(struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 32, 32, 0, 0, 1, 32, len(png), 22) + png)
print("ok")
