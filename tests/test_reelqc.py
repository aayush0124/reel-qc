"""Plain-assert tests, no pytest needed:  python tests/test_reelqc.py

Every fixture carries one defect. A test passes only if the clip gets the expected exit code
AND the defect shows up on the expected check AND every other check passes, so a clip that
fails for the wrong reason is caught too.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import make_fixtures  # noqa: E402  (sibling module)
from reelqc import QCError, check  # noqa: E402

FIX = make_fixtures.OUT
EXPECT = {  # clip: (exit code, {check: status}); unlisted checks must PASS
    "pass": (0, {}),
    "black_gap": (1, {"black": "FAIL"}),
    "black_first": (1, {"first_frame": "FAIL"}),
    "too_loud": (1, {"loudness": "FAIL", "true_peak": "FAIL"}),
    "no_audio": (1, {"audio": "FAIL", "loudness": "SKIP", "true_peak": "SKIP", "silence": "SKIP"}),
    "wrong_aspect": (1, {"aspect": "FAIL", "resolution": "WARN"}),
    "frozen": (1, {"freeze": "FAIL"}),
    "silence": (10, {"silence": "WARN"}),
    "static_hook": (10, {"hook_motion": "WARN"}),
}


def cli(*args):
    return subprocess.run([sys.executable, "-m", "reelqc", *args], cwd=ROOT, capture_output=True, text=True)


def test_fixture_verdicts(tmp):
    for clip, (code, special) in EXPECT.items():
        r = cli("check", str(FIX / f"{clip}.mp4"), "--profile", "reel", "--out", str(tmp), "--no-sheet")
        assert r.returncode == code, f"{clip}: exit {r.returncode}, expected {code}\n{r.stdout}{r.stderr}"
        got = {c["check"]: c["status"] for c in json.loads((tmp / f"{clip}.qc.json").read_text())["checks"]}
        want = {name: special.get(name, "PASS") for name in got}
        assert got == want, f"{clip}: {got} != {want}"
        print(f"  {clip:<13} exit {code:>2}  {' '.join(f'{k}={v}' for k, v in special.items()) or 'all PASS'}")


def test_overrides(tmp):
    # API: a stricter loudness target turns the passing clip into a fail
    report = check(FIX / "pass.mp4", "reel", lufs=-20)
    assert report["exit_code"] == 1 and report["settings"]["lufs"] == -20.0
    # CLI: raise the minimum so the 16 s clip fails a 20 s window
    r = cli("check", str(FIX / "pass.mp4"), "--set", "min_s=20", "--out", str(tmp), "--no-sheet")
    assert r.returncode == 1 and "FAIL  duration" in r.stdout, r.stdout


def test_cannot_run_is_exit_2(tmp):
    assert cli("check", str(tmp / "missing.mp4")).returncode == 2
    broken = tmp / "truncated.mp4"  # a render that died half-way must block, not pass
    broken.write_bytes((FIX / "pass.mp4").read_bytes()[:200_000])
    assert cli("check", str(broken)).returncode == 2
    assert cli("check", str(FIX / "pass.mp4"), "--set", "nonsense=1").returncode == 2
    try:
        check(FIX / "pass.mp4", "tiktok")
        raise AssertionError("unknown profile accepted")
    except QCError:
        pass


def test_contact_sheet(tmp):
    r = cli("check", str(FIX / "pass.mp4"), "--out", str(tmp))
    png = (tmp / "pass.sheet.png").read_bytes()
    assert r.returncode == 0 and png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 10_000


if __name__ == "__main__":
    make_fixtures.build()
    failed = 0
    for name, fn in [(k, v) for k, v in globals().items() if k.startswith("test_")]:
        with tempfile.TemporaryDirectory() as d:
            try:
                fn(Path(d))
                print(f"ok    {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL  {name}: {e}")
    print(f"\n{'FAILED' if failed else 'all tests passed'} ({failed} failing)")
    sys.exit(1 if failed else 0)
