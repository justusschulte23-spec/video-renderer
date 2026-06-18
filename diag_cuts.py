"""
Validate the image-cutaway compositing filtergraph (same construction as render()).
Dummy inputs: red broll(top), blue facecam, white caption bar @y=700, 2 full-frame
cuts (yellow @1.5-3.5s, orange @4.0-5.5s). Verifies via pixel probes:
  - during a cut: full-frame image shows AND caption bar is ON TOP
  - outside cuts: no image (facecam visible), captions still there
"""
import subprocess
from pathlib import Path

W, H, FPS, FACECAM_H, DIVIDER_Y, PROGRESS_Y = 1080, 1920, 30, 1188, 622, 1916
DUR = 6.0
IMAGE_CUT_FADE = 0.5
D = Path("C:/tmp_diag/cuts"); D.mkdir(parents=True, exist_ok=True)


def sh(args):
    r = subprocess.run(args, capture_output=True, text=True)
    return r


# ── dummy inputs ──────────────────────────────────────────────────────────────
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=red:s={W}x{DIVIDER_Y}:r={FPS}:d={DUR}",
    "-an", str(D/"broll.mp4")])
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=green:s={W}x110","-frames:v","1", str(D/"div.png")])
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=blue:s={W}x{FACECAM_H}:r={FPS}:d={DUR}",
    "-f","lavfi","-i",f"sine=frequency=200:d={DUR}",
    "-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac","-shortest", str(D/"face.mp4")])
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=white:s={W}x120",
    "-f","lavfi","-i",f"color=c=black@0.0:s={W}x{H}",
    "-filter_complex","[1][0]overlay=0:700,format=rgba","-frames:v","1", str(D/"cap.png")])
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=white:s={W}x4","-frames:v","1", str(D/"prog.png")])
for nm in ("scan.png","hud.png"):
    sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=black@0.0:s={W}x{H},format=rgba","-frames:v","1", str(D/nm)])
sh(["ffmpeg","-y","-f","lavfi","-i","color=c=yellow:s=576x1024","-frames:v","1", str(D/"cut_0.jpg")])
sh(["ffmpeg","-y","-f","lavfi","-i","color=c=orange:s=576x1024","-frames:v","1", str(D/"cut_1.jpg")])

image_cuts = [
    {"start": 1.5, "end": 3.5, "path": D/"cut_0.jpg"},
    {"start": 4.0, "end": 5.5, "path": D/"cut_1.jpg"},
]

# ── EXACT filter construction copied from render() ────────────────────────────
fadeout_start = max(0.0, DUR - 1.0)
parts = [
    f"[0:v]trim=duration={DUR:.3f},setpts=PTS-STARTPTS,setsar=1[broll];",
    "[1:v]setsar=1[div];",
    # plain scale for the test dummy (production uses zoompan on real facecam)
    f"[2:v]scale={W}:{FACECAM_H},setsar=1[face];",
    "[broll][div][face]vstack=inputs=3[stacked];",
    "[stacked][5:v]overlay=x=0:y=0[with_scan];",
]
IMG_BASE = 7
extra_inputs = []
prev = "with_scan"
for i, cut in enumerate(image_cuts):
    extra_inputs += ["-loop","1","-framerate",str(FPS),"-t",f"{DUR:.3f}","-i",str(cut["path"])]
    fout = max(cut["end"] - IMAGE_CUT_FADE, cut["start"])
    parts.append(
        f"[{IMG_BASE+i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},setsar=1,format=rgba,"
        f"fade=t=in:st={cut['start']:.3f}:d={IMAGE_CUT_FADE}:alpha=1,"
        f"fade=t=out:st={fout:.3f}:d={IMAGE_CUT_FADE}:alpha=1[cut{i}];"
    )
    parts.append(f"[{prev}][cut{i}]overlay=x=0:y=0:enable='between(t,{cut['start']:.3f},{cut['end']:.3f})'[ic{i}];")
    prev = f"ic{i}"
parts.append(f"[{prev}][3:v]overlay=x=0:y={DIVIDER_Y}[with_cap];")
parts.append("[with_cap][6:v]overlay=x=0:y=0[with_hud];")
parts.append(f"[with_hud][4:v]overlay=x=0:y={PROGRESS_Y}[with_prog];")
parts.append(f"[with_prog]fade=t=out:st={fadeout_start:.3f}:d=1[final]")
filt = "".join(parts)

out = D/"out.mp4"
cmd = ["ffmpeg","-y",
    "-i",str(D/"broll.mp4"),"-i",str(D/"div.png"),"-i",str(D/"face.mp4"),
    "-framerate",str(FPS),"-i",str(D/"cap.png"),
    "-framerate",str(FPS),"-i",str(D/"prog.png"),
    "-i",str(D/"scan.png"),"-i",str(D/"hud.png"),
    *extra_inputs,
    "-filter_complex",filt,"-map","[final]","-map","2:a",
    "-c:v","libx264","-crf","18","-preset","ultrafast","-c:a","aac","-t",str(DUR),
    "-pix_fmt","yuv420p",str(out)]
r = sh(cmd)
print("ffmpeg returncode:", r.returncode)
if r.returncode != 0:
    print(r.stderr[-1500:]); raise SystemExit("FILTERGRAPH FAILED")


def px(t, x, y):
    r = subprocess.run(["ffmpeg","-ss",str(t),"-i",str(out),"-frames:v","1",
        "-vf",f"crop=4:4:{x}:{y},scale=1:1","-f","rawvideo","-pix_fmt","rgb24","-"],
        capture_output=True)
    b = r.stdout[:3]
    return tuple(b) if len(b) == 3 else (None, None, None)


# probes
mid_during = px(2.4, 540, 1300)   # center-lower, during cut0 -> yellow image
cap_during = px(2.4, 540, 760)    # caption band, during cut0 -> WHITE (caption on top)
mid_before = px(0.6, 540, 1300)   # before cuts -> blue facecam
mid_cut1   = px(4.7, 540, 1300)   # during cut1 -> orange

def is_yellow(p): return p[0] > 150 and p[1] > 150 and p[2] < 120
def is_white(p):  return p[0] > 200 and p[1] > 200 and p[2] > 200
def is_blue(p):   return p[2] > 120 and p[0] < 120 and p[1] < 120
def is_orange(p): return p[0] > 180 and 60 < p[1] < 200 and p[2] < 120

print(f"  during cut0 center (want yellow): {mid_during}  -> {is_yellow(mid_during)}")
print(f"  during cut0 caption (want white): {cap_during}  -> {is_white(cap_during)}")
print(f"  before cuts center (want blue) : {mid_before}  -> {is_blue(mid_before)}")
print(f"  during cut1 center (want orange): {mid_cut1}  -> {is_orange(mid_cut1)}")

a = is_yellow(mid_during)
b = is_white(cap_during)
c = is_blue(mid_before)
d = is_orange(mid_cut1)
print(f"\n  [a] full-frame image during cut : {'PASS' if a else 'FAIL'}")
print(f"  [b] captions ON TOP of image    : {'PASS' if b else 'FAIL'}")
print(f"  [c] no image outside windows    : {'PASS' if c else 'FAIL'}")
print(f"  [d] second cut shows (orange)   : {'PASS' if d else 'FAIL'}")
print(f"\n  ==> {'ALL PASS' if (a and b and c and d) else 'FAIL'}")
