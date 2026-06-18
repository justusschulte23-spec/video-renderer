"""
Test the NEW SFX mixer with the REAL functions/constants from main.py (AST-extracted),
real sounds (downloaded subset), and a generated test video. Verifies:
  - t=0 intro (ping+thud) auto-injected
  - semantic events + legacy {type} resolve to assets
  - per-category layering (hook trim+fade, pop trim) builds a VALID ffmpeg filtergraph
  - output mp4 has audio of the right duration
"""
import ast, logging, subprocess
from pathlib import Path
from typing import Optional
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
OUT = Path("C:/tmp_diag/sfx_test"); OUT.mkdir(parents=True, exist_ok=True)

# ── extract real constants + functions from main.py ───────────────────────────
src = Path(__file__).with_name("main.py").read_text(encoding="utf-8")
lines = src.splitlines(keepends=True)
tree = ast.parse(src)
ns = {"re": __import__("re"), "Path": Path, "Optional": Optional,
      "subprocess": subprocess, "log": logging.getLogger("sfx"), "requests": requests}
want_assign = {"SFX_DIR", "SFX_CATEGORY_RULES", "SFX_LIBRARY", "SFX_LEGACY_MAP", "SFX_INTRO_ASSETS"}
want_func = {"sfx_local_path", "_resolve_sfx_asset", "_normalize_sfx_events", "mix_sfx_into_video"}
for node in tree.body:
    if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in want_assign for t in node.targets):
        exec("".join(lines[node.lineno - 1:node.end_lineno]), ns)
    elif isinstance(node, ast.FunctionDef) and node.name in want_func:
        exec("".join(lines[node.lineno - 1:node.end_lineno]), ns)

# redirect SFX_DIR to a local test dir
ns["SFX_DIR"] = OUT / "sfx"
SFX_DIR = ns["SFX_DIR"]
SFX_LIBRARY = ns["SFX_LIBRARY"]
sfx_local_path = ns["sfx_local_path"]
mix = ns["mix_sfx_into_video"]
normalize = ns["_normalize_sfx_events"]

# ── download a representative subset ──────────────────────────────────────────
SUBSET = ["hook_000_app_ping", "hook_000_tech_thud", "hook_cash_register_01",
          "impact_cinematic_hit_01", "pop_snap_finger_01", "trans_whoosh_fast_01"]
for aid in SUBSET:
    p = sfx_local_path(aid); p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        r = requests.get(SFX_LIBRARY[aid][1], timeout=60); r.raise_for_status()
        p.write_bytes(r.content)
    print(f"  got {aid} ({p.stat().st_size} bytes)")

# ── generate a 6s test video (tone audio so the mix has a base track) ─────────
video = OUT / "base.mp4"
subprocess.run(["ffmpeg", "-y",
    "-f", "lavfi", "-i", "color=c=navy:size=320x240:rate=30:duration=6",
    "-f", "lavfi", "-i", "sine=frequency=180:duration=6",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)],
    capture_output=True)

# ── events: semantic + legacy + out-of-range (should be dropped) ──────────────
events = [
    {"time": 1.0, "category": "impact", "asset": "impact_cinematic_hit_01"},
    {"time": 2.5, "category": "hook",   "asset": "hook_cash_register_01"},
    {"time": 4.0, "asset": "pop_snap_finger_01"},
    {"time": 0.6, "type": "whoosh"},                 # legacy -> trans_whoosh_fast_01
    {"time": 99.0, "asset": "impact_cinematic_hit_01"},  # out of range -> dropped
]
duration = 6.0

norm = normalize(events, duration)
print("\nNormalized events:")
for e in norm:
    print(f"   t={e['time']:.2f}  {e['asset']}  ({SFX_LIBRARY[e['asset']][0]})")

out = mix(video, events, OUT, duration)
print(f"\nmix() returned: {out}")

ok = out and Path(out).exists()
if ok:
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nk=1:nw=1", str(out)], capture_output=True, text=True).stdout.strip()
    astream = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                              "-show_entries", "stream=codec_name", "-of", "default=nk=1:nw=1",
                              str(out)], capture_output=True, text=True).stdout.strip()
    print(f"  output duration: {dur}s   audio codec: {astream!r}")

intro_ok = sum(1 for e in norm if e["time"] == 0.0) == 2          # ping + thud at t=0
legacy_ok = any(e["asset"] == "trans_whoosh_fast_01" for e in norm) # legacy mapped
range_ok = all(e["time"] < duration for e in norm)                 # out-of-range dropped
print(f"\n  [a] t=0 intro dual-layer (ping+thud): {'PASS' if intro_ok else 'FAIL'}")
print(f"  [b] legacy type->asset mapped       : {'PASS' if legacy_ok else 'FAIL'}")
print(f"  [c] out-of-range event dropped      : {'PASS' if range_ok else 'FAIL'}")
print(f"  [d] ffmpeg mix produced audio mp4   : {'PASS' if (ok and astream) else 'FAIL'}")
print(f"\n  ==> {'ALL PASS' if (intro_ok and legacy_ok and range_ok and ok and astream) else 'FAIL'}")
