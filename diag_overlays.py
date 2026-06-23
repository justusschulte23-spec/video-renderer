"""Validate the new overlay chain (image cut + video cut + logo) filtergraph.
Mirrors the exact filter strings from _render_impl fixed graph. Dummy inputs.
Checks: ffmpeg rc 0, 1080x1920, frames show right overlay in each window."""
import subprocess
from pathlib import Path

W, H, FPS, BROLL_H, FACECAM_H, DIVIDER_Y, PROGRESS_Y = 1080, 1920, 30, 622, 1188, 622, 1916
DUR = 12.0
cut_fade = 0.15
D = Path("C:/tmp_diag/ov"); D.mkdir(parents=True, exist_ok=True)
def sh(a): return subprocess.run(a, capture_output=True, text=True)

# dummy base inputs
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=red:s={W}x{BROLL_H}:r={FPS}:d={DUR}","-an",str(D/"broll.mp4")])
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=green:s={W}x110","-frames:v","1",str(D/"div.png")])
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=blue:s={W}x{FACECAM_H}:r={FPS}:d={DUR}",
    "-f","lavfi","-i",f"sine=frequency=200:d={DUR}","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac","-shortest",str(D/"face.mp4")])
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=white:s={W}x120","-f","lavfi","-i",f"color=c=black@0.0:s={W}x{H}","-filter_complex","[1][0]overlay=0:700,format=rgba","-frames:v","1",str(D/"cap.png")])
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=black@0.0:s={W}x4,format=rgba","-frames:v","1",str(D/"prog.png")])
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=black@0.0:s={W}x{H},format=rgba","-frames:v","1",str(D/"scan.png")])
sh(["ffmpeg","-y","-f","lavfi","-i",f"color=c=black@0.0:s={W}x{H},format=rgba","-frames:v","1",str(D/"hud.png")])
# overlays: image=yellow still, video=cyan moving testsrc clip, logo=magenta small
sh(["ffmpeg","-y","-f","lavfi","-i","color=c=yellow:s=576x1024","-frames:v","1",str(D/"img.jpg")])
sh(["ffmpeg","-y","-f","lavfi","-i",f"testsrc=s=576x1024:r={FPS}:d=3","-pix_fmt","yuv420p",str(D/"vid.mp4")])  # 3s clip
sh(["ffmpeg","-y","-f","lavfi","-i","color=c=magenta:s=512x512","-frames:v","1",str(D/"logo.png")])

image_cuts=[{"start":1.5,"end":3.5,"path":D/"img.jpg"}]
video_cuts=[{"start":5.0,"end":8.0,"path":D/"vid.mp4"}]
logos=[{"start":9.5,"end":11.0,"path":D/"logo.png"}]
fadeout_start=max(0.0,DUR-1.0)

parts=[
 f"[0:v]trim=duration={DUR:.3f},setpts=PTS-STARTPTS,setsar=1[broll];",
 "[1:v]setsar=1[div];",
 f"[2:v]scale={W}:{FACECAM_H},setsar=1[face];",
 "[broll][div][face]vstack=inputs=3[stacked];",
 "[stacked][5:v]overlay=x=0:y=0[with_scan];",
 "[with_scan][6:v]overlay=x=0:y=0[with_hud];",
 f"[with_hud][4:v]overlay=x=0:y={PROGRESS_Y}[with_ui];",
]
extra=[]; idx=7; ov=0; prev="with_ui"
for cut in image_cuts:
    extra+=["-loop","1","-framerate",str(FPS),"-t",f"{DUR:.3f}","-i",str(cut["path"])]
    fout=max(cut["end"]-cut_fade,cut["start"])
    parts.append(f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,format=rgba,fade=t=in:st={cut['start']:.3f}:d={cut_fade}:alpha=1,fade=t=out:st={fout:.3f}:d={cut_fade}:alpha=1[o{ov}];")
    parts.append(f"[{prev}][o{ov}]overlay=x=0:y=0:enable='between(t,{cut['start']:.3f},{cut['end']:.3f})'[s{ov}];")
    prev=f"s{ov}";idx+=1;ov+=1
for v in video_cuts:
    extra+=["-i",str(v["path"])]
    fout=max(v["end"]-cut_fade,v["start"])
    parts.append(f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,format=rgba,setpts=PTS-STARTPTS+{v['start']:.3f}/TB,fade=t=in:st={v['start']:.3f}:d={cut_fade}:alpha=1,fade=t=out:st={fout:.3f}:d={cut_fade}:alpha=1[o{ov}];")
    parts.append(f"[{prev}][o{ov}]overlay=x=0:y=0:enable='between(t,{v['start']:.3f},{v['end']:.3f})'[s{ov}];")
    prev=f"s{ov}";idx+=1;ov+=1
for lg in logos:
    extra+=["-loop","1","-framerate",str(FPS),"-t",f"{DUR:.3f}","-i",str(lg["path"])]
    fout=max(lg["end"]-cut_fade,lg["start"])
    parts.append(f"[{idx}:v]scale=360:-1,setsar=1,format=rgba,fade=t=in:st={lg['start']:.3f}:d={cut_fade}:alpha=1,fade=t=out:st={fout:.3f}:d={cut_fade}:alpha=1[o{ov}];")
    parts.append(f"[{prev}][o{ov}]overlay=x=(W-w)/2:y=360:enable='between(t,{lg['start']:.3f},{lg['end']:.3f})'[s{ov}];")
    prev=f"s{ov}";idx+=1;ov+=1
parts.append(f"[{prev}][3:v]overlay=x=0:y={DIVIDER_Y}[with_cap];")
parts.append(f"[with_cap]fade=t=out:st={fadeout_start:.3f}:d=1[final]")
filt="".join(parts)

out=D/"out.mp4"
cmd=["ffmpeg","-y","-i",str(D/"broll.mp4"),"-i",str(D/"div.png"),"-i",str(D/"face.mp4"),
 "-framerate",str(FPS),"-i",str(D/"cap.png"),"-framerate",str(FPS),"-i",str(D/"prog.png"),
 "-i",str(D/"scan.png"),"-i",str(D/"hud.png"),*extra,
 "-filter_complex",filt,"-map","[final]","-map","2:a","-c:v","libx264","-crf","20","-preset","ultrafast",
 "-c:a","aac","-t",str(DUR),"-pix_fmt","yuv420p",str(out)]
r=sh(cmd)
print("rc:",r.returncode)
if r.returncode!=0:
    print(r.stderr[-1800:]); raise SystemExit("FAIL")
dims=sh(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=width,height","-of","csv=p=0",str(out)]).stdout.strip()
print("dims:",dims)
for t,name in [(0.6,"before"),(2.4,"image"),(6.5,"video"),(10.2,"logo"),(11.6,"after")]:
    sh(["ffmpeg","-y","-ss",str(t),"-i",str(out),"-frames:v","1",str(D/f"f_{name}.png")])
print("frames extracted: f_before/image/video/logo/after .png in",D)
print("==> PASS (graph valid)" if dims=="1080,1920" else "==> DIM FAIL")
