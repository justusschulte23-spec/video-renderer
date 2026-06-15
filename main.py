import os
import uuid
import shutil
import logging
import subprocess
import math
import asyncio
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import httpx
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

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL     = "https://openrouter.ai/api/v1/chat/completions"

FAL_API_KEY           = os.environ.get("FAL_API_KEY", "")
FAL_THUMBNAIL_ENDPOINT = "https://fal.run/fal-ai/nano-banana-pro"

# ── Brand colours ─────────────────────────────────────────────────────────────
AMETHYST      = (139, 92, 246)
AMETHYST_DARK = (124, 58, 237)
SILVER        = (192, 192, 192)
BG            = (8, 8, 8)
WHITE         = (255, 255, 255)

# ── Canvas ────────────────────────────────────────────────────────────────────
W, H, FPS         = 1080, 1920, 30
BROLL_H           = 622
DIVIDER_Y         = 622
DIVIDER_H         = 110
FACECAM_Y         = 732
FACECAM_H         = 1188
PROGRESS_Y        = 1916
PROGRESS_H        = 4
CAPTION_FONT_SIZE = 95

FONT_DIR       = Path("/tmp/fonts")
FONT_BLACK     = FONT_DIR / "Montserrat-Black.ttf"
FONT_SEMIBOLD  = FONT_DIR / "Montserrat-SemiBold.ttf"
SCANLINES_PATH = FONT_DIR / "scanlines.png"
HUD_PATH       = FONT_DIR / "hud.png"
GSAP_LOCAL     = FONT_DIR / "gsap.min.js"
GSAP_CDN       = "https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.2/gsap.min.js"

FONT_URLS = {
    FONT_BLACK:    "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Black.ttf",
    FONT_SEMIBOLD: "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-SemiBold.ttf",
}

# ── SFX ───────────────────────────────────────────────────────────────────────
SFX_DIR  = Path("/tmp/sfx")
SFX_URLS = {
    "whoosh":  "https://res.cloudinary.com/poweroflillith/raw/upload/audio/sfx/whoosh.mp3",
    "impact":  "https://res.cloudinary.com/poweroflillith/raw/upload/audio/sfx/impact.mp3",
    "benefit": "https://res.cloudinary.com/poweroflillith/raw/upload/audio/sfx/benefit.mp3",
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


def _bootstrap_gsap():
    if GSAP_LOCAL.exists():
        log.info("GSAP already cached (%d KB)", GSAP_LOCAL.stat().st_size // 1024)
        return
    log.info("Downloading GSAP from CDN …")
    try:
        r = requests.get(GSAP_CDN, timeout=30)
        r.raise_for_status()
        GSAP_LOCAL.write_bytes(r.content)
        log.info("GSAP cached: %s (%d KB)", GSAP_LOCAL, len(r.content) // 1024)
    except Exception as exc:
        log.error("GSAP download FAILED — broll will be grey: %s", exc)

_bootstrap_gsap()


def _inject_gsap_inline(html: str) -> str:
    """Remove all external GSAP script tags and inject inline. Also strips CSP meta tags."""
    if not GSAP_LOCAL.exists():
        return html
    gsap_js = GSAP_LOCAL.read_text(encoding="utf-8")
    inline  = f"<script>{gsap_js}</script>"
    # Strip CSP meta tags that would block inline scripts in file:// context
    patched = re.sub(r'<meta[^>]*Content-Security-Policy[^>]*/?>', '', html, flags=re.IGNORECASE)
    # Remove any <script> tags referencing gsap/greensock (paired and self-closing)
    patched = re.sub(r'<script[^>]*?(gsap|greensock)[^>]*?>.*?</script>',
                     lambda _: "", patched, flags=re.IGNORECASE | re.DOTALL)
    patched = re.sub(r'<script[^>]*?(gsap|greensock)[^>]*?/?>',
                     lambda _: "", patched, flags=re.IGNORECASE)
    # Inject inline before the first remaining <script
    # Use lambda so GSAP's \d \w etc. are not treated as regex backreferences
    if "<script" in patched:
        patched = re.sub(r'<script', lambda m: inline + "\n" + m.group(0), patched, count=1)
    else:
        patched = patched.replace("</body>", inline + "\n</body>")
    return patched


def _bootstrap_sfx():
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in SFX_URLS.items():
        path = SFX_DIR / f"{name}.mp3"
        if path.exists():
            continue
        log.info("Downloading SFX %s", name)
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            path.write_bytes(r.content)
            log.info("SFX saved: %s", path)
        except Exception as exc:
            log.warning("SFX download failed [%s]: %s", name, exc)

_bootstrap_sfx()


def _generate_scanlines():
    img  = Image.new("RGBA", (W, BROLL_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for y in range(0, BROLL_H, 4):
        draw.rectangle([0, y, W, y + 1], fill=(0, 0, 0, int(255 * 0.08)))
    img.save(str(SCANLINES_PATH))
    log.info("Scanlines PNG generated")


def _generate_hud():
    img     = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw    = ImageDraw.Draw(img)
    font_sm = ImageFont.truetype(str(FONT_SEMIBOLD), 22)
    lw, m, arm = 4, 24, 55

    draw.line([m, m, m, m+arm],           fill=(*AMETHYST, 178), width=lw)
    draw.line([m, m, m+arm, m],           fill=(*AMETHYST, 178), width=lw)
    draw.line([W-m, m, W-m, m+arm],       fill=(*SILVER,   128), width=lw)
    draw.line([W-m, m, W-m-arm, m],       fill=(*SILVER,   128), width=lw)
    bl_y = H - m
    draw.line([m, bl_y, m, bl_y-arm],     fill=(*AMETHYST, 102), width=lw)
    draw.line([m, bl_y, m+arm, bl_y],     fill=(*AMETHYST, 102), width=lw)

    dot_x, dot_y = m, m + arm + 28
    draw.ellipse([dot_x-8, dot_y-8, dot_x+8, dot_y+8], fill=(*AMETHYST, 204))
    draw.text((dot_x + 20, dot_y - 11), "LIVE", font=font_sm, fill=(*AMETHYST, 178))
    draw.text((W - m - 5, m + arm + 16), "AI.DEEP",
              font=font_sm, fill=(*SILVER, 128), anchor="rs")

    pill_w, pill_h = 220, 52
    pill_x = W - m - pill_w
    pill_y = H - m - pill_h - 10
    draw.rounded_rectangle(
        [pill_x, pill_y, pill_x+pill_w, pill_y+pill_h],
        radius=14, fill=(*AMETHYST, 38), outline=(*AMETHYST, 128), width=2
    )
    draw.text((pill_x + pill_w//2, pill_y + pill_h//2),
              "AI · DEEPTECH", font=font_sm, fill=(*AMETHYST, 230), anchor="mm")

    wm_y = H - m - pill_h - 60
    draw.text((m + arm + 20, wm_y), "@JUSTUS.AUTOMATES",
              font=font_sm, fill=(*AMETHYST, 140))

    img.save(str(HUD_PATH))
    log.info("HUD PNG generated")


_generate_scanlines()
_generate_hud()

# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI()
_html_executor = ThreadPoolExecutor(max_workers=6)


# ── Pydantic models ───────────────────────────────────────────────────────────
class RenderRequest(BaseModel):
    facecam:          str
    broll_html_url:   Optional[str] = None
    broll_video_url:  Optional[str] = None
    hook_text:        str
    impacts:          Optional[list] = None
    thumbnail_url:    Optional[str] = None


class ThumbnailRequest(BaseModel):
    topic:               str
    thumbnail_concept:   Optional[str] = None
    thumbnail_prompt:    Optional[str] = None  # alias used by N8N workflow
    brand_color_primary: str = "#8B5CF6"


class GenerateBrollRequest(BaseModel):
    topic:                 str
    topic_slug:            str
    brand_color_primary:   str = "#8B5CF6"
    brand_color_secondary: str = "#C0C0C0"
    duration:              float = 70.0


class BrollSyncedRequest(BaseModel):
    facecam:               str
    topic:                 str
    topic_slug:            str
    brand_color_primary:   str = "#8B5CF6"
    brand_color_secondary: str = "#C0C0C0"


class DetectImpactsRequest(BaseModel):
    facecam: str


# ── OpenRouter helper ─────────────────────────────────────────────────────────
def call_openrouter(system_prompt: str, user_message: str,
                    model: str = "anthropic/claude-haiku-4.5",
                    max_tokens: int = 6000) -> str:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://schultensolutions.app.n8n.cloud",
        "X-Title": "Schulten Solutions Video Renderer",
    }
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
    }
    resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=180)
    resp.raise_for_status()
    try:
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        log.error("OpenRouter parse error. status=%d body=%s", resp.status_code, resp.text[:400])
        raise RuntimeError(f"OpenRouter non-JSON response: {resp.text[:200]}") from exc


# ── Playwright: HTML → looped MP4 ─────────────────────────────────────────────
async def render_html_to_video(html_path: Path, output_path: Path, duration: float) -> bool:
    """Record the HTML animation with Playwright, loop to match duration. Returns True on success."""
    record_dir = output_path.parent / "pw_record"
    record_dir.mkdir(parents=True, exist_ok=True)

    try:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            log.error("[RENDER] Playwright not installed — cannot render HTML broll")
            return False

        record_secs   = min(duration + 6.0, 72.0)  # generous — dynamic skip handles grey
        log.info("[RENDER] Recording HTML broll via chromium (%.1fs)", duration)
        webm_path_str = None
        skip_secs     = 2.0  # default fallback; overwritten after page load

        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox",
                          "--disable-dev-shm-usage"],
                )
            except Exception as exc:
                log.error("[RENDER] Chromium launch failed: %s", exc)
                return False

            context = await browser.new_context(
                viewport={"width": 1080, "height": BROLL_H},
                record_video_dir=str(record_dir),
                record_video_size={"width": 1080, "height": BROLL_H},
            )
            # Inject GSAP via init_script — runs before any page script, bypasses CSP
            if GSAP_LOCAL.exists():
                await context.add_init_script(path=str(GSAP_LOCAL))
                log.info("[RENDER] GSAP init_script injected from %s", GSAP_LOCAL)
            else:
                log.error("[RENDER] GSAP_LOCAL missing — broll will be grey!")

            page = await context.new_page()
            _t_load_start = time.time()
            try:
                await page.goto(
                    f"file://{html_path.absolute()}",
                    wait_until="load",
                    timeout=30000,
                )
            except Exception as exc:
                log.warning("[RENDER] Page load warning (continuing): %s", exc)

            # GSAP is guaranteed via init_script — seek to 0 and play
            try:
                await page.evaluate("""() => {
                    if (window.gsap) {
                        gsap.globalTimeline.pause();
                        gsap.globalTimeline.seek(0);
                        gsap.globalTimeline.play();
                    }
                }""")
            except Exception as exc:
                log.warning("[RENDER] GSAP force-start: %s", exc)

            # Measure actual page load time to skip grey startup frames precisely
            skip_secs = round(time.time() - _t_load_start + 0.3, 2)
            log.info("[RENDER] Page load took %.2fs — will skip %.2fs in ffmpeg", skip_secs - 0.3, skip_secs)

            await asyncio.sleep(record_secs)

            video_ref = page.video
            await context.close()

            if video_ref is not None:
                try:
                    webm_path_str = await video_ref.path()
                except Exception as exc:
                    log.warning("[RENDER] Could not get video path: %s", exc)

            await browser.close()

        if not webm_path_str or not Path(webm_path_str).exists():
            log.error("[RENDER] Playwright video file not found: %s", webm_path_str)
            return False

        # Convert WebM to H.264 MP4 — force CFR 30fps, skip grey startup frames
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(skip_secs),
            "-stream_loop", "-1",
            "-i", str(webm_path_str),
            "-t", str(duration),
            "-vf", f"fps=30,scale=1080:{BROLL_H}:flags=lanczos,setsar=1",
            "-an",
            "-c:v", "libx264", "-crf", "16", "-preset", "medium",
            "-pix_fmt", "yuv420p",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log.error("[RENDER] WebM→MP4 conversion failed: %s", result.stderr[-1000:])
            return False

        log.info("[RENDER] HTML broll rendered: %s", output_path)
        return True

    except Exception as exc:
        log.error("[RENDER] HTML broll render error: %s", exc)
        return False
    finally:
        shutil.rmtree(record_dir, ignore_errors=True)


# ── SFX mixer ────────────────────────────────────────────────────────────────
def mix_sfx_into_video(video: Path, impacts: list, job_dir: Path, duration: float):
    """Overlay impact sounds at their timestamps. Returns new Path or None on failure."""
    valid = [
        i for i in impacts
        if i.get("type") in SFX_URLS
        and i.get("time") is not None
        and (SFX_DIR / f"{i['type']}.mp3").exists()
    ]
    if not valid:
        return None

    inputs       = ["-i", str(video)]
    filter_parts = []

    for idx, impact in enumerate(valid):
        delay_ms = int(float(impact["time"]) * 1000)
        sfx_path = SFX_DIR / f"{impact['type']}.mp3"
        inputs  += ["-i", str(sfx_path)]
        filter_parts.append(
            f"[{idx+1}:a]adelay={delay_ms}|{delay_ms},volume=0.8[sfx{idx}]"
        )

    n          = len(valid)
    sfx_labels = "".join(f"[sfx{i}]" for i in range(n))
    filter_parts.append(
        f"[0:a]{sfx_labels}amix=inputs={n+1}:duration=first:normalize=0[aout]"
    )

    out = job_dir / "output_sfx.mp4"
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(duration),
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("[SFX] Mix failed: %s", result.stderr[-1000:])
        return None

    log.info("[SFX] Mixed %d impact sounds into video", n)
    return out


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
    except Exception as exc:
        log.error("Download failed %s: %s", url, exc)
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


def log_duration(path: Path, label: str) -> float:
    try:
        d = probe_duration(path)
        log.info("DURATION [%s]: %.3fs", label, d)
        return d
    except Exception as exc:
        log.error("DURATION probe failed [%s]: %s", label, exc)
        return 0.0


def run(cmd: list, label: str = ""):
    log.info("RUN %s: %s", label, " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("STDERR: %s", result.stderr[-2000:])
        raise RuntimeError(f"ffmpeg failed [{label}]: {result.stderr[-500:]}")


def scale_crop(src: Path, dest: Path, tw: int, th: int):
    run([
        "ffmpeg", "-y", "-i", str(src),
        "-vf", (
            f"scale={tw}:{th}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={tw}:{th},"
            # S-curve: crush blacks, lift mids, keep highlights clean
            # Blue slightly up in shadows → cool/brand-matching darkness
            # Red slightly up in mids → natural warm skin tones
            "curves="
                "r='0/0 0.12/0.08 0.5/0.54 0.88/0.93 1/1':"
                "g='0/0 0.12/0.07 0.5/0.52 0.88/0.92 1/1':"
                "b='0/0 0.12/0.11 0.5/0.52 0.88/0.92 1/1',"
            # Shadow push: cool blues, mid warmth, highlight neutral
            "colorbalance=rs=-0.06:gs=-0.04:bs=0.10:rm=0.04:gm=0.01:bm=-0.06:rh=0.02:gh=0.01:bh=-0.02,"
            "unsharp=5:5:0.7:3:3:0,"
            "eq=contrast=1.04:brightness=0.005:saturation=0.88,"
            "vignette=angle=PI/4"
        ),
        "-c:v", "libx264", "-crf", "16", "-preset", "medium",
        "-c:a", "copy",
        str(dest),
    ], "scale_crop")


def build_broll_track(clips, target_duration, w=W, h=BROLL_H, job_dir=None):
    prepared = []
    total    = 0.0
    for i, clip_path in enumerate(clips):
        if not clip_path or not os.path.exists(str(clip_path)):
            continue
        if total >= target_duration:
            break
        clip_dur  = probe_duration(Path(clip_path))
        remaining = target_duration - total
        out = str(job_dir / f"broll_seg_{i}.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-ss", "60", "-i", str(clip_path),
            "-t", "180",
            "-vf", (
                f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},"
                "colorchannelmixer=rr=1.0:rg=0:rb=0.08:gr=0:gg=0.92:gb=0:br=0.06:bg=0:bb=1.0,"
                "eq=contrast=1.08:brightness=-0.02:saturation=0.9"
            ),
            "-an", "-c:v", "libx264", "-crf", "16", "-preset", "medium", out
        ], check=True)
        actual = probe_duration(Path(out))
        prepared.append(out)
        total += actual
        log.info("[BROLL] Clip %d: %.2fs | total: %.2fs / %.2fs", i+1, actual, total, target_duration)

    if not prepared:
        raise RuntimeError("No broll clips could be prepared")

    if total < target_duration - 0.5:
        gap    = target_duration - total
        looped = str(job_dir / "broll_loop_fill.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", prepared[-1],
            "-t", str(gap), "-vf", "setpts=PTS-STARTPTS",
            "-an", "-c:v", "libx264", "-crf", "16", "-preset", "medium", looped
        ], check=True)
        prepared.append(looped)

    if len(prepared) == 1:
        return prepared[0]

    inputs    = []
    for p in prepared:
        inputs += ["-i", p]
    durations = [probe_duration(Path(p)) for p in prepared]

    if len(prepared) == 2:
        offset     = durations[0] - 0.3
        filter_str = f"[0:v][1:v]xfade=transition=fade:duration=0.3:offset={offset:.3f}[v]"
    else:
        d0, d1     = durations[0], durations[1]
        filter_str = (
            f"[0:v][1:v]xfade=transition=fade:duration=0.3:offset={d0-0.3:.3f}[v01];"
            f"[v01][2:v]xfade=transition=fade:duration=0.3:offset={d0+d1-0.6:.3f}[v]"
        )

    concat_out = str(job_dir / "broll_final.mp4")
    subprocess.run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_str,
        "-map", "[v]",
        "-t", str(target_duration),
        "-c:v", "libx264", "-crf", "16", "-preset", "medium", concat_out
    ], check=True)
    return concat_out


def make_gradient_png(path: Path, w: int, h: int,
                      left: tuple, right: tuple, alpha: int = 255):
    img  = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for x in range(w):
        t = x / (w - 1)
        r = int(left[0] + t * (right[0] - left[0]))
        g = int(left[1] + t * (right[1] - left[1]))
        b = int(left[2] + t * (right[2] - left[2]))
        draw.line([(x, 0), (x, h - 1)], fill=(r, g, b, alpha))
    img.save(str(path))


def transcribe_audio(video_path: Path) -> list:
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
    except Exception as exc:
        log.error("Whisper transcription failed: %s", exc)
        return []


def get_adaptive_font(word: str, max_width: int = W,
                      max_size: int = CAPTION_FONT_SIZE, min_size: int = 48):
    size = max_size
    while size >= min_size:
        font = ImageFont.truetype(str(FONT_BLACK), size)
        bbox = font.getbbox(word)
        if (bbox[2] - bbox[0]) <= max_width - 60:
            return font, size
        size -= 4
    return ImageFont.truetype(str(FONT_BLACK), min_size), min_size


def _draw_caption_frame(img: Image.Image, word: str, scale: float = 1.0):
    draw            = ImageDraw.Draw(img)
    font, actual_size = get_adaptive_font(word, max_size=int(CAPTION_FONT_SIZE * scale))

    bbox = draw.textbbox((0, 0), word, font=font, stroke_width=0)
    tw   = bbox[2] - bbox[0]
    th   = bbox[3] - bbox[1]
    x    = (W - tw) // 2 - bbox[0]
    y    = (DIVIDER_H - th) // 2 - bbox[1]

    outer_stroke = max(8, int(actual_size * 0.17))
    mid_stroke   = max(5, int(actual_size * 0.09))
    inner_stroke = max(3, int(actual_size * 0.04))

    draw.text((x, y), word, font=font,
              fill=(0, 0, 0, 0),
              stroke_fill=(109, 40, 217, 100),
              stroke_width=outer_stroke)
    draw.text((x, y), word, font=font,
              fill=(0, 0, 0, 0),
              stroke_fill=(139, 92, 246, 160),
              stroke_width=mid_stroke)
    draw.text((x, y), word, font=font,
              fill=(255, 255, 255, 255),
              stroke_fill=(124, 58, 237, 230),
              stroke_width=inner_stroke)


def build_caption_frames(words: list, total_frames: int,
                         cap_dir: Path, gradient_base: Path):
    cap_dir.mkdir(parents=True, exist_ok=True)
    log.info("Rendering %d caption frames …", total_frames)

    word_at_frame: dict = {}
    for i, w in enumerate(words):
        start_f = int(w["start"] * FPS)
        end_f   = int(w["end"]   * FPS)
        for f in range(start_f, min(end_f + 1, total_frames)):
            word_at_frame[f] = i

    transition_frames: set = set()
    prev = -1
    for f in range(total_frames):
        cur = word_at_frame.get(f, -1)
        if cur != prev and cur != -1:
            transition_frames.update([f, f + 1, f + 2])
            prev = cur

    base_img = Image.open(str(gradient_base)).convert("RGBA")

    for frame_n in range(total_frames):
        img = base_img.copy()
        wi  = word_at_frame.get(frame_n, -1)
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
    prog_dir.mkdir(parents=True, exist_ok=True)
    log.info("Rendering %d progress frames …", total_frames)
    for frame_n in range(total_frames):
        img  = Image.new("RGBA", (W, PROGRESS_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        fw   = int((frame_n / max(total_frames - 1, 1)) * W)
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
    img  = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONT_SEMIBOLD), 22)
    text = "@justus.automates"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw   = bbox[2] - bbox[0]
    x    = 1048 - tw
    y    = FACECAM_Y + 2
    draw.text((x, y), text, font=font, fill=(232, 232, 232, 77))
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


# ── fal.ai thumbnail generator ───────────────────────────────────────────────
def _call_fal_thumbnail(concept: str, accent: str) -> str:
    """Generate a thumbnail via fal.ai nano-banana-pro. Returns image URL."""
    if not FAL_API_KEY:
        raise RuntimeError("FAL_API_KEY not set")

    negative = (
        "cluttered, busy, multiple objects, text, letters, words, watermark, "
        "logo, oversaturated, neon overload, rainbow colors, cartoonish, anime, "
        "low quality, blurry, generic stock photo, chaotic background, messy, "
        "people, faces, hands, distorted, ugly, amateur"
    )
    prompt = (
        f"Premium minimal tech thumbnail for a social media video. "
        f"Hero subject: {concept}. "
        "Single hero object, centered composition, lots of empty negative space around it. "
        "Deep dark charcoal background (#12101a). "
        f"The ONLY light source is a soft amethyst purple glow ({accent}) rimming the object. "
        "Cinematic studio lighting, premium 3D render aesthetic, ultra clean, sharp focus, "
        "high-end product photography style like an Apple keynote reveal. "
        f"Sophisticated, minimalist, expensive-looking. Subtle, not oversaturated. "
        f"Avoid: {negative}"
    )

    headers = {
        "Authorization": f"Key {FAL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "prompt": prompt,
        "negative_prompt": negative,
        "aspect_ratio": "9:16",
        "num_images": 1,
    }

    log.info("[THUMB] calling fal.ai nano-banana-pro")
    resp = requests.post(FAL_THUMBNAIL_ENDPOINT, headers=headers, json=payload, timeout=90)
    resp.raise_for_status()
    data = resp.json()

    # Direct result (synchronous endpoint)
    if data.get("images"):
        return data["images"][0]["url"]

    # Queued result — poll status_url / response_url
    request_id = data.get("request_id")
    status_url  = data.get("status_url") or data.get("response_url")
    if not status_url and request_id:
        status_url = f"https://queue.fal.run/fal-ai/nano-banana-pro/requests/{request_id}"
    if not status_url:
        raise RuntimeError(f"fal.ai unexpected response: {data}")

    poll_headers = {"Authorization": f"Key {FAL_API_KEY}"}
    for _ in range(60):
        time.sleep(1)
        poll = requests.get(status_url, headers=poll_headers, timeout=30)
        poll.raise_for_status()
        result = poll.json()
        status = result.get("status", "")
        if status == "COMPLETED":
            images = (result.get("output") or result).get("images", [])
            if images:
                return images[0]["url"]
            raise RuntimeError(f"fal.ai completed but no images in response")
        if status in ("FAILED", "ERROR"):
            raise RuntimeError(f"fal.ai generation failed: {result}")

    raise RuntimeError("fal.ai polling timed out after 60s")


# ── Broll prompt builder ──────────────────────────────────────────────────────
def _broll_system_prompt(topic: str, accent: str, dur: float) -> str:
    n_primary  = max(int(dur / 2.5), 6)   # primary beat every 2.5s
    n_second   = max(int(dur / 5.0), 3)   # secondary beat every 5s
    p_beat     = dur / n_primary
    s_beat     = dur / n_second
    return f"""You are a motion graphics director. Output: a self-contained HTML file — {dur:.0f}s B-Roll for a German AI/tech creator.

BRAND: bg #141218 · text #ffffff · labels #d0d0d0 · accent {accent}
FONTS: use font-family:'Arial Black','Impact',sans-serif for all bold/heavy text. Load Montserrat async
  via @font-face or Google Fonts ONLY as enhancement — never block animation on font load.
RULE: every text/number is color:#fff, opacity:1, text-shadow:0 2px 24px rgba(0,0,0,0.95). Never dim data elements.
FRAME-1 RULE: ALL animations must be visible from frame 1. Do NOT use window.onload, DOMContentLoaded,
  or document.fonts.ready to delay GSAP start. All gsap.timeline() and gsap.to() calls execute
  synchronously in inline <script>. Frame 1 = something is already moving or visible.
  ALL CSS @keyframes rules must have animation-delay:0s and animation-play-state:running on their elements.
  The PRIMARY STAT counter starts counting from 0 immediately at t=0 — the countUp must fire at position 0
  in the gsap timeline, not delayed. Viewer must see movement within the first 100ms.

━━━ ARCHITECTURE: 4 INDEPENDENT STREAMS, ALL RUNNING SIMULTANEOUSLY ━━━
At every frame, the viewer sees: SCENE + TICKER + PRIMARY STAT + SECONDARY STAT.
Nothing is ever blank. No gaps. No waiting.

──────────────────────────────────────────────────────────────────────
STREAM A — SCENE (t=0 → t={dur:.0f}, never exits)
──────────────────────────────────────────────────────────────────────
Full-canvas SVG or pixel-art illustration at opacity 0.6.
Represents the topic as a physical environment. Always subtly animating.

Scene types — pick the exact match:
· Nuclear / Energy      → SVG cooling towers (rounded rectangles), steam circles rising (gsap y:-40 repeat:-1),
                          power transmission lines (SVG polyline), voltage meter needle oscillating
· AI / LLM / Agents     → pixel-art canvas (270×144 → 1080×576, image-rendering:pixelated):
                          server rack rows, blinking LEDs (random interval setInterval), data flow dots
· Finance / Markets     → candlestick chart (SVG rects, reds+greens), price line drawing itself, volume bars
· Hacking / Security    → monospace terminal (green #00ff41 on black), lines of code scrolling upward via CSS
· Social / Viral        → phone outline SVG, notification cards stacking, follower counter spinning
· Healthcare / Bio      → ECG line (SVG stroke-dashoffset animating), heartbeat pulse, DNA helix (2 sine SVG paths)
· Geopolitics           → world map SVG (simplified continents), pulsing country dots, arc trade routes
· Robotics / Industry   → mechanical arm (SVG jointed segments), gear (CSS rotate repeat:-1), conveyor belt

The scene has 2-3 internal animated elements. They run independently with gsap repeat:-1 or CSS animation.

──────────────────────────────────────────────────────────────────────
STREAM B — LIVE TICKER (t=0 → t={dur:.0f}, never exits)
──────────────────────────────────────────────────────────────────────
A strip at the very bottom: y=530, height=46px, full width.
Background: rgba(0,0,0,0.75). Border-top: 1px solid rgba(255,255,255,0.12).
Contains data pills scrolling left continuously (CSS @keyframes translateX).
Each pill: rounded-rect bg rgba(255,255,255,0.08), text 13px Montserrat 600, white.
Pill content = 5-8 real data points about the topic (numbers, percentages, short labels).
New pill enters from right every 3s via GSAP (repeat, x: 1100→-200).
The ticker NEVER stops. It is always visible.

──────────────────────────────────────────────────────────────────────
STREAM C — PRIMARY STAT (changes every {p_beat:.1f}s — {n_primary} total)
──────────────────────────────────────────────────────────────────────
Large hero number or stat, center-stage (y: 200-280).
Font-size: 96-120px, font-weight: 900, color: #fff.
Enter: gsap.from(el, {{x:80, opacity:0, duration:0.3, ease:"power3.out"}}, t)
Exit:  gsap.to(el, {{x:-80, opacity:0, duration:0.25, ease:"power2.in"}}, t+{p_beat:.1f}-0.3)
Numbers use countUp: gsap.to(counter, {{innerHTML:TARGET, snap:{{snapTo:1}}, duration:{min(p_beat*0.6,1.8):.1f}}})
Below: uppercase silver label 14px letter-spacing:0.18em, appears 0.15s after stat.
Every stat = a DIFFERENT data point. No repetition. Cover: growth %, absolute numbers,
comparisons, time stats, cost, speed, market size — all from the topic.

──────────────────────────────────────────────────────────────────────
STREAM D — SECONDARY STAT (changes every {s_beat:.1f}s — {n_second} total)
──────────────────────────────────────────────────────────────────────
Smaller supporting stat always visible in top-right corner (x:860, y:24).
Font-size: 28px weight:700. Label above it 11px.
Alternates position every change: top-right → top-left → top-right.
Enters: y:-20→0, opacity:0→1, 0.3s. Exits: y:0→-20, opacity:1→0.
Offset from Stream C by {p_beat/2:.1f}s so they never change simultaneously.
This stat is different data than Stream C — complementary angle on the same topic.

──────────────────────────────────────────────────────────────────────
STREAM E — ACCENT PULSE (every 6s, 1 element at a time)
──────────────────────────────────────────────────────────────────────
ONE element in {accent}: glowing horizontal bar under Stream C stat,
OR pulsing ring around a key number. gsap repeat:-1 yoyo:true duration:0.7s.
Box-shadow: 0 0 18px {accent}, 0 0 40px {accent}44.

──────────────────────────────────────────────────────────────────────
GSAP IMPLEMENTATION
──────────────────────────────────────────────────────────────────────
Import: https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.2/gsap.min.js
Use ONE gsap.timeline({{repeat:-1}}) for streams C+D+E — repeat:-1 is MANDATORY so it loops forever.
Streams A+B use separate gsap.to() / CSS @keyframes with repeat:-1.
Timeline must have entries from t=0.1 through t={dur:.0f}.

CRITICAL — NO DEAD ZONES: Every stream must be animated until t={dur:.0f}. The timeline uses repeat:-1
so it loops automatically after one full cycle. Never leave any period where all streams are idle.
Do not hardcode a fixed end — the repeat:-1 handles looping.

TOPIC DATA — derive REAL specific numbers (invent plausible ones if needed):
Build a list of 12+ distinct data points before coding. Each stream uses different ones.
Example for "China Atomkraftwerke":
  22 reactors under construction / 6 new starts per year / build time 5.4 years /
  54 GW new capacity / cost $6.4B per reactor / 15% of global nuclear output /
  CO2 saved: 400Mt/year / 1,000 engineers per site / 3× faster than EU average /
  nuclear share 5% → target 10% by 2035 / 150,000 workers / $440B total investment

──────────────────────────────────────────────────────────────────────
HTML SKELETON
──────────────────────────────────────────────────────────────────────
<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1080px;height:576px;overflow:hidden;background:#141218;font-family:'Arial Black','Impact',Arial,sans-serif;position:relative}}
</style>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700;900&display=swap" rel="stylesheet" media="print" onload="this.media='all'">
</head><body>
<!-- STREAM A: scene SVG or canvas -->
<!-- STREAM B: ticker strip (position:absolute, bottom) -->
<!-- STREAM C: primary stat elements (position:absolute) -->
<!-- STREAM D: secondary stat (position:absolute, corner) -->
<!-- STREAM E: accent element (position:absolute) -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.2/gsap.min.js"></script>
<script>/* streams A+B ambient | streams C+D+E timeline */</script>
</body></html>

Return ONLY raw HTML. No markdown. No explanation."""


def _segment_into_scenes(words: list, duration: float, topic: str) -> list:
    n_scenes = max(8, min(15, int(duration / 4)))
    system_prompt = (
        "You are a video editor. Segment a transcript into visual scenes for B-Roll sync.\n"
        f"Total duration: {duration:.1f}s. Create exactly {n_scenes} scenes covering 0s to {duration:.1f}s.\n"
        "Per scene: start, end (seconds), visual_theme (what to SHOW — specific, not generic), "
        "data_point (one key stat/number or empty), mood (dark|urgent|bright), "
        "line (the EXACT spoken sentence(s) in that time window — copy from transcript).\n"
        'Return ONLY valid JSON: {"scenes":[{"start":0,"end":4.0,"visual_theme":"...","data_point":"...","mood":"dark","line":"..."}]}\n'
        "Scenes must be contiguous. Last end == total duration."
    )
    # Full word list for line extraction
    full_words = [{"word": w["word"], "t": round(w["start"], 1)} for w in words]
    try:
        raw = call_openrouter(
            system_prompt,
            f"Topic: {topic}\nFull transcript: {json.dumps(full_words)}",
            model="anthropic/claude-haiku-4.5",
            max_tokens=1200,
        )
        m      = re.search(r'\{.*\}', raw, re.DOTALL)
        scenes = json.loads(m.group()).get("scenes", []) if m else []
        if scenes:
            scenes[0]["start"]  = 0.0
            scenes[-1]["end"]   = duration
            return scenes
    except Exception as exc:
        log.warning("[BROLL_SYNC] Segmentation failed: %s", exc)
    seg = duration / n_scenes
    return [{"start": i*seg, "end": (i+1)*seg,
              "visual_theme": topic, "data_point": "", "mood": "dark", "line": ""}
            for i in range(n_scenes)]


def _strip_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        end   = -1 if lines[-1].strip() == "```" else len(lines)
        s     = "\n".join(lines[1:end])
    return s


def _broll_system_prompt_v2(topic: str, accent: str, scenes: list) -> str:
    n = len(scenes)
    scene_lines = "\n".join(
        f'  scene{i} (start={s["start"]:.3f}s, dur={s["end"]-s["start"]:.1f}s): '
        f'"{s["visual_theme"]}" | data_point={s.get("data_point","—")} | "{s.get("line","")}"'
        for i, s in enumerate(scenes)
    )
    return f"""Du bist Motion-Graphics-Direktor. Erzeuge {n} Szenen-DIVs + 1 Animations-Script für B-Roll (1080×{BROLL_H}px, bg #141218, accent {accent}).

AUSGABE-FORMAT (kein Wrapper, kein Markdown):
  1. {n} × <div class="scene" id="sceneN"> ... </div>
  2. Danach: EIN <script>-Block mit per-Szene-Animation
  KEIN <html>/<head>/<body>/<style>/<script src=...>

CSS-Klassen (bereits definiert):
  .scene → position:absolute; inset:0; opacity:0; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:16px; padding:40px 60px
  .dp    → font-size:110px; font-weight:900; color:{accent}; line-height:1; text-align:center
  .lbl   → font-size:28px; font-weight:700; color:#d0d0d0; text-align:center; max-width:920px

GSAP-Hilfsfunktionen (global verfügbar, nicht neu definieren):
  animateCounter(el, target, dur, suffix)      — countUp-Zähler
  addAmbientPulse(el, scaleAmt=1.15, dur=1.2) — endloser Glow-Pulse

{n} SZENEN (start-Zeiten für gsap.delayedCall):
{scene_lines}

=== PFLICHT: JEDE SZENE BRAUCHT 3 ANIMATIONS-EBENEN ===

EBENE 1 — ENTRY (erste 0.4s, Pflicht für JEDES Element):
Kein Element erscheint sofort. Alle Elemente animieren beim Erscheinen:
  gsap.from(el, {{scale:0, opacity:0, duration:0.4, ease:"back.out(1.7)"}})
  oder: {{y:20, opacity:0, rotation:-10, duration:0.35, ease:"power3.out"}}

EBENE 2 — SATELLITEN (Pflicht, 3–5 pro Szene):
Um das Hauptelement: 3–5 kleine Begleitelemente (Mini-Icons, Datenpunkte, Labels).
Gestaffelter Entry: gsap.from([".sN-0",".sN-1",".sN-2"], {{scale:0, opacity:0, stagger:0.12, duration:0.3, ease:"back.out(2)", delay:0.35}})
Verbinde Haupt↔Satelliten mit dünnen SVG-Linien (stroke:{accent}, opacity:0.4, stroke-dasharray="4 6").

EBENE 3 — AMBIENT (Pflicht, läuft während der ganzen Szenendauer):
Nachdem alles eingeblendet ist, muss sich dauerhaft etwas bewegen:
  Option A: addAmbientPulse(document.querySelector("#sceneN .glow"))
  Option B: gsap.to([".sN-0",".sN-1"], {{y:"+=6", duration:1.5, repeat:-1, yoyo:true, stagger:{{each:0.1,from:"random"}}, ease:"sine.inOut", delay:0.6}})
  Option C: gsap.to(lineEl, {{strokeDashoffset:"-=20", duration:2, repeat:-1, ease:"none", delay:0.6}})

TIMING IM SCRIPT-BLOCK: gsap.delayedCall(sceneStart, function(){{...}}) pro Szene.
Innerhalb der Funktion: gsap.from/to/set mit delay: statt absoluter Position.

BEISPIEL für scene0 (start=0.000s):
gsap.delayedCall(0.000, function(){{
  gsap.from("#scene0 .hero", {{scale:0.3, opacity:0, duration:0.45, ease:"back.out(1.7)"}});
  gsap.from([".s0-0",".s0-1",".s0-2"], {{scale:0, opacity:0, stagger:0.1, duration:0.3, ease:"back.out(2)", delay:0.35}});
  gsap.to(".s0-line", {{strokeDashoffset:"-=30", duration:2, repeat:-1, ease:"none", delay:0.6}});
  addAmbientPulse(document.querySelector("#scene0 .glow"));
}});

CHECK: 3 Frames bei start+0.5s, start+2s, start+3.5s MÜSSEN sich unterscheiden
(Glow-Intensität ODER Satelliten-Y ODER Linien-Dash-Offset). Nur Counter = FEHLER.

Gib NUR die {n} divs + abschließenden <script>-Block zurück. Kein Markdown."""


def _build_broll_html(scene_divs: str, scenes: list, accent: str) -> str:
    """Wrap Sonnet's scene divs with Python-generated helpers + GSAP timeline.

    Script order in final HTML (after _inject_gsap_inline replaces src tag):
      1. GSAP 69KB inline           ← injected before first <script>
      2. helpers + tl (Python)      ← animateCounter, addAmbientPulse, opacity timeline
      3. scene_divs HTML            ← Sonnet's divs
      4. Sonnet's <script> block    ← gsap.delayedCall entries (helpers+tl already defined)
    """
    tl_lines = []
    for i, s in enumerate(scenes):
        fade_in  = s["start"]
        fade_out = max(s["start"] + 0.31, s["end"] - 0.25)
        tl_lines.append(f'  tl.to("#scene{i}",{{opacity:1,duration:0.3}},{fade_in:.3f});')
        tl_lines.append(f'  tl.to("#scene{i}",{{opacity:0,duration:0.2}},{fade_out:.3f});')
    tl_code = "\n".join(tl_lines)

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1080px;height:{BROLL_H}px;overflow:hidden;background:#141218;position:relative;font-family:"Arial Black",Impact,sans-serif}}
.scene{{position:absolute;inset:0;opacity:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;padding:40px 60px}}
.dp{{font-size:110px;font-weight:900;color:{accent};text-shadow:0 0 40px {accent}88,0 2px 24px rgba(0,0,0,0.9);line-height:1;text-align:center}}
.lbl{{font-size:28px;font-weight:700;color:#d0d0d0;text-align:center;max-width:920px;line-height:1.3}}
svg{{overflow:visible}}
</style>
</head>
<body>
<script src="gsap.min.js"></script>
<script>
function animateCounter(el, target, dur, suffix) {{
  var o = {{v: 0}};
  gsap.to(o, {{v: target, duration: dur, ease: "power2.out",
    onUpdate: function() {{ el.textContent = Math.round(o.v) + (suffix || ""); }}
  }});
}}
function addAmbientPulse(el, scaleAmt, dur) {{
  if (!el) return;
  gsap.to(el, {{scale: scaleAmt||1.15, opacity:0.6, duration: dur||1.2, repeat:-1, yoyo:true, ease:"sine.inOut"}});
}}
var tl = gsap.timeline();
{tl_code}
</script>
{scene_divs}
</body></html>"""


def _validate_broll_html(html: str, n_scenes: int) -> bool:
    """Returns True if HTML has the last scene ID (single or double quotes)."""
    last_n = n_scenes - 1
    return f'id="scene{last_n}"' in html or f"id='scene{last_n}'" in html



async def _render_scene_html(html: str, job_dir: Path, idx: int, scene_dur: float) -> Path:
    html_path  = job_dir / f"scene_{idx}.html"
    video_path = job_dir / f"scene_{idx}.mp4"
    html_path.write_text(_inject_gsap_inline(html), encoding="utf-8")
    ok = await render_html_to_video(html_path, video_path, scene_dur)
    if not ok:
        run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=black:size=1080x{BROLL_H}:rate={FPS}",
            "-t", str(scene_dur),
            "-c:v", "libx264", "-crf", "16", "-preset", "medium",
            "-pix_fmt", "yuv420p", str(video_path)
        ], f"black_scene_{idx}")
    return video_path


# ── POST /generate-broll-synced ───────────────────────────────────────────────
@app.post("/generate-broll-synced")
async def generate_broll_synced(req: BrollSyncedRequest):
    job_id  = str(uuid.uuid4())
    job_dir = Path(f"/tmp/brollsync_{job_id}")
    job_dir.mkdir(parents=True, exist_ok=True)
    log.info("[BROLL_SYNC] START topic=%s", req.topic)

    try:
        # 1. Download facecam + probe duration
        facecam_path = job_dir / "facecam.mp4"
        if not download_file(req.facecam, facecam_path):
            raise HTTPException(status_code=500, detail="facecam download failed")
        duration = probe_duration(facecam_path)
        log.info("[BROLL_SYNC] facecam duration=%.1fs", duration)

        # 2. Transcribe
        words = transcribe_audio(facecam_path)
        if not words:
            raise HTTPException(status_code=500, detail="transcription failed")

        # 3. Segment into scenes
        scenes = _segment_into_scenes(words, duration, req.topic)
        log.info("[BROLL_SYNC] %d scenes", len(scenes))

        # 4. Generate full B-Roll HTML in ONE Sonnet call
        n_scenes = len(scenes)
        log.info("[BROLL_SYNC] generating %d-scene HTML in one call", n_scenes)
        system_prompt = _broll_system_prompt_v2(req.topic, req.brand_color_primary, scenes)
        user_msg      = f"Topic: {req.topic}. Erzeuge alle {n_scenes} Szenen."

        def _gen_full_html():
            return call_openrouter(system_prompt, user_msg,
                                   model="anthropic/claude-sonnet-4.6",
                                   max_tokens=11500)

        loop     = asyncio.get_event_loop()
        html_raw = None
        for attempt in range(1, 4):
            try:
                html_raw = _strip_fences(str(
                    await loop.run_in_executor(_html_executor, _gen_full_html)
                ))
                if not _validate_broll_html(html_raw, n_scenes):
                    log.warning("[BROLL_SYNC] validation warn on attempt %d (using anyway)", attempt)
                else:
                    log.info("[BROLL_SYNC] HTML valid on attempt %d", attempt)
                break  # use whatever came back — only retry on exception
            except Exception as exc:
                log.warning("[BROLL_SYNC] attempt %d call failed: %s", attempt, exc)
                html_raw = None
                if attempt < 3:
                    await asyncio.sleep(3)

        if not html_raw:
            raise HTTPException(status_code=500, detail="Broll HTML generation failed after 3 attempts")

        log.info("[BROLL_SYNC] scene divs (%d chars): %s", len(html_raw),
                 html_raw[:300].replace('\n', ' '))

        # Wrap Sonnet's divs with Python-generated GSAP timeline
        full_html_raw  = _build_broll_html(html_raw, scenes, req.brand_color_primary)
        full_html_path = job_dir / "broll_full.html"
        full_html_path.write_text(_inject_gsap_inline(full_html_raw), encoding="utf-8")
        total_duration = scenes[-1]["end"]

        # 4b. Render full HTML as one video, then split by scene timestamps
        full_video_path = job_dir / "broll_full.mp4"
        ok = await render_html_to_video(full_html_path, full_video_path, total_duration)

        # Extract per-scene clips from the full render
        scene_videos = []
        for i, scene in enumerate(scenes):
            scene_dur  = scene["end"] - scene["start"]
            clip_path  = job_dir / f"scene_{i}.mp4"
            if ok:
                run([
                    "ffmpeg", "-y",
                    "-ss", str(scene["start"]),
                    "-i", str(full_video_path),
                    "-t", str(scene_dur),
                    "-c:v", "libx264", "-crf", "16", "-preset", "medium",
                    "-pix_fmt", "yuv420p", str(clip_path)
                ], f"clip_scene_{i}")
            else:
                run([
                    "ffmpeg", "-y", "-f", "lavfi",
                    "-i", f"color=c=black:size=1080x{BROLL_H}:rate={FPS}",
                    "-t", str(scene_dur),
                    "-c:v", "libx264", "-crf", "16", "-preset", "medium",
                    "-pix_fmt", "yuv420p", str(clip_path)
                ], f"black_fallback_{i}")
            scene_videos.append(str(clip_path))
            log.info("[BROLL_SYNC] scene %d/%d clipped", i+1, n_scenes)

        # 5. Concatenate scenes
        broll_final = job_dir / "broll_synced.mp4"
        if len(scene_videos) == 1:
            shutil.copy(scene_videos[0], str(broll_final))
        else:
            # Crossfade 0.4s between scenes
            xfade_dur = 0.4
            durations = [probe_duration(Path(p)) for p in scene_videos]
            inputs = []
            for p in scene_videos:
                inputs += ["-i", p]

            if len(scene_videos) == 2:
                offset = durations[0] - xfade_dur
                filt = (f"[0:v][1:v]xfade=transition=fade:duration={xfade_dur}:offset={offset:.3f}[v]")
                maps = ["-map", "[v]"]
            else:
                parts, offset = [], 0.0
                label_in = "[0:v]"
                for i in range(1, len(scene_videos)):
                    offset += durations[i-1] - xfade_dur
                    label_out = "[v]" if i == len(scene_videos)-1 else f"[v{i}]"
                    parts.append(f"{label_in}[{i}:v]xfade=transition=fade:duration={xfade_dur}:offset={offset:.3f}{label_out}")
                    label_in = label_out
                filt = ";".join(parts)
                maps = ["-map", "[v]"]

            run([
                "ffmpeg", "-y", *inputs,
                "-filter_complex", filt,
                *maps,
                "-c:v", "libx264", "-crf", "16", "-preset", "medium",
                "-pix_fmt", "yuv420p", str(broll_final)
            ], "concat_scenes")

        # 6. Upload
        result = cloudinary.uploader.upload(
            str(broll_final),
            resource_type="video",
            folder="broll_synced",
            public_id=f"broll_{req.topic_slug}_{job_id[:8]}",
            overwrite=True,
        )
        url = result["secure_url"]
        log.info("[BROLL_SYNC] uploaded: %s", url)
        return {"broll_video_url": url, "scenes": scenes, "duration": duration}

    except HTTPException:
        raise
    except Exception as exc:
        log.error("[BROLL_SYNC] Error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Broll sync failed: {exc}")
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


# ── POST /generate-broll ──────────────────────────────────────────────────────
@app.post("/generate-broll")
async def generate_broll(req: GenerateBrollRequest):
    html_path = Path(f"/tmp/broll_{req.topic_slug}.html")
    try:
        log.info("[BROLL] Generating HTML for: %s", req.topic)

        dur       = req.duration
        n_beats   = max(8, int(dur / 3.5))
        beat_secs = dur / n_beats

        system_prompt = _broll_system_prompt(req.topic, req.brand_color_primary, dur)

        user_message = (
            f"Topic: {req.topic}\n"
            f"Duration: {dur:.0f}s → {n_beats} beats × {beat_secs:.1f}s each.\n"
            "Build the illustrated scene first, then layer the data beats on top. "
            "Every beat must feel like a new revelation about the topic."
        )

        html_content = call_openrouter(
            system_prompt, user_message,
            model="anthropic/claude-sonnet-4.6",
            max_tokens=12000,
        )

        html_content = _strip_fences(html_content)
        html_path.write_text(html_content, encoding="utf-8")
        log.info("[BROLL] HTML saved: %s", html_path)

        result   = cloudinary.uploader.upload(
            str(html_path),
            resource_type="raw",
            folder="broll_html",
            public_id=req.topic_slug,
            overwrite=True,
        )
        html_url = result["secure_url"]
        log.info("[BROLL] HTML uploaded: %s", html_url)

        return {"html_url": html_url, "topic_slug": req.topic_slug}

    except Exception as exc:
        log.error("[BROLL] Error: %s", exc)
        raise HTTPException(status_code=500, detail=f"B-Roll generation failed: {exc}")
    finally:
        html_path.unlink(missing_ok=True)


# ── POST /detect-impacts ──────────────────────────────────────────────────────
@app.post("/detect-impacts")
async def detect_impacts(req: DetectImpactsRequest):
    job_id  = str(uuid.uuid4())
    job_dir = Path(f"/tmp/impacts_{job_id}")
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        facecam_path = job_dir / "facecam.mp4"
        if not download_file(req.facecam, facecam_path):
            raise HTTPException(status_code=500, detail="facecam download failed")

        duration = probe_duration(facecam_path)
        words    = transcribe_audio(facecam_path)
        if not words:
            raise HTTPException(status_code=500, detail="Whisper transcription returned no words")

        transcript = " ".join(w["word"] for w in words)

        system_prompt = (
            "You are a video editor. Find moments for impact sound effects.\n"
            "Max 6 moments, only the strongest. Types:\n"
            "'impact'=hook/revelation/CTA punch, "
            "'whoosh'=number/stat mentioned, "
            "'benefit'=user benefit statement (you get/save/can now, advantage, profit), "
            "'subtle'=scene transition.\n"
            'Return ONLY JSON: {"impacts":[{"time":2.34,"type":"benefit","word":"x","reason":"y"}]}'
        )

        raw = call_openrouter(
            system_prompt,
            json.dumps(words),
            model="anthropic/claude-haiku-4.5",
            max_tokens=1000,
        )

        try:
            m            = re.search(r'\{.*\}', raw, re.DOTALL)
            impacts_data = json.loads(m.group()) if m else {"impacts": []}
        except (json.JSONDecodeError, AttributeError):
            log.warning("[IMPACTS] Could not parse JSON: %s", raw[:200])
            impacts_data = {"impacts": []}

        n = len(impacts_data.get("impacts", []))
        log.info("[IMPACTS] Detected %d moments", n)

        return {
            "impacts":        impacts_data.get("impacts", []),
            "transcript":     transcript,
            "total_duration": duration,
        }

    except HTTPException:
        raise
    except Exception as exc:
        log.error("[IMPACTS] Error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Impact detection failed: {exc}")
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


# ── POST /generate-thumbnail ──────────────────────────────────────────────────
@app.post("/generate-thumbnail")
async def generate_thumbnail(req: ThumbnailRequest):
    job_id  = str(uuid.uuid4())
    job_dir = Path(f"/tmp/thumb_{job_id}")
    job_dir.mkdir(parents=True, exist_ok=True)
    tmp_raw   = job_dir / "raw.jpg"
    tmp_final = job_dir / "final.jpg"
    try:
        # ── fal.ai generation ─────────────────────────────────────────────────
        concept = req.thumbnail_concept or req.thumbnail_prompt or req.topic
        ok = False
        try:
            loop    = asyncio.get_event_loop()
            img_url = await loop.run_in_executor(
                None, _call_fal_thumbnail, concept, req.brand_color_primary
            )
            log.info("[THUMB] fal.ai returned: %s", img_url)
            ok = download_file(img_url, tmp_raw)
        except Exception as exc:
            log.warning("[THUMB] fal.ai failed — dark fallback: %s", exc)

        # ── Fallback: solid dark canvas ───────────────────────────────────────
        if not ok:
            log.warning("[THUMB] using dark #12101a fallback canvas")
            Image.new("RGB", (W, H), (18, 16, 26)).save(str(tmp_raw), "JPEG", quality=95)

        # ── Resize / center-crop to exactly 1080×1920, hero centered ─────────
        img          = Image.open(str(tmp_raw)).convert("RGB")
        target_ratio = W / H
        src_ratio    = img.width / img.height
        if src_ratio > target_ratio:
            new_h, new_w = H, int(img.width * H / img.height)
        else:
            new_w, new_h = W, int(img.height * W / img.width)
        img  = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - W) // 2
        top  = (new_h - H) // 2
        img  = img.crop((left, top, left + W, top + H))
        img.save(str(tmp_final), "JPEG", quality=95)

        # ── Upload to Cloudinary ──────────────────────────────────────────────
        result = cloudinary.uploader.upload(
            str(tmp_final),
            resource_type="image",
            folder="thumbnails",
            public_id=f"thumb_{job_id}",
            overwrite=True,
        )
        url = result["secure_url"]
        log.info("[THUMB] uploaded: %s", url)
        return {"thumbnail_url": url}

    except Exception as exc:
        log.error("[THUMB] Error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Thumbnail generation failed: {exc}")
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


# ── POST /render ──────────────────────────────────────────────────────────────
@app.post("/render")
async def render(req: RenderRequest):
    job_id  = str(uuid.uuid4())
    job_dir = Path(f"/tmp/render_{job_id}")
    job_dir.mkdir(parents=True, exist_ok=True)
    log.info("=== JOB %s START ===", job_id)

    try:
        # ── 0. Thumbnail freeze-frame clip (optional, created early) ─────────
        thumb_clip = None
        if req.thumbnail_url:
            log.info("[RENDER] prepending 0.3s thumbnail freeze-frame")
            thumb_img = job_dir / "thumb.jpg"
            if download_file(req.thumbnail_url, thumb_img):
                thumb_clip = job_dir / "thumb_clip.mp4"
                try:
                    run([
                        "ffmpeg", "-y",
                        "-loop", "1", "-framerate", str(FPS),
                        "-i", str(thumb_img),
                        "-f", "lavfi",
                        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                        "-t", "0.2",
                        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                               f"crop={W}:{H},setsar=1",
                        "-c:v", "libx264", "-crf", "16", "-preset", "medium",
                        "-c:a", "aac", "-b:a", "192k",
                        "-pix_fmt", "yuv420p",
                        "-shortest",
                        str(thumb_clip),
                    ], "thumb_clip")
                except Exception as exc:
                    log.warning("[RENDER] thumbnail clip creation failed: %s", exc)
                    thumb_clip = None

        # ── 1. Download facecam ──────────────────────────────────────────────
        facecam_raw = job_dir / "facecam_raw.mp4"
        if not download_file(req.facecam, facecam_raw):
            raise HTTPException(status_code=500, detail="facecam download failed")

        # ── 2. Probe duration ────────────────────────────────────────────────
        duration     = probe_duration(facecam_raw)
        total_frames = int(duration * FPS)
        log.info("Facecam duration=%.3fs  frames=%d", duration, total_frames)

        # ── 3. B-Roll: pre-rendered video > HTML > black strip ───────────────
        broll_final = job_dir / "broll_final.mp4"
        broll_ok    = False

        if req.broll_video_url:
            log.info("[RENDER] Using pre-rendered synced broll")
            broll_ok = download_file(req.broll_video_url, broll_final)
        elif req.broll_html_url:
            html_path = job_dir / "broll.html"
            if download_file(req.broll_html_url, html_path):
                broll_ok = await render_html_to_video(html_path, broll_final, duration)
            if not broll_ok:
                log.warning("[RENDER] HTML broll failed — using black strip")

        if not broll_ok:
            run([
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", f"color=c=black:size=1080x{BROLL_H}:rate={FPS}",
                "-t", str(duration),
                "-c:v", "libx264", "-crf", "16", "-preset", "medium",
                "-pix_fmt", "yuv420p",
                str(broll_final),
            ], "black_broll")

        log_duration(broll_final, "broll_final")

        # ── 4. Scale/crop facecam ────────────────────────────────────────────
        facecam_scaled = job_dir / "facecam_scaled.mp4"
        scale_crop(facecam_raw, facecam_scaled, W, FACECAM_H)

        # ── 5. Divider gradient PNG ──────────────────────────────────────────
        divider_png = job_dir / "divider.png"
        make_gradient_png(divider_png, W, DIVIDER_H, AMETHYST, SILVER)

        # ── 6. Whisper captions ──────────────────────────────────────────────
        log.info("[RENDER] Transcribing facecam for captions")
        words = transcribe_audio(facecam_raw)

        # ── 7. Caption frames ─────────────────────────────────────────────────
        cap_dir = job_dir / "captions"
        build_caption_frames(words, total_frames, cap_dir, divider_png)

        # ── 8. Progress frames ───────────────────────────────────────────────
        prog_dir = job_dir / "progress"
        build_progress_frames(total_frames, prog_dir)

        # ── 9. Static overlays ───────────────────────────────────────────────
        hud_png       = HUD_PATH
        scanlines_png = SCANLINES_PATH

        # ── 10. Final ffmpeg compose ──────────────────────────────────────────
        log.info("[RENDER] Compositing: broll + divider + facecam + captions")
        output_mp4 = job_dir / "output.mp4"

        cap_pattern  = str(cap_dir  / "frame_%06d.png")
        prog_pattern = str(prog_dir / "frame_%06d.png")

        fadeout_start  = max(0.0, duration - 1.0)
        filter_complex = (
            f"[0:v]trim=duration={duration:.3f},setpts=PTS-STARTPTS,"
            f"zoompan=z='min(zoom+0.0002,1.05)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1080x{BROLL_H}:fps={FPS},"
            f"eq=brightness=0.08:contrast=1.1:saturation=1.05,"
            f"setsar=1[broll];"
            "[1:v]setsar=1[div];"
            "[2:v]setsar=1[face];"
            "[broll][div][face]vstack=inputs=3[stacked];"
            "[stacked][5:v]overlay=x=0:y=0[with_scan];"
            f"[with_scan][3:v]overlay=x=0:y={DIVIDER_Y}[with_cap];"
            "[with_cap][6:v]overlay=x=0:y=0[with_hud];"
            f"[with_hud][4:v]overlay=x=0:y={PROGRESS_Y}[with_prog];"
            f"[with_prog]zoompan=z='if(lte(on,5),1+0.15*(on/5),1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS},"
            f"fade=t=out:st={fadeout_start:.3f}:d=1[final]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", str(broll_final),        # [0]
            "-i", str(divider_png),         # [1]
            "-i", str(facecam_scaled),      # [2]
            "-framerate", str(FPS),
            "-i", cap_pattern,              # [3]
            "-framerate", str(FPS),
            "-i", prog_pattern,             # [4]
            "-i", str(scanlines_png),       # [5]
            "-i", str(hud_png),             # [6]
            "-filter_complex", filter_complex,
            "-map", "[final]",
            "-map", "2:a",
            "-af", "loudnorm=I=-14:LRA=11:TP=-1.5",
            "-c:v", "libx264", "-crf", "16", "-preset", "medium",
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            str(output_mp4),
        ]
        run(cmd, "final_compose")
        log.info("Output: %s (%.1f MB)", output_mp4, output_mp4.stat().st_size / 1e6)

        # ── 11. SFX mixing (optional) ─────────────────────────────────────────
        if req.impacts:
            mixed = mix_sfx_into_video(output_mp4, req.impacts, job_dir, duration)
            if mixed:
                output_mp4 = mixed

        # ── 12. Prepend thumbnail freeze-frame (optional) ─────────────────────
        if thumb_clip and thumb_clip.exists():
            output_with_thumb = job_dir / "output_thumb.mp4"
            try:
                run([
                    "ffmpeg", "-y",
                    "-i", str(thumb_clip),
                    "-i", str(output_mp4),
                    "-filter_complex",
                    "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vout][aout]",
                    "-map", "[vout]",
                    "-map", "[aout]",
                    "-c:v", "libx264", "-crf", "16", "-preset", "medium",
                    "-c:a", "aac", "-b:a", "192k",
                    "-pix_fmt", "yuv420p",
                    str(output_with_thumb),
                ], "thumb_concat")
                output_mp4 = output_with_thumb
                log.info("[RENDER] thumbnail + main concat done")
            except Exception as exc:
                log.warning("[RENDER] thumbnail concat failed, skipping: %s", exc)

        # ── 14. Upload ────────────────────────────────────────────────────────
        url = upload_cloudinary(output_mp4, job_id)
        log.info("[UPLOAD] %s", url)
        log.info("=== JOB %s DONE ===", job_id)
        return {"url": url}

    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
        log.info("Cleaned up %s", job_dir)


@app.get("/health")
def health():
    return {"status": "ok"}
