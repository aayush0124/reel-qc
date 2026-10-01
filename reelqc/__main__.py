"""CLI: python -m reelqc check video.mp4 --profile reel   (or the `reelqc` console script)"""
import argparse
import json
import sys
from pathlib import Path

from .core import PROFILES, QCError, __version__, check, format_report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="reelqc", description="Delivery QC gate for short-form video. "
                                 "Exit 0 = pass, 10 = pass with warnings, 1 = fail, 2 = could not run.")
    ap.add_argument("--version", action="version", version=f"reelqc {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="check one video against a delivery profile")
    c.add_argument("video")
    c.add_argument("--profile", default="reel", choices=list(PROFILES))
    c.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="override a threshold, e.g. --set lufs=-16 --set max_s=60 (repeatable)")
    c.add_argument("--out", help="folder for <name>.qc.json and <name>.sheet.png (default: next to the video)")
    c.add_argument("--no-sheet", action="store_true", help="skip the contact sheet")
    sub.add_parser("profiles", help="print the built-in profiles and every overridable key")
    a = ap.parse_args(argv)

    if a.cmd == "profiles":
        print(json.dumps(PROFILES, indent=2))
        return 0
    bad = [kv for kv in a.set if "=" not in kv]
    if bad:
        ap.error(f"--set expects KEY=VALUE, got {bad[0]!r}")
    overrides = dict(kv.split("=", 1) for kv in a.set)
    video = Path(a.video)
    out = Path(a.out) if a.out else video.parent
    try:
        out.mkdir(parents=True, exist_ok=True)
        sheet = None if a.no_sheet else out / f"{video.stem}.sheet.png"
        report = check(video, a.profile, sheet=sheet, **overrides)
    except QCError as e:
        print(f"reelqc: error: {e}", file=sys.stderr)
        return 2
    json_path = out / f"{video.stem}.qc.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(format_report(report))
    print(f"json: {json_path.as_posix()}")
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
