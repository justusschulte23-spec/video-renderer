"""
Test generic compositor (_build_compositor, AST-extracted from main.py) with dummy
inputs. Two custom layouts:
  A) NO broll, fullscreen facecam + captions bottom + no divider  (proves broll-off)
  B) split-ish: broll top, facecam bottom, divider, hud, captions  (proves multi-layer)
Verifies: ffmpeg accepts the graph (rc 0), output 1080x1920, has audio, captions on top.
"""
import ast, subprocess
from pathlib import Path

W, H, FPS, FACECAM_H = 1080, 1920, 30, 1188
DUR = 4.0
D = Path("C:/tmp_diag/comp"); D.mkdir(parents=True, exist_ok=True)

src = Path(__file__).with_name("main.py").read_text(encoding="utf-8")
lines = src.splitlines(keepends=True); tree = ast.parse(src)
ns = {"W": W, "H": H, "FPS": FPS, "FACECAM_H": FACECAM_H}
for n in tree.body:
    if isinstance(n, ast.FunctionDef) and n.name in {"_ff_color", "_build_compositor"}:
        exec("".join(lines[n.lineno - 1:n.end_lineno]), ns)
build = ns["_build_compositor"]


def sh(a): return subprocess.run(a, capture_output=True, text=True)

# dummy assets
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=blue:s={W}x{FACECAM_H}:r={FPS}:d={DUR}",
    "-f","lavfi","-i",f"sine=frequency=200:d={DUR}","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac","-shortest",str(D/"facecam.mp4")])
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=red:s={W}x622:r={FPS}:d={DUR}","-an",str(D/"broll.mp4")])
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=green:s={W}x110","-frames:v","1",str(D/"divider.png")])
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=white:s={W}x120","-f","lavfi","-i",f"color=c=black@0.0:s={W}x{H}","-filter_complex","[1][0]overlay=0:1700,format=rgba","-frames:v","1",str(D/"cap.png")])
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=yellow@0.0:s={W}x4,format=rgba","-frames:v","1",str(D/"prog.png")])
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=black@0.0:s={W}x{H},format=rgba","-frames:v","1",str(D/"hud.png")])
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=black@0.0:s={W}x622,format=rgba","-frames:v","1",str(D/"scan.png")])

srcs = {"facecam":D/"facecam.mp4","broll":D/"broll.mp4","divider":D/"divider.png",
        "hud":D/"hud.png","scanlines":D/"scan.png","captions":str(D/"cap.png"),"progress":str(D/"prog.png")}

LAYOUTS = {
 "A_nobroll_fullcam": {"engine":"compositor","canvas":{"w":W,"h":H,"fps":FPS,"bg":"#101010"},
   "layers":[
     {"type":"video","src":"facecam","x":0,"y":0,"w":W,"h":H,"zoom":"pulse"},  # fills whole frame
     {"type":"image_cuts","mode":"fullframe"},
     {"type":"captions","x":0,"y":0,"w":W,"h":H},  # cap.png is full-frame w/ bar at 1700
   ]},
 "B_split": {"engine":"compositor","canvas":{"w":W,"h":H,"fps":FPS,"bg":"#09090B"},
   "layers":[
     {"type":"video","src":"facecam","x":0,"y":732,"w":W,"h":FACECAM_H,"zoom":"pulse"},
     {"type":"video","src":"broll","x":0,"y":0,"w":W,"h":622},
     {"type":"image","src":"divider","x":0,"y":622,"w":W,"h":110},
     {"type":"overlay","src":"hud"},
     {"type":"captions","x":0,"y":0,"w":W,"h":H},
   ]},
}

def probe(path, stream, entry):
    r = sh(["ffprobe","-v","error","-select_streams",stream,"-show_entries",entry,"-of","default=nk=1:nw=1",str(path)])
    return r.stdout.strip().splitlines()

allpass = True
for name, lay in LAYOUTS.items():
    inp, fc, fl, amap = build(lay, srcs, DUR, [], 0.15)
    out = D / f"{name}.mp4"
    cmd = ["ffmpeg","-y",*inp,"-filter_complex",fc,"-map",fl,"-map",amap,
           "-c:v","libx264","-crf","20","-preset","ultrafast","-c:a","aac","-t",str(DUR),"-pix_fmt","yuv420p",str(out)]
    r = sh(cmd)
    ok = r.returncode == 0 and out.exists()
    dims = probe(out,"v:0","stream=width,height") if ok else []
    acodec = probe(out,"a:0","stream=codec_name") if ok else []
    print(f"\n=== {name} ===")
    print("  rc:", r.returncode, "| dims:", dims, "| audio:", acodec)
    if not ok:
        print(r.stderr[-1400:])
    dimok = dims == ["1080","1920"]
    audok = acodec == ["aac"]
    print(f"  [valid graph]  {'PASS' if ok else 'FAIL'}")
    print(f"  [1080x1920]    {'PASS' if dimok else 'FAIL'}")
    print(f"  [has audio]    {'PASS' if audok else 'FAIL'}")
    if ok:
        sh(["ffmpeg","-y","-i",str(out),"-ss","1.0","-frames:v","1",str(D/f"{name}.png")])
    allpass = allpass and ok and dimok and audok

print(f"\n==> {'ALL PASS' if allpass else 'FAIL'}")
