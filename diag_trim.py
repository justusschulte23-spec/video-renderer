"""
Test the Max-Gap silence trimmer with the REAL functions from main.py (AST-extracted).
- Unit-checks _compute_keep_segments against a hand-computed expectation
- Runs _trim_dead_air on a generated 6s clip and verifies output duration == kept sum
  and that video & audio stream durations match (no A/V drift).
"""
import ast, subprocess, logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
D = Path("C:/tmp_diag/trim"); D.mkdir(parents=True, exist_ok=True)

src = Path(__file__).with_name("main.py").read_text(encoding="utf-8")
lines = src.splitlines(keepends=True); tree = ast.parse(src)
ns = {"re": __import__("re"), "Path": Path, "subprocess": subprocess,
      "log": logging.getLogger("t"), "FPS": 30}
for n in tree.body:
    if isinstance(n, ast.FunctionDef) and n.name in {"_compute_keep_segments", "_trim_dead_air"}:
        exec("".join(lines[n.lineno - 1:n.end_lineno]), ns)
keepsf = ns["_compute_keep_segments"]; trimf = ns["_trim_dead_air"]

# ── unit: hand-computed expectation ───────────────────────────────────────────
words = [
    {"word": "a", "start": 0.5, "end": 1.0},
    {"word": "b", "start": 1.2, "end": 1.8},   # gap 0.2 <=0.3 keep
    {"word": "c", "start": 4.0, "end": 4.5},   # gap 2.2 >0.3 trim
    {"word": "d", "start": 4.6, "end": 5.0},   # gap 0.1 keep
]
DUR = 6.0
keeps = keepsf(words, DUR, 0.30, 0.05)
expect = [(0.45, 1.85), (3.95, 5.05)]   # leading + mid + trailing trimmed
print("keep-segments:", keeps)
kept = round(sum(e - s for s, e in keeps), 3)
print(f"kept sum: {kept}s (expect ~2.5)")
unit_ok = (len(keeps) == 2
           and abs(keeps[0][0]-0.45) < 0.01 and abs(keeps[0][1]-1.85) < 0.01
           and abs(keeps[1][0]-3.95) < 0.01 and abs(keeps[1][1]-5.05) < 0.01)
print(f"  [unit] keep math correct: {'PASS' if unit_ok else 'FAIL'}")

# ── end-to-end ffmpeg trim ────────────────────────────────────────────────────
clip = D / "in.mp4"
subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=30:d={DUR}",
                "-f", "lavfi", "-i", f"sine=frequency=300:d={DUR}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)],
               capture_output=True)
out = D / "out.mp4"
ok = trimf(clip, keeps, out)
print(f"\n  trim ran: {ok}")

def dur(path, stream):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", stream,
                        "-show_entries", "stream=duration", "-of", "default=nk=1:nw=1", str(path)],
                       capture_output=True, text=True)
    try: return float(r.stdout.strip().splitlines()[0])
    except Exception: return -1.0

if ok:
    vd, ad = dur(out, "v:0"), dur(out, "a:0")
    print(f"  output video dur: {vd:.2f}s   audio dur: {ad:.2f}s   (expect ~{kept:.2f})")
    dur_ok  = abs(vd - kept) < 0.15
    sync_ok = abs(vd - ad) < 0.10
    print(f"\n  [a] output duration == kept sum : {'PASS' if dur_ok else 'FAIL'}")
    print(f"  [b] video/audio in sync (no drift): {'PASS' if sync_ok else 'FAIL'}")
    print(f"\n  ==> {'ALL PASS' if (unit_ok and dur_ok and sync_ok) else 'FAIL'}")
else:
    print("  ==> FAIL (trim returned False)")
