"""Build the synthetic @AIwithAayush reel used in docs/awa-audit.md.

    python docs/awa-sample/make_sample.py [--font PATH]

Writes two 1080x1920 renders next to this file:

  awa_sample.mp4        as rendered: a 27 s edit with a 30 s voiceover muxed on (the script ran
                        long), so the last 3 s show a frozen end slate
  awa_sample_fixed.mp4  the same reel re-cut to the voiceover's 30 s

Layout: hook card that fades in, footage, a point card held 3.5 s, footage, an end slate held
3 s. Cards use the brand background #0A0B14 with white / cyan / muted text. "Footage" is
testsrc2 with a slow pan, standing in for screen recordings and b-roll; the voiceover is a
tone at -14 LUFS. On Windows pass e.g. --font C:/Windows/Fonts/arialbd.ttf.
"""
import argparse
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
W, H, R = 1080, 1920, 30
BG, WHITE, CYAN, MUTED = "0x0A0B14", "0xFFFFFF", "0x00D9FF", "0xB0B3C6"


def footage(d):
    return f"testsrc2=s={W}x{H}:r={R}:d={d},scroll=h=0.004"


def card(d, font, lines, fade=0.0):
    """Brand card; lines are (text, color, size, y)."""
    alpha = f":alpha='min(t/{fade}\\,1)'" if fade else ""
    font = font.replace("\\", "/").replace(":", "\\:")
    text = "".join(f",drawtext=fontfile='{font}':text='{t}':fontcolor={c}:fontsize={s}"
                   f":x=(w-tw)/2:y={y}{alpha}" for t, c, s, y in lines)
    return f"color=c={BG}:s={W}x{H}:r={R}:d={d}{text}"


def build(name, footage2, font, vo=30):
    hook = card(3, font, [("ChatGPT se 5x zyada kaam", WHITE, 60, 870),
                          ("sirf 3 free tools se", CYAN, 56, 960)], fade=0.4)
    point = card(3.5, font, [("TOOL 1", CYAN, 44, 800), ("Napkin.ai", WHITE, 110, 880),
                             ("text ko diagram banao", MUTED, 52, 1030)])
    slate = card(3, font, [("Follow @AIwithAayush", WHITE, 68, 900),
                           ("Daily AI tips, Hinglish mein", MUTED, 48, 1000)])
    parts = [hook, footage(8), point, footage(footage2), slate]
    graph = ("".join(f"{p}[s{i}];" for i, p in enumerate(parts))
             + "".join(f"[s{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0")
    dst = HERE / f"{name}.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-filter_complex", graph,
                    "-f", "lavfi", "-i", f"sine=f=440:r=48000:d={vo},volume=7.75dB",
                    "-ac", "2", "-c:a", "aac", "-b:a", "192k",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(dst)],
                   check=True)  # no -shortest: the mismatch is the point
    print(f"made {dst.relative_to(HERE.parent.parent).as_posix()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--font", default="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    a = ap.parse_args()
    build("awa_sample", 9.5, a.font)         # 3 + 8 + 3.5 + 9.5 + 3 = 27 s of picture
    build("awa_sample_fixed", 12.5, a.font)  # 30 s, matches the voiceover
