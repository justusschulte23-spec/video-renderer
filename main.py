import os
import uuid
import shutil
import logging
import subprocess
import math
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from PIL import Image, ImageDraw, ImageFont
import cloudinary
import cloudinary.uploader
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Cloudinary ────────────────────────────────────────────────────────────────
cloudinary.config(
    cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
    api_key=os.environ["CLOUDINARY_API_KEY"],
    api_secret=os.environ["CLOUDINARY_API_SECRET"],
)

openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# ── Brand colours ─────────────────────────────────────────────────────────────
AMETHYST      = (139, 92, 246)
AMETHYST_DARK = (124, 58, 237)
SILVER        = (192, 192, 192)
BG            = (8, 8, 8)
WHITE         = (255, 255, 255)

# ── Canvas ────────────────────────────────────────────────────────────────────
W, H, FPS   = 1080, 1920, 30
BROLL_H     = 578
DIVIDER_Y   = 578
DIVIDER_H   = 110
FACECAM_Y   = 688    # 578 + 110
FACECAM_H   = 1232   # 1920 - 688
PROGRESS_Y  = 1916
PROGRESS_H  = 4

FONT_DIR = Path("/tmp/fonts")
FONT_BLACK    = FONT_DIR / "Montserrat-Black.ttf"
FONT_SEMIBOLD = FONT_DIR / "Montserrat-SemiBold.ttf"

FONT_URLS = {
    FONT_BLACK:    "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Black.ttf",
    FONT_SEMIBOLD: "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-SemiBold.ttf",
}

# ── Font bootstrap ─────────────────────────────────────────────────────────────
def _bootstrap_fonts():
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    for path, url in FONT_URLS.items():
        if path.exists():
            continue
        log.info("Downloading font %s", path.name)
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Font download failed ({r.status_code}): {url}")
        path.write_bytes(r.content)
        log.info("Font saved: %s", path)

_bootstrap_fonts()

# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI()

class RenderRequest(BaseModel):
    facecam:   str
    broll:     str
    broll2:    str | None = None
    broll3:    str | None = None
    hook_text: str

# ── Helpers ───────────────────────────────────────────────────────────────────

def download_file(url: str, dest: Path) -> bool:
    try:
        r = requests.get(url, timeout=60, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
        log.info("Downloaded %s → %s", url, dest)
        return True
    except Exception as e:
        log.error("Download failed %s: %s", url, e)
        return False


def probe_duration(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
    return float(out)


def run(cmd: list[str], label: str = ""):
    log.info("RUN %s: %s", label, " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("STDERR: %s", result.stderr[-2000:])
        raise RuntimeError(f"ffmpeg failed [{label}]: {result.stderr[-500:]}")


def scale_crop(src: Path, dest: Path, tw: int, th: int):
    """Scale-and-center-crop to target w×h, keeping audio."""
    run([
        "ffmpeg", "-y", "-i", str(src),
        "-vf", (
            f"scale={tw}:{th}:force_original_aspect_ratio=increase,"
            f"crop={tw}:{th}"
        ),
        "-c:a", "copy",
        str(dest),
    ], "scale_crop")


def prepare_broll_clip(src: Path, dest: Path, duration: float):
    """Scale-crop to 1080×578, loop if shorter, trim to exact duration."""
    scaled = dest.parent / (dest.stem + "_scaled.mp4")
    run([
        "ffmpeg", "-y", "-i", str(src),
        "-vf", "scale=1080:578:force_original_aspect_ratio=increase,crop=1080:578",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-an",
        str(scaled),
    ], "broll_scale")
    run([
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(scaled),
        "-t", str(duration),
        "-vf", "setpts=PTS-STARTPTS",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-an",
        str(dest),
    ], "broll_loop")
    scaled.unlink(missing_ok=True)


def apply_zoompan(src: Path, dest: Path):
    """Apply zoom-out effect on first 0.5s (15 frames)."""
    run([
        "ffmpeg", "-y", "-i", str(src),
        "-vf", (
            "zoompan=z='if(lte(on,1),1.2,max(1,1.2-on*0.013))':"
            "d=1:s=1080x578,setsar=1"
        ),
        "-an", str(dest),
    ], "zoompan")


def concat_brolls(clips: list[Path], dest: Path, total_dur: float, job_dir: Path):
    """Concatenate broll clips to a single video of exact total_dur."""
    list_file = job_dir / "broll_list.txt"
    with open(list_file, "w") as f:
        for c in clips:
            f.write(f"file '{c}'\n")
    run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-t", str(total_dur),
        "-vf", "setsar=1",
        "-an", str(dest),
    ], "concat_broll")


def make_gradient_png(path: Path, w: int, h: int,
                      left: tuple, right: tuple, alpha: int = 255):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for x in range(w):
        t = x / (w - 1)
        r = int(left[0] + t * (right[0] - left[0]))
        g = int(left[1] + t * (right[1] - left[1]))
        b = int(left[2] + t * (right[2] - left[2]))
        draw.line([(x, 0), (x, h - 1)], fill=(r, g, b, alpha))
    img.save(str(path))


def transcribe_audio(video_path: Path) -> list[dict]:
    """Returns list of {word, start, end} or [] on failure."""
    try:
        log.info("Extracting audio for Whisper …")
        audio_path = video_path.parent / "audio.mp3"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path),
            "-vn", "-ar", "16000", "-ac", "1", "-b:a", "64k",
            str(audio_path),
        ], check=True, capture_output=True)

        log.info("Calling Whisper API …")
        with open(audio_path, "rb") as af:
            resp = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=af,
                response_format="verbose_json",
                timestamp_granularities=["word"],
            )
        words = []
        for w in (resp.words or []):
            words.append({"word": w.word.strip(), "start": w.start, "end": w.end})
        log.info("Whisper returned %d words", len(words))
        return words
    except Exception as e:
        log.error("Whisper transcription failed: %s", e)
        return []


def _draw_caption_frame(img: Image.Image, word: str, scale: float = 1.0):
    """Draw one caption word onto a 1080×110 RGBA image with 3-layer soft blend."""
    draw = ImageDraw.Draw(img)
    font_size = int(86 * scale)
    font = ImageFont.truetype(str(FONT_BLACK), font_size)

    bbox = draw.textbbox((0, 0), word, font=font, stroke_width=0)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (1080 - tw) // 2 - bbox[0]
    y = (DIVIDER_H - th) // 2 - bbox[1]

    # Layer 1: outer soft halo — wide, semi-transparent deep purple
    draw.text((x, y), word, font=font,
              fill=(0, 0, 0, 0),
              stroke_fill=(109, 40, 217, 100),
              stroke_width=18)
    # Layer 2: mid glow — tighter, more opaque amethyst
    draw.text((x, y), word, font=font,
              fill=(0, 0, 0, 0),
              stroke_fill=(139, 92, 246, 160),
              stroke_width=10)
    # Layer 3: sharp inner stroke + pure white fill
    draw.text((x, y), word, font=font,
              fill=(255, 255, 255, 255),
              stroke_fill=(124, 58, 237, 230),
              stroke_width=5)


def build_caption_frames(words: list[dict], total_frames: int,
                         cap_dir: Path, gradient_base: Path):
    """Pre-render caption PNG sequence to cap_dir."""
    cap_dir.mkdir(parents=True, exist_ok=True)
    log.info("Rendering %d caption frames …", total_frames)

    # build per-frame word index
    word_at_frame = {}
    for i, w in enumerate(words):
        start_f = int(w["start"] * FPS)
        end_f   = int(w["end"]   * FPS)
        for f in range(start_f, min(end_f + 1, total_frames)):
            word_at_frame[f] = i

    # find transition frames (first frame of each new word)
    transition_frames: set[int] = set()
    prev = -1
    for f in range(total_frames):
        cur = word_at_frame.get(f, -1)
        if cur != prev and cur != -1:
            transition_frames.update([f, f + 1, f + 2])
            prev = cur

    base_img = Image.open(str(gradient_base)).convert("RGBA")

    for frame_n in range(total_frames):
        img = base_img.copy()
        wi = word_at_frame.get(frame_n, -1)
        if wi >= 0:
            word = words[wi]["word"]
            if frame_n in transition_frames:
                pulse_map = {0: 1.0, 1: 1.04, 2: 1.0}
                offset = frame_n - min(f for f in transition_frames
                                       if f >= frame_n - 2 and
                                       word_at_frame.get(f - 2, -1) != wi
                                       and word_at_frame.get(f, -1) == wi)
                scale = pulse_map.get(offset % 3, 1.0)
            else:
                scale = 1.0
            _draw_caption_frame(img, word, scale)

        out = cap_dir / f"frame_{frame_n:06d}.png"
        img.save(str(out))

    log.info("Caption frames done")


def build_progress_frames(total_frames: int, prog_dir: Path):
    """Pre-render progress bar PNG sequence."""
    prog_dir.mkdir(parents=True, exist_ok=True)
    log.info("Rendering %d progress frames …", total_frames)
    for frame_n in range(total_frames):
        img = Image.new("RGBA", (W, PROGRESS_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        fw = int((frame_n / max(total_frames - 1, 1)) * W)
        if fw > 0:
            for x in range(fw):
                t = x / (W - 1)
                r = int(AMETHYST[0] + t * (SILVER[0] - AMETHYST[0]))
                g = int(AMETHYST[1] + t * (SILVER[1] - AMETHYST[1]))
                b = int(AMETHYST[2] + t * (SILVER[2] - AMETHYST[2]))
                draw.line([(x, 0), (x, PROGRESS_H - 1)], fill=(r, g, b, 255))
        out = prog_dir / f"frame_{frame_n:06d}.png"
        img.save(str(out))
    log.info("Progress frames done")


def build_watermark_png(path: Path):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONT_SEMIBOLD), 22)
    text = "@justus.automates"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x = 1048 - tw
    y = 670
    y = FACECAM_Y + 2
    draw.text((x, y), text, font=font, fill=(232, 232, 232, 77))  # 30% opacity
    img.save(str(path))


def upload_cloudinary(path: Path, public_id: str) -> str:
    log.info("Uploading to Cloudinary …")
    result = cloudinary.uploader.upload(
        str(path),
        resource_type="video",
        folder="renders",
        public_id=public_id,
        overwrite=True,
    )
    return result["secure_url"]


# ── Main render route ─────────────────────────────────────────────────────────

@app.post("/render")
async def render(req: RenderRequest):
    job_id  = str(uuid.uuid4())
    job_dir = Path(f"/tmp/render_{job_id}")
    job_dir.mkdir(parents=True, exist_ok=True)
    log.info("=== JOB %s START ===", job_id)

    try:
        # ── 1. Download inputs ───────────────────────────────────────────────
        facecam_raw = job_dir / "facecam_raw.mp4"
        broll1_raw  = job_dir / "broll1_raw.mp4"

        if not download_file(req.facecam, facecam_raw):
            raise HTTPException(500, "facecam download failed")
        if not download_file(req.broll, broll1_raw):
            raise HTTPException(500, "broll download failed")

        broll2_raw = job_dir / "broll2_raw.mp4"
        broll3_raw = job_dir / "broll3_raw.mp4"

        b2_ok = bool(req.broll2) and download_file(req.broll2, broll2_raw)
        b3_ok = bool(req.broll3) and download_file(req.broll3, broll3_raw)

        if not b2_ok:
            log.warning("broll2 unavailable — using broll1")
            broll2_raw = broll1_raw
        if not b3_ok:
            log.warning("broll3 unavailable — using broll1")
            broll3_raw = broll1_raw

        # ── 2. Probe facecam duration ────────────────────────────────────────
        duration     = probe_duration(facecam_raw)
        total_frames = int(duration * FPS)
        slot_dur     = duration / 3
        log.info("Facecam duration=%.3fs  frames=%d  slot=%.3fs",
                 duration, total_frames, slot_dur)

        # ── 3. Prepare brolls (scale, crop, loop to fill slot) ───────────────
        b1_trimmed = job_dir / "b1_trimmed.mp4"
        b2_trimmed = job_dir / "b2_trimmed.mp4"
        b3_trimmed = job_dir / "b3_trimmed.mp4"

        prepare_broll_clip(broll1_raw, b1_trimmed, slot_dur)
        prepare_broll_clip(broll2_raw, b2_trimmed, slot_dur)
        prepare_broll_clip(broll3_raw, b3_trimmed, slot_dur)

        # ── 4. Zoom on broll1 ────────────────────────────────────────────────
        b1_zoom = job_dir / "b1_zoom.mp4"
        apply_zoompan(b1_trimmed, b1_zoom)

        # ── 5. Concat brolls ─────────────────────────────────────────────────
        broll_concat = job_dir / "broll_concat.mp4"
        concat_brolls([b1_zoom, b2_trimmed, b3_trimmed],
                      broll_concat, duration, job_dir)

        # ── 6. Scale/crop facecam ────────────────────────────────────────────
        facecam_scaled = job_dir / "facecam_scaled.mp4"
        scale_crop(facecam_raw, facecam_scaled, W, FACECAM_H)

        # ── 7. Divider gradient PNG ──────────────────────────────────────────
        divider_png = job_dir / "divider.png"
        make_gradient_png(divider_png, W, DIVIDER_H, AMETHYST, SILVER)

        # ── 8. Whisper transcription ──────────────────────────────────────────
        words = transcribe_audio(facecam_raw)

        # ── 9. Caption frames ─────────────────────────────────────────────────
        cap_dir = job_dir / "captions"
        build_caption_frames(words, total_frames, cap_dir, divider_png)

        # ── 10. Progress frames ───────────────────────────────────────────────
        prog_dir = job_dir / "progress"
        build_progress_frames(total_frames, prog_dir)

        # ── 11. Watermark PNG ─────────────────────────────────────────────────
        watermark_png = job_dir / "watermark.png"
        build_watermark_png(watermark_png)

        # ── 12. Final ffmpeg compose ──────────────────────────────────────────
        output_mp4 = job_dir / "output.mp4"

        cap_pattern  = str(cap_dir  / "frame_%06d.png")
        prog_pattern = str(prog_dir / "frame_%06d.png")

        fadeout_start = max(0.0, duration - 1.0)
        filter_complex = (
            # inputs: [0]=broll_concat [1]=divider [2]=facecam_scaled
            #         [3]=caption seq  [4]=progress seq  [5]=watermark
            "[0:v]setsar=1[broll];"
            "[1:v]setsar=1[div];"
            "[2:v]setsar=1[face];"
            "[broll][div][face]vstack=inputs=3[stacked];"
            f"[stacked][3:v]overlay=x=0:y={DIVIDER_Y}[with_cap];"
            "[with_cap][5:v]overlay=x=0:y=0[with_wm];"
            f"[with_wm][4:v]overlay=x=0:y={PROGRESS_Y}[with_prog];"
            f"[with_prog]fade=t=out:st={fadeout_start:.3f}:d=1[final]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", str(broll_concat),
            "-i", str(divider_png),
            "-i", str(facecam_scaled),
            "-framerate", str(FPS),
            "-i", cap_pattern,
            "-framerate", str(FPS),
            "-i", prog_pattern,
            "-i", str(watermark_png),
            "-filter_complex", filter_complex,
            "-map", "[final]",
            "-map", "2:a",
            "-c:v", "libx264", "-crf", "18", "-preset", "slow",
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            str(output_mp4),
        ]
        run(cmd, "final_compose")
        log.info("Output: %s (%.1f MB)",
                 output_mp4, output_mp4.stat().st_size / 1e6)

        # ── 13. Upload ────────────────────────────────────────────────────────
        url = upload_cloudinary(output_mp4, job_id)
        log.info("=== JOB %s DONE → %s ===", job_id, url)
        return {"url": url}

    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
        log.info("Cleaned up %s", job_dir)


@app.get("/health")
def health():
    return {"status": "ok"}
