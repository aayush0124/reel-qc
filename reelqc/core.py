"""Probe a video, run the delivery checks, build the report. Needs ffmpeg + ffprobe on PATH."""
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

__version__ = "0.1.0"

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
EXIT_CODES = {PASS: 0, WARN: 10, FAIL: 1}  # 2 = could not run (bad args, missing ffmpeg, unreadable file)

# Shared thresholds. Units: seconds, LUFS, dBTP, dB, 0-255 luma for hook motion.
DEFAULTS = dict(
    fps=30.0, fps_tol=0.5,
    codecs="h264,hevc",
    lufs=-14.0, lufs_tol=1.5, true_peak_max=-1.0,
    black_max=0.5, black_pix_th=0.10,
    freeze_max=2.0, freeze_noise_db=-60.0,
    silence_max=2.0, silence_db=-50.0,
    hook_s=2.0, hook_motion_min=1.5,
    sheet_frames=12,
)

PROFILES = {
    "reel": dict(DEFAULTS, width=1080, height=1920, min_s=15.0, max_s=90.0),
    "short": dict(DEFAULTS, width=1080, height=1920, min_s=5.0, max_s=180.0),
    "yt": dict(DEFAULTS, width=1920, height=1080, min_s=60.0, max_s=3600.0,
               freeze_max=5.0, silence_max=3.0),  # long-form: slides and pauses are normal
    "square": dict(DEFAULTS, width=1080, height=1080, min_s=5.0, max_s=60.0),
}


class QCError(Exception):
    """The check could not run (as opposed to: the video failed it)."""


def settings(profile="reel", overrides=None):
    """Profile thresholds with overrides applied; values are coerced to the default's type."""
    if profile not in PROFILES:
        raise QCError(f"unknown profile {profile!r}; choose from {', '.join(PROFILES)}")
    p = dict(PROFILES[profile])
    for key, value in (overrides or {}).items():
        if key not in p:
            raise QCError(f"unknown setting {key!r}; see `reelqc profiles`")
        try:
            p[key] = type(p[key])(value)
        except ValueError:
            raise QCError(f"bad value for {key}: {value!r}") from None
    return p


def _run(args, text=True):
    return subprocess.run(args, capture_output=True, text=text, errors="replace" if text else None)


def probe(path):
    """Stream facts from ffprobe. Width/height are as displayed (rotation metadata applied)."""
    r = _run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    if r.returncode:
        raise QCError(f"ffprobe could not read {path}: {r.stderr.strip()[-300:]}")
    data = json.loads(r.stdout)
    streams = data.get("streams", [])
    v = next((s for s in streams if s["codec_type"] == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in streams if s["codec_type"] == "audio"), None)
    if v is None:
        raise QCError(f"{path} has no video stream")
    w, h = v["width"], v["height"]
    rotation = next((sd["rotation"] for sd in v.get("side_data_list", []) if "rotation" in sd), 0)
    if abs(int(rotation)) % 180 == 90:  # phone footage: stored landscape, shown portrait
        w, h = h, w
    num, den = (float(x) for x in v.get("avg_frame_rate", "0/1").split("/"))
    return dict(
        width=w, height=h, fps=round(num / den, 3) if den else 0.0,
        vcodec=v["codec_name"], pix_fmt=v.get("pix_fmt"),
        acodec=a["codec_name"] if a else None,
        duration=round(float(data.get("format", {}).get("duration") or v.get("duration") or 0), 3),
    )


def _spans(log, start_key, end_key, until):
    """Pair detector start/end log lines into (start, end); an open span runs to `until`."""
    starts = [float(x) for x in re.findall(start_key + r":\s*(-?[\d.]+)", log)]
    ends = [float(x) for x in re.findall(end_key + r":\s*(-?[\d.]+)", log)]
    ends += [until] * (len(starts) - len(ends))
    return [(round(s, 2), round(e, 2)) for s, e in zip(starts, ends)]


def detect(path, p, duration, has_audio):
    """One decode pass: blackdetect + freezedetect on video, silencedetect + ebur128 on audio."""
    vf = (f"blackdetect=d=0:pix_th={p['black_pix_th']},"
          f"freezedetect=n={p['freeze_noise_db']}dB:d={p['freeze_max']}")
    args = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:v:0", "-vf", vf]
    if has_audio:
        args += ["-map", "0:a:0", "-af",
                 f"silencedetect=n={p['silence_db']}dB:d={p['silence_max']},"
                 "ebur128=peak=true:framelog=verbose"]
    r = _run(args + ["-f", "null", "-"])
    if r.returncode:  # a decode error must never read as "no defects found"
        raise QCError(f"ffmpeg could not decode {path}: {r.stderr.strip()[-300:]}")
    log = r.stderr
    out = dict(
        black=_spans(log, "black_start", "black_end", duration),
        freeze=_spans(log, "freeze_start", "freeze_end", duration),
        silence=_spans(log, "silence_start", "silence_end", duration),
        lufs=None, true_peak=None,
    )
    _, found, summary = log.rpartition("Summary:")
    if has_audio and found:
        lufs = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
        peak = re.search(r"Peak:\s+(-?inf|-?[\d.]+) dBFS", summary)
        out["lufs"] = float(lufs.group(1)) if lufs else None
        out["true_peak"] = float(peak.group(1)) if peak and "inf" not in peak.group(1) else None
    return out


def hook_motion(path, seconds):
    """Mean absolute frame difference (0-255) over the first `seconds`, on 64x64 grey frames at
    10 fps. Area-downscaling averages grain away, so only structural motion registers, and the
    fixed sample rate keeps the number comparable across 24/30/60 fps sources."""
    raw = _run(["ffmpeg", "-v", "error", "-t", str(seconds), "-i", str(path), "-an",
                "-vf", "fps=10,scale=64:64:flags=area,format=gray", "-f", "rawvideo", "-"],
               text=False).stdout
    n = 64 * 64
    frames = [raw[i:i + n] for i in range(0, len(raw) - n + 1, n)]
    diffs = [sum(abs(x - y) for x, y in zip(a, b)) / n for a, b in zip(frames, frames[1:])]
    return round(sum(diffs) / len(diffs), 2) if diffs else 0.0


def contact_sheet(path, out, duration, frames):
    """`frames` evenly spaced frames (the first one included) tiled into one PNG."""
    cols = min(frames, 6)
    rows = math.ceil(frames / cols)
    vf = (f"fps={frames / max(duration, 0.1):.6f},"
          "scale=320:320:force_original_aspect_ratio=decrease:force_divisible_by=2,"
          f"tile={cols}x{rows}:padding=6:margin=6:color=0x161616")
    r = _run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vf", vf, "-frames:v", "1", str(out)])
    if r.returncode:
        raise QCError(f"contact sheet failed: {r.stderr.strip()[-300:]}")


def _ratio(w, h):
    g = math.gcd(w, h) or 1
    return f"{w // g}:{h // g}"


def _fmt_spans(spans):
    return ", ".join(f"{s:.2f}-{e:.2f}s" for s, e in spans[:4]) + (" ..." if len(spans) > 4 else "")


def check(path, profile="reel", sheet=None, **overrides):
    """Run every check on `path`. Returns the report dict; report["exit_code"] is 0, 10 or 1.
    Raises QCError when the check itself cannot run."""
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise QCError(f"{tool} not found on PATH (install ffmpeg: https://ffmpeg.org/download.html)")
    if not Path(path).is_file():
        raise QCError(f"no such file: {path}")
    p = settings(profile, overrides)
    m = probe(path)
    d = detect(path, p, m["duration"], has_audio=m["acodec"] is not None)
    motion = hook_motion(path, p["hook_s"])
    results = []

    def add(name, ok, detail, severity=FAIL):
        results.append(dict(check=name, status=PASS if ok else severity, detail=detail))

    w, h = m["width"], m["height"]
    add("aspect", abs((w / h) / (p["width"] / p["height"]) - 1) <= 0.01,
        f"{_ratio(w, h)} ({w}x{h}), expected {_ratio(p['width'], p['height'])}")
    add("resolution", (w, h) == (p["width"], p["height"]),
        f"{w}x{h}, expected {p['width']}x{p['height']}", WARN)
    add("fps", abs(m["fps"] - p["fps"]) <= p["fps_tol"],
        f"{m['fps']:.2f} fps, expected {p['fps']:g} +/-{p['fps_tol']:g}", WARN)
    codecs = p["codecs"].split(",")
    add("codec", m["vcodec"] in codecs, f"{m['vcodec']}, allowed {'/'.join(codecs)}")
    add("pix_fmt", m["pix_fmt"] == "yuv420p",
        f"{m['pix_fmt']}, expected yuv420p (8-bit 4:2:0 plays everywhere)", WARN)
    add("audio", m["acodec"] is not None, f"audio stream: {m['acodec'] or 'missing'}")
    add("duration", p["min_s"] <= m["duration"] <= p["max_s"],
        f"{m['duration']:.2f}s, window {p['min_s']:g}-{p['max_s']:g}s")

    if m["acodec"] is None:
        results += [dict(check=c, status=SKIP, detail="no audio stream")
                    for c in ("loudness", "true_peak", "silence")]
    else:
        lufs, tp = d["lufs"], d["true_peak"]
        add("loudness", lufs is not None and abs(lufs - p["lufs"]) <= p["lufs_tol"],
            f"{lufs} LUFS integrated, target {p['lufs']:g} +/-{p['lufs_tol']:g}")
        add("true_peak", tp is None or tp <= p["true_peak_max"],
            f"{'-inf' if tp is None else tp} dBTP, max {p['true_peak_max']:g}")
        add("silence", not d["silence"],
            f"silence > {p['silence_max']:g}s: {_fmt_spans(d['silence']) or 'none'}", WARN)

    long_black = [(s, e) for s, e in d["black"] if e - s > p["black_max"]]
    add("black", not long_black, f"black > {p['black_max']:g}s: {_fmt_spans(long_black) or 'none'}")
    first_black = any(s <= 0.05 for s, _ in d["black"])  # tolerate a non-zero first pts
    add("first_frame", not first_black,
        "first frame is black (dead hook)" if first_black else "first frame has picture")
    add("freeze", not d["freeze"], f"frozen > {p['freeze_max']:g}s: {_fmt_spans(d['freeze']) or 'none'}")
    add("hook_motion", motion >= p["hook_motion_min"],
        f"{motion} mean frame diff in first {p['hook_s']:g}s, min {p['hook_motion_min']:g}", WARN)

    statuses = {r["status"] for r in results}
    verdict = FAIL if FAIL in statuses else WARN if WARN in statuses else PASS
    if sheet:
        contact_sheet(path, sheet, m["duration"], int(p["sheet_frames"]))
    return dict(tool=f"reelqc {__version__}", file=Path(path).as_posix(), profile=profile,
                settings=p, media=m, measured=dict(d, hook_motion=motion), checks=results,
                verdict=verdict, exit_code=EXIT_CODES[verdict],
                sheet=Path(sheet).as_posix() if sheet else None)


def format_report(report):
    """The human-readable report: one line per check, verdict last."""
    m = report["media"]
    lines = [
        f"{report['tool']}  {report['file']}  profile={report['profile']}",
        f"media: {m['width']}x{m['height']} {m['fps']:.2f}fps {m['vcodec']}/{m['pix_fmt']}, "
        f"audio {m['acodec'] or 'none'}, {m['duration']:.2f}s",
        "",
    ]
    lines += [f"  {r['status']:<4}  {r['check']:<12} {r['detail']}" for r in report["checks"]]
    lines += ["", f"VERDICT: {report['verdict']} (exit {report['exit_code']})"]
    if report["sheet"]:
        lines.append(f"contact sheet: {report['sheet']}")
    return "\n".join(lines)
