"""
Test Phase-1 filler trimmer (real funcs from main.py, AST-extracted):
- _filler_keep_segments removes äh/ähm/öhm (also with punctuation) but KEEPS so/halt/ja
- _trim_dead_air stitches → output duration == kept sum, video==audio (in sync)
(auto-editor / Phase 2 runs only on Railway; here we verify Phase 1.)
"""
import ast, subprocess, logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
D = Path("C:/tmp_diag/filler"); D.mkdir(parents=True, exist_ok=True)
src = Path(__file__).with_name("main.py").read_text(encoding="utf-8")
lines = src.splitlines(keepends=True); tree = ast.parse(src)
ns = {"re": __import__("re"), "Path": Path, "subprocess": subprocess,
      "log": logging.getLogger("f"), "FPS": 30}
for n in tree.body:
    if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "FILLER_WORDS" for t in n.targets):
        exec("".join(lines[n.lineno - 1:n.end_lineno]), ns)
    if isinstance(n, ast.FunctionDef) and n.name in {"_filler_keep_segments", "_trim_dead_air"}:
        exec("".join(lines[n.lineno - 1:n.end_lineno]), ns)
fk = ns["_filler_keep_segments"]; trim = ns["_trim_dead_air"]

words = [
    {"word": "Also",    "start": 0.0, "end": 0.4},
    {"word": "äh",      "start": 0.5, "end": 0.7},   # filler -> cut
    {"word": "das",     "start": 0.8, "end": 1.1},
    {"word": "ist",     "start": 1.1, "end": 1.3},
    {"word": "ähm,",    "start": 1.5, "end": 1.8},   # filler+comma -> cut
    {"word": "so",      "start": 1.9, "end": 2.1},   # KEEP (contextual)
    {"word": "halt",    "start": 2.1, "end": 2.4},   # KEEP
    {"word": "wichtig", "start": 2.5, "end": 3.0},
]
DUR = 3.5
keeps, n = fk(words, DUR)
kept = round(sum(e - s for s, e in keeps), 3)
print("keeps:", keeps, "fillers_cut:", n, "kept_sum:", kept)

# unit checks
unit_fillers = (n == 2)
# the two filler spans (~0.48-0.72, ~1.48-1.82) must NOT be inside any keep
def covered(t):
    return any(s <= t <= e for s, e in keeps)
keeps_so_halt = covered(2.0) and covered(2.25)          # so / halt kept
cuts_fillers  = (not covered(0.6)) and (not covered(1.65))  # äh / ähm cut
print(f"  [a] exactly 2 fillers cut       : {'PASS' if unit_fillers else 'FAIL'}")
print(f"  [b] so/halt KEPT (not cut)      : {'PASS' if keeps_so_halt else 'FAIL'}")
print(f"  [c] äh/ähm spans removed        : {'PASS' if cuts_fillers else 'FAIL'}")

# e2e stitch
clip = D / "in.mp4"
subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=30:d={DUR}",
                "-f", "lavfi", "-i", f"sine=frequency=300:d={DUR}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)],
               capture_output=True)
out = D / "out.mp4"
ok = trim(clip, keeps, out)
def dur(p, st):
    r = subprocess.run(["ffprobe","-v","error","-select_streams",st,"-show_entries","stream=duration",
                        "-of","default=nk=1:nw=1",str(p)], capture_output=True, text=True)
    try: return float(r.stdout.strip().splitlines()[0])
    except Exception: return -1
if ok:
    vd, ad = dur(out, "v:0"), dur(out, "a:0")
    print(f"  output v={vd:.2f}s a={ad:.2f}s (expect ~{kept:.2f})")
    d_ok = abs(vd - kept) < 0.15; s_ok = abs(vd - ad) < 0.10
    print(f"  [d] duration == kept sum        : {'PASS' if d_ok else 'FAIL'}")
    print(f"  [e] video/audio in sync         : {'PASS' if s_ok else 'FAIL'}")
    allp = unit_fillers and keeps_so_halt and cuts_fillers and d_ok and s_ok
    print(f"\n  ==> {'ALL PASS' if allp else 'FAIL'}")
else:
    print("  ==> FAIL (stitch returned False)")
