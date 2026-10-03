"""Generate synthetic test clips with ffmpeg lavfi, so the tests need no source media.

    python tests/make_fixtures.py        # writes tests/fixtures/*.mp4

Each clip is a 16 s 1080x1920 reel with exactly one defect (or none) for the profile it is
tested against, so a test can assert both the verdict and *which* check produced it.
"""
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "fixtures"
W, H, R, D = 1080, 1920, 30, 16


def src(w=W, h=H, d=D):
    """testsrc2 with a slow horizontal pan, so the picture moves like real footage does."""
    return f"testsrc2=s={w}x{h}:r={R}:d={d},scroll=h=0.004"


def tone(d=D):
    return f"sine=f=440:r=48000:d={d},volume=7.75dB"  # stereo 440 Hz tone, measured at -14 LUFS


def card(d, text_from=0.0):
    """An @AIwithAayush card: brand background #0A0B14, two lines of "text" and an accent rule.
    The foreground covers ~1.2 % of the frame, like a line of 80 px hook text, and is drawn as
    boxes so the fixtures need no font. It appears at `text_from` s, as an animated-in card does."""
    on = f":enable='gte(t,{text_from})'" if text_from else ""
    return (f"color=c=0x0A0B14:s={W}x{H}:r={R}:d={d},"
            f"drawbox=x=240:y=900:w=600:h=24:c=white:t=fill{on},"
            f"drawbox=x=340:y=950:w=400:h=24:c=white:t=fill{on},"
            f"drawbox=x=460:y=1000:w=160:h=6:c=0x00D9FF:t=fill{on}")


def seq(*parts):
    """Play video graphs back to back."""
    return ("".join(f"{p}[s{i}];" for i, p in enumerate(parts))
            + "".join(f"[s{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0")


VIDEO = src()
AUDIO = tone()

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
    # a voiceover muxed onto a timeline of a different length (built without -shortest)
    "video_short": (src(d=D - 3), AUDIO),  # the platform holds the last frame for 3 s
    "audio_short": (VIDEO, tone(D - 3)),  # 3 s of dead air at the end
    # clean @AIwithAayush layouts: the awa profile must pass them
    "awa_cards": (seq(src(d=5), card(3), src(d=5), card(3)), AUDIO),  # point card, end slate
    "awa_open": (seq(card(3, text_from=0.2), src(d=D - 3)), AUDIO),  # hook card animates in
    # 2.2 s end slate, then the voiceover runs 2.6 s past the picture: the slate is on screen
    # 4.8 s, inside the 5 s card limit, so only av_length may object
    "awa_overrun": (seq(src(d=11.2), card(2.2)), AUDIO),
}
UNEVEN = {"video_short", "audio_short", "awa_overrun"}  # -shortest would trim away the defect


def build(force=False):
    OUT.mkdir(exist_ok=True)
    for name, (video, audio) in CLIPS.items():
        dst = OUT / f"{name}.mp4"
        if dst.exists() and not force:
            continue
        args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", video]
        args += ["-f", "lavfi", "-i", audio, "-ac", "2", "-c:a", "aac", "-b:a", "192k"] if audio else []
        args += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
        args += [] if name in UNEVEN else ["-shortest"]
        args += [str(dst)]
        subprocess.run(args, check=True)
        print(f"made {dst.name}")


if __name__ == "__main__":
    build(force="--force" in sys.argv)
