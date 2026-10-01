"""Generate synthetic test clips with ffmpeg lavfi, so the tests need no source media.

    python tests/make_fixtures.py        # writes tests/fixtures/*.mp4

Each clip is a 16 s 1080x1920 reel with exactly one defect (or none), so a test can assert
both the verdict and *which* check produced it.
"""
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "fixtures"
W, H, R, D = 1080, 1920, 30, 16


def src(w=W, h=H, d=D):
    """testsrc2 with a slow horizontal pan, so the picture moves like real footage does."""
    return f"testsrc2=s={w}x{h}:r={R}:d={d},scroll=h=0.004"


VIDEO = src()
AUDIO = f"sine=f=440:r=48000:d={D},volume=7.75dB"  # stereo 440 Hz tone, measured at -14 LUFS

CLIPS = {  # name: (video lavfi graph, audio lavfi graph or None)
    "pass": (VIDEO, AUDIO),
    "black_gap": (VIDEO + ",drawbox=c=black:t=fill:enable='between(t,6,7)'", AUDIO),
    "black_first": (VIDEO + ",drawbox=c=black:t=fill:enable='eq(n,0)'", AUDIO),
    "too_loud": (VIDEO, AUDIO + ",volume=13dB"),  # ~-1 LUFS, clipped peaks
    "no_audio": (VIDEO, None),
    "wrong_aspect": (src(1080, 1350), AUDIO),  # a 4:5 feed export sent as a reel
    "frozen": (src(d=D - 3) + ",loop=loop=90:size=1:start=150", AUDIO),  # 3 s hold at 5 s
    "silence": (VIDEO, AUDIO + ",volume=0:enable='between(t,6,9)'"),
    # flat frame with live grain for 3 s: never "frozen" to freezedetect, but nothing moves
    "static_hook": (f"color=c=0x2a3b4c:s={W}x{H}:r={R}:d=3,noise=alls=12:allf=t[a];"
                    f"{src(d=D - 3)}[b];[a][b]concat=n=2:v=1:a=0", AUDIO),
}


def build(force=False):
    OUT.mkdir(exist_ok=True)
    for name, (video, audio) in CLIPS.items():
        dst = OUT / f"{name}.mp4"
        if dst.exists() and not force:
            continue
        args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", video]
        args += ["-f", "lavfi", "-i", audio, "-ac", "2", "-c:a", "aac", "-b:a", "192k"] if audio else []
        args += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-shortest", str(dst)]
        subprocess.run(args, check=True)
        print(f"made {dst.name}")


if __name__ == "__main__":
    build(force="--force" in sys.argv)
