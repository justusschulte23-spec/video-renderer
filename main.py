import os
import hashlib
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
from fastapi.responses import StreamingResponse
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

# WhisperX via Replicate — precise word-level timestamps (wav2vec2 align) for cut+captions
REPLICATE_API_TOKEN = os.environ.get("REPLICATE_API_TOKEN", "")   # set in Railway env

FAL_API_KEY           = os.environ.get("FAL_API_KEY", "")
FAL_THUMBNAIL_ENDPOINT = "https://fal.run/fal-ai/nano-banana-pro"
FAL_FLUX_ENDPOINT      = "https://fal.run/fal-ai/flux-2/flash"   # Flux 2 [fast]

# Camera/brand enforcement tail appended to every image prompt (belt & suspenders
# on top of the global enricher) — forces photorealism + brand identity.
BRAND_IMAGE_TAIL = (
    "Amethyst (#8B5CF6) and cyan (#06B6D4) razor-sharp rim lighting on a dark cinematic "
    "studio background, shot on 35mm anamorphic lens, Arri Alexa, cinematic volumetric "
    "lighting, sharp focus on hardware textures, shallow depth of field, corporate thriller "
    "aesthetic, photorealistic, 8k"
)
BRAND_IMAGE_NEGATIVE = (
    "abstract shapes, glowing orb, floating glass sphere, neon cube, hologram network, "
    "digital particles, 3D render look, text, letters, words, captions, watermark, logo, "
    "people, faces, hands, distorted, cluttered, oversaturated, rainbow, cartoon, anime, "
    "low quality, blurry, generic stock photo, ugly, amateur, deformed, "
    "macro photography, coral, reef, organic texture, fluid art, paint, marble, ink, "
    "psychedelic, gradient blob, abstract background, fractal, microscopic, slime, goo"
)

# ── Brand colours (Justus defaults — used when no client template) ────────────
AMETHYST      = (139, 92, 246)
AMETHYST_DARK = (124, 58, 237)
SILVER        = (192, 192, 192)
BG            = (8, 8, 8)
WHITE         = (255, 255, 255)

# ── Multi-tenant template loader ──────────────────────────────────────────────
SUPABASE_URL         = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
_TEMPLATE_CACHE: dict = {}


def _hex_rgb(h, default=(139, 92, 246)):
    """'#8B5CF6' -> (139,92,246). Falls back to default on bad input."""
    try:
        h = str(h).lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except Exception:
        return default


def _load_template(client_id: Optional[str], inline: Optional[dict]) -> dict:
    """Resolve a client's visual template. inline dict wins; else fetch from Supabase
    by client_id -> template_id -> client_templates. Returns {} when nothing found
    (every downstream reader then falls back to the Justus default = no behaviour change)."""
    if inline:
        return inline
    if not client_id or not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {}
    if client_id in _TEMPLATE_CACHE:
        return _TEMPLATE_CACHE[client_id]
    hdr = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    try:
        c = requests.get(f"{SUPABASE_URL}/rest/v1/clients",
                         params={"client_id": f"eq.{client_id}", "select": "template_id"},
                         headers=hdr, timeout=15).json()
        tid = (c[0]["template_id"] if c else None) or "default"
        t = requests.get(f"{SUPABASE_URL}/rest/v1/client_templates",
                         params={"template_id": f"eq.{tid}", "select": "*"},
                         headers=hdr, timeout=15).json()
        tpl = t[0] if t else {}
    except Exception as exc:
        log.warning("[TPL] load failed for %s: %s — using Justus defaults", client_id, exc)
        tpl = {}
    _TEMPLATE_CACHE[client_id] = tpl
    return tpl


def _tpl_colors(tpl: dict) -> dict:
    return (tpl or {}).get("colors") or {}

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

FONT_OSWALD   = FONT_DIR / "Oswald.ttf"     # heading/punch (uppercase, high-impact)
FONT_INTER    = FONT_DIR / "Inter.ttf"      # body/base (clean, premium)
FONT_CAVEAT   = FONT_DIR / "Caveat.ttf"     # cursive (personal, handwritten)
FONT_ANTON    = FONT_DIR / "Anton.ttf"      # ultra-black display (premium caption block)
FONT_PLAYFAIR = FONT_DIR / "PlayfairDisplay.ttf"  # high-contrast serif (luxury editorial captions)

FONT_URLS = {
    FONT_BLACK:    "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Black.ttf",
    FONT_SEMIBOLD: "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-SemiBold.ttf",
    FONT_OSWALD:   "https://github.com/google/fonts/raw/main/ofl/oswald/Oswald%5Bwght%5D.ttf",
    FONT_INTER:    "https://github.com/google/fonts/raw/main/ofl/inter/Inter%5Bopsz,wght%5D.ttf",
    FONT_CAVEAT:   "https://github.com/google/fonts/raw/main/ofl/caveat/Caveat%5Bwght%5D.ttf",
    FONT_ANTON:    "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf",
    FONT_PLAYFAIR: "https://github.com/google/fonts/raw/main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf",
}

# ── SFX library ───────────────────────────────────────────────────────────────
SFX_DIR = Path("/tmp/sfx")

# Per-category mix behaviour applied by the FFmpeg mixer.
#   volume  : gain multiplier
#   trim    : hard cut length in seconds (None = full clip)
#   fadeout : linear fade-out length over the clip's final seconds (0 = none)
SFX_CATEGORY_RULES = {
    "first0":     {"volume": 1.00, "trim": None, "fadeout": 0.0},   # intro stinger @ t=0
    "hook":       {"volume": 0.32, "trim": 3.5,  "fadeout": 1.0},   # background bed
    "impact":     {"volume": 0.95, "trim": None, "fadeout": 0.0},   # one-shot punctuation
    "pop":        {"volume": 0.60, "trim": 0.40, "fadeout": 0.0},   # micro interaction
    "transition": {"volume": 0.78, "trim": None, "fadeout": 0.0},   # media swap
}

# asset_id -> (category, cloudinary url)
SFX_LIBRARY = {
    "hook_000_app_ping":       ("first0", "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/first0/hook_000_app_ping.mp3"),
    "hook_000_tech_thud":      ("first0", "https://res.cloudinary.com/poweroflillith/video/upload/v1782482278/audio/sfx/hook/hook_000_tech_thud.mp3"),
    "distant_police_siren_bg": ("hook",   "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/hook/distant_police_siren_bg.mp3"),
    "hook_cash_register_01":   ("hook",   "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/hook/hook_cash_register_01.mp3"),
    "hook_clock_tick_01":      ("hook",   "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/hook/hook_clock_tick_01.mp3"),
    "hook_coin_drop_01":       ("hook",   "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/hook/hook_coin_drop_01.mp3"),
    "hook_error_buzz_01":      ("hook",   "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/hook/hook_error_buzz_01.mp3"),
    "hook_notification_01":    ("hook",   "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/hook/hook_notification_01.mp3"),
    "hook_success_chime_01":   ("hook",   "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/hook/hook_success_chime_01.mp3"),
    "hook_warning_sonar_01":   ("hook",   "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/hook/hook_warning_sonar_01.mp3"),
    "impact_bass_drop_01":     ("impact", "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/impact/impact_bass_drop_01.mp3"),
    "impact_cinematic_hit_01": ("impact", "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/impact/impact_cinematic_hit_01.mp3"),
    "impact_digital_boom_01":  ("impact", "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/impact/impact_digital_boom_01.mp3"),
    "impact_gong_reversed_01": ("impact", "https://res.cloudinary.com/poweroflillith/video/upload/v1782418124/audio/sfx/impact/Impact_Gong.mp3"),
    "impact_heartbeat_01":     ("impact", "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/impact/impact_heartbeat_01.mp3"),
    "impact_metal_thud_01":    ("impact", "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/impact/impact_metal_thud_01.mp3"),
    "impact_shatter_muted_01": ("impact", "https://res.cloudinary.com/poweroflillith/video/upload/v1782418124/audio/sfx/impact/Impact_Gong.mp3"),
    "impact_tape_stop_01":     ("impact", "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/impact/impact_tape_stop_01.mp3"),
    "pop_blip_organic_01":     ("pop",    "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/pop/pop_blip_organic_01.mp3"),
    "pop_bubble_muted_01":     ("pop",    "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/pop/pop_bubble_muted_01.mp3"),
    "pop_camera_shutter_01":   ("pop",    "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/pop/pop_camera_shutter_01.mp3"),
    "pop_click_mech_01":       ("pop",    "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/pop/pop_click_mech_01.mp3"),
    "pop_glass_tap_01":        ("pop",    "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/pop/pop_glass_tap_01.mp3"),
    "pop_snap_finger_01":      ("pop",    "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/pop/pop_snap_finger_01.mp3"),
    "pop_ui_clean_01":         ("pop",    "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/pop/pop_ui_clean_01.mp3"),
    "trans_digital_swipe_01":  ("transition", "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/transition/trans_digital_swipe_01.mp3"),
    "trans_reverse_suck_01":   ("transition", "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/transition/trans_reverse_suck_01.mp3"),
    "trans_swish_fabric_01":   ("transition", "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/transition/trans_swish_fabric_01.mp3"),
    "trans_swish_paper_01":    ("transition", "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/transition/trans_swish_paper_01.mp3"),
    "trans_whoosh_deep_01":    ("transition", "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/transition/trans_whoosh_deep_01.mp3"),
    "trans_whoosh_fast_01":    ("transition", "https://res.cloudinary.com/poweroflillith/video/upload/audio/sfx/transition/trans_whoosh_fast_01.mp3"),
}

# Backward-compat: old detect-impacts types -> new assets (keeps existing N8N calls working)
SFX_LEGACY_MAP = {
    "whoosh":  "trans_whoosh_fast_01",
    "impact":  "impact_cinematic_hit_01",
    "benefit": "hook_success_chime_01",
    "subtle":  "trans_swish_paper_01",
}

# t=0 intro stinger: ping + thud layered concurrently (deterministic, always added)
SFX_INTRO_ASSETS = ["hook_000_tech_thud"]   # THE hook sound at t=0 (single clean thud)


def sfx_local_path(asset_id: str) -> Path:
    cat = SFX_LIBRARY[asset_id][0]
    return SFX_DIR / cat / f"{asset_id}.mp3"


# ── Font bootstrap ─────────────────────────────────────────────────────────────
_CRITICAL_FONTS = {FONT_BLACK, FONT_SEMIBOLD}  # Justus captions depend on these


def _bootstrap_fonts():
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    for path, url in FONT_URLS.items():
        if path.exists():
            continue
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            path.write_bytes(r.content)
            log.info("Font saved: %s", path.name)
        except Exception as exc:
            if path in _CRITICAL_FONTS:
                raise RuntimeError(f"Critical font download failed: {url} — {exc}")
            log.warning("Optional font download failed (%s): %s", path.name, exc)

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
    # Remove only <script src="...gsap..."> / <script src="...greensock..."> tags.
    # Deliberately NOT matching id/class attrs so Sonnet's <script id="gsap-*"> is kept.
    patched = re.sub(
        r'<script[^>]+src=["\'][^"\']*(?:gsap|greensock)[^"\']*["\'][^>]*>.*?</script>',
        lambda _: "", patched, flags=re.IGNORECASE | re.DOTALL)
    patched = re.sub(
        r'<script[^>]+src=["\'][^"\']*(?:gsap|greensock)[^"\']*["\'][^>]*/?>',
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
    ok = 0
    for asset_id, (cat, url) in SFX_LIBRARY.items():
        path = sfx_local_path(asset_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            ok += 1
            continue
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            path.write_bytes(r.content)
            ok += 1
        except Exception as exc:
            log.warning("SFX download failed [%s]: %s", asset_id, exc)
    log.info("SFX library ready: %d/%d assets cached", ok, len(SFX_LIBRARY))

_bootstrap_sfx()


def _generate_scanlines(out_path: Path = SCANLINES_PATH, tpl: dict = None):
    sc      = (tpl or {}).get("scanlines") or {}
    opacity = float(sc.get("opacity", 0.08))
    img  = Image.new("RGBA", (W, BROLL_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for y in range(0, BROLL_H, 4):
        draw.rectangle([0, y, W, y + 1], fill=(0, 0, 0, int(255 * opacity)))
    img.save(str(out_path))


def _generate_hud(out_path: Path = HUD_PATH, tpl: dict = None):
    hud     = (tpl or {}).get("hud") or {}
    cols    = _tpl_colors(tpl)
    primary = _hex_rgb(cols.get("primary"), AMETHYST)
    secondary = _hex_rgb(cols.get("secondary"), SILVER)
    corner  = _hex_rgb(hud.get("corner_color"), primary)
    handle  = hud.get("handle", "@JUSTUS.AUTOMATES")
    tag     = hud.get("tag", "AI · DEEPTECH")
    img     = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw    = ImageDraw.Draw(img)
    font_sm = ImageFont.truetype(str(FONT_SEMIBOLD), 22)
    lw, m, arm = 4, 24, 55

    draw.line([m, m, m, m+arm],           fill=(*corner, 178), width=lw)
    draw.line([m, m, m+arm, m],           fill=(*corner, 178), width=lw)
    draw.line([W-m, m, W-m, m+arm],       fill=(*secondary, 128), width=lw)
    draw.line([W-m, m, W-m-arm, m],       fill=(*secondary, 128), width=lw)
    bl_y = H - m
    draw.line([m, bl_y, m, bl_y-arm],     fill=(*corner, 102), width=lw)
    draw.line([m, bl_y, m+arm, bl_y],     fill=(*corner, 102), width=lw)

    dot_x, dot_y = m, m + arm + 28
    draw.ellipse([dot_x-8, dot_y-8, dot_x+8, dot_y+8], fill=(*corner, 204))
    draw.text((dot_x + 20, dot_y - 11), "LIVE", font=font_sm, fill=(*corner, 178))
    draw.text((W - m - 5, m + arm + 16), "AI.DEEP",
              font=font_sm, fill=(*secondary, 128), anchor="rs")

    pill_w, pill_h = 220, 52
    pill_x = W - m - pill_w
    pill_y = H - m - pill_h - 10
    draw.rounded_rectangle(
        [pill_x, pill_y, pill_x+pill_w, pill_y+pill_h],
        radius=14, fill=(*corner, 38), outline=(*corner, 128), width=2
    )
    draw.text((pill_x + pill_w//2, pill_y + pill_h//2),
              tag, font=font_sm, fill=(*corner, 230), anchor="mm")

    wm_y = H - m - pill_h - 60
    draw.text((m + arm + 20, wm_y), handle, font=font_sm, fill=(*corner, 140))

    img.save(str(out_path))


_generate_scanlines()
_generate_hud()
log.info("Scanlines + HUD PNG generated (Justus default)")

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
    image_cuts:       bool = True                  # full-frame AI image cutaways at keyword moments
    image_cut_events: Optional[list] = None        # N8N may pass explicit [{time, keyword}] (skips LLM detection)
    brand_color_primary: str = "#8B5CF6"
    client_id:        Optional[str] = None         # multi-tenant: resolves visual template from Supabase
    template:         Optional[dict] = None         # OR pass the template JSON inline (wins over client_id)


# ── Remotion renderer integration (primary motion-graphics; ffmpeg = audio mux) ──
REMOTION_URL = os.environ.get(
    "REMOTION_URL", "https://remotion-renderer-production-4e7d.up.railway.app"
).rstrip("/")
FORMAT_COMPOSITION = {
    "broll_automated":      "JustusBroll",
    "usecase_bubble":       "JustusUsecase",
    "talking_head_punches": "JustusPunches",
}


class RemotionRenderRequest(BaseModel):
    facecam:     str
    format:      str
    hook_text:   str = ""
    screen_url:  Optional[str]  = None   # usecase_bubble
    client_id:   Optional[str]  = "justus"
    topic_label: Optional[str]  = None   # broll
    headline:    Optional[str]  = None   # broll
    stats:       Optional[list] = None   # broll [{value,label}]
    ticker:      Optional[list] = None   # broll [str]
    code_lines:  Optional[list] = None   # broll [str]
    punch_ins:   Optional[list] = None   # punches [seconds]
    impacts:     Optional[list] = None   # fallback source for punch_ins


class ThumbnailRequest(BaseModel):
    topic:               str
    thumbnail_concept:   Optional[str] = None
    thumbnail_prompt:    Optional[str] = None  # alias used by N8N workflow
    brand_color_primary: str = "#8B5CF6"
    client_id:           str = "justus"   # resolves thumbnail brand from template


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


class EnrichImageRequest(BaseModel):
    keyword:             str
    brand_color_primary: str = "#8B5CF6"
    generate:            bool = False   # also run Flux.1 [schnell] and return image_url


class TrimSilenceRequest(BaseModel):
    facecam: str
    max_gap: float = 0.30   # gaps <= this stay (natural cadence)
    pad:     float = 0.05   # silence kept on each side of a trimmed gap
    smart_cut: bool = True  # phase 0: LLM coherence cut (duplicate takes / false starts)


class KeyFact(BaseModel):
    value: str
    label: str


class InfosheetRequest(BaseModel):
    topic_tag:          str
    headline:           str
    subhook:            str
    key_facts:          list[KeyFact]
    was_passiert:       list[str]
    was_bringt_mir:     list[str]
    justus_take:        str
    konkreter_schritt:  str
    client_id:          str = "justus"   # resolves branding+palette; justus = byte-identical


# ── OpenRouter helper ─────────────────────────────────────────────────────────
CHEAP_MODEL = "z-ai/glm-4.6"   # mechanical/derivative tasks (detectors, repurposing) — ~25x cheaper than sonnet


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
    if "glm" in model.lower():
        payload["reasoning"] = {"enabled": False}   # GLM burns tokens on reasoning otherwise
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
            page.on("console", lambda msg: log.warning("[BROLL-JS] %s", msg.text)
                    if msg.type in ("error", "warning") else None)
            page.on("pageerror", lambda err: log.error("[BROLL-PAGEERROR] %s", err))
            _t_load_start = time.time()
            try:
                await page.goto(
                    f"file://{html_path.absolute()}",
                    wait_until="load",
                    timeout=30000,
                )
            except Exception as exc:
                log.warning("[RENDER] Page load warning (continuing): %s", exc)

            # Ensure GSAP is loaded before doing anything
            try:
                await page.wait_for_function("() => !!window.gsap", timeout=3000)
            except Exception as exc:
                log.warning("[RENDER] GSAP wait_for_function: %s", exc)

            # Reset GSAP to t=0 so all delayedCall animations sync with recording start
            try:
                await page.evaluate("""() => {
                    if (window.gsap) {
                        gsap.globalTimeline.pause();
                        gsap.globalTimeline.seek(0);
                        gsap.globalTimeline.play();
                    }
                }""")
                log.info("[RENDER] GSAP globalTimeline reset to t=0 and playing")
            except Exception as exc:
                log.warning("[RENDER] GSAP reset failed: %s", exc)

            # Enhanced DOM check — includes tween count to verify animations registered
            try:
                debug = await page.evaluate("""() => ({
                    gsap: !!window.gsap,
                    scenes: document.querySelectorAll('.scene').length,
                    s0opacity: (document.getElementById('scene0') || {}).style?.opacity || 'css',
                    gsapTime: window.gsap ? gsap.globalTimeline.time().toFixed(3) : 'n/a',
                    tweens: window.gsap ? gsap.globalTimeline.getChildren(true,true,true).length : 0
                })""")
                log.info("[RENDER] DOM check: %s", debug)
            except Exception as exc:
                log.warning("[RENDER] evaluate: %s", exc)

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
            "-c:v", "libx264", "-crf", "20", "-preset", "medium",
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
def _resolve_sfx_asset(ev: dict) -> Optional[str]:
    """Map an event to a known asset_id. Accepts {asset}, {category,asset} or legacy {type}."""
    a = ev.get("asset")
    if a in SFX_LIBRARY:
        return a
    return SFX_LEGACY_MAP.get(ev.get("type"))


def _ensure_sfx(asset_id: str) -> bool:
    """Guarantee the asset file is local — just-in-time download if a boot fetch was
    missed. Prevents a transient download miss from silently dropping a layer."""
    p = sfx_local_path(asset_id)
    if p.exists() and p.stat().st_size > 0:
        return True
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(SFX_LIBRARY[asset_id][1], timeout=30)
        r.raise_for_status()
        p.write_bytes(r.content)
        log.info("[SFX] JIT-fetched %s", asset_id)
        return True
    except Exception as exc:
        log.warning("[SFX] JIT fetch failed [%s]: %s", asset_id, exc)
        return False


def _normalize_sfx_events(impacts: list, duration: float) -> list:
    """Build the full layered event list: deterministic t=0 intro + resolved triggers."""
    events = [{"asset": a, "time": 0.0} for a in SFX_INTRO_ASSETS if a in SFX_LIBRARY]
    for ev in (impacts or []):
        if ev.get("time") is None:
            continue
        asset = _resolve_sfx_asset(ev)
        t = float(ev["time"])
        if asset and 0.0 <= t < duration:
            events.append({"asset": asset, "time": t})
    # guarantee each asset is present (JIT download); drop only on hard failure
    events = [e for e in events if _ensure_sfx(e["asset"])]
    intro = [e["asset"] for e in events if e["time"] == 0.0]
    log.info("[SFX] intro layer @ t=0: %s", "+".join(intro) or "NONE")
    return events


def mix_sfx_into_video(video: Path, impacts: list, job_dir: Path, duration: float):
    """Layer the SFX library into the video per category rules. Returns Path or None."""
    events = _normalize_sfx_events(impacts, duration)
    if not events:
        return None

    inputs       = ["-i", str(video)]
    filter_parts = []

    for idx, ev in enumerate(events):
        asset    = ev["asset"]
        cat      = SFX_LIBRARY[asset][0]
        rule     = SFX_CATEGORY_RULES.get(cat, {"volume": 0.8, "trim": None, "fadeout": 0.0})
        delay_ms = max(int(ev["time"] * 1000), 0)
        inputs  += ["-i", str(sfx_local_path(asset))]

        chain = [f"[{idx+1}:a]"]
        trim    = rule.get("trim")
        fadeout = rule.get("fadeout") or 0.0
        if trim:
            chain.append(f"atrim=0:{trim:.3f},asetpts=PTS-STARTPTS")
            if fadeout > 0:
                st = max(trim - fadeout, 0.0)
                chain.append(f"afade=t=out:st={st:.3f}:d={fadeout:.3f}")
        chain.append(f"volume={rule['volume']:.3f}")
        chain.append(f"adelay={delay_ms}|{delay_ms}")
        filter_parts.append("".join([chain[0], ",".join(chain[1:])]) + f"[sfx{idx}]")

    n          = len(events)
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

    cats = ", ".join(sorted({SFX_LIBRARY[e['asset']][0] for e in events}))
    log.info("[SFX] Mixed %d sounds (%s)", n, cats)
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
            "unsharp=3:3:0.4:3:3:0"
        ),
        "-c:v", "libx264", "-crf", "20", "-preset", "medium",
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
            "-an", "-c:v", "libx264", "-crf", "20", "-preset", "medium", out
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
            "-an", "-c:v", "libx264", "-crf", "20", "-preset", "medium", looped
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
        "-c:v", "libx264", "-crf", "20", "-preset", "medium", concat_out
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


_TRANSCRIPT_CACHE: dict = {}   # md5(audio)+prompt -> words; dedupes Whisper across endpoints (detect-impacts + render hit the same trimmed audio)


def _whisperx_words(audio_path: Path) -> Optional[list]:
    """Precise word-level timestamps via Replicate WhisperX (wav2vec2 align, ~10ms).
    Returns [{word,start,end}] or None on any failure (caller falls back to whisper-1)."""
    if not REPLICATE_API_TOKEN:
        return None
    try:
        import base64
        with open(audio_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        data_uri = "data:audio/mpeg;base64," + b64
        r = requests.post(
            "https://api.replicate.com/v1/models/victor-upmeet/whisperx/predictions",
            headers={"Authorization": "Token " + REPLICATE_API_TOKEN,
                     "Content-Type": "application/json", "Prefer": "wait"},
            json={"input": {"audio_file": data_uri, "language": "de",
                            "align_output": True, "batch_size": 16}},
            timeout=180)
        r.raise_for_status()
        out = (r.json() or {}).get("output") or {}
        segs = out.get("segments") or []
        words = []
        for s in segs:
            for w in (s.get("words") or []):
                txt = str(w.get("word", "")).strip()
                st, en = w.get("start"), w.get("end")
                if txt and st is not None and en is not None:
                    words.append({"word": txt, "start": float(st), "end": float(en)})
        if words:
            log.info("[WHISPERX] %d words (precise align)", len(words))
            return words
        log.warning("[WHISPERX] no words in output — falling back")
        return None
    except Exception as exc:
        log.warning("[WHISPERX] failed (%s) — falling back to whisper-1", exc)
        return None


def transcribe_audio(video_path: Path, prompt: str = "") -> list:
    """Returns list of {word, start, end} or [] on failure.
    `prompt` biases Whisper to KEEP disfluencies (äh/ähm) instead of cleaning them up —
    used by the filler-word trimmer. Leave empty for normal clean transcription.
    Result is cached by audio-content hash so the same clip is transcribed only once."""
    try:
        log.info("Extracting audio for Whisper …")
        audio_path = video_path.parent / "audio.mp3"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path),
            "-vn", "-ar", "16000", "-ac", "1", "-b:a", "64k",
            str(audio_path),
        ], check=True, capture_output=True)

        try:
            with open(audio_path, "rb") as _af:
                ckey = hashlib.md5(_af.read()).hexdigest() + "|" + (prompt or "")
        except Exception:
            ckey = None
        if ckey and ckey in _TRANSCRIPT_CACHE:
            log.info("Whisper cache hit — skipping API call")
            return _TRANSCRIPT_CACHE[ckey]

        # No filler-prompt = clean pass (captions / cuts / dead-air) -> use precise WhisperX.
        # Filler-prompt passes stay on whisper-1 (it honours the prompt to KEEP äh/ähm).
        if not prompt:
            wx = _whisperx_words(audio_path)
            if wx:
                if ckey:
                    _TRANSCRIPT_CACHE[ckey] = wx
                return wx

        log.info("Calling Whisper API …")
        kwargs = {
            "model": "whisper-1",
            "response_format": "verbose_json",
            "timestamp_granularities": ["word"],
        }
        if prompt:
            kwargs["prompt"] = prompt
        with open(audio_path, "rb") as af:
            resp = openai_client.audio.transcriptions.create(file=af, **kwargs)
        words = []
        for w in (resp.words or []):
            words.append({"word": w.word.strip(), "start": w.start, "end": w.end})
        log.info("Whisper returned %d words", len(words))
        if ckey:
            _TRANSCRIPT_CACHE[ckey] = words
        return words
    except Exception as exc:
        log.error("Whisper transcription failed: %s", exc)
        return []


# ── Deterministic max-gap silence trimmer ─────────────────────────────────────
def _compute_keep_segments(words: list, duration: float,
                           max_gap: float = 0.30, pad: float = 0.05) -> list:
    """Max-Gap rule on Whisper word timestamps. Gaps <= max_gap stay (natural cadence);
    gaps > max_gap keep only `pad` after the previous word and `pad` before the next,
    the dead-air between is discarded. Leading/trailing dead-air trimmed the same way.
    Returns the list of (start, end) intervals to KEEP."""
    if not words:
        return [(0.0, duration)]
    removes = []
    first = float(words[0]["start"])
    if first > max_gap:
        removes.append((0.0, max(0.0, first - pad)))
    for i in range(len(words) - 1):
        e  = float(words[i]["end"])
        s2 = float(words[i + 1]["start"])
        if s2 - e > max_gap:
            a, b = e + pad, s2 - pad
            if b > a:
                removes.append((a, b))
    last = float(words[-1]["end"])
    if duration - last > max_gap:
        removes.append((min(last + pad, duration), duration))

    keeps, cursor = [], 0.0
    for a, b in removes:
        if a > cursor:
            keeps.append((round(cursor, 3), round(a, 3)))
        cursor = max(cursor, b)
    if cursor < duration:
        keeps.append((round(cursor, 3), round(duration, 3)))
    return [(s, e) for s, e in keeps if e - s > 0.02]


def _trim_with_crossfade(src: Path, keeps: list, out_path: Path, d: float = 0.08) -> bool:
    """Smooth pro joins: each keep-segment cross-faded into the next (video xfade +
    audio acrossfade, SAME duration each join -> A and V shrink equally -> stays in sync).
    No abrupt jump-cuts. Returns False if it can't build a valid graph (caller falls back)."""
    n = len(keeps)
    if n < 2:
        return False
    parts = []
    lengths = []
    for i, (s, e) in enumerate(keeps):
        ln = e - s
        lengths.append(ln)
        parts.append(f"[0:v]trim={s:.3f}:{e:.3f},setpts=PTS-STARTPTS,fps={FPS},setsar=1,format=yuv420p[v{i}];")
        parts.append(f"[0:a]atrim={s:.3f}:{e:.3f},asetpts=PTS-STARTPTS[a{i}];")
    cur_v, cur_a, cum = "v0", "a0", lengths[0]
    for i in range(1, n):
        dd = min(d, lengths[i] * 0.45, lengths[i - 1] * 0.45, max(0.02, cum * 0.45))
        if dd < 0.02:
            return False
        off = max(0.0, cum - dd)
        parts.append(f"[{cur_v}][v{i}]xfade=transition=fade:duration={dd:.3f}:offset={off:.3f}[vx{i}];")
        parts.append(f"[{cur_a}][a{i}]acrossfade=d={dd:.3f}[ax{i}];")
        cur_v, cur_a = f"vx{i}", f"ax{i}"
        cum = cum + lengths[i] - dd
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-filter_complex", "".join(parts),
        "-map", f"[{cur_v}]", "-map", f"[{cur_a}]",
        "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out_path.exists():
        log.warning("[TRIM] crossfade failed, falling back to hard cut: %s", (r.stderr or "")[-500:])
        return False
    return True


def _trim_dead_air(src: Path, keeps: list, out_path: Path, edge_fade: float = 0.03) -> bool:
    """Smooth pro cut via crossfades; falls back to hard concat (+declick) if that fails."""
    if not keeps:
        return False
    if _trim_with_crossfade(src, keeps, out_path):
        return True
    parts, concat_in = [], []
    for i, (s, e) in enumerate(keeps):
        seg = e - s
        parts.append(f"[0:v]trim={s:.3f}:{e:.3f},setpts=PTS-STARTPTS,fps={FPS},setsar=1[v{i}];")
        af = f"[0:a]atrim={s:.3f}:{e:.3f},asetpts=PTS-STARTPTS"
        if seg > 2 * edge_fade + 0.01:
            af += (f",afade=t=in:st=0:d={edge_fade:.3f}"
                   f",afade=t=out:st={seg - edge_fade:.3f}:d={edge_fade:.3f}")
        parts.append(af + f"[a{i}];")
        concat_in.append(f"[v{i}][a{i}]")
    n = len(keeps)
    parts.append(f"{''.join(concat_in)}concat=n={n}:v=1:a=1[vout][aout]")
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-filter_complex", "".join(parts),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("[TRIM] ffmpeg failed: %s", result.stderr[-1200:])
        return False
    return True


# ── Smart coherence cut: remove duplicate takes / false starts via LLM ────────
COHERENCE_SYS = (
    "Du bekommst ein Wort-fuer-Wort-Transkript (je Zeile: INDEX<TAB>WORT) einer Facecam-Aufnahme. "
    "Der Sprecher nimmt EINEN Gedanken oft in MEHREREN Anlaeufen auf: verhaspelt sich, bricht ab, "
    "setzt neu an, wiederholt denselben Satz/Satzanfang, formuliert denselben Inhalt nochmal um. "
    "Deine Aufgabe: entferne ALLE Fehlstarts, Abbrueche, doppelten Takes und Wiederholungen, sodass "
    "ein MAKELLOSER Take bleibt - aber so dass die Schnitte SAUBER und unhoerbar wirken.\n"
    "GRUENDLICH schneiden:\n"
    "- JEDE Wiederholung desselben Gedankens/Satzes/Satzanfangs -> behalte NUR den besten (meist letzten) Take, alle anderen Anlaeufe KOMPLETT raus.\n"
    "- JEDEN Fehlstart/Abbruch/Selbstkorrektur ('also- nein', '...ich mein') als GANZE Einheit raus.\n"
    "EISERNE SAUBERKEITS-REGEL (sonst klingt's zerhackt):\n"
    "- Schneide AUSSCHLIESSLICH an SATZ-/PHRASEN-Grenzen. Ein Entfernungs-Bereich MUSS an einer natuerlichen Pause beginnen und enden.\n"
    "- NIE mitten in einem zusammenhaengenden Satz ein paar Woerter rausschneiden. Entweder der ganze Anlauf weg oder gar nicht.\n"
    "- Das erste behaltene Wort nach einem Schnitt muss ein SATZANFANG sein, nicht ein Satz-Mittelstueck.\n"
    'OUTPUT NUR JSON: {"remove": [[startIdx, endIdx], ...]} - Indizes inklusive, auf die Wort-Indizes '
    'bezogen, aufsteigend, ohne Ueberlappung. Nichts zu entfernen -> {"remove": []}.'
)


def _coherence_keep_segments(words: list, duration: float, pad: float = 0.12):
    """LLM analysiert das Wort-Transkript und entfernt doppelte Takes / Fehlstarts.
    Returns (keeps, n_removed_words). Faellt sicher auf 'alles behalten' zurueck."""
    if not words or len(words) < 8:
        return [(0.0, duration)], 0
    transcript = "\n".join(f"{i}\t{w.get('word','')}" for i, w in enumerate(words))
    try:
        raw = call_openrouter(COHERENCE_SYS, transcript,
                              model="anthropic/claude-sonnet-4.5", max_tokens=1500)
        m = re.search(r"\{[\s\S]*\}", raw)
        remove = json.loads(m.group(0)).get("remove", []) if m else []
    except Exception as exc:
        log.warning("[SMART] coherence analysis failed: %s", exc)
        return [(0.0, duration)], 0
    n = len(words)
    removes, removed_words = [], 0
    for pair in remove:
        try:
            a, b = int(pair[0]), int(pair[1])
        except Exception:
            continue
        a, b = max(0, a), min(n - 1, b)
        if b < a:
            continue
        s = max(0.0, float(words[a]["start"]) - pad)
        e = min(duration, float(words[b]["end"]) + pad)
        if e > s:
            removes.append((s, e)); removed_words += (b - a + 1)
    if not removes:
        return [(0.0, duration)], 0
    removes.sort()
    keeps, cursor = [], 0.0
    for a, b in removes:
        if a > cursor:
            keeps.append((round(cursor, 3), round(a, 3)))
        cursor = max(cursor, b)
    if cursor < duration:
        keeps.append((round(cursor, 3), round(duration, 3)))
    keeps = [(s, e) for s, e in keeps if e - s > 0.05]
    return (keeps or [(0.0, duration)]), removed_words


# Force Whisper to KEEP fillers so we can cut them deterministically.
WHISPER_FILLER_PROMPT = "Äh, hallo. Ähm, in diesem Video geht es um Deep-Tech. Öhm, ja genau."

# ONLY pure acoustic fillers — never contextual words (halt, so, ja).
FILLER_WORDS = {"äh", "ähm", "öhm", "öh", "ähem", "uh", "uhm", "hm", "hmm", "äm", "em"}


def _filler_keep_segments(words: list, duration: float, pad: float = 0.02) -> list:
    """Keep-segments with pure acoustic filler words ('äh'/'ähm'/...) removed.
    Matching is normalized (lowercase, punctuation/ellipsis stripped)."""
    removes = []
    for w in words:
        tok = re.sub(r"[^\wäöü]", "", str(w.get("word", "")).lower())
        if tok in FILLER_WORDS:
            a, b = max(0.0, float(w["start"]) - pad), min(duration, float(w["end"]) + pad)
            if b > a:
                removes.append((a, b))
    if not removes:
        return [(0.0, duration)], 0
    removes.sort()
    keeps, cursor = [], 0.0
    for a, b in removes:
        if a > cursor:
            keeps.append((round(cursor, 3), round(a, 3)))
        cursor = max(cursor, b)
    if cursor < duration:
        keeps.append((round(cursor, 3), round(duration, 3)))
    return [(s, e) for s, e in keeps if e - s > 0.02], len(removes)


def _run_auto_editor(input_path: Path, output_path: Path) -> bool:
    """Phase 2: waveform dead-air trim. Returns True on success, False to fall back."""
    cmd = [
        "auto-editor", str(input_path),
        "--edit", "audio:-35dB",   # noise-floor threshold
        "--margin", "0.12s",       # 120ms safety padding so words aren't shaved
        "--no-open",
        "--output", str(output_path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except Exception as exc:
        log.warning("[TRIM] auto-editor not runnable: %s", exc)
        return False
    if r.returncode != 0 or not output_path.exists():
        log.warning("[TRIM] auto-editor failed (rc=%s): %s", r.returncode, (r.stderr or "")[-600:])
        return False
    return True


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


# Justus default caption style; a client template can override fill/strokes/size.
_CAP_DEFAULT = {
    "mode": "stroke",     # "stroke" = Justus 3-layer; "perword" = per-word color/font (Tim)
    "fill": (255, 255, 255, 255),
    "strokes": [(109, 40, 217, 100), (139, 92, 246, 160), (124, 58, 237, 230)],
    "size": CAPTION_FONT_SIZE,
    "font_path": str(FONT_BLACK),
}

_FONT_MAP = {"montserrat": FONT_BLACK, "oswald": FONT_OSWALD, "inter": FONT_INTER, "caveat": FONT_CAVEAT, "anton": FONT_ANTON, "playfair": FONT_PLAYFAIR}


def _caption_style(tpl: dict) -> dict:
    """Resolve caption style. Default = Justus 3-stroke. mode='perword' enables
    per-word color+font classes (base/punch/data/anecdote) for premium clients (Tim)."""
    cap  = (tpl or {}).get("captions") or {}
    cols = _tpl_colors(tpl)
    st   = dict(_CAP_DEFAULT)
    if cols.get("caption_fill"):
        st["fill"] = (*_hex_rgb(cols["caption_fill"], (255, 255, 255)), 255)
    strokes = cols.get("caption_strokes")
    if isinstance(strokes, list) and len(strokes) == 3:
        alphas = [100, 160, 230]
        st["strokes"] = [(*_hex_rgb(strokes[i]), alphas[i]) for i in range(3)]
    if cap.get("size"):
        st["size"] = int(cap["size"])
    if cap.get("mode") in ("perword", "danilo", "karaoke"):
        st["mode"] = cap["mode"]
        wc = cap.get("colors") or {}
        st["colors"] = {k: (*_hex_rgb(wc.get(k), (245, 242, 236)), 255)
                        for k in ("base", "punch", "data", "anecdote")}
        st["colors"]["inactive"] = (*_hex_rgb(wc.get("inactive"), (110, 110, 115)), 255)  # karaoke muted slate #6E6E73
        wf = cap.get("fonts") or {}
        st["fonts"] = {k: _FONT_MAP.get(wf.get(k), FONT_INTER) for k in ("base", "punch", "cursive")}
        st["shadow"] = (*_hex_rgb(cap.get("shadow"), (6, 18, 15)), 200)
        st["position"] = cap.get("position", "lowerthird")
    return st


def _classify_caption_words(words: list) -> list:
    """Tag each word base/punch/data/anecdote. Numbers->data (regex). punch/anecdote via Haiku."""
    classes = {}
    for i, w in enumerate(words):
        if re.search(r"\d", str(w.get("word", ""))):
            classes[i] = "data"
    try:
        wl = [{"i": i, "w": w["word"]} for i, w in enumerate(words)]
        sys_p = (
            "Classify caption words for a warm, authentic finance/life creator. Return word indices "
            "that are 'punch' (the FEW hardest-hitting emotional-trigger / key-claim words — keep sparse) "
            "or 'anecdote' (words inside a personal-story phrase, e.g. a memory or 'aus meinem Bollerwagen'). "
            "Everything else is base (don't return). Return ONLY JSON {\"punch\":[i],\"anecdote\":[i]}"
        )
        raw = call_openrouter(sys_p, json.dumps(wl), model=CHEAP_MODEL, max_tokens=500)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        d = json.loads(m.group()) if m else {}
        for i in d.get("anecdote", []):
            classes.setdefault(int(i), "anecdote")
        for i in d.get("punch", []):
            classes.setdefault(int(i), "punch")
    except Exception as exc:
        log.warning("[CAP] word classify failed: %s", exc)
    out = []
    for i, w in enumerate(words):
        w2 = dict(w); w2["cls"] = classes.get(i, "base"); out.append(w2)
    return out


def _draw_caption_frame(img: Image.Image, word: str, scale: float = 1.0,
                        style: dict = None, cls: str = "base", cx: int = None, cy: int = None):
    style = style or _CAP_DEFAULT
    draw  = ImageDraw.Draw(img)

    if style.get("mode") == "perword":
        size = int(style["size"] * scale)
        if cls == "punch":
            font_path = style["fonts"]["punch"]; color = style["colors"]["punch"]; txt = word.upper()
        elif cls == "data":
            font_path = style["fonts"]["base"];  color = style["colors"]["data"];  txt = word
        elif cls == "anecdote":
            font_path = style["fonts"]["cursive"]; color = style["colors"]["anecdote"]; txt = word
        else:
            font_path = style["fonts"]["base"];  color = style["colors"]["base"];  txt = word
        font, asz = _fit_font(txt, font_path, size)
        bbox = draw.textbbox((0, 0), txt, font=font, stroke_width=0)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (cx if cx is not None else W // 2) - tw // 2 - bbox[0]
        y = (cy if cy is not None else 1350) - th // 2 - bbox[1]
        sh = style["shadow"]
        for dx, dy in ((3, 3), (2, 2), (-2, 2), (2, -2)):   # diffuse drop shadow
            draw.text((x + dx, y + dy), txt, font=font, fill=sh)
        draw.text((x, y), txt, font=font, fill=color)
        return

    # default Justus 3-stroke
    font, actual_size = get_adaptive_font(word, max_size=int(style["size"] * scale))
    bbox = draw.textbbox((0, 0), word, font=font, stroke_width=0)
    tw   = bbox[2] - bbox[0]; th = bbox[3] - bbox[1]
    x    = (W - tw) // 2 - bbox[0]
    y    = (DIVIDER_H - th) // 2 - bbox[1]
    outer_stroke = max(8, int(actual_size * 0.17))
    mid_stroke   = max(5, int(actual_size * 0.09))
    inner_stroke = max(3, int(actual_size * 0.04))
    s0, s1, s2 = style["strokes"]
    draw.text((x, y), word, font=font, fill=(0, 0, 0, 0), stroke_fill=s0, stroke_width=outer_stroke)
    draw.text((x, y), word, font=font, fill=(0, 0, 0, 0), stroke_fill=s1, stroke_width=mid_stroke)
    draw.text((x, y), word, font=font, fill=style["fill"], stroke_fill=s2, stroke_width=inner_stroke)


def _fit_font(word: str, font_path, max_size: int, min_size: int = 44, max_width: int = W - 120):
    size = max_size
    while size >= min_size:
        f = ImageFont.truetype(str(font_path), size)
        b = f.getbbox(word)
        if (b[2] - b[0]) <= max_width:
            return f, size
        size -= 4
    return ImageFont.truetype(str(font_path), min_size), min_size


def _danilo_chunk_ids(words: list, max_words: int = 5) -> list:
    """Group words into half-sentence chunks: break on sentence punctuation or max_words."""
    ids, cid, count = [], 0, 0
    for w in words:
        ids.append(cid)
        count += 1
        tok = str(w.get("word", "")).strip()
        if re.search(r"[.,!?;:]$", tok) or count >= max_words:
            cid += 1; count = 0
    return ids


def _premium_chunk_ids(words: list, max_words: int = 3, long_len: int = 7) -> list:
    """Long-Word-Isolation chunker (karaoke): a word with pure length > long_len gets its
    OWN box; punctuation closes a box; otherwise max_words per box. Returns chunk-id/word."""
    def pure(w: str) -> int:
        return len(re.sub(r"[.,/#!$%^&*;:{}=\-_`~()?]", "", w))
    ids, cid, count = [], 0, 0
    for i, w in enumerate(words):
        tok   = str(w.get("word", "")).strip()
        plen  = pure(tok)
        islong = plen > long_len
        # current word long but box already filling -> close old box first so long word is isolated
        if islong and count > 0:
            cid += 1; count = 0
        ids.append(cid); count += 1
        split = bool(re.search(r"[.,!?;:]", tok)) or islong
        if not split and i + 1 < len(words):
            if pure(str(words[i + 1].get("word", "")).strip()) > long_len:
                split = True            # next word long -> break now so it starts fresh
        if count >= max_words:
            split = True
        if split:
            cid += 1; count = 0
    return ids


def _draw_danilo_frame(img: Image.Image, reveal: list, style: dict, cy: int):
    """Danilo-style (exact): half-sentence builds word-by-word, key words much bigger
    INLINE, pure white, tight -0.05em kerning, tight line-height, soft diffuse drop
    shadow (no hard outline), German capitalization kept (no all-caps)."""
    draw = ImageDraw.Draw(img)
    S          = int(style.get("size", 90))
    small_size = int(S * 0.82)            # ~7vw filler words
    big_size   = int(S * 1.55)            # ~13vw key words
    font_path  = style["fonts"]["base"]   # Inter Black recommended
    white      = style["colors"].get("base", "#FFFFFF")
    sc         = style.get("shadow", (0, 0, 0, 200))
    shadow     = (sc[0], sc[1], sc[2], 70) if isinstance(sc, (list, tuple)) and len(sc) >= 3 else (0, 0, 0, 70)
    max_w      = W - 120
    word_gap   = int(small_size * 0.18)
    sh_offsets = [(0, 6), (0, 4), (4, 5), (-4, 5), (3, 3), (-3, 3)]   # fake soft/diffuse shadow

    def is_punch(w):
        if w.get("cls") == "punch":
            return True
        t = re.sub(r"[^\wäöüÄÖÜß]", "", str(w.get("word", "")))
        return bool(t) and t[0].isupper() and len(t) > 5

    def kwidth(txt, font, tr):
        return (sum(draw.textlength(c, font=font) + tr for c in txt) - tr) if txt else 0

    def kdraw(txt, font, x, ymid, fill, tr):
        for c in txt:
            draw.text((x, ymid), c, font=font, fill=fill, anchor="lm")
            x += draw.textlength(c, font=font) + tr

    toks = []
    for w in reveal:
        txt = str(w.get("word", "")).strip()
        if not txt:
            continue
        sz = big_size if is_punch(w) else small_size
        f  = ImageFont.truetype(str(font_path), sz)
        tr = -int(sz * 0.05)              # -0.05em kerning
        wpx = kwidth(txt, f, tr)
        while wpx > max_w and sz > 34:    # auto-shrink so a long word never clips out of 9:16
            sz -= 6
            f  = ImageFont.truetype(str(font_path), sz)
            tr = -int(sz * 0.05)
            wpx = kwidth(txt, f, tr)
        toks.append({"txt": txt, "f": f, "tr": tr, "w": wpx, "size": sz})
    if not toks:
        return

    lines, cur, curw = [], [], 0.0
    for t in toks:
        add = t["w"] + (word_gap if cur else 0)
        if cur and curw + add > max_w:
            lines.append(cur); cur = [t]; curw = t["w"]
        else:
            cur.append(t); curw += add
    if cur:
        lines.append(cur)

    line_hs = [max(t["size"] for t in ln) * 1.0 for ln in lines]   # tight line-height
    y = cy - sum(line_hs) / 2
    for ln, lh in zip(lines, line_hs):
        lw  = sum(t["w"] for t in ln) + word_gap * (len(ln) - 1)
        x0  = (W - lw) / 2
        cyl = y + lh / 2
        x = x0                                  # soft shadow pass
        for t in ln:
            for dx, dy in sh_offsets:
                kdraw(t["txt"], t["f"], x + dx, cyl + dy, shadow, t["tr"])
            x += t["w"] + word_gap
        x = x0                                  # crisp white pass
        for t in ln:
            kdraw(t["txt"], t["f"], x, cyl, white, t["tr"])
            x += t["w"] + word_gap
        y += lh


def _karaoke_font(font_path, size: int):
    f = ImageFont.truetype(str(font_path), size)
    try:
        f.set_variation_by_axes([700])    # Playfair Display variable -> weight 700 (no-op/raises for static)
    except Exception:
        pass
    return f


def _draw_karaoke_frame(img: Image.Image, items: list, style: dict, cy: int):
    """Karaoke black-box: pitch-black rounded box (fit-content) around a 1-3 word phrase,
    active (currently spoken) word pure white, inactive words muted gray. Hard cut, no
    animation. items = [(word_obj, is_active)]. German caps kept. ABSOLUTE anti-overflow:
    box can never exceed the 9:16 safe zone (10vw walls each side) — shrinks font if needed."""
    draw = ImageDraw.Draw(img)
    font_path = style["fonts"]["base"]
    active   = style["colors"].get("base", (255, 255, 255, 255))
    inactive = style["colors"].get("inactive", (110, 110, 115, 255))   # solid #6E6E73 luxury slate, not transparent
    max_box  = int(W * 0.80)              # 10vw invisible wall on each side

    texts = [str(w.get("word", "")).strip() for w, _ in items if str(w.get("word", "")).strip()]
    flags = [is_a for w, is_a in items if str(w.get("word", "")).strip()]
    if not texts:
        return

    size = int(style.get("size", 90) * 0.85)
    while size > 28:
        f_act = _karaoke_font(font_path, int(size * 1.12))   # active word emphasised (+12%)
        f_in  = _karaoke_font(font_path, size)
        gap  = int(size * 0.30)
        padx = int(size * 0.55)
        widths = [draw.textlength(t, font=(f_act if a else f_in)) for t, a in zip(texts, flags)]
        box_w  = sum(widths) + gap * (len(texts) - 1) + 2 * padx
        if box_w <= max_box:
            break
        size -= 4

    act_h = int(size * 1.12)
    pady  = int(size * 0.38)
    box_h = act_h + 2 * pady
    bx = (W - box_w) // 2
    by = int(cy - box_h / 2)
    draw.rounded_rectangle([bx, by, bx + box_w, by + box_h],
                           radius=int(size * 0.14), fill=(0, 0, 0, 235))
    x = bx + padx
    mid = by + box_h / 2
    for t, wpx, is_a in zip(texts, widths, flags):
        draw.text((x, mid), t, font=(f_act if is_a else f_in),
                  fill=(active if is_a else inactive), anchor="lm")
        x += wpx + gap


def build_caption_frames(words: list, total_frames: int,
                         cap_dir: Path, gradient_base: Path, style: dict = None,
                         lift_ranges: list = None):
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

    mode    = (style or {}).get("mode")
    danilo  = (mode == "danilo")
    perword = (mode == "perword")
    karaoke = (mode == "karaoke")
    KARA_RED    = int(H * 0.80)   # default: deep lower third
    KARA_YELLOW = int(H * 0.12)   # lifted to top while an image/video insert is on screen
    if perword or danilo or karaoke:
        cy = KARA_RED if karaoke else (1350 if (style or {}).get("position", "lowerthird") == "lowerthird" else H // 2)
        blank = Image.new("RGBA", (W, H), (0, 0, 0, 0))   # full-frame transparent (Tim fullcam)
    else:
        blank = Image.open(str(gradient_base)).convert("RGBA")

    chunk_id = chunks = frame_cid = chunk_cy = None
    if danilo:
        chunk_id = _danilo_chunk_ids(words)
        chunks = {}
        for j, c in enumerate(chunk_id):
            chunks.setdefault(c, []).append(j)
    if karaoke:
        chunk_id = _premium_chunk_ids(words, max_words=3, long_len=7)
        chunks = {}
        for j, c in enumerate(chunk_id):
            chunks.setdefault(c, []).append(j)
        ranges = lift_ranges or []
        frame_cid, chunk_cy = {}, {}
        first_cid = min(chunks) if chunks else 0
        for cid, members in chunks.items():
            cs = words[members[0]]["start"]
            ce = words[members[-1]]["end"]
            # lift this phrase to the top if it overlaps any image/video insert
            lifted = any(cs < r_end and ce > r_start for (r_start, r_end) in ranges)
            chunk_cy[cid] = KARA_YELLOW if lifted else KARA_RED
            sf = 0 if cid == first_cid else int(cs * FPS)   # first phrase visible from second 0
            for fr in range(sf, min(int(ce * FPS) + 1, total_frames)):
                frame_cid[fr] = cid

    for frame_n in range(total_frames):
        img = blank.copy()
        if karaoke:
            cid = frame_cid.get(frame_n)
            if cid is not None:
                active = word_at_frame.get(frame_n, -1)
                items = [(words[j], j == active) for j in chunks[cid]]
                _draw_karaoke_frame(img, items, style, chunk_cy[cid])
        else:
            wi = word_at_frame.get(frame_n, -1)
            if wi >= 0:
                if danilo:
                    members = chunks.get(chunk_id[wi], [wi])
                    reveal  = [words[j] for j in members if j <= wi]
                    _draw_danilo_frame(img, reveal, style, cy)
                else:
                    word = words[wi]["word"]
                    # smooth ease-out pop-in: 1.16 -> 1.0 over first ~5 frames of each word
                    fsw = frame_n - int(words[wi]["start"] * FPS)
                    scale = 1.0 + 0.16 * max(0.0, 1.0 - (fsw / 5.0))
                    if perword:
                        _draw_caption_frame(img, word, scale, style, cls=words[wi].get("cls", "base"), cy=cy)
                    else:
                        _draw_caption_frame(img, word, scale, style)

        out = cap_dir / f"frame_{frame_n:06d}.png"
        img.save(str(out))

    log.info("Caption frames done")


def build_progress_frames(total_frames: int, prog_dir: Path, colors: tuple = None):
    left, right = colors or (AMETHYST, SILVER)
    prog_dir.mkdir(parents=True, exist_ok=True)
    log.info("Rendering %d progress frames …", total_frames)
    for frame_n in range(total_frames):
        img  = Image.new("RGBA", (W, PROGRESS_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        fw   = int((frame_n / max(total_frames - 1, 1)) * W)
        if fw > 0:
            for x in range(fw):
                t = x / (W - 1)
                r = int(left[0] + t * (right[0] - left[0]))
                g = int(left[1] + t * (right[1] - left[1]))
                b = int(left[2] + t * (right[2] - left[2]))
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
def _call_fal_thumbnail(concept: str, accent: str, bg: str = "#12101a",
                        glow_word: str = "amethyst purple", vibe: str = None) -> str:
    """Generate a thumbnail via fal.ai nano-banana-pro. Returns image URL.
    bg/glow_word/vibe are per-client (template); defaults = Justus tech look."""
    if not FAL_API_KEY:
        raise RuntimeError("FAL_API_KEY not set")

    vibe = vibe or (
        "Premium minimal tech thumbnail for a social media video. "
        "Cinematic studio lighting, premium 3D render aesthetic, ultra clean, sharp focus, "
        "high-end product photography style like an Apple keynote reveal. "
        "Sophisticated, minimalist, expensive-looking."
    )
    negative = (
        "cluttered, busy, multiple objects, text, letters, words, watermark, "
        "logo, oversaturated, neon overload, rainbow colors, cartoonish, anime, "
        "low quality, blurry, generic stock photo, chaotic background, messy, "
        "people, faces, hands, distorted, ugly, amateur"
    )
    prompt = (
        f"{vibe} "
        f"Hero subject: {concept}. "
        "Single hero object, centered composition, lots of empty negative space around it. "
        f"Deep dark background ({bg}). "
        f"The ONLY light source is a soft {glow_word} glow ({accent}) rimming the object. "
        f"Subtle, not oversaturated. "
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


# ── Image prompt enrichment (3-layer brand pipeline) ──────────────────────────
# Global, script-aware visual enricher — drives photorealistic hardware cutaways.
GLOBAL_VISUAL_ENRICHER_PROMPT = """You are the Lead Visual Designer for an elite tech channel. Read the ENTIRE script below, digest its global narrative atmosphere ("Deep-Tech Thriller / Silicon Valley Industrial Espionage / High-End Medical Pivot"), and generate hyper-realistic, concrete image prompts for the requested keyword moments.

CRITICAL VISUAL RULES:
1. STRICTLY NO ABSTRACT ELEMENTS: never glowing orbs, floating glass spheres, neon cubes, or generic digital hologram networks.
2. PHOTOREALISM & PREMIUM HARDWARE: every image must look like a physical, tangible photograph — matte industrial design, brushed titanium, anodized aluminium, high-end medical equipment designed like Apple, clean lab environments, or high-tech manufacturing plants.
3. BRAND COLORS: dark cinematic studio backgrounds with razor-sharp rim-lighting in Amethyst (#8B5CF6) and Cyan (#06B6D4).
4. CAMERA SPECIFICATIONS: append advanced cinematic camera config to every prompt (e.g. "shot on 35mm anamorphic lens, Arri Alexa, cinematic volumetric lighting, sharp focus on hardware textures, shallow depth of field, corporate thriller aesthetic, 8k").

GLOBAL SCRIPT CONTEXT:
{INSERT_FULL_VIDEO_SCRIPT_HERE}

OUTPUT FORMAT: a highly descriptive, concrete, hardware-focused prompt for each requested keyword that directly visualizes the script's core concepts."""


def _enrich_image_prompt(keyword: str, accent: str = "#8B5CF6") -> dict:
    """Single-keyword enrichment (used by the standalone /enrich-image-prompt endpoint).
    Photorealistic tangible hardware — never abstract shapes. Returns {prompt,negative,...}."""
    subject = keyword.strip()
    try:
        sys_p = (
            "Turn a keyword into ONE concrete, photorealistic description of a SINGLE tangible, "
            "high-end hardware object (matte industrial design, brushed titanium, anodized aluminium, "
            "premium medical/lab equipment, clean tech). NO abstract shapes, NO glowing orbs/holograms, "
            "no people, no text. 1-2 sentences, object + material/form only."
        )
        subject = _strip_fences(str(call_openrouter(
            sys_p, f"Keyword: {keyword}", model=CHEAP_MODEL, max_tokens=120))).strip() \
            or keyword.strip()
    except Exception as exc:
        log.warning("[IMG] enrich LLM failed, using raw keyword: %s", exc)
        subject = keyword.strip()
    prompt = f"{subject}. {BRAND_IMAGE_TAIL}."
    return {"keyword": keyword, "subject": subject,
            "prompt": prompt, "negative": BRAND_IMAGE_NEGATIVE}


def _enrich_image_prompts(script: str, cuts: list, accent: str = "#8B5CF6",
                          img_cfg: dict = None) -> list:
    """Batch enrichment: ONE LLM call sees the WHOLE script + all keyword moments and
    returns a concrete prompt per cut. img_cfg (client template) can override the global
    enricher prompt + the brand/camera tail; defaults to Justus constants."""
    if not cuts:
        return []
    img_cfg  = img_cfg or {}
    enricher = img_cfg.get("enricher_prompt") or GLOBAL_VISUAL_ENRICHER_PROMPT
    tail     = img_cfg.get("tail") or BRAND_IMAGE_TAIL
    kw = "\n".join(f'{i+1}. "{c["keyword"]}"' for i, c in enumerate(cuts))
    sys_p = enricher.replace("{INSERT_FULL_VIDEO_SCRIPT_HERE}", (script or "")[:6000])
    user = ('Generate ONE concrete image prompt per keyword below.\n'
            'Return ONLY JSON: {"prompts":[{"i":1,"prompt":"..."}]}\n\nKEYWORDS:\n' + kw)
    prompts = [None] * len(cuts)
    try:
        raw = call_openrouter(sys_p, user, model="anthropic/claude-sonnet-4.6", max_tokens=2500)
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        for item in (json.loads(m.group()).get("prompts", []) if m else []):
            idx = int(item.get("i", 0)) - 1
            if 0 <= idx < len(cuts) and item.get("prompt"):
                prompts[idx] = str(item["prompt"]).strip()
    except Exception as exc:
        log.warning("[IMG] global script enrich failed, per-keyword fallback: %s", exc)
    out = []
    for i, c in enumerate(cuts):
        p = prompts[i] or (_enrich_image_prompt(c["keyword"], accent)["subject"] + f". {tail}.")
        tail_marker = (tail.split()[0] if tail else "anamorphic").lower()
        if tail_marker not in p.lower():
            p = f"{p}. {tail}."
        # force a concrete real-world photo — kills flux's abstract macro/coral/paint default
        p = "Photorealistic real-world photograph of a concrete, recognizable subject — NOT abstract. " + p
        out.append(p)
    return out


def _call_fal_flux(prompt: str, negative: str = "", endpoint: str = None) -> str:
    """Generate a 9:16 image via fal.ai Flux 2 [flash] (or a per-template endpoint)."""
    if not FAL_API_KEY:
        raise RuntimeError("FAL_API_KEY not set")
    endpoint = endpoint or FAL_FLUX_ENDPOINT
    headers = {"Authorization": f"Key {FAL_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "prompt": prompt,
        "image_size": "portrait_16_9",
        "num_images": 1,
        "guidance_scale": 2.5,
        "output_format": "jpeg",
    }
    resp = requests.post(endpoint, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    if data.get("images"):
        return data["images"][0]["url"]

    request_id = data.get("request_id")
    status_url = data.get("status_url") or data.get("response_url")
    if not status_url and request_id:
        status_url = f"https://queue.fal.run/fal-ai/flux-2/requests/{request_id}"
    if not status_url:
        raise RuntimeError(f"fal.ai flux unexpected response: {data}")
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
            raise RuntimeError("fal.ai flux completed but no images")
        if status in ("FAILED", "ERROR"):
            raise RuntimeError(f"fal.ai flux failed: {result}")
    raise RuntimeError("fal.ai flux polling timed out after 60s")


# ── Full-frame image cutaways ─────────────────────────────────────────────────
IMAGE_CUT_DUR  = 2.0    # seconds on screen
HOOK_SECONDS   = 2.0    # hook zone: face fullcam + hook image, no broll/cutaways before this
IMAGE_CUT_FADE = 0.15   # crossfade in/out — snappy short-form, minimal UI bleed-through
IMAGE_CUT_MAX  = 5      # max cutaways per video


def _detect_image_cuts(words: list, duration: float) -> list:
    """Pick up to IMAGE_CUT_MAX strong visual keywords + timestamps for full-frame cutaways."""
    if not words or duration < 6:
        return []
    sys_p = (
        "You are a short-form video editor choosing full-frame B-roll image cutaways. From the "
        "word-level transcript pick the strongest VISUAL moments to illustrate with one AI image each.\n"
        f"Max {IMAGE_CUT_MAX} cuts. Rules: concrete visual subject (object/brand/place/scene), NOT abstract "
        "words; space cuts >= 3s apart; none before 1.0s or after duration-2.0s; keyword = 1-3 words, an "
        "English noun phrase that generates well as an image; time = that word's start time (seconds).\n"
        'Return ONLY JSON: {"cuts":[{"time":4.2,"keyword":"Tesla Cybertruck"}]}'
    )
    try:
        raw = call_openrouter(sys_p, json.dumps(words),
                              model=CHEAP_MODEL, max_tokens=400)
        m    = re.search(r'\{.*\}', raw, re.DOTALL)
        cuts = json.loads(m.group()).get("cuts", []) if m else []
    except Exception as exc:
        log.warning("[IMG] cut detection failed: %s", exc)
        return []
    clean, last = [], -999.0
    for c in sorted(cuts, key=lambda x: x.get("time", 0)):
        try:
            t = float(c.get("time", -1))
        except (TypeError, ValueError):
            continue
        kw = (c.get("keyword") or "").strip()
        if not kw or t < 1.0 or t > duration - 2.0 or (t - last) < 3.0:
            continue
        clean.append({"time": round(t, 3), "keyword": kw})
        last = t
        if len(clean) >= IMAGE_CUT_MAX:
            break
    return clean


def _prepare_image_cuts(cuts: list, job_dir: Path, accent: str, duration: float,
                        script: str = "", img_cfg: dict = None) -> list:
    """Global-script enrich -> Flux 2 -> download each cut (parallel).
    img_cfg (client template) overrides enricher/tail/negative/model. Default = Justus."""
    if not cuts:
        return []
    img_cfg  = img_cfg or {}
    negative = img_cfg.get("negative") or BRAND_IMAGE_NEGATIVE
    endpoint = (f"https://fal.run/{img_cfg['model']}" if img_cfg.get("model") else None)
    prompts = _enrich_image_prompts(script, cuts, accent, img_cfg)   # one LLM call, whole-script aware

    def _gen(idx_cut):
        idx, c = idx_cut
        try:
            url = _call_fal_flux(prompts[idx], negative, endpoint)
            p   = job_dir / f"cut_{idx}.jpg"
            if not download_file(url, p):
                return None
            start = float(c["time"])
            end   = min(start + IMAGE_CUT_DUR, duration)
            if end - start < 0.8:
                return None
            return {"start": start, "end": end, "path": p, "keyword": c["keyword"]}
        except Exception as exc:
            log.warning("[IMG] cut %s (%s) failed: %s", idx, c.get("keyword"), exc)
            return None

    prepared = []
    with ThreadPoolExecutor(max_workers=min(len(cuts), 5)) as pool:
        for r in pool.map(_gen, list(enumerate(cuts))):
            if r:
                prepared.append(r)
    prepared.sort(key=lambda x: x["start"])
    log.info("[IMG] %d/%d cutaways generated", len(prepared), len(cuts))
    return prepared


def _style_lowerthird_image(src: Path, out: Path, box_w: int, box_h: int,
                            radius: int = 38, border: int = 5,
                            gold: tuple = (212, 175, 55)) -> bool:
    """Tim fullcam: cover-crop Flux image into a lower-third card with rounded
    corners + light gold trim ring on a transparent box-sized canvas."""
    try:
        im = Image.open(src).convert("RGB")
        inner_w, inner_h = box_w - 2 * border, box_h - 2 * border
        sw, sh = im.size
        scale = max(inner_w / sw, inner_h / sh)
        im = im.resize((max(1, int(sw * scale)), max(1, int(sh * scale))), Image.LANCZOS)
        rw, rh = im.size
        left, top = (rw - inner_w) // 2, (rh - inner_h) // 2
        im = im.crop((left, top, left + inner_w, top + inner_h)).convert("RGBA")
        inner_radius = max(radius - border, 2)
        mask = Image.new("L", (inner_w, inner_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, inner_w - 1, inner_h - 1],
                                               radius=inner_radius, fill=255)
        im.putalpha(mask)
        canvas = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        # gold rounded-rect shows only as a thin ring around the inner image
        ImageDraw.Draw(canvas).rounded_rectangle(
            [0, 0, box_w - 1, box_h - 1], radius=radius,
            fill=(gold[0], gold[1], gold[2], 235))
        canvas.alpha_composite(im, (border, border))
        canvas.save(out)
        return True
    except Exception as exc:
        log.warning("[IMG] lowerthird style failed: %s", exc)
        return False


def _make_hook_image(hook_text: str, job_dir: Path, img_cfg: dict,
                     script_ctx: str = "") -> Optional[Path]:
    """THE hook visual (0-2s): ONE precise editorial image that nails the topic at a glance
    WITH the real recognizable brand logos/products from the script — in the CLIENT's brand
    style (img_cfg.hook_style). NOT generic AI slop, NOT abstract. Returns raw image path."""
    try:
        img_cfg    = img_cfg or {}
        hook_style = img_cfg.get("hook_style") or "clean modern premium editorial product shot, studio lighting, crisp"
        topic = (hook_text or "").strip()[:240]
        ctx   = (script_ctx or "").strip()[:400]
        prompt = (
            "Editorial hero image for a short-form video hook — ONE cinematic SCENE that visually "
            "tells the topic's OUTCOME at a glance (what changes for the viewer), so it's clear in "
            "<1s. Compose a real, specific scene/moment — NOT a logo wall, NOT a grid of icons, NOT "
            "floating logos. If a brand is central, show its product/app naturally inside the scene "
            "(a screen, a device) — max ONE brand, never a logo collage. Photoreal, intentional, "
            "premium, varied composition each time. NOT abstract, NOT generic AI art, NOT clipart. "
            "Style: " + hook_style + ".\n"
            "TOPIC: " + topic + (("\nKONTEXT (Skript): " + ctx) if ctx else "")
        )
        neg = (img_cfg.get("negative") or BRAND_IMAGE_NEGATIVE) + \
              ", logo wall, grid of logos, floating logos, icon collage, generic AI slop, " \
              "abstract blobs, fake gibberish logos, watermark, ugly text"
        url = _call_fal_flux(prompt, neg)
        if not url:
            log.warning("[HOOK] flux returned no url")
            return None
        raw = job_dir / "hook_src.png"
        if not download_file(url, raw):
            log.warning("[HOOK] hook image download failed")
            return None
        log.info("[HOOK] hook image generated")
        return raw
    except Exception as exc:
        log.warning("[HOOK] hook image failed: %s", exc)
        return None


def _make_hook_title_png(text: str, style: dict, out_path: Path) -> bool:
    """On-screen hook title (frame 0): the hook topic in the client's caption style
    (3-stroke, NO box), big, centered in the upper third — so the eye reads the topic
    instantly. Sits above the lower-third hook image."""
    try:
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        fill    = style.get("fill", (255, 255, 255, 255))
        strokes = style.get("strokes", [(0, 0, 0, 180)])
        fpath   = style.get("font_path", str(FONT_BLACK))
        words = (text or "").strip().split()
        if not words:
            return False
        max_w = W - 140

        # auto-fit: moderate, premium — biggest size that fits ALL words in <=3 lines
        size, lines = int(W * 0.078), None
        while size >= 40:
            f = ImageFont.truetype(fpath, size)
            wrapped, cur, overflow = [], "", False
            for w in words:
                if d.textlength(w, font=f) > max_w:
                    overflow = True; break
                t = (cur + " " + w).strip()
                if d.textlength(t, font=f) <= max_w:
                    cur = t
                else:
                    wrapped.append(cur); cur = w
            if cur:
                wrapped.append(cur)
            if not overflow and len(wrapped) <= 3:
                lines = wrapped; break
            size -= 5
        if lines is None:
            lines = wrapped[:3]
        f = ImageFont.truetype(fpath, size)

        # premium treatment: white fill, ONE clean dark stroke, soft drop shadow (no cheap triple-stroke)
        stroke_col = (0, 0, 0, 220)
        sw = max(3, int(size * 0.055))
        sh = max(4, int(size * 0.06))
        lh = size * 1.18
        total = lh * len(lines)
        y = int(H * 0.27) - total / 2
        for ln in lines:
            lw = d.textlength(ln, font=f)
            x = (W - lw) / 2
            cyl = y + lh / 2
            d.text((x + sh, cyl + sh), ln, font=f, fill=(0, 0, 0, 110), anchor="lm")
            d.text((x, cyl), ln, font=f, fill=fill, anchor="lm",
                   stroke_width=sw, stroke_fill=stroke_col)
            y += lh
        img.save(out_path)
        return True
    except Exception as exc:
        log.warning("[HOOK] title render failed: %s", exc)
        return False


def _hex_to_rgb(h: str, default: tuple = (212, 175, 55)) -> tuple:
    try:
        h = (h or "").lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except Exception:
        return default


# ── Stock-video inserts (Pexels) at emotional peaks ───────────────────────────
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
VIDEO_CUT_MAX     = 3      # max stock-video inserts per video
VIDEO_CUT_MIN_DUR = 1.2    # below this, skip (too short to register)
VIDEO_CUT_MAX_DUR = 6.0    # cap insert length
LOGO_MAX          = 3
LOGO_DUR          = 2.0


def _detect_video_cuts(words: list, duration: float,
                       max_cuts: int = VIDEO_CUT_MAX, style_hint: str = "") -> list:
    """Haiku: pick EMOTIONAL PEAKS where a real cinematic clip hits hardest.
    max_cuts + style_hint are per-client (template). query stays RELEVANT to the spoken
    moment but in the client's visual vibe. Returns [{time, end, query}]."""
    if not words or duration < 8 or not PEXELS_API_KEY:
        return []
    style_line = (f"\nVIBE of ALL clips (queries MUST fit this look while staying relevant to the moment): {style_hint}" if style_hint else "")
    gap = 3.5 if max_cuts > 3 else 5.0
    sys_p = (
        f"You are a short-form editor. From the word-level transcript pick the {max_cuts} moments with the "
        "HIGHEST EMOTIONAL IMPACT where a real cinematic stock VIDEO clip would maximize watchtime "
        "(turning points, shocking stats, stakes, the hook). For each: time = start word's start time; "
        "end = end of that spoken phrase (a few words later); query = 3-5 word ENGLISH cinematic stock "
        "search that is CONCRETE/filmable AND fits the moment." + style_line + "\n"
        "Every query MUST read as a MODERN, current, premium clip: append cues like 'modern', '4k', "
        "'cinematic', 'aesthetic' where natural. NEVER produce dated/90s/retro/grainy/stocky/cheesy "
        "corporate footage.\n"
        f"Space peaks >= {gap}s apart; none after duration-2s.\n"
        'Return ONLY JSON: {"cuts":[{"time":1.0,"end":3.4,"query":"..."}]}'
    )
    try:
        raw = call_openrouter(sys_p, json.dumps(words),
                              model=CHEAP_MODEL, max_tokens=700)
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        cuts = json.loads(m.group()).get("cuts", []) if m else []
    except Exception as exc:
        log.warning("[VID] peak detection failed: %s", exc)
        return []
    clean, last = [], -999.0
    for c in sorted(cuts, key=lambda x: x.get("time", 0)):
        try:
            t = float(c.get("time", -1)); e = float(c.get("end", t + 2.5))
        except (TypeError, ValueError):
            continue
        q = (c.get("query") or "").strip()
        dur = min(max(e - t, 0), VIDEO_CUT_MAX_DUR)
        if not q or t < 0.5 or t > duration - 1.0 or dur < VIDEO_CUT_MIN_DUR or (t - last) < gap:
            continue
        clean.append({"time": round(t, 3), "end": round(t + dur, 3), "query": q})
        last = t
        if len(clean) >= max_cuts:
            break
    return clean


def _fetch_pexels_clip(query: str, dur: float, job_dir: Path, idx: int) -> Optional[Path]:
    """Search Pexels portrait video, download, mid-trim to `dur` (most dynamic part)."""
    try:
        r = requests.get("https://api.pexels.com/videos/search",
                         headers={"Authorization": PEXELS_API_KEY},
                         params={"query": query, "per_page": 12, "orientation": "portrait", "size": "large"},
                         timeout=30)
        r.raise_for_status()
        vids = r.json().get("videos", [])
        if not vids:
            return None
        # pick the most modern/premium candidate: high-res portrait, decent length, prefer recent ids
        def _score(v):
            h = v.get("height") or 0
            w = v.get("width") or 1
            portrait = 1 if h >= w else 0
            res = min(h, 2400)                       # reward resolution (cap so 8k junk doesn't dominate)
            vid_id = v.get("id") or 0                # higher Pexels id ~ newer upload
            return (portrait, res, vid_id)
        best = max(vids, key=_score)
        files = sorted(best["video_files"],
                       key=lambda f: abs((f.get("height") or 0) - 1920))
        link = files[0]["link"]
        src = job_dir / f"vstock_src_{idx}.mp4"
        if not download_file(link, src):
            return None
        srcdur = probe_duration(src)
        start = max(0.0, (srcdur - dur) / 2.0) if srcdur > dur else 0.0
        out = job_dir / f"vcut_{idx}.mp4"
        run(["ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", str(src), "-t", f"{dur:.2f}",
             "-an", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
             str(out)], f"vcut_trim_{idx}")
        return out if out.exists() else None
    except Exception as exc:
        log.warning("[VID] pexels fetch failed (%s): %s", query, exc)
        return None


def _prepare_video_cuts(cuts: list, job_dir: Path, duration: float) -> list:
    """Fetch+trim each peak clip (parallel). Returns [{start,end,path,query}]."""
    if not cuts:
        return []
    def _gen(idx_cut):
        idx, c = idx_cut
        dur = min(c["end"] - c["time"], duration - c["time"])
        if dur < VIDEO_CUT_MIN_DUR:
            return None
        p = _fetch_pexels_clip(c["query"], dur, job_dir, idx)
        if not p:
            return None
        return {"start": c["time"], "end": round(c["time"] + dur, 3), "path": p, "query": c["query"]}
    out = []
    with ThreadPoolExecutor(max_workers=min(len(cuts), 3)) as pool:
        for r in pool.map(_gen, list(enumerate(cuts))):
            if r:
                out.append(r)
    out.sort(key=lambda x: x["start"])
    log.info("[VID] %d/%d stock-video inserts ready", len(out), len(cuts))
    return out


def _detect_logos(words: list, duration: float) -> list:
    """Haiku: find brand/product mentions with a known web domain (for Clearbit logo)."""
    if not words or duration < 6:
        return []
    sys_p = (
        "From the transcript find up to 3 mentions of a well-known COMPANY/PRODUCT/AI-MODEL that has a "
        "real website. For each: time = the mention's start time; brand = name; domain = its main web "
        "domain (e.g. openai.com, tesla.com, anthropic.com). Only entities you are CONFIDENT have that domain.\n"
        'Return ONLY JSON: {"logos":[{"time":2.1,"brand":"OpenAI","domain":"openai.com"}]}'
    )
    try:
        raw = call_openrouter(sys_p, json.dumps(words),
                              model=CHEAP_MODEL, max_tokens=300)
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        logos = json.loads(m.group()).get("logos", []) if m else []
    except Exception as exc:
        log.warning("[LOGO] detection failed: %s", exc)
        return []
    clean, last = [], -999.0
    for l in sorted(logos, key=lambda x: x.get("time", 0)):
        try:
            t = float(l.get("time", -1))
        except (TypeError, ValueError):
            continue
        dom = (l.get("domain") or "").strip()
        if not dom or "." not in dom or t < 0.5 or t > duration - 1.0 or (t - last) < 4.0:
            continue
        clean.append({"time": round(t, 3), "brand": l.get("brand", ""), "domain": dom})
        last = t
        if len(clean) >= LOGO_MAX:
            break
    return clean


def _prepare_logos(logos: list, job_dir: Path, duration: float) -> list:
    """Download each brand logo via Clearbit. Returns [{start,end,path,brand}]."""
    out = []
    for idx, l in enumerate(logos):
        url = f"https://logo.clearbit.com/{l['domain']}?size=512&format=png"
        p = job_dir / f"logo_{idx}.png"
        if download_file(url, p):
            out.append({"start": l["time"], "end": round(min(l["time"] + LOGO_DUR, duration), 3),
                        "path": p, "brand": l.get("brand", "")})
    log.info("[LOGO] %d/%d logos fetched", len(out), len(logos))
    return out


def _dedupe_overlays(video_cuts: list, logos: list, image_cuts: list, gap: float = 2.5):
    """Keep layers from stacking at the same moment, but INTERLEAVE video + image so a
    short video still gets a MIX of both (videos-first would starve images of all slots).
    Logos are lowest priority and fill leftover gaps."""
    claimed = []
    def free(t):
        return all(abs(t - c) >= gap for c in claimed)
    v_sorted = sorted(video_cuts, key=lambda x: x["start"])
    i_sorted = sorted(image_cuts, key=lambda x: x["start"])
    kept_v, kept_i = [], []
    vi = ii = 0
    turn = "v"
    while vi < len(v_sorted) or ii < len(i_sorted):
        if turn == "v" and vi < len(v_sorted):
            it = v_sorted[vi]; vi += 1
            if free(it["start"]):
                kept_v.append(it); claimed.append(it["start"])
            turn = "i"
        elif turn == "i" and ii < len(i_sorted):
            it = i_sorted[ii]; ii += 1
            if free(it["start"]):
                kept_i.append(it); claimed.append(it["start"])
            turn = "v"
        else:
            turn = "i" if turn == "v" else "v"
    kept_lg = []
    for it in sorted(logos, key=lambda x: x["start"]):
        if free(it["start"]):
            kept_lg.append(it); claimed.append(it["start"])
    return kept_v, kept_lg, kept_i


# ── Generic layout compositor (Stage 2b) ─────────────────────────────────────
def _ff_color(hexstr: str) -> str:
    return "0x" + str(hexstr or "#09090B").lstrip("#")


def _build_compositor(layout: dict, srcs: dict, duration: float,
                      image_cuts: list, cut_fade: float):
    """Data-driven ffmpeg compose from layout.layers[] (z-index bottom->top).
    Returns (input_args, filter_complex, final_label, audio_map). Used ONLY when a
    template sets layout.engine='compositor' — the fixed Justus graph is untouched.

    Layer types: video(facecam/broll) · image(divider/hud/scanlines/logo) ·
    captions · progress · image_cuts. Each: x,y,w,h (default = full canvas / its asset).
    A layer whose source is missing/disabled is simply skipped (e.g. no broll)."""
    canvas = layout.get("canvas") or {}
    cw  = int(canvas.get("w", W)); ch = int(canvas.get("h", H))
    fps = int(canvas.get("fps", FPS)); bg = _ff_color(canvas.get("bg", "#09090B"))
    fadeout_start = max(0.0, duration - 1.0)

    inputs, filt = [], []
    # base canvas (lavfi color) = input 0
    inputs += ["-f", "lavfi", "-i", f"color=c={bg}:s={cw}x{ch}:r={fps}:d={duration:.3f}"]
    filt.append("[0:v]setsar=1[base];")
    idx = 1
    prev = "base"
    audio_map = None

    def box(layer):
        return (int(layer.get("x", 0)), int(layer.get("y", 0)),
                int(layer.get("w", cw)), int(layer.get("h", ch)))

    for li, layer in enumerate(layout.get("layers") or []):
        typ = layer.get("type"); src = layer.get("src")
        if layer.get("enabled") is False:
            continue

        if typ == "image_cuts":
            for ci, cut in enumerate(image_cuts):
                inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{duration:.3f}", "-i", str(cut["path"])]
                fout = max(cut["end"] - cut_fade, cut["start"])
                lbl = f"ic{ci}"
                filt.append(
                    f"[{idx}:v]scale={cw}:{ch}:force_original_aspect_ratio=increase,"
                    f"crop={cw}:{ch},setsar=1,format=rgba,"
                    f"fade=t=in:st={cut['start']:.3f}:d={cut_fade}:alpha=1,"
                    f"fade=t=out:st={fout:.3f}:d={cut_fade}:alpha=1[{lbl}];")
                filt.append(f"[{prev}][{lbl}]overlay=x=0:y=0:"
                            f"enable='between(t,{cut['start']:.3f},{cut['end']:.3f})'[stp{li}_{ci}];")
                prev = f"stp{li}_{ci}"; idx += 1
            continue

        path = srcs.get(src)
        if not path:        # source missing/disabled -> skip layer (e.g. broll off)
            continue
        x, y, w, h = box(layer)
        lbl = f"L{li}"

        if typ == "video":
            inputs += ["-i", str(path)]
            if src == "facecam":
                audio_map = f"{idx}:a"
            if layer.get("zoom") == "pulse" and src == "facecam":
                pre = (f"[{idx}:v]zoompan="
                       f"z='if(lte(on,20),1.0+0.08*(on/20),if(lte(on,40),1.08-0.08*((on-20)/20),1.0))':"
                       f"x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':d=1:s={w}x{h}:fps={fps},setsar=1[{lbl}];")
            else:
                pre = (f"[{idx}:v]trim=duration={duration:.3f},setpts=PTS-STARTPTS,"
                       f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1[{lbl}];")
            filt.append(pre)
        elif typ in ("image", "overlay"):
            inputs += ["-i", str(path)]
            filt.append(f"[{idx}:v]setsar=1[{lbl}];")
        elif typ in ("captions", "progress"):
            inputs += ["-framerate", str(fps), "-i", str(path)]
            filt.append(f"[{idx}:v]setsar=1[{lbl}];")
        else:
            idx += 1
            continue

        filt.append(f"[{prev}][{lbl}]overlay=x={x}:y={y}[stp{li}];")
        prev = f"stp{li}"; idx += 1

    filt.append(f"[{prev}]fade=t=out:st={fadeout_start:.3f}:d=1[final]")
    return inputs, "".join(filt), "[final]", (audio_map or "1:a")


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
    n_scenes = max(7, min(9, int(duration / 6)))
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
            model=CHEAP_MODEL,
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


# ── FEW-SHOT REFERENZ (visueller Anker für _broll_system_prompt_v2) ─────────
_BROLL_REFERENCE_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body {
  width:1080px; height:1920px; overflow:hidden;
  background: #0a0910;
  font-family: 'SF Mono','Fira Code','Consolas',monospace;
  position:relative;
  color:#e8e8e8;
}
.scanlines {
  position:absolute; inset:0; pointer-events:none; z-index:100;
  background: repeating-linear-gradient(0deg,transparent,transparent 3px,rgba(0,0,0,0.06) 3px,rgba(0,0,0,0.06) 4px);
}
.glow { position:absolute; border-radius:50%; pointer-events:none; }
.header {
  position:absolute; top:80px; left:60px; right:60px;
  display:flex; align-items:center; justify-content:space-between;
}
.header-tag {
  font-size:11px; letter-spacing:.2em; color:#8B5CF6;
  text-transform:uppercase; border:1px solid rgba(139,92,246,0.3);
  padding:6px 16px; border-radius:20px;
  background:rgba(139,92,246,0.06);
}
.header-live {
  font-size:11px; color:#06B6D4; letter-spacing:.15em;
  display:flex; align-items:center; gap:8px;
}
.live-dot {
  width:7px; height:7px; border-radius:50%; background:#06B6D4;
}
.hero-wrap {
  position:absolute; top:180px; left:50%; transform:translateX(-50%);
  width:400px; height:400px; display:flex; align-items:center; justify-content:center;
}
.big-section {
  position:absolute; top:620px; left:60px; right:60px;
  text-align:center;
}
.big-pre { font-size:12px; color:#555; letter-spacing:.2em; text-transform:uppercase; margin-bottom:8px; }
.big-num {
  font-size:160px; font-weight:900; line-height:1;
  font-family:'Arial Black','Arial',sans-serif;
  background:linear-gradient(135deg,#fff 0%,#C0C0C0 40%,#8B5CF6 100%);
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  filter:drop-shadow(0 0 50px rgba(139,92,246,0.6));
}
.big-post { font-size:18px; color:#06B6D4; letter-spacing:.12em; margin-top:8px; }
.hdivider {
  position:absolute; top:870px; left:60px; right:60px; height:1px;
  background:linear-gradient(90deg,transparent,#8B5CF6,#06B6D4,transparent);
  opacity:0.35;
}
.terminal {
  position:absolute; top:910px; left:60px; right:60px;
  background:rgba(139,92,246,0.04);
  border:1px solid rgba(139,92,246,0.18);
  border-radius:12px; overflow:hidden;
}
.tbar {
  background:rgba(139,92,246,0.08); padding:12px 20px;
  display:flex; align-items:center; gap:8px;
  border-bottom:1px solid rgba(139,92,246,0.12);
}
.tdot { width:10px; height:10px; border-radius:50%; }
.ttitle { margin-left:6px; font-size:12px; color:#555; letter-spacing:.06em; }
.tbody { padding:18px 24px; font-size:14px; line-height:2.1; }
.tline { display:flex; gap:10px; align-items:baseline; min-height:30px; }
.tprompt { color:#8B5CF6; }
.tcmd { color:#e8e8e8; }
.tout { padding-left:22px; }
.tok { color:#10B981; }
.tinfo { color:#06B6D4; }
.twarn { color:#F59E0B; }
.tcursor { display:inline-block; width:9px; height:17px; background:#8B5CF6; margin-left:2px; vertical-align:middle; }
.mcards {
  position:absolute; top:1400px; left:60px; right:60px;
  display:flex; gap:14px;
}
.mcard {
  flex:1; border:1px solid rgba(139,92,246,0.2); border-radius:10px;
  padding:18px; background:rgba(139,92,246,0.04); position:relative; overflow:hidden;
}
.mcard::before { content:''; position:absolute; top:0; left:0; right:0; height:2px; background:linear-gradient(90deg,#8B5CF6,#06B6D4); }
.mval { font-size:32px; font-weight:900; color:#fff; font-family:'Arial Black',sans-serif; }
.mkey { font-size:10px; color:#555; margin-top:4px; letter-spacing:.1em; text-transform:uppercase; }
.mdelta { font-size:11px; color:#10B981; margin-top:6px; }
.mbar { height:3px; background:rgba(255,255,255,0.06); border-radius:2px; margin-top:10px; overflow:hidden; }
.mfill { height:100%; border-radius:2px; background:linear-gradient(90deg,#8B5CF6,#06B6D4); width:0; }
.graphsec {
  position:absolute; top:1650px; left:60px; right:60px;
}
.gtitle { font-size:10px; color:#444; letter-spacing:.12em; text-transform:uppercase; margin-bottom:10px; }
.dstream {
  position:absolute; top:180px; right:0; width:50px; height:700px;
  overflow:hidden; opacity:0.3;
  display:flex; flex-direction:column; gap:2px; padding:0 6px;
}
.dsnum { font-size:9px; color:#8B5CF6; text-align:right; }
</style>
</head>
<body>
<div class="scanlines"></div>
<div class="glow" id="g1" style="width:900px;height:900px;top:-300px;left:-200px;background:radial-gradient(circle,rgba(139,92,246,0.13) 0%,transparent 65%);filter:blur(30px);"></div>
<div class="glow" id="g2" style="width:700px;height:700px;top:500px;right:-200px;background:radial-gradient(circle,rgba(6,182,212,0.09) 0%,transparent 65%);filter:blur(25px);"></div>
<div class="glow" id="g3" style="width:500px;height:500px;bottom:200px;left:-100px;background:radial-gradient(circle,rgba(139,92,246,0.07) 0%,transparent 65%);filter:blur(20px);"></div>
<div class="header">
  <div class="header-tag">NEURALINK · MONITOR</div>
  <div class="header-live"><div class="live-dot" id="liveDot"></div>LIVE FEED</div>
</div>
<div class="hero-wrap">
  <svg id="heroSvg" viewBox="0 0 400 400" width="400" height="400">
    <circle id="r1" cx="200" cy="200" r="160" fill="none" stroke="#8B5CF6" stroke-width="1.5" opacity="0.15" stroke-dasharray="8 12"/>
    <circle id="r2" cx="200" cy="200" r="130" fill="none" stroke="#06B6D4" stroke-width="1" opacity="0.1" stroke-dasharray="4 16"/>
    <g id="brain" transform="translate(200,200)">
      <path d="M 0,-60 C -20,-80 -70,-80 -80,-50 C -90,-25 -85,10 -70,30 C -55,50 -40,65 -20,70 C -10,72 0,70 0,70 Z" fill="rgba(139,92,246,0.15)" stroke="#8B5CF6" stroke-width="2"/>
      <path d="M 0,-60 C 20,-80 70,-80 80,-50 C 90,-25 85,10 70,30 C 55,50 40,65 20,70 C 10,72 0,70 0,70 Z" fill="rgba(139,92,246,0.12)" stroke="#8B5CF6" stroke-width="2"/>
      <line x1="0" y1="-55" x2="0" y2="68" stroke="#8B5CF6" stroke-width="1.5" opacity="0.5" stroke-dasharray="4 4"/>
      <path d="M -70,-30 Q -50,-40 -40,-20" fill="none" stroke="#8B5CF6" stroke-width="1.5" opacity="0.5"/>
      <path d="M -75,5 Q -55,-5 -45,15" fill="none" stroke="#8B5CF6" stroke-width="1.5" opacity="0.4"/>
      <path d="M -65,35 Q -45,25 -38,42" fill="none" stroke="#8B5CF6" stroke-width="1.5" opacity="0.4"/>
      <path d="M 70,-30 Q 50,-40 40,-20" fill="none" stroke="#8B5CF6" stroke-width="1.5" opacity="0.5"/>
      <path d="M 75,5 Q 55,-5 45,15" fill="none" stroke="#8B5CF6" stroke-width="1.5" opacity="0.4"/>
      <path d="M 65,35 Q 45,25 38,42" fill="none" stroke="#8B5CF6" stroke-width="1.5" opacity="0.4"/>
    </g>
    <g id="chip" transform="translate(200,150)">
      <rect x="-22" y="-22" width="44" height="44" rx="6" fill="#0a0910" stroke="#06B6D4" stroke-width="2"/>
      <rect x="-14" y="-14" width="28" height="28" rx="3" fill="rgba(6,182,212,0.15)" stroke="#06B6D4" stroke-width="1" opacity="0.8"/>
      <line x1="-22" y1="-10" x2="-32" y2="-10" stroke="#06B6D4" stroke-width="1.5" opacity="0.7"/>
      <line x1="-22" y1="0" x2="-32" y2="0" stroke="#06B6D4" stroke-width="1.5" opacity="0.7"/>
      <line x1="-22" y1="10" x2="-32" y2="10" stroke="#06B6D4" stroke-width="1.5" opacity="0.7"/>
      <line x1="22" y1="-10" x2="32" y2="-10" stroke="#06B6D4" stroke-width="1.5" opacity="0.7"/>
      <line x1="22" y1="0" x2="32" y2="0" stroke="#06B6D4" stroke-width="1.5" opacity="0.7"/>
      <line x1="22" y1="10" x2="32" y2="10" stroke="#06B6D4" stroke-width="1.5" opacity="0.7"/>
      <line x1="-10" y1="-22" x2="-10" y2="-32" stroke="#06B6D4" stroke-width="1.5" opacity="0.7"/>
      <line x1="0" y1="-22" x2="0" y2="-32" stroke="#06B6D4" stroke-width="1.5" opacity="0.7"/>
      <line x1="10" y1="-22" x2="10" y2="-32" stroke="#06B6D4" stroke-width="1.5" opacity="0.7"/>
      <line x1="-10" y1="-14" x2="-10" y2="14" stroke="#06B6D4" stroke-width="0.5" opacity="0.4"/>
      <line x1="0" y1="-14" x2="0" y2="14" stroke="#06B6D4" stroke-width="0.5" opacity="0.4"/>
      <line x1="10" y1="-14" x2="10" y2="14" stroke="#06B6D4" stroke-width="0.5" opacity="0.4"/>
      <line x1="-14" y1="-10" x2="14" y2="-10" stroke="#06B6D4" stroke-width="0.5" opacity="0.4"/>
      <line x1="-14" y1="0" x2="14" y2="0" stroke="#06B6D4" stroke-width="0.5" opacity="0.4"/>
      <line x1="-14" y1="10" x2="14" y2="10" stroke="#06B6D4" stroke-width="0.5" opacity="0.4"/>
    </g>
    <circle id="sp1" cx="200" cy="150" r="30" fill="none" stroke="#06B6D4" stroke-width="2" opacity="0"/>
    <circle id="sp2" cx="200" cy="150" r="30" fill="none" stroke="#06B6D4" stroke-width="1.5" opacity="0"/>
    <line x1="200" y1="150" x2="80" y2="80" stroke="#06B6D4" stroke-width="1" stroke-dasharray="4 6" opacity="0.3"/>
    <line x1="200" y1="150" x2="320" y2="80" stroke="#06B6D4" stroke-width="1" stroke-dasharray="4 6" opacity="0.3"/>
    <line x1="200" y1="150" x2="60" y2="300" stroke="#8B5CF6" stroke-width="1" stroke-dasharray="4 6" opacity="0.2"/>
    <line x1="200" y1="150" x2="340" y2="300" stroke="#8B5CF6" stroke-width="1" stroke-dasharray="4 6" opacity="0.2"/>
    <g id="dn1" transform="translate(65,75)" opacity="0">
      <rect x="-28" y="-11" width="56" height="22" rx="11" fill="rgba(6,182,212,0.12)" stroke="#06B6D4" stroke-width="1"/>
      <text x="0" y="4" text-anchor="middle" fill="#06B6D4" font-size="9" font-family="monospace">SIGNAL</text>
    </g>
    <g id="dn2" transform="translate(335,75)" opacity="0">
      <rect x="-28" y="-11" width="56" height="22" rx="11" fill="rgba(139,92,246,0.12)" stroke="#8B5CF6" stroke-width="1"/>
      <text x="0" y="4" text-anchor="middle" fill="#8B5CF6" font-size="9" font-family="monospace">STABLE</text>
    </g>
    <g id="dn3" transform="translate(55,305)" opacity="0">
      <rect x="-24" y="-11" width="48" height="22" rx="11" fill="rgba(192,192,192,0.08)" stroke="#C0C0C0" stroke-width="1"/>
      <text x="0" y="4" text-anchor="middle" fill="#C0C0C0" font-size="9" font-family="monospace">10h/d</text>
    </g>
    <g id="dn4" transform="translate(345,305)" opacity="0">
      <rect x="-24" y="-11" width="48" height="22" rx="11" fill="rgba(16,185,129,0.1)" stroke="#10B981" stroke-width="1"/>
      <text x="0" y="4" text-anchor="middle" fill="#10B981" font-size="9" font-family="monospace">99.8%</text>
    </g>
  </svg>
</div>
<div class="big-section">
  <div class="big-pre">GESAMT-NUTZUNGSZEIT</div>
  <div class="big-num" id="bigNum">0</div>
  <div class="big-post">STUNDEN · 12 NUTZER AKTIV</div>
</div>
<div class="hdivider" id="hdiv"></div>
<div class="terminal" id="term">
  <div class="tbar">
    <div class="tdot" style="background:#EF4444;"></div>
    <div class="tdot" style="background:#F59E0B;"></div>
    <div class="tdot" style="background:#10B981;"></div>
    <span class="ttitle">justus@automates ~ neuralink-cli</span>
  </div>
  <div class="tbody">
    <div class="tline"><span class="tprompt">&#x203A;</span><span class="tcmd" id="c1"></span></div>
    <div class="tline"><span class="tout tok" id="o1" style="opacity:0">&#x2713; device_count: 12 &middot; status: ACTIVE</span></div>
    <div class="tline"><span class="tprompt" id="p2" style="opacity:0">&#x203A;</span><span class="tcmd" id="c2" style="opacity:0"></span></div>
    <div class="tline"><span class="tout tinfo" id="o2" style="opacity:0">&#x2192; avg_usage: 10.2h &middot; stability: 99.8%</span></div>
    <div class="tline"><span class="tprompt" id="p3" style="opacity:0">&#x203A;</span><span class="tcmd" id="c3" style="opacity:0"></span></div>
    <div class="tline"><span class="tout twarn" id="o3" style="opacity:0">&#x26A1; prototype &#x2192; everyday tool confirmed</span></div>
    <div class="tline"><span class="tprompt" id="p4" style="opacity:0">&#x203A;</span><span class="tcursor" id="cur"></span></div>
  </div>
</div>
<div class="dstream" id="ds"></div>
<div class="mcards">
  <div class="mcard">
    <div class="mval" id="mv1">0</div>
    <div class="mkey">Nutzer</div>
    <div class="mdelta">&#x25B2; +2 this week</div>
    <div class="mbar"><div class="mfill" id="mf1"></div></div>
  </div>
  <div class="mcard">
    <div class="mval" id="mv2">0h</div>
    <div class="mkey">&#xD8; t&#xe4;glich</div>
    <div class="mdelta">&#x25B2; +2.1h MoM</div>
    <div class="mbar"><div class="mfill" id="mf2" style="background:linear-gradient(90deg,#06B6D4,#10B981);"></div></div>
  </div>
  <div class="mcard">
    <div class="mval" id="mv3">0%</div>
    <div class="mkey">Stabilit&#xe4;t</div>
    <div class="mdelta" style="color:#06B6D4;">&#x25CF; live</div>
    <div class="mbar"><div class="mfill" id="mf3" style="background:linear-gradient(90deg,#10B981,#06B6D4);"></div></div>
  </div>
</div>
<div class="graphsec">
  <div class="gtitle">USAGE TREND &middot; 8 WOCHEN</div>
  <svg viewBox="0 0 960 120" width="100%">
    <defs>
      <linearGradient id="gfill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#8B5CF6" stop-opacity="0.3"/>
        <stop offset="100%" stop-color="#8B5CF6" stop-opacity="0"/>
      </linearGradient>
      <linearGradient id="lgrad" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0%" stop-color="#8B5CF6"/>
        <stop offset="100%" stop-color="#06B6D4"/>
      </linearGradient>
    </defs>
    <line x1="0" y1="30" x2="960" y2="30" stroke="#8B5CF6" stroke-width="1" stroke-dasharray="3 9" opacity="0.12"/>
    <line x1="0" y1="60" x2="960" y2="60" stroke="#8B5CF6" stroke-width="1" stroke-dasharray="3 9" opacity="0.12"/>
    <line x1="0" y1="90" x2="960" y2="90" stroke="#8B5CF6" stroke-width="1" stroke-dasharray="3 9" opacity="0.12"/>
    <path id="ga" d="M 0,120" fill="url(#gfill)"/>
    <path id="gl" d="" fill="none" stroke="url(#lgrad)" stroke-width="3" stroke-linecap="round"/>
    <circle id="gd" cx="0" cy="120" r="5" fill="#06B6D4"/>
  </svg>
</div>
<script>
gsap.to("#g1",{x:50,y:40,scale:1.2,duration:7,repeat:-1,yoyo:true,ease:"sine.inOut"});
gsap.to("#g2",{x:-30,y:-20,scale:1.15,duration:6,repeat:-1,yoyo:true,ease:"sine.inOut",delay:1});
gsap.to("#g3",{scale:1.3,duration:5,repeat:-1,yoyo:true,ease:"sine.inOut",delay:0.5});
gsap.to("#liveDot",{opacity:0.2,duration:0.7,repeat:-1,yoyo:true,ease:"steps(1)"});
gsap.to("#hdiv",{opacity:0.8,duration:1.5,repeat:-1,yoyo:true,ease:"sine.inOut"});
gsap.to("#brain",{scale:1.04,duration:1.8,repeat:-1,yoyo:true,ease:"sine.inOut",transformOrigin:"center"});
gsap.to("#chip",{filter:"drop-shadow(0 0 12px #06B6D4)",duration:1.2,repeat:-1,yoyo:true,ease:"sine.inOut"});
function chipPulse(el,delay){
  gsap.fromTo(el,{attr:{r:30},opacity:0.7},{attr:{r:100},opacity:0,duration:1.8,repeat:-1,delay:delay,ease:"power1.out"});
}
chipPulse("#sp1",0); chipPulse("#sp2",0.9);
gsap.to("#r1",{rotation:360,duration:20,repeat:-1,ease:"linear",transformOrigin:"200px 200px"});
gsap.to("#r2",{rotation:-360,duration:30,repeat:-1,ease:"linear",transformOrigin:"200px 200px"});
gsap.delayedCall(0.5,function(){
  ["#dn1","#dn2","#dn3","#dn4"].forEach(function(n,i){
    gsap.to(n,{opacity:1,duration:0.4,delay:i*0.2});
    gsap.to(n,{y:"+=8",duration:2+i*0.4,repeat:-1,yoyo:true,ease:"sine.inOut",delay:i*0.3});
  });
});
var ds=document.getElementById("ds");
for(var i=0;i<80;i++){var d=document.createElement("div");d.className="dsnum";d.textContent=(Math.random()*999|0).toString().padStart(3,"0");ds.appendChild(d);}
gsap.to("#ds",{y:-600,duration:10,repeat:-1,ease:"linear"});
animateCounter(document.getElementById("bigNum"),15284,2.5);
gsap.delayedCall(0.4,function(){animateCounter(document.getElementById("mv1"),12,1.5);});
gsap.delayedCall(0.7,function(){animateCounter(document.getElementById("mv2"),10,1.4,"h");});
gsap.delayedCall(1.0,function(){animateCounter(document.getElementById("mv3"),99,1.6,"%");});
gsap.to("#mf1",{width:"80%",duration:2,ease:"power2.out",delay:0.5});
gsap.to("#mf2",{width:"92%",duration:2.2,ease:"power2.out",delay:0.7});
gsap.to("#mf3",{width:"99%",duration:2.4,ease:"power2.out",delay:1.0});
function typeText(el,text,dur,cb){var i=0;var chars=text.split("");var iv=setInterval(function(){el.textContent+=chars[i++];if(i>=chars.length){clearInterval(iv);if(cb)cb();}},dur/chars.length*1000);}
gsap.delayedCall(0.3,function(){
  typeText(document.getElementById("c1"),"neuralink status --all",0.6,function(){
    gsap.to("#o1",{opacity:1,duration:0.3});
    gsap.delayedCall(0.4,function(){
      gsap.set("#p2",{opacity:1}); gsap.set("#c2",{opacity:1});
      typeText(document.getElementById("c2"),"fetch usage --live",0.5,function(){
        gsap.to("#o2",{opacity:1,duration:0.3});
        gsap.delayedCall(0.4,function(){
          gsap.set("#p3",{opacity:1}); gsap.set("#c3",{opacity:1});
          typeText(document.getElementById("c3"),"analyze --trend now",0.5,function(){
            gsap.to("#o3",{opacity:1,duration:0.3});
            gsap.delayedCall(0.3,function(){
              gsap.set("#p4",{opacity:1});
              gsap.to("#cur",{opacity:0,duration:0.5,repeat:-1,yoyo:true,ease:"steps(1)"});
            });
          });
        });
      });
    });
  });
});
var pts=[[0,110],[120,95],[240,82],[360,70],[480,55],[600,42],[720,30],[840,18],[960,8]];
var p=0;
gsap.to({},{duration:3,delay:0.5,ease:"power1.inOut",onUpdate:function(){
  p=this.progress();
  var idx=Math.floor(p*(pts.length-1));
  var fr=(p*(pts.length-1))-idx;
  var curr=idx<pts.length-1?[pts[idx][0]+fr*(pts[idx+1][0]-pts[idx][0]),pts[idx][1]+fr*(pts[idx+1][1]-pts[idx][1])]:pts[pts.length-1];
  var ld="M "+pts[0][0]+","+pts[0][1];
  var ad="M 0,120 L "+pts[0][0]+","+pts[0][1];
  for(var i=1;i<=idx;i++){ld+=" L "+pts[i][0]+","+pts[i][1];ad+=" L "+pts[i][0]+","+pts[i][1];}
  ld+=" L "+curr[0]+","+curr[1];
  ad+=" L "+curr[0]+","+curr[1]+" L "+curr[0]+",120 Z";
  document.getElementById("gl").setAttribute("d",ld);
  document.getElementById("ga").setAttribute("d",ad);
  document.getElementById("gd").setAttribute("cx",curr[0]);
  document.getElementById("gd").setAttribute("cy",curr[1]);
}});
gsap.delayedCall(3.5,function(){
  gsap.to("#gd",{attr:{r:8},opacity:0.4,duration:0.8,repeat:-1,yoyo:true,ease:"sine.inOut"});
});
gsap.from("#term",{y:24,opacity:0,duration:0.8,ease:"power3.out",delay:0.2});
gsap.from(".mcards",{y:24,opacity:0,duration:0.8,ease:"power3.out",delay:0.4});
gsap.from(".graphsec",{y:24,opacity:0,duration:0.8,ease:"power3.out",delay:0.6});
gsap.from("#heroSvg",{scale:0.85,opacity:0,duration:1,ease:"back.out(1.4)",delay:0.1,transformOrigin:"center"});
</script>
</body>
</html>"""


def _broll_system_prompt_v2(topic: str, accent: str, scenes: list) -> str:
    n = len(scenes)
    scene_lines = "\n".join(
        f'  scene{i} (start={s["start"]:.3f}s, dur={s["end"]-s["start"]:.1f}s): '
        f'"{s["visual_theme"]}" | data_point={s.get("data_point","—")} | "{s.get("line","")}"'
        for i, s in enumerate(scenes)
    )
    ref = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body {
  width:1080px; height:1920px; overflow:hidden;
  background:#09090B;
  font-family:'SF Mono','Fira Code','Consolas',monospace;
  position:relative;
}
.glow {
  position:absolute; border-radius:50%; pointer-events:none;
  animation: glowPulse 4s ease-in-out infinite alternate;
}
@keyframes glowPulse {
  0%   { transform:scale(0.85) translate(0,0);       opacity:0.7; }
  100% { transform:scale(1.2)  translate(30px,-20px); opacity:1;   }
}
.scanlines {
  position:absolute; inset:0; pointer-events:none; z-index:50;
  background:repeating-linear-gradient(0deg,transparent,transparent 3px,rgba(0,0,0,0.05) 3px,rgba(0,0,0,0.05) 4px);
}
.hero-wrap {
  position:absolute; top:50%; left:50%;
  transform:translate(-50%,-60%);
  animation:heroFloat 5s ease-in-out infinite;
}
@keyframes heroFloat {
  0%,100% { transform:translate(-50%,-60%) translateY(0px); }
  50%      { transform:translate(-50%,-60%) translateY(-28px); }
}
.pulse-ring {
  position:absolute; top:50%; left:50%;
  width:320px; height:320px; border-radius:50%;
  border:2px solid rgba(139,92,246,0.5);
  transform:translate(-50%,-50%) scale(0.5);
  animation:ringExpand 2.4s ease-out infinite;
}
.pulse-ring:nth-child(2){ animation-delay:1.2s; border-color:rgba(6,182,212,0.4); }
@keyframes ringExpand {
  0%   { transform:translate(-50%,-50%) scale(0.5); opacity:0.8; }
  100% { transform:translate(-50%,-50%) scale(2.2); opacity:0;   }
}
.glass-card {
  position:absolute;
  background:rgba(255,255,255,0.04);
  backdrop-filter:blur(24px);
  -webkit-backdrop-filter:blur(24px);
  border:1px solid rgba(255,255,255,0.1);
  border-radius:20px;
  padding:18px 28px;
  opacity:0;
  animation:cardFlyIn 0.7s cubic-bezier(0.175,0.885,0.32,1.275) forwards;
}
@keyframes cardFlyIn {
  0%   { opacity:0; transform:translateY(40px) scale(0.85); }
  100% { opacity:1; transform:translateY(0)    scale(1);    }
}
.glass-card.floating {
  animation:cardFlyIn 0.7s cubic-bezier(0.175,0.885,0.32,1.275) forwards,
            cardFloat  3s ease-in-out infinite;
  animation-delay:0s,0.7s;
}
@keyframes cardFloat {
  0%,100% { transform:translateY(0px)    rotate(0deg);   }
  50%      { transform:translateY(-12px) rotate(0.5deg); }
}
.card-label { font-size:11px; letter-spacing:.15em; text-transform:uppercase; color:rgba(192,192,192,0.7); margin-bottom:6px; }
.card-value { font-size:32px; font-weight:900; font-family:'Arial Black','Arial',sans-serif; background:linear-gradient(135deg,#fff,#a78bfa); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
.card-sub   { font-size:12px; color:rgba(192,192,192,0.6); margin-top:4px; }
.big-number-wrap { position:absolute; bottom:280px; left:0; right:0; text-align:center; opacity:0; animation:fadeUp 0.8s ease forwards; animation-delay:0.4s; }
@keyframes fadeUp { 0%{opacity:0;transform:translateY(30px);} 100%{opacity:1;transform:translateY(0);} }
.big-pre { font-size:12px; letter-spacing:.25em; color:#8B5CF6; text-transform:uppercase; margin-bottom:10px; }
.big-num  { font-size:160px; font-weight:900; line-height:1; font-family:'Arial Black','Arial',sans-serif; background:linear-gradient(135deg,#fff 0%,#C0C0C0 40%,#8B5CF6 100%); -webkit-background-clip:text; -webkit-text-fill-color:transparent; filter:drop-shadow(0 0 60px rgba(139,92,246,0.5)); }
.big-post { font-size:18px; color:#06B6D4; letter-spacing:.12em; margin-top:8px; }
.bottom-bar { position:absolute; bottom:80px; left:60px; right:60px; background:rgba(139,92,246,0.06); border:1px solid rgba(139,92,246,0.2); border-radius:14px; padding:16px 24px; display:flex; justify-content:space-between; align-items:center; opacity:0; animation:fadeUp 0.8s ease forwards; animation-delay:0.9s; }
.bar-item { text-align:center; }
.bar-val  { font-size:20px; font-weight:700; color:#fff; font-family:'Arial Black',sans-serif; }
.bar-lbl  { font-size:10px; color:#666; letter-spacing:.1em; text-transform:uppercase; margin-top:3px; }
.bar-sep  { width:1px; height:36px; background:rgba(139,92,246,0.2); }
.header   { position:absolute; top:80px; left:60px; right:60px; display:flex; justify-content:space-between; align-items:center; opacity:0; animation:fadeUp 0.6s ease forwards; }
.header-tag { font-size:11px; letter-spacing:.2em; color:#8B5CF6; border:1px solid rgba(139,92,246,0.3); padding:6px 16px; border-radius:100px; background:rgba(139,92,246,0.06); }
.header-live { font-size:11px; color:#06B6D4; display:flex; align-items:center; gap:7px; }
.live-dot { width:7px; height:7px; border-radius:50%; background:#06B6D4; animation:liveBlink 1s steps(1) infinite; }
@keyframes liveBlink { 0%,100%{opacity:1;} 50%{opacity:0.2;} }
#flame1,#flame2 { animation:flameFlicker 0.15s ease-in-out infinite alternate; transform-origin:center top; }
#flame3,#flame4 { animation:flameFlicker 0.12s ease-in-out infinite alternate; animation-delay:0.06s; transform-origin:center top; }
@keyframes flameFlicker {
  0%   { transform:scaleY(1)   scaleX(1);    opacity:0.9; }
  100% { transform:scaleY(1.3) scaleX(0.85); opacity:0.7; }
}
</style>
</head>
<body>

<div class="scanlines"></div>
<div class="glow" style="width:900px;height:900px;top:-150px;left:-150px;background:radial-gradient(circle,rgba(139,92,246,0.13) 0%,transparent 65%);filter:blur(30px);"></div>
<div class="glow" style="width:700px;height:700px;bottom:-100px;right:-100px;background:radial-gradient(circle,rgba(6,182,212,0.09) 0%,transparent 65%);filter:blur(25px);animation-delay:1.5s;"></div>

<div class="header">
  <div class="header-tag">SPACEX · CURSOR AI</div>
  <div class="header-live"><div class="live-dot"></div>LIVE DEAL</div>
</div>

<div class="pulse-ring"></div>
<div class="pulse-ring" style="width:280px;height:280px;"></div>

<div class="hero-wrap">
  <svg width="260" height="260" viewBox="0 0 260 260" fill="none">
    <ellipse cx="130" cy="185" rx="55" ry="18" fill="rgba(139,92,246,0.2)"/>
    <path d="M130 30 C155 55 165 110 162 160 L98 160 C95 110 105 55 130 30 Z" fill="#1a1726" stroke="#8B5CF6" stroke-width="3"/>
    <path d="M130 30 C140 42 148 60 150 80 L110 80 C112 60 120 42 130 30 Z" fill="rgba(139,92,246,0.3)" stroke="#8B5CF6" stroke-width="2"/>
    <circle cx="130" cy="112" r="22" fill="#0a0910" stroke="#06B6D4" stroke-width="2.5"/>
    <circle cx="130" cy="112" r="15" fill="rgba(6,182,212,0.15)" stroke="#06B6D4" stroke-width="1.5"/>
    <circle cx="124" cy="106" r="4" fill="rgba(255,255,255,0.3)"/>
    <path d="M98 140 L68 175 L98 160 Z" fill="#8B5CF6" opacity="0.85"/>
    <path d="M98 130 L75 155 L98 145 Z" fill="rgba(139,92,246,0.5)"/>
    <path d="M162 140 L192 175 L162 160 Z" fill="#8B5CF6" opacity="0.85"/>
    <path d="M162 130 L185 155 L162 145 Z" fill="rgba(139,92,246,0.5)"/>
    <rect x="114" y="160" width="24" height="22" rx="4" fill="#2a2040" stroke="#8B5CF6" stroke-width="1.5"/>
    <rect x="122" y="158" width="16" height="24" rx="3" fill="#1a1726" stroke="#06B6D4" stroke-width="1"/>
    <ellipse id="flame1" cx="122" cy="192" rx="8"  ry="16" fill="#F59E0B" opacity="0.9"/>
    <ellipse id="flame2" cx="138" cy="192" rx="8"  ry="16" fill="#F59E0B" opacity="0.9"/>
    <ellipse id="flame3" cx="122" cy="196" rx="5"  ry="10" fill="#fff"    opacity="0.6"/>
    <ellipse id="flame4" cx="138" cy="196" rx="5"  ry="10" fill="#fff"    opacity="0.6"/>
    <text x="178" y="55"  font-size="22" font-family="monospace" fill="#06B6D4" opacity="0.8">&lt;/&gt;</text>
    <text x="175" y="72"  font-size="10" font-family="monospace" fill="rgba(6,182,212,0.6)" letter-spacing="1">CURSOR</text>
    <text x="88"  y="46"  font-size="9"  font-family="monospace" fill="rgba(192,192,192,0.5)" letter-spacing="1.5">SPACEX</text>
  </svg>
</div>

<div class="glass-card floating" style="top:330px;left:70px;animation-delay:0.3s;">
  <div class="card-label">Übernahme-Option</div>
  <div class="card-value">$60 Mrd.</div>
  <div class="card-sub">vollständig · später</div>
</div>
<div class="glass-card floating" style="top:310px;right:70px;animation-delay:0.55s;">
  <div class="card-label">Partnerschaft</div>
  <div class="card-value">$10 Mrd.</div>
  <div class="card-sub">sofort · Option</div>
</div>
<div class="glass-card floating" style="top:600px;left:70px;animation-delay:0.7s;">
  <div class="card-label">Zeitplan</div>
  <div class="card-value" style="font-size:26px;">Q3 2026</div>
  <div class="card-sub">mögliche Übernahme</div>
</div>
<div class="glass-card floating" style="top:580px;right:70px;animation-delay:0.85s;">
  <div class="card-label">Entwickler täglich</div>
  <div class="card-value" id="devCount" style="font-size:26px;">0</div>
  <div class="card-sub">Nutzer · Cursor AI</div>
</div>

<div class="big-number-wrap">
  <div class="big-pre">DEAL-VOLUMEN GESAMT</div>
  <div class="big-num" id="bigNum">0</div>
  <div class="big-post">MRD. DOLLAR · OPTION GESICHERT</div>
</div>

<div class="bottom-bar">
  <div class="bar-item"><div class="bar-val" style="color:#8B5CF6;">SpaceX</div><div class="bar-lbl">Käufer</div></div>
  <div class="bar-sep"></div>
  <div class="bar-item"><div class="bar-val" style="color:#06B6D4;">Cursor</div><div class="bar-lbl">KI-Tool</div></div>
  <div class="bar-sep"></div>
  <div class="bar-item"><div class="bar-val" style="color:#10B981;">Musk</div><div class="bar-lbl">Deal</div></div>
  <div class="bar-sep"></div>
  <div class="bar-item"><div class="bar-val" style="color:#F59E0B;">Option</div><div class="bar-lbl">nicht sofort</div></div>
</div>

<script>
window.animateCounter = function(el, target, dur, suffix) {
  if (!el) return;
  const start = performance.now();
  const durationMs = dur * 1000;
  function step(now) {
    const p = Math.min((now - start) / durationMs, 1);
    const ease = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(ease * target).toLocaleString('de-DE') + (suffix || '');
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
};
setTimeout(function() {
  animateCounter(document.getElementById('bigNum'), 60, 2.2, '');
  animateCounter(document.getElementById('devCount'), 800, 1.8, 'k+');
}, 600);
</script>
</body>
</html>"""
    return f"""Du bist Senior Motion-Graphics-Direktor für premium Social-Media B-Roll.
Erzeuge {n} Szenen-DIVs (Viewport 1080×{BROLL_H}px, accent {accent}).

DESIGN-PHILOSOPHIE (ersetzt alles Bisherige): WENIGER ELEMENTE, MEHR ANIMATION.
Nicht 15 Dinge gleichzeitig — lieber 5 Dinge die wirklich LEBEN. Clean, premium,
relatable. Kein überladenes Dashboard. Bekannte Brand-Logos/Symbole > abstrakte Deko.

AUSGABE-FORMAT (kein Wrapper, kein Markdown):
  1. EIN <style>-Block mit allen @keyframes (wie in der Referenz: glowPulse, heroFloat,
     ringExpand, cardFlyIn, cardFloat, flameFlicker bzw. Hero-Aktion, liveBlink, fadeUp)
  2. {n} × <div class="scene" id="sceneN"> … </div> in der Struktur unten
  3. EIN <script>-Block, der NUR window.animateCounter definiert (Pattern unten)
  KEIN <html>/<head>/<body>, KEIN <script src=...>
  (.scene ist bereits position:absolute;inset:0 + Opacity wird vom System gesteuert — NICHT anfassen)

PRO SZENE — GENAU DIESE STRUKTUR (nicht mehr, nicht weniger):

1. HEADER (oben): Topic-Tag links (pill) + LIVE-Badge rechts (blinkender Punkt).

2. HERO-OBJEKT (Mitte): EIN sauber gezeichnetes SVG-Icon zum Thema (~150–190px).
   PFLICHT BRAND/SYMBOL: Nennt das Thema eine bekannte Brand/Firma/Tool (SpaceX,
   Google, OpenAI, Cursor, Meta, Apple, Tesla, Nvidia …), MUSS deren Name/Kürzel/
   Logo-Symbol am Hero stehen — gezeichnet + Text-Label (wie "SPACEX" + "</> CURSOR"
   in der Referenz). Immer relatable, lieber bekanntes Symbol als Abstraktes.
   Das Hero macht eine PHYSISCHE LOOP-AKTION (CSS): Rakete→schwebt+Flammen flackern ·
   Gehirn→pulsiert · Uhr→Zeiger dreht · Editor→Cursor blinkt. NIE statisch.

3. PULSE-RINGE: 2 expandierende Ringe (CSS ringExpand) zentriert hinterm Hero, permanent.

4. GLASS-CARDS (2–4, gestaffelt): fliegen mit cubic-bezier(0.175,0.885,0.32,1.275)
   rein (cardFlyIn) und schweben danach (cardFloat). Inhalt: die wichtigsten
   Daten/Fakten der Szene (Label + Wert + kurzer Sub). KEINE Card mit Skript-Text.

5. BIG COUNTER (unten/zentral): die EINE große Leitzahl der Szene.
   Element: <div class="big-num" id="..." data-count="ENDWERT">ENDWERT</div>
   data-count = reine Zahl (z.B. 60), Text = derselbe Endwert. Einheit als
   kurzes Label darunter (z.B. .big-post). Das System zählt die Zahl hoch GENAU
   wenn die Szene erscheint — mit DEINER animateCounter (unten). Du rufst sie NICHT
   selbst auf, KEIN setTimeout. Nur das Element taggen.

6. BOTTOM-BAR: 4 Key-Terms zum Thema, farbig (z.B. Käufer/Tool/Person/Status).

ANIMATIONS-REGELN:
- ALLE Basis-Animationen (Float, Pulse-Ringe, Card-FlyIn, Hero-Aktion/Flammen, Glow,
  Live-Blink, fadeUp) AUSSCHLIESSLICH per CSS @keyframes — headless-bombensicher.
- KEINE gsap-Aufrufe, KEINE SVG-Methoden im JS (kein getTotalLength etc.).
- Counter: definiere im <script> NUR diese Funktion, EXAKT so (window-Zuweisung ist Pflicht):

<script>
window.animateCounter = function(el, target, dur, suffix) {{
  if (!el) return;
  const start = performance.now();
  const durationMs = dur * 1000;
  function step(now) {{
    const p = Math.min((now - start) / durationMs, 1);
    const ease = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(ease * target).toLocaleString('de-DE') + (suffix || '');
    if (p < 1) requestAnimationFrame(step);
  }}
  requestAnimationFrame(step);
}};
</script>

LAYOUT-ANPASSUNG (WICHTIG): Die Referenz ist 1080×1920. DEIN Viewport ist 1080×{BROLL_H}px
— viel flacher. Komprimiere konsequent: Header oben (~top:16px), Hero+Ringe zentriert,
Glass-Cards kompakt links/rechts neben dem Hero, Big-Counter unten-zentral, Bottom-Bar
ganz unten (~bottom:14px). position:absolute INNERHALB des .scene-divs (das ist der
Positions-Container). Keine 1920er-Abstände, nichts darf abgeschnitten werden.

FARBPALETTE (FIX): Amethyst #8B5CF6 · Cyan #06B6D4 · Silber #C0C0C0 ·
Grün #10B981 · Amber #F59E0B · BG #09090B/#0a0910 · Text #fff/#C0C0C0.

=== VISUELLER STYLE-ANKER (Niveau treffen, NICHT 1:1 kopieren) ===
Dieses fertige Beispiel zeigt EXAKT Stil, Sauberkeit und Animations-Niveau, das du
treffen MUSST — gezeichnetes Brand-Icon mit Label, Pulse-Ringe, Glass-Cards, großer
Counter, Bottom-Bar, alles via CSS-Keyframes + die animateCounter oben. Baue pro Thema
eine NEUE Szene auf diesem Niveau — nicht überladen, immer relatable, eigene Daten/Icons.

{ref}

{n} SZENEN (Inhalt/Daten pro Szene):
{scene_lines}

=== VERBOTEN ===
- Überladene Dashboards / 10+ Mini-Elemente pro Szene
- Skript-Satz als Fließtext oder in einer Glass-Card
- Bedeutungslose Deko-Labels ohne echten Datenbezug
- gsap.*, getTotalLength, innerHTML-Tweens, eigene setTimeout/Counter-Aufrufe
- "0" als finaler Counter-Text (immer Endwert als Text + data-count)
- Andere Leitfarben als die definierten

Gib NUR den <style>-Block, die {n} <div class="scene" id="sceneN">…</div> Blöcke und
den einen window.animateCounter-<script>-Block zurück. Kein Markdown."""


def _broll_anim_bootstrap(scenes: list) -> str:
    """Deterministic, Python-generated animation driver — CANNOT be truncated.

    Sonnet only has to produce richly-designed DIVs and TAG the animated bits
    (numbers get data-count, bars get data-fill). This bootstrap then animates
    each element IN PLACE, exactly when its scene fades in, so the motion is
    contextual (the number Sonnet placed counts up where it sits — not a bare
    centred 0). Runs entirely in Python-emitted JS, so it can never be cut off
    by Sonnet's token limit. Per scene, at its start time:
      - numbers ([data-count] / .dp .big-num .stat .mval .num) -> count 0->value in place
      - bars ([data-fill] / .mfill .bar .fill)                 -> grow to target width
      - stroked SVG <path> (len>40)                            -> draw themselves
      - ring <circle> (fill=none) & [data-spin]                -> slow continuous spin
      - hero <svg>                                             -> gentle idle scale-pulse
      - radial-gradient glow divs                              -> ambient pulse
    animateCounter is idempotent (data-counting guard) so nothing double-runs.
    """
    sc = ",".join(
        f'{{s:{s["start"]:.3f},d:{max(s["end"]-s["start"],0.5):.3f}}}'
        for s in scenes
    )
    return f"""<script>
;(function(){{
  if(!window.gsap) return;
  var SC=[{sc}];
  function parseNum(raw){{
    if(raw==null) return null;
    raw=""+raw;
    var m=raw.match(/-?\\d[\\d.,]*/);
    if(!m) return null;
    var ns=m[0];
    var val=parseFloat(ns.replace(/\\./g,"").replace(/,/g,"."));
    if(isNaN(val)) return null;
    var suffix=raw.substring(raw.indexOf(ns)+ns.length); // keep original spacing/unit
    return {{val:val, suffix:suffix}};
  }}
  function animNumber(el,dur){{
    var disp=el.textContent||"";
    var pv=parseNum(el.getAttribute("data-count"));
    var pt=parseNum(disp);
    var val=pv?pv.val:(pt?pt.val:null);
    if(val==null) return;
    var suffix=pt?pt.suffix:(pv?pv.suffix:"");
    animateCounter(el, val, dur, suffix);
  }}
  SC.forEach(function(o,i){{
    gsap.delayedCall(o.s, function(){{
      var sc=document.getElementById("scene"+i);
      if(!sc) return;
      var cdur=Math.min(o.d*0.7,2.2);
      // 1) counters — animate Sonnet's number elements in place
      sc.querySelectorAll("[data-count],.dp,.big-num,.bignum,.stat,.mval,.num,.counter").forEach(function(el){{
        animNumber(el,cdur);
      }});
      // 2) fill / progress bars -> grow to target width
      sc.querySelectorAll("[data-fill],.mfill,.bar,.fill").forEach(function(b){{
        if(b.getAttribute("data-filled")) return;
        b.setAttribute("data-filled","1");
        var w=b.getAttribute("data-fill")||b.getAttribute("data-width")||"80%";
        gsap.fromTo(b,{{width:"0%"}},{{width:w,duration:Math.min(o.d*0.6,2.0),ease:"power2.out",delay:0.2}});
      }});
      // 3) self-drawing stroked paths (graphs, line icons)
      var di=0;
      sc.querySelectorAll("svg path").forEach(function(pth){{
        if(pth.getAttribute("data-drawn")) return;
        try{{
          var L=pth.getTotalLength();
          if(!L || L<40) return;
          var stroke=pth.getAttribute("stroke")||getComputedStyle(pth).stroke;
          if(!stroke || stroke==="none" || stroke==="rgba(0, 0, 0, 0)") return;
          pth.setAttribute("data-drawn","1");
          gsap.set(pth,{{strokeDasharray:L,strokeDashoffset:L}});
          gsap.to(pth,{{strokeDashoffset:0,duration:Math.min(o.d*0.6,2.0),delay:di*0.08,ease:"power2.out"}});
          di++;
        }}catch(e){{}}
      }});
      // 4) ring outlines + [data-spin] -> slow continuous rotation (idle life)
      sc.querySelectorAll('svg circle[fill="none"],[data-spin]').forEach(function(r,idx){{
        if(r.getAttribute("data-spinning")) return;
        r.setAttribute("data-spinning","1");
        gsap.to(r,{{rotation:(idx%2?-360:360),duration:14+idx*4,repeat:-1,ease:"none",transformOrigin:"50% 50%",svgOrigin:"90 90"}});
      }});
      // 5) hero svg -> gentle idle scale-pulse so nothing is frozen after entry
      var hero=sc.querySelector("svg");
      if(hero && !hero.getAttribute("data-pulsed")){{
        hero.setAttribute("data-pulsed","1");
        gsap.to(hero,{{scale:1.04,duration:1.8,repeat:-1,yoyo:true,ease:"sine.inOut",transformOrigin:"center"}});
      }}
      // 6) radial-gradient glow divs -> ambient pulse
      sc.querySelectorAll('div[style*="radial-gradient"]').forEach(function(g,idx){{
        if(g.getAttribute("data-glowed")) return;
        g.setAttribute("data-glowed","1");
        gsap.to(g,{{scale:1.18,opacity:0.6,duration:2.4+idx*0.3,repeat:-1,yoyo:true,ease:"sine.inOut"}});
      }});
    }});
  }});
}})();
</script>"""


def _build_broll_html(scene_divs: str, scenes: list, accent: str) -> str:
    """Wrap Sonnet's scene divs with CSS-driven scene visibility + GSAP helpers.

    Scene opacity is controlled by CSS @keyframes (no GSAP timeline needed).
    This avoids all variable-collision issues where Sonnet's 'var tl' could
    interfere with a Python-generated GSAP timeline.
    GSAP is still available for Sonnet's inner-element animations.
    A Python-generated bootstrap (_broll_anim_bootstrap) guarantees that every
    counter/graph animates even when Sonnet's own <script> is missing/truncated.
    """
    # Per-scene CSS: fade in at scene["start"], hold, fade out at scene["end"]
    scene_css_lines = []
    for i, s in enumerate(scenes):
        dur = max(round(s["end"] - s["start"], 3), 0.5)
        scene_css_lines.append(
            f"  #scene{i}{{animation:_broll_scene {dur:.3f}s {s['start']:.3f}s both;}}"
        )
    scene_css = "\n".join(scene_css_lines)
    safe_divs = _safe_wrap_scripts(scene_divs)
    anim_bootstrap = _broll_anim_bootstrap(scenes)

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1080px;height:{BROLL_H}px;overflow:hidden;background:#141218;position:relative;font-family:"Arial Black",Impact,sans-serif}}
.scene{{position:absolute;inset:0;opacity:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;padding:40px 60px}}
.dp{{font-size:110px;font-weight:900;color:{accent};text-shadow:0 0 40px {accent}88,0 2px 24px rgba(0,0,0,0.9);line-height:1;text-align:center}}
.lbl{{font-size:28px;font-weight:700;color:#d0d0d0;text-align:center;max-width:920px;line-height:1.3}}
svg{{overflow:visible}}
@keyframes _broll_scene{{
  0%{{opacity:0}}
  5%{{opacity:1}}
  95%{{opacity:1}}
  100%{{opacity:0}}
}}
{scene_css}
</style>
</head>
<body>
<script src="gsap.min.js"></script>
<script>
function animateCounter(el,target,dur,suffix){{if(!el)return;if(el.getAttribute("data-counting"))return;el.setAttribute("data-counting","1");var o={{v:0}};gsap.to(o,{{v:target,duration:dur||2,ease:"power2.out",onUpdate:function(){{el.textContent=Math.round(o.v)+(suffix||"");}}}});}}
function addAmbientPulse(el,s,d){{if(!el)return;gsap.to(el,{{scale:s||1.15,opacity:0.6,duration:d||1.2,repeat:-1,yoyo:true,ease:"sine.inOut"}});}}
</script>
<script>
/* Patch gsap.delayedCall: auto-wrap every callback in try-catch.
   Prevents a single null-SVG-path or undefined-var error from silently
   killing animateCounter() and other animations that come after it. */
;(function(){{
  if(!window.gsap)return;
  var _dc=gsap.delayedCall.bind(gsap);
  gsap.delayedCall=function(t,fn,params,scope){{
    return _dc(t,function(){{
      try{{fn.apply(scope||this,params||[]);}}
      catch(e){{console.error('[BROLL-DC]',e.message,e.stack||'');}}
    }},undefined,scope);
  }};
}})();
</script>
{safe_divs}
{anim_bootstrap}
</body></html>"""


def _count_broll_scenes(html: str, n_scenes: int) -> int:
    return sum(1 for i in range(n_scenes)
               if f'id="scene{i}"' in html or f"id='scene{i}'" in html)


def _validate_broll_html(html: str, n_scenes: int) -> bool:
    return _count_broll_scenes(html, n_scenes) >= n_scenes


def _safe_wrap_scripts(scene_divs: str) -> str:
    """Wrap Sonnet's <script> blocks in try-catch so one JS error doesn't kill all animations."""
    def _wrap(m):
        attrs = m.group(1); content = m.group(2)
        if 'src=' in attrs.lower() or 'try{' in content or 'try {' in content:
            return m.group(0)
        return (f'<script{attrs}>\ntry{{\n{content}\n}}'
                f'catch(e){{console.error("[BROLL-JS]",e.message,e);}}\n</script>')
    return re.sub(r'<script([^>]*)>(.*?)</script>', _wrap, scene_divs,
                  flags=re.DOTALL | re.IGNORECASE)


def _has_complete_animation_script(html: str) -> bool:
    """True only if there's a CLOSED <script>...</script> that actually drives animations.
    Sonnet truncation drops the trailing script entirely (or cuts it mid-way), which
    leaves all counters/graphs static. We detect both: missing AND incomplete scripts."""
    blocks = re.findall(r'<script[^>]*>(.*?)</script>', html, flags=re.DOTALL | re.IGNORECASE)
    for b in blocks:
        if "gsap." in b or "animateCounter" in b or "addAmbientPulse" in b:
            return True
    return False


def _gen_animation_script(scene_divs_html: str, scenes: list, accent: str) -> str:
    """Second, focused Sonnet call: generate ONLY the GSAP <script> for already-built divs.
    Small output → no truncation. Touches nothing about layout/content/timing/colors —
    it only produces the animation logic for the exact element IDs Sonnet already emitted."""
    n = len(scenes)
    starts = ", ".join(f'scene{i}={s["start"]:.2f}s' for i, s in enumerate(scenes))
    sys_p = f"""Du bekommst {n} fertige Szenen-DIVs einer B-Roll-Animation (1080x{BROLL_H}px, accent {accent}).
Erzeuge AUSSCHLIESSLICH EINEN <script>-Block mit GSAP-Animationen für GENAU diese DIVs.
Ändere KEINE Inhalte, kein Layout — nur Animationen.

GLOBALE HELFER (bereits definiert, NICHT neu definieren):
  animateCounter(el, target, dur, suffix)  — zählt eine Zahl von 0 hoch
  addAmbientPulse(el, scaleAmt, dur)        — endloser Glow-Pulse

REGELN:
- Pro Szene EIN gsap.delayedCall(sceneStart, function(){{ ... }}) mit diesen Start-Zeiten: {starts}
- Jede sichtbare Kennzahl (Elemente mit class "dp" oder id counter*/num*/val*) → animateCounter(el, zielzahl, dauer, " einheit")
- Jeder SVG-Pfad der sich zeichnen soll — NULL-GUARD ist PFLICHT:
    var p=document.getElementById("DEINE_ID");
    if(p){{var L=p.getTotalLength();gsap.set(p,{{strokeDasharray:L,strokeDashoffset:L}});gsap.to(p,{{strokeDashoffset:0,duration:2,ease:"power2.out"}});}}
- Glows/Ringe/Hero-SVGs: kontinuierliche Bewegung (rotation/scale/opacity, repeat:-1, yoyo:true) — starten SOFORT
- Metric-Fill-Bars: gsap.to(el,{{width:"NN%",duration:2}})
- VERBOTEN: gsap.timeline(), <html>/<style>/<head>, Helfer neu definieren
- Verwende AUSSCHLIESSLICH die EXAKTEN id-Attribute aus den DIVs unten.
- Gib NUR den einen <script>...</script>-Block zurück. Kein Markdown, kein Text drumherum.

SZENEN-DIVS:
{scene_divs_html}
"""
    out = _strip_fences(str(call_openrouter(
        sys_p, f"Erzeuge den vollständigen <script>-Block für alle {n} Szenen.",
        model="anthropic/claude-sonnet-4.6", max_tokens=8000)))
    if "<script" not in out.lower():
        out = f"<script>\n{out}\n</script>"
    return out


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
            "-c:v", "libx264", "-crf", "20", "-preset", "medium",
            "-pix_fmt", "yuv420p", str(video_path)
        ], f"black_scene_{idx}")
    return video_path


# ── POST /generate-broll-synced ───────────────────────────────────────────────
async def _generate_broll_synced_impl(req: BrollSyncedRequest):
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
                                   max_tokens=32000)

        loop     = asyncio.get_event_loop()
        html_raw = None
        for attempt in range(1, 4):
            try:
                html_raw = _strip_fences(str(
                    await loop.run_in_executor(_html_executor, _gen_full_html)
                ))
                found = _count_broll_scenes(html_raw, n_scenes)
                if found < n_scenes:
                    log.warning("[BROLL_SYNC] %d/%d scenes — using partial HTML (no retry)", found, n_scenes)
                else:
                    log.info("[BROLL_SYNC] HTML valid (%d/%d scenes)", found, n_scenes)
                break  # accept whatever came back; only retry on exception below
            except Exception as exc:
                log.warning("[BROLL_SYNC] attempt %d call failed: %s", attempt, exc)
                html_raw = None
                if attempt < 3:
                    await asyncio.sleep(3)

        if not html_raw:
            raise HTTPException(status_code=500, detail="Broll HTML generation failed after 3 attempts")

        has_script = _has_complete_animation_script(html_raw)
        log.info("[BROLL_SYNC] scene divs (%d chars) has_anim_script=%s tail=%s",
                 len(html_raw), has_script,
                 repr(html_raw[-200:].replace('\n', ' ')))
        # NOTE: animations are now driven deterministically by the Python bootstrap
        # in _build_broll_html (_broll_anim_bootstrap). No 2nd Sonnet call needed —
        # counters/graphs animate from the divs' data-count tags regardless of
        # whether Sonnet's own <script> survived the token limit.

        # Wrap Sonnet's divs with Python-generated GSAP timeline + animation bootstrap
        full_html_raw  = _build_broll_html(html_raw, scenes, req.brand_color_primary)
        full_html_path = job_dir / "broll_full.html"
        _injected_html = _inject_gsap_inline(full_html_raw)
        full_html_path.write_text(_injected_html, encoding="utf-8")
        Path("/tmp/last_broll.html").write_text(_injected_html, encoding="utf-8")
        log.info("[BROLL_SYNC] HTML saved to /tmp/last_broll.html for inspection")
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
                    "-c:v", "libx264", "-crf", "20", "-preset", "medium",
                    "-pix_fmt", "yuv420p", str(clip_path)
                ], f"clip_scene_{i}")
            else:
                run([
                    "ffmpeg", "-y", "-f", "lavfi",
                    "-i", f"color=c=black:size=1080x{BROLL_H}:rate={FPS}",
                    "-t", str(scene_dur),
                    "-c:v", "libx264", "-crf", "20", "-preset", "medium",
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
                "-c:v", "libx264", "-crf", "20", "-preset", "medium",
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


@app.post("/generate-broll-synced")
async def generate_broll_synced(req: BrollSyncedRequest):
    """Streaming wrapper — sends keepalive bytes every 20s to survive Railway proxy timeout."""
    result_holder: dict = {}
    done_event = asyncio.Event()

    async def _worker():
        try:
            result_holder["ok"] = await _generate_broll_synced_impl(req)
        except HTTPException as exc:
            result_holder["err"] = exc.detail
        except Exception as exc:
            result_holder["err"] = str(exc)
        finally:
            done_event.set()

    asyncio.create_task(_worker())

    async def _stream():
        while not done_event.is_set():
            try:
                await asyncio.wait_for(done_event.wait(), timeout=20)
            except asyncio.TimeoutError:
                yield b" "  # keepalive — JSON.parse skips leading whitespace
        payload = result_holder.get("ok") or {"error": result_holder.get("err", "unknown")}
        yield json.dumps(payload).encode()

    return StreamingResponse(
        _stream(),
        media_type="application/json",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


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
            "You are a sound designer for short-form video. Read the word-level transcript and place\n"
            "sound effects from the library below at the strongest moments. Pick the asset whose MEANING\n"
            "best fits what is being said — match SEMANTICALLY (synonyms, related concepts, the emotional\n"
            "vibe), not only the literal example words. Use 4-8 cues total. Do NOT place anything at t=0\n"
            "(the intro stinger is added automatically). At most ONE 'hook' background bed per video.\n"
            "\n"
            "LIBRARY (category | asset | when to trigger):\n"
            "hook (3.5s background bed, sparingly):\n"
            "  distant_police_siren_bg | crisis, law, police, crime, scandal, lawsuit, danger, high stakes\n"
            "  hook_cash_register_01   | revenue, sales, money won, scaling, profit, deals, funding, valuation\n"
            "  hook_clock_tick_01      | deadlines, time pressure, history, urgency, countdown, 'too late'\n"
            "  hook_coin_drop_01       | micro-savings, small amounts, cents, budgeting, finance tips\n"
            "  hook_error_buzz_01      | failure, mistakes, penalties, losses, crashes, negative numbers\n"
            "  hook_notification_01    | DMs, inbound pings, messages, notifications, digital comms\n"
            "  hook_success_chime_01   | achievements, milestones, wins, unlocked value, breakthroughs\n"
            "  hook_warning_sonar_01   | upcoming threats, critical alerts, macro risk, caution\n"
            "impact (one-shot punch on a strong beat):\n"
            "  impact_bass_drop_01     | focus shift, gravity, dramatic conceptual reveal\n"
            "  impact_cinematic_hit_01 | bold cinematic statement / headline punctuation\n"
            "  impact_digital_boom_01  | heavy tech realization, AI/software breakthrough\n"
            "  impact_gong_reversed_01 | swell into a plot shift / reverse reveal / twist\n"
            "  impact_heartbeat_01     | tension, fear, suspense, life-or-death stakes\n"
            "  impact_metal_thud_01    | finality, hard proof, definitive structural fact\n"
            "  impact_shatter_muted_01 | shock, breaking a pattern, a concept failing, busting a myth\n"
            "  impact_tape_stop_01     | hard pattern interrupt, 'wait/actually', correction\n"
            "pop (micro <0.4s, fast UI / keyword pop-ins):\n"
            "  pop_blip_organic_01 | fluid minimalist pop      pop_bubble_muted_01 | light casual reveal\n"
            "  pop_camera_shutter_01 | freeze-frame/snapshot    pop_click_mech_01 | code/data/typing\n"
            "  pop_glass_tap_01 | premium UI tap                pop_snap_finger_01 | instant realization/choice\n"
            "  pop_ui_clean_01 | minimal tech notification\n"
            "transition (on a B-roll / full-frame swap):\n"
            "  trans_digital_swipe_01 | data slide/panel wipe   trans_reverse_suck_01 | vacuum pull into next\n"
            "  trans_swish_fabric_01 | organic whip-pan         trans_swish_paper_01 | flat/doc layout swipe\n"
            "  trans_whoosh_deep_01 | deep cinematic sweep      trans_whoosh_fast_01 | fast modern whip\n"
            "\n"
            "time = the word's start time (seconds) from the transcript.\n"
            'Return ONLY JSON: {"impacts":[{"time":2.34,"category":"impact","asset":"impact_bass_drop_01","word":"x","reason":"y"}]}'
        )

        raw = call_openrouter(
            system_prompt,
            json.dumps(words),
            model=CHEAP_MODEL,
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
        # per-client thumbnail brand from template (defaults = Justus tech look)
        _tpl  = _load_template(req.client_id, None)
        _cols = _tpl_colors(_tpl)
        _timg = ((_tpl or {}).get("images") or {})
        thumb_accent = _cols.get("primary") or req.brand_color_primary
        thumb_bg     = _cols.get("bg") or "#12101a"
        thumb_glow   = _timg.get("glow_word") or "amethyst purple"
        thumb_vibe   = _timg.get("thumbnail_vibe")
        ok = False
        try:
            loop    = asyncio.get_event_loop()
            img_url = await loop.run_in_executor(
                None, _call_fal_thumbnail, concept, thumb_accent, thumb_bg, thumb_glow, thumb_vibe
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
async def _render_impl(req: RenderRequest):
    job_id  = str(uuid.uuid4())
    job_dir = Path(f"/tmp/render_{job_id}")
    job_dir.mkdir(parents=True, exist_ok=True)
    log.info("=== JOB %s START ===", job_id)

    try:
        # ── 0. Thumbnail freeze-frame clip (optional, created early) ─────────
        thumb_clip = None
        if req.thumbnail_url:
            log.info("[RENDER] appending 0.2s thumbnail freeze-frame at end")
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
                        "-c:v", "libx264", "-crf", "20", "-preset", "medium",
                        "-c:a", "aac", "-b:a", "192k",
                        "-pix_fmt", "yuv420p",
                        "-shortest",
                        str(thumb_clip),
                    ], "thumb_clip")
                except Exception as exc:
                    log.warning("[RENDER] thumbnail clip creation failed: %s", exc)
                    thumb_clip = None

        # ── 0. Resolve client visual template (empty = Justus defaults) ───────
        tpl       = _load_template(req.client_id, req.template)
        tcols     = _tpl_colors(tpl)
        cap_style = _caption_style(tpl)
        img_cfg   = (tpl.get("images") or {}) if tpl else {}
        cut_fade  = float(img_cfg.get("fade", IMAGE_CUT_FADE))
        div_l = _hex_rgb((tcols.get("divider_gradient") or [None])[0], AMETHYST)
        div_r = _hex_rgb((tcols.get("divider_gradient") or [None, None])[1], SILVER) \
                if tcols.get("divider_gradient") else SILVER
        prog_l = _hex_rgb((tcols.get("progress_gradient") or [None])[0], AMETHYST)
        prog_r = _hex_rgb((tcols.get("progress_gradient") or [None, None])[1], SILVER) \
                 if tcols.get("progress_gradient") else SILVER
        if tpl:
            log.info("[RENDER] client_id=%s template loaded (keys=%s)",
                     req.client_id, ",".join(k for k in tpl if tpl.get(k)))

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
                "-c:v", "libx264", "-crf", "20", "-preset", "medium",
                "-pix_fmt", "yuv420p",
                str(broll_final),
            ], "black_broll")

        log_duration(broll_final, "broll_final")

        # ── 4. Scale/crop facecam ────────────────────────────────────────────
        facecam_scaled = job_dir / "facecam_scaled.mp4"
        scale_crop(facecam_raw, facecam_scaled, W, FACECAM_H)

        # ── 5. Divider gradient PNG ──────────────────────────────────────────
        divider_png = job_dir / "divider.png"
        make_gradient_png(divider_png, W, DIVIDER_H, div_l, div_r)

        # ── 6. Whisper captions ──────────────────────────────────────────────
        log.info("[RENDER] Transcribing facecam for captions")
        words = transcribe_audio(facecam_raw)

        # ── 7. Caption dir (frames built later — after cuts, so karaoke can react) ─
        cap_dir = job_dir / "captions"

        # ── 8. Progress frames ───────────────────────────────────────────────
        prog_dir = job_dir / "progress"
        build_progress_frames(total_frames, prog_dir, (prog_l, prog_r))

        # ── 9. Static overlays (per-template when a client template is loaded) ─
        hud_png       = HUD_PATH
        scanlines_png = SCANLINES_PATH
        if tpl:
            hud_png       = job_dir / "hud.png"
            scanlines_png = job_dir / "scanlines.png"
            _generate_hud(hud_png, tpl)
            _generate_scanlines(scanlines_png, tpl)

        # ── 9b. Full-frame AI image cutaways (optional) ───────────────────────
        image_cuts = []
        if req.image_cuts and img_cfg.get("enabled", True):
            loop_ic    = asyncio.get_event_loop()
            cut_events = req.image_cut_events or _detect_image_cuts(words, duration)
            if cut_events:
                script_ctx = " ".join(w.get("word", "") for w in words)   # whole-script context for the enricher
                image_cuts = await loop_ic.run_in_executor(
                    None, _prepare_image_cuts, cut_events, job_dir,
                    req.brand_color_primary, duration, script_ctx, img_cfg)

        # ── 9c. Stock-video inserts at emotional peaks (default on, per-template) ─
        video_cuts = []
        vid_cfg = (tpl.get("video_cuts") or {}) if tpl else {}
        if vid_cfg.get("enabled", True):
            vmax = int(vid_cfg.get("max_cuts", VIDEO_CUT_MAX))
            vstyle = vid_cfg.get("style", "")
            vpeaks = await asyncio.get_event_loop().run_in_executor(
                None, _detect_video_cuts, words, duration, vmax, vstyle)
            if vpeaks:
                video_cuts = await asyncio.get_event_loop().run_in_executor(
                    None, _prepare_video_cuts, vpeaks, job_dir, duration)

        # ── 9d. Brand-logo inserts on entity mentions (default on, per-template) ─
        logos = []
        logo_cfg = (tpl.get("logos") or {}) if tpl else {}
        if logo_cfg.get("enabled", True):
            lmentions = await asyncio.get_event_loop().run_in_executor(None, _detect_logos, words, duration)
            if lmentions:
                logos = await asyncio.get_event_loop().run_in_executor(
                    None, _prepare_logos, lmentions, job_dir, duration)

        # priority video > logo > image; never stack at same moment
        video_cuts, logos, image_cuts = _dedupe_overlays(video_cuts, logos, image_cuts)

        # ── 9d2. HOOK ZONE (per-template): first 2s = clean fullcam face + hook image, ──
        #         no broll/cutaways. Everything else delayed to >= HOOK_SECONDS.
        hook_zone   = bool((tpl or {}).get("hook_zone")) or bool(img_cfg.get("hook_zone"))
        hook_raw    = None
        facecam_full = None
        if hook_zone:
            def _delay(cuts):
                out = []
                for c in cuts:
                    if c["start"] < HOOK_SECONDS:
                        shift = HOOK_SECONDS - c["start"]
                        ns, ne = c["start"] + shift, c["end"] + shift
                        if ne <= duration - 0.2:
                            out.append({**c, "start": round(ns, 3), "end": round(ne, 3)})
                    else:
                        out.append(c)
                return out
            image_cuts = _delay(image_cuts); video_cuts = _delay(video_cuts); logos = _delay(logos)
            hook_script_ctx = " ".join(w.get("word", "") for w in words)[:400]
            hook_raw = await asyncio.get_event_loop().run_in_executor(
                None, _make_hook_image, req.hook_text, job_dir, img_cfg, hook_script_ctx)
            facecam_full = job_dir / "facecam_full.mp4"
            scale_crop(facecam_raw, facecam_full, W, H)
            hook_title_png = job_dir / "hook_title.png"
            if not _make_hook_title_png(req.hook_text, cap_style, hook_title_png):
                hook_title_png = None

        # ── 9e. Caption frames (built now so karaoke lifts to the top during inserts) ─
        lift_ranges = [(c["start"], c["end"]) for c in (image_cuts + video_cuts)]
        cap_words = words
        if cap_style.get("mode") in ("perword", "danilo"):
            cap_words = await asyncio.get_event_loop().run_in_executor(
                None, _classify_caption_words, words)
        build_caption_frames(cap_words, total_frames, cap_dir, divider_png, cap_style, lift_ranges)

        # ── 10. Final ffmpeg compose ──────────────────────────────────────────
        log.info("[RENDER] Compositing: broll+divider+facecam | %d img, %d video, %d logo | captions",
                 len(image_cuts), len(video_cuts), len(logos))
        output_mp4 = job_dir / "output.mp4"

        cap_pattern  = str(cap_dir  / "frame_%06d.png")
        prog_pattern = str(prog_dir / "frame_%06d.png")
        fadeout_start = max(0.0, duration - 1.0)

        _lay = (tpl.get("layout") or {}) if tpl else {}
        use_comp = _lay.get("engine") == "compositor" and bool(_lay.get("layers"))

        if use_comp:
            # ── Stage 2b: generic data-driven compositor (custom client layout) ──
            log.info("[RENDER] custom compositor layout (%d layers, %d cutaways)",
                     len(_lay.get("layers", [])), len(image_cuts))
            srcs = {
                "facecam": facecam_scaled, "broll": broll_final,
                "divider": divider_png, "hud": hud_png, "scanlines": scanlines_png,
                "captions": cap_pattern, "progress": prog_pattern,
            }
            inp, fc, final_label, amap = _build_compositor(_lay, srcs, duration, image_cuts, cut_fade)
            cmd = [
                "ffmpeg", "-y", *inp,
                "-filter_complex", fc,
                "-map", final_label, "-map", amap,
                "-af", "loudnorm=I=-14:LRA=11:TP=-1.5",
                "-c:v", "libx264", "-crf", "20", "-preset", "medium",
                "-c:a", "aac", "-b:a", "192k",
                "-t", str(duration), "-pix_fmt", "yuv420p",
                str(output_mp4),
            ]
            run(cmd, "final_compose_custom")
        elif _lay.get("mode") == "fullcam":
            # ── Fullcam (Tim): facecam full-frame + overlays + captions, no broll/UI ──
            log.info("[RENDER] fullcam layout (%d img, %d video, %d logo)",
                     len(image_cuts), len(video_cuts), len(logos))
            parts = [
                f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[base];",
            ]
            extra_inputs = []
            idx = 1   # facecam is [0]; extra inputs start at [1]
            ov = 0
            prev = "base"
            # Tim layout: Flux images = lower-third card (rounded + gold trim); videos stay full-frame.
            gold_rgb = _hex_to_rgb(((tpl.get("colors") or {}).get("primary")) if tpl else None)
            lt_box_w, lt_box_h = int(W * 0.84), int(H * 0.30)
            lt_x, lt_y = (W - lt_box_w) // 2, int(H * 0.64)   # lower third; karaoke captions sit above it (~0.55H)
            for cut in image_cuts:
                styled = cut["path"].with_name(f"lt_{cut['path'].stem}.png")
                ok = _style_lowerthird_image(cut["path"], styled, lt_box_w, lt_box_h, gold=gold_rgb)
                img_in = styled if ok else cut["path"]
                extra_inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{duration:.3f}", "-i", str(img_in)]
                fout = max(cut["end"] - cut_fade, cut["start"])
                pre = "format=rgba," if ok else f"scale={lt_box_w}:{lt_box_h}:force_original_aspect_ratio=increase,crop={lt_box_w}:{lt_box_h},setsar=1,format=rgba,"
                parts.append(f"[{idx}:v]{pre}"
                    f"fade=t=in:st={cut['start']:.3f}:d={cut_fade}:alpha=1,"
                    f"fade=t=out:st={fout:.3f}:d={cut_fade}:alpha=1[o{ov}];")
                parts.append(f"[{prev}][o{ov}]overlay=x={lt_x}:y={lt_y}:enable='between(t,{cut['start']:.3f},{cut['end']:.3f})'[s{ov}];")
                prev = f"s{ov}"; idx += 1; ov += 1
            for v in video_cuts:
                extra_inputs += ["-i", str(v["path"])]
                fout = max(v["end"] - cut_fade, v["start"])
                parts.append(
                    f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,format=rgba,"
                    f"setpts=PTS-STARTPTS+{v['start']:.3f}/TB,"
                    f"fade=t=in:st={v['start']:.3f}:d={cut_fade}:alpha=1,"
                    f"fade=t=out:st={fout:.3f}:d={cut_fade}:alpha=1[o{ov}];")
                parts.append(f"[{prev}][o{ov}]overlay=x=0:y=0:enable='between(t,{v['start']:.3f},{v['end']:.3f})'[s{ov}];")
                prev = f"s{ov}"; idx += 1; ov += 1
            for lg in logos:
                extra_inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{duration:.3f}", "-i", str(lg["path"])]
                fout = max(lg["end"] - cut_fade, lg["start"])
                parts.append(f"[{idx}:v]scale=360:-1,setsar=1,format=rgba,"
                    f"fade=t=in:st={lg['start']:.3f}:d={cut_fade}:alpha=1,"
                    f"fade=t=out:st={fout:.3f}:d={cut_fade}:alpha=1[o{ov}];")
                parts.append(f"[{prev}][o{ov}]overlay=x=(W-w)/2:y=360:enable='between(t,{lg['start']:.3f},{lg['end']:.3f})'[s{ov}];")
                prev = f"s{ov}"; idx += 1; ov += 1
            # captions: full-frame perword frames, overlaid on top
            extra_inputs += ["-framerate", str(FPS), "-i", cap_pattern]
            parts.append(f"[{prev}][{idx}:v]overlay=x=0:y=0[with_cap];")
            parts.append(f"[with_cap]fade=t=out:st={fadeout_start:.3f}:d=1[final]")
            filter_complex = "".join(parts)
            cmd = [
                "ffmpeg", "-y",
                "-i", str(facecam_raw),         # [0] facecam (full-frame base + audio)
                *extra_inputs,                  # [2..] cuts/logos then [last]=captions
                "-filter_complex", filter_complex,
                "-map", "[final]", "-map", "0:a",
                "-af", "loudnorm=I=-14:LRA=11:TP=-1.5",
                "-c:v", "libx264", "-crf", "20", "-preset", "medium",
                "-c:a", "aac", "-b:a", "192k",
                "-t", str(duration), "-pix_fmt", "yuv420p",
                str(output_mp4),
            ]
            run(cmd, "final_compose_fullcam")
        else:
            # ── Fixed Justus split-graph (UNCHANGED — default / no client_id) ────
            parts = [
                f"[0:v]trim=duration={duration:.3f},setpts=PTS-STARTPTS,setsar=1[broll];",
                "[1:v]setsar=1[div];",
                (f"[2:v]zoompan="
                 f"z='min(zoom+0.00007,1.10)':"
                 f"x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':"
                 f"d=1:s={W}x{FACECAM_H}:fps={FPS},"
                 f"eq=contrast=1.06:saturation=1.12:brightness=0.01,unsharp=5:5:0.4,setsar=1[face];"),
                "[broll][div][face]vstack=inputs=3[stacked];",
                "[stacked][5:v]overlay=x=0:y=0[with_scan];",
                "[with_scan][6:v]overlay=x=0:y=0[with_hud];",
                f"[with_hud][4:v]overlay=x=0:y={PROGRESS_Y}[with_ui];",
            ]
            extra_inputs = []
            idx = 7
            ov = 0
            prev = "with_ui"
            # full-frame AI image cutaways (looped still)
            for cut in image_cuts:
                extra_inputs += ["-loop", "1", "-framerate", str(FPS),
                                 "-t", f"{duration:.3f}", "-i", str(cut["path"])]
                fout = max(cut["end"] - cut_fade, cut["start"])
                parts.append(
                    f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                    f"crop={W}:{H},setsar=1,format=rgba,"
                    f"fade=t=in:st={cut['start']:.3f}:d={cut_fade}:alpha=1,"
                    f"fade=t=out:st={fout:.3f}:d={cut_fade}:alpha=1[o{ov}];")
                parts.append(f"[{prev}][o{ov}]overlay=x=0:y=0:"
                             f"enable='between(t,{cut['start']:.3f},{cut['end']:.3f})'[s{ov}];")
                prev = f"s{ov}"; idx += 1; ov += 1
            # full-frame stock-video inserts at peaks (real clip, shifted to its timeline slot)
            for v in video_cuts:
                extra_inputs += ["-i", str(v["path"])]
                fout = max(v["end"] - cut_fade, v["start"])
                parts.append(
                    f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                    f"crop={W}:{H},setsar=1,format=rgba,"
                    f"setpts=PTS-STARTPTS+{v['start']:.3f}/TB,"
                    f"fade=t=in:st={v['start']:.3f}:d={cut_fade}:alpha=1,"
                    f"fade=t=out:st={fout:.3f}:d={cut_fade}:alpha=1[o{ov}];")
                parts.append(f"[{prev}][o{ov}]overlay=x=0:y=0:"
                             f"enable='between(t,{v['start']:.3f},{v['end']:.3f})'[s{ov}];")
                prev = f"s{ov}"; idx += 1; ov += 1
            # brand-logo badges (small, centered-upper)
            for lg in logos:
                extra_inputs += ["-loop", "1", "-framerate", str(FPS),
                                 "-t", f"{duration:.3f}", "-i", str(lg["path"])]
                fout = max(lg["end"] - cut_fade, lg["start"])
                parts.append(
                    f"[{idx}:v]scale=360:-1,setsar=1,format=rgba,"
                    f"fade=t=in:st={lg['start']:.3f}:d={cut_fade}:alpha=1,"
                    f"fade=t=out:st={fout:.3f}:d={cut_fade}:alpha=1[o{ov}];")
                parts.append(f"[{prev}][o{ov}]overlay=x=(W-w)/2:y=360:"
                             f"enable='between(t,{lg['start']:.3f},{lg['end']:.3f})'[s{ov}];")
                prev = f"s{ov}"; idx += 1; ov += 1
            # HOOK ZONE: 0-2s fullcam face covers the split, + hook image lower third
            if hook_zone and facecam_full and facecam_full.exists():
                extra_inputs += ["-i", str(facecam_full)]
                parts.append(f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                             f"crop={W}:{H},"
                             f"zoompan=z='min(zoom+0.0035,1.18)':d=1:"
                             f"x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':s={W}x{H}:fps={FPS},"
                             f"setsar=1,format=rgba[hookface];")
                parts.append(f"[{prev}][hookface]overlay=x=0:y=0:"
                             f"enable='between(t,0,{HOOK_SECONDS:.3f})'[hs];")
                prev = "hs"; idx += 1
                if hook_title_png:
                    extra_inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{duration:.3f}", "-i", str(hook_title_png)]
                    parts.append(f"[{idx}:v]format=rgba,"
                                 f"fade=t=in:st=0:d={cut_fade}:alpha=1,"
                                 f"fade=t=out:st={max(HOOK_SECONDS - cut_fade, 0):.3f}:d={cut_fade}:alpha=1[hooktitle];")
                    parts.append(f"[{prev}][hooktitle]overlay=x=0:y=0:"
                                 f"enable='between(t,0,{HOOK_SECONDS:.3f})'[hst];")
                    prev = "hst"; idx += 1
                if hook_raw and hook_raw.exists():
                    hk_w, hk_h = int(W * 0.50), int(H * 0.16)   # small rectangle, not a full card
                    hk_x, hk_y = (W - hk_w) // 2, int(H * 0.70)
                    hk_styled = job_dir / "hook_card.png"
                    hk_gold = _hex_to_rgb(((tpl.get("colors") or {}).get("primary")) if tpl else None)
                    hk_ok = _style_lowerthird_image(hook_raw, hk_styled, hk_w, hk_h, gold=hk_gold)
                    hk_in = hk_styled if hk_ok else hook_raw
                    extra_inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{duration:.3f}", "-i", str(hk_in)]
                    hk_pre = "format=rgba," if hk_ok else f"scale={hk_w}:{hk_h}:force_original_aspect_ratio=increase,crop={hk_w}:{hk_h},setsar=1,format=rgba,"
                    parts.append(f"[{idx}:v]{hk_pre}"
                                 f"fade=t=in:st=0:d={cut_fade}:alpha=1,"
                                 f"fade=t=out:st={max(HOOK_SECONDS - cut_fade, 0):.3f}:d={cut_fade}:alpha=1[hookimg];")
                    parts.append(f"[{prev}][hookimg]overlay=x={hk_x}:y={hk_y}:"
                                 f"enable='between(t,0,{HOOK_SECONDS:.3f})'[hs2];")
                    prev = "hs2"; idx += 1
            cap_enable = f":enable='gte(t,{HOOK_SECONDS:.3f})'" if hook_zone else ""
            parts.append(f"[{prev}][3:v]overlay=x=0:y={DIVIDER_Y}{cap_enable}[with_cap];")
            parts.append(f"[with_cap]fade=t=out:st={fadeout_start:.3f}:d=1[final]")
            filter_complex = "".join(parts)
            cmd = [
                "ffmpeg", "-y",
                "-i", str(broll_final),         # [0]
                "-i", str(divider_png),         # [1]
                "-i", str(facecam_scaled),      # [2]
                "-framerate", str(FPS),
                "-i", cap_pattern,              # [3]
                "-framerate", str(FPS),
                "-i", prog_pattern,             # [4]
                "-i", str(scanlines_png),       # [5]
                "-i", str(hud_png),             # [6]
                *extra_inputs,                  # [7..] image cuts, video cuts, logos
                "-filter_complex", filter_complex,
                "-map", "[final]",
                "-map", "2:a",
                "-af", "loudnorm=I=-14:LRA=11:TP=-1.5",
                "-c:v", "libx264", "-crf", "20", "-preset", "medium",
                "-c:a", "aac", "-b:a", "192k",
                "-t", str(duration),
                "-pix_fmt", "yuv420p",
                str(output_mp4),
            ]
            run(cmd, "final_compose")
        log.info("Output: %s (%.1f MB)", output_mp4, output_mp4.stat().st_size / 1e6)

        # ── 11. SFX mixing (per-template toggle; default on for Justus) ───────
        if (tpl.get("sfx") or {}).get("enabled", True) if tpl else True:
            # whoosh on EVERY image/video insertion (synced to the cut start)
            cut_whoosh = [{"asset": "trans_whoosh_fast_01", "time": c["start"]}
                          for c in (image_cuts + video_cuts)]
            all_impacts = (req.impacts or []) + cut_whoosh
            mixed = mix_sfx_into_video(output_mp4, all_impacts, job_dir, duration)
            if mixed:
                output_mp4 = mixed

        # ── 12. Append thumbnail freeze-frame at the END (optional) ───────────
        if thumb_clip and thumb_clip.exists():
            output_with_thumb = job_dir / "output_thumb.mp4"
            try:
                run([
                    "ffmpeg", "-y",
                    "-i", str(output_mp4),
                    "-i", str(thumb_clip),
                    "-filter_complex",
                    "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vout][aout]",
                    "-map", "[vout]",
                    "-map", "[aout]",
                    "-c:v", "libx264", "-crf", "20", "-preset", "medium",
                    "-c:a", "aac", "-b:a", "192k",
                    "-pix_fmt", "yuv420p",
                    str(output_with_thumb),
                ], "thumb_concat")
                output_mp4 = output_with_thumb
                log.info("[RENDER] main + thumbnail(end) concat done")
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


@app.post("/render")
async def render(req: RenderRequest):
    """Streaming wrapper — keepalive bytes every 20s so Railway's proxy doesn't 502
    on long renders (captions + Flux image cuts + compose can exceed ~100s)."""
    result_holder: dict = {}
    done_event = asyncio.Event()

    async def _worker():
        try:
            result_holder["ok"] = await _render_impl(req)
        except HTTPException as exc:
            result_holder["err"] = exc.detail
        except Exception as exc:
            result_holder["err"] = str(exc)
        finally:
            done_event.set()

    asyncio.create_task(_worker())

    async def _stream():
        while not done_event.is_set():
            try:
                await asyncio.wait_for(done_event.wait(), timeout=20)
            except asyncio.TimeoutError:
                yield b" "  # keepalive
        payload = result_holder.get("ok") or {"error": result_holder.get("err", "unknown")}
        yield json.dumps(payload).encode()

    return StreamingResponse(
        _stream(),
        media_type="application/json",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ── Infosheet: HTML template ──────────────────────────────────────────────────
def _build_infosheet_html(req: InfosheetRequest) -> str:
    import html as _html

    def esc(s: str) -> str:
        return _html.escape(str(s))

    facts_html = "".join(
        f'<div class="fact-card">'
        f'<div class="fact-val">{esc(f.value)}</div>'
        f'<div class="fact-lbl">{esc(f.label)}</div>'
        f'</div>'
        for f in req.key_facts
    )
    was_passiert_html = "\n".join(
        f'<div class="bullet"><span class="bpre">›</span><span>{esc(b)}</span></div>'
        for b in req.was_passiert
    )
    was_bringt_html = "\n".join(
        f'<div class="bullet"><span class="bpre bpre-cyan">◆</span><span>{esc(b)}</span></div>'
        for b in req.was_bringt_mir
    )

    # ── brand + palette (client template; justus defaults => byte-identical) ──
    tpl  = _load_template(req.client_id, None)
    cols = _tpl_colors(tpl)
    info = (tpl.get("infosheet") or {}) if tpl else {}
    primary   = cols.get("primary")   or "#8B5CF6"
    accent    = cols.get("accent")    or "#06B6D4"
    secondary = cols.get("secondary") or "#C0C0C0"
    b = {
        "handle_html": info.get("handle_html", "@<em>justus</em>.automates"),
        "take_label":  info.get("take_label", "Justus&rsquo; Take"),
        "p2_eyebrow":  info.get("p2_eyebrow", "Justus Schulte &middot; KI-Automatisierung"),
        "p2_headline": info.get("p2_headline", "Willst du wissen wie ich sowas <em>automatisiere?</em>"),
        "p2_body":     info.get("p2_body", "Folg mir f&uuml;r t&auml;gliche KI-Insights &mdash; und kommentier wenn du den vollst&auml;ndigen Automation-Blueprint willst."),
        "p2_handle":   info.get("p2_handle", "@justus.automates"),
        "p2_url":      info.get("p2_url", "justus.automates"),
    }

    _html_doc = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
html, body {{ background:#0a0910; -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
@page {{ size:1080px 1920px; margin:0; }}

.page {{
  width:1080px; height:1920px;
  background:#0a0910;
  color:#e8e8e8;
  font-family:-apple-system,'Helvetica Neue',Arial,sans-serif;
  position:relative; overflow:hidden;
  -webkit-print-color-adjust:exact; print-color-adjust:exact;
}}
.page::after {{
  content:''; position:absolute; inset:0; pointer-events:none; z-index:1;
  background:repeating-linear-gradient(0deg,transparent,transparent 3px,rgba(0,0,0,0.04) 3px,rgba(0,0,0,0.04) 4px);
}}
.gl1,.gl2 {{ position:absolute; border-radius:50%; pointer-events:none; }}
.gl1 {{ width:800px; height:800px; top:-200px; left:-200px;
  background:radial-gradient(circle,rgba(139,92,246,0.12) 0%,transparent 70%); filter:blur(40px); }}
.gl2 {{ width:600px; height:600px; bottom:50px; right:-150px;
  background:radial-gradient(circle,rgba(6,182,212,0.08) 0%,transparent 70%); filter:blur(30px); }}

.content {{
  position:relative; z-index:2;
  padding:72px 84px;
}}

/* HEADER */
.header {{
  display:flex; align-items:center; justify-content:space-between;
  margin-bottom:44px;
}}
.handle {{ font-size:22px; font-weight:700; color:#e8e8e8; letter-spacing:-0.01em; }}
.handle em {{ color:#8B5CF6; font-style:normal; }}
.topic-tag {{
  display:flex; align-items:center; gap:8px;
  background:rgba(139,92,246,0.1); border:1px solid rgba(139,92,246,0.3);
  border-radius:20px; padding:8px 18px;
  font-size:12px; font-weight:700; color:#8B5CF6;
  letter-spacing:0.1em; text-transform:uppercase; font-family:'SF Mono','Fira Code',monospace;
}}
.tdot {{ width:6px; height:6px; background:#8B5CF6; transform:rotate(45deg); flex-shrink:0; }}

/* HEADLINE */
.headline {{
  font-size:76px; font-weight:900; line-height:1.1;
  color:#fff; letter-spacing:-0.03em; margin-bottom:18px;
}}
.subhook {{
  font-size:26px; font-weight:400; color:#666; line-height:1.5; margin-bottom:44px;
}}

/* DIVIDER */
.hdivider {{
  height:1px; margin-bottom:36px;
  background:linear-gradient(90deg,transparent,rgba(139,92,246,0.5),rgba(6,182,212,0.3),transparent);
}}

/* KEY FACTS */
.facts-label {{
  font-size:11px; font-weight:700; color:#8B5CF6;
  letter-spacing:0.18em; text-transform:uppercase; margin-bottom:16px;
  font-family:'SF Mono','Fira Code',monospace;
}}
.facts-grid {{ display:flex; gap:14px; margin-bottom:36px; }}
.fact-card {{
  flex:1; background:rgba(139,92,246,0.06); border:1px solid rgba(139,92,246,0.22);
  border-radius:14px; padding:26px 16px; text-align:center; position:relative; overflow:hidden;
}}
.fact-card::before {{
  content:''; position:absolute; top:0; left:0; right:0; height:2px;
  background:linear-gradient(90deg,#8B5CF6,#06B6D4);
}}
.fact-val {{
  font-size:56px; font-weight:900; line-height:1; letter-spacing:-0.02em;
  background:linear-gradient(135deg,#fff 0%,#C0C0C0 45%,#8B5CF6 100%);
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  margin-bottom:10px;
}}
.fact-lbl {{
  font-size:10px; font-weight:700; color:#444; letter-spacing:0.12em;
  text-transform:uppercase; line-height:1.35;
}}

/* SECTIONS */
.sec-title {{
  font-size:11px; font-weight:700; color:#8B5CF6;
  letter-spacing:0.18em; text-transform:uppercase; margin-bottom:14px;
  font-family:'SF Mono','Fira Code',monospace;
}}
.section {{ margin-bottom:28px; }}
.bullet {{
  display:flex; gap:14px; align-items:flex-start;
  margin-bottom:10px; font-size:22px; line-height:1.5; color:#c0c0c0;
}}
.bpre {{ color:#8B5CF6; font-size:22px; flex-shrink:0; margin-top:1px; }}
.bpre-cyan {{ color:#06B6D4; font-size:14px; margin-top:5px; }}

/* JUSTUS TAKE */
.take-block {{
  border-left:3px solid #8B5CF6; padding:20px 24px;
  background:rgba(139,92,246,0.05); border-radius:0 10px 10px 0;
  margin-bottom:26px;
}}
.take-label {{
  font-size:10px; font-weight:700; color:#8B5CF6;
  letter-spacing:0.15em; text-transform:uppercase; margin-bottom:8px;
  font-family:'SF Mono','Fira Code',monospace;
}}
.take-text {{ font-size:21px; line-height:1.55; color:#d0d0d0; font-style:italic; }}

/* KONKRETER SCHRITT */
.step-block {{
  background:rgba(16,185,129,0.06); border:1px solid rgba(16,185,129,0.25);
  border-radius:12px; padding:22px 24px;
  display:flex; gap:16px; align-items:flex-start;
}}
.step-check {{ color:#10B981; font-size:22px; flex-shrink:0; margin-top:2px; }}
.step-label {{
  font-size:10px; font-weight:700; color:#10B981;
  letter-spacing:0.15em; text-transform:uppercase; margin-bottom:6px;
  font-family:'SF Mono','Fira Code',monospace;
}}
.step-text {{ font-size:20px; line-height:1.5; color:#c0c0c0; }}

/* PAGE 2 */
.p2 {{
  display:flex; flex-direction:column;
  align-items:center; justify-content:center;
  text-align:center; padding:100px 100px;
  height:100%;
}}
.p2-eyebrow {{
  font-size:12px; color:#8B5CF6; letter-spacing:0.2em;
  text-transform:uppercase; margin-bottom:50px;
  font-family:'SF Mono','Fira Code',monospace;
}}
.p2-headline {{
  font-size:70px; font-weight:900; color:#fff;
  line-height:1.15; letter-spacing:-0.03em; margin-bottom:48px;
}}
.p2-headline em {{ color:#8B5CF6; font-style:normal; }}
.p2-body {{
  font-size:27px; color:#666; line-height:1.65;
  max-width:820px; margin-bottom:80px;
}}
.p2-deco {{
  width:80px; height:2px; border-radius:1px; margin:0 auto 80px;
  background:linear-gradient(90deg,#8B5CF6,#06B6D4);
}}
.p2-footer {{
  border-top:1px solid rgba(139,92,246,0.18); padding-top:40px; width:100%;
}}
.p2-handle {{ font-size:26px; font-weight:700; color:#8B5CF6; margin-bottom:8px; }}
.p2-url {{ font-size:18px; color:#333; }}
</style>
</head>
<body>

<!-- PAGE 1 -->
<div class="page" style="page-break-after:always;">
  <div class="gl1"></div><div class="gl2"></div>
  <div class="content">

    <div class="header">
      <div class="handle">{b['handle_html']}</div>
      <div class="topic-tag"><div class="tdot"></div>{esc(req.topic_tag)}</div>
    </div>

    <div class="headline">{esc(req.headline)}</div>
    <div class="subhook">{esc(req.subhook)}</div>

    <div class="hdivider"></div>

    <div class="facts-label">Key Facts</div>
    <div class="facts-grid">{facts_html}</div>

    <div class="hdivider"></div>

    <div class="section">
      <div class="sec-title">Was passiert gerade?</div>
      {was_passiert_html}
    </div>

    <div class="section">
      <div class="sec-title">Was bringt mir das?</div>
      {was_bringt_html}
    </div>

    <div class="take-block">
      <div class="take-label">{b['take_label']}</div>
      <div class="take-text">{esc(req.justus_take)}</div>
    </div>

    <div class="step-block">
      <div class="step-check">&#x2713;</div>
      <div>
        <div class="step-label">1 konkreter Schritt den du heute tun kannst</div>
        <div class="step-text">{esc(req.konkreter_schritt)}</div>
      </div>
    </div>

  </div>
</div>

<!-- PAGE 2: CTA (statisch) -->
<div class="page">
  <div class="gl1"></div><div class="gl2"></div>
  <div class="p2">
    <div class="p2-eyebrow">{b['p2_eyebrow']}</div>
    <div class="p2-headline">{b['p2_headline']}</div>
    <div class="p2-body">
      {b['p2_body']}
    </div>
    <div class="p2-deco"></div>
    <div class="p2-footer">
      <div class="p2-handle">@justus.automates</div>
      <div class="p2-url">justus.automates</div>
    </div>
  </div>
</div>

</body>
</html>"""
    # ── brand-swap: replace hardcoded Justus tokens with client template values ──
    bgc = (((tpl or {}).get("colors") or {}).get("bg")) or "#0a0910"
    _html_doc = (_html_doc
        .replace("#8B5CF6", primary)
        .replace("#06B6D4", accent)
        .replace("#C0C0C0", secondary)
        .replace("#0a0910", bgc)
        .replace("@justus.automates", b["p2_handle"])
        .replace("justus.automates", b["p2_url"]))
    return _html_doc


# ── Infosheet: HTML → PDF via Playwright ──────────────────────────────────────
async def render_html_to_pdf(html_path: Path, output_path: Path) -> bool:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.error("[PDF] Playwright not installed")
        return False
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            page = await browser.new_page(viewport={"width": 1080, "height": 1920})
            await page.goto(f"file://{html_path.absolute()}", wait_until="load", timeout=30000)
            await page.pdf(
                path=str(output_path),
                print_background=True,
                width="1080px",
                height="1920px",
            )
            await browser.close()
        log.info("[PDF] rendered: %s (%d bytes)", output_path.name, output_path.stat().st_size)
        return True
    except Exception as exc:
        log.error("[PDF] render failed: %s", exc)
        return False


# ── POST /generate-infosheet ──────────────────────────────────────────────────
@app.post("/generate-infosheet")
async def generate_infosheet(req: InfosheetRequest):
    job_id  = str(uuid.uuid4())
    job_dir = Path(f"/tmp/infosheet_{job_id}")
    job_dir.mkdir(parents=True, exist_ok=True)
    log.info("[INFOSHEET] START topic=%s", req.topic_tag)
    try:
        html      = _build_infosheet_html(req)
        html_path = job_dir / "infosheet.html"
        html_path.write_text(html, encoding="utf-8")

        pdf_path = job_dir / "infosheet.pdf"
        ok = await render_html_to_pdf(html_path, pdf_path)
        if not ok or not pdf_path.exists():
            raise HTTPException(status_code=500, detail="PDF rendering failed")

        result = cloudinary.uploader.upload(
            str(pdf_path),
            resource_type="image",          # PDFs as image deliver publicly (raw delivery is blocked → 401)
            type="upload",
            access_mode="public",
            folder="infosheets",
            public_id=f"infosheet_{job_id}",
            format="pdf",
            overwrite=True,
        )
        url = result["secure_url"]
        log.info("[INFOSHEET] uploaded: %s", url)
        return {"url": url}

    except HTTPException:
        raise
    except Exception as exc:
        log.error("[INFOSHEET] Error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Infosheet generation failed: {exc}")
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def _remotion_captions(words: list, max_words: int = 4) -> list:
    """Group WhisperX words into short caption phrases with start/end (seconds)."""
    items, cur, start = [], [], None
    for w in words:
        if start is None:
            start = w["start"]
        cur.append(w)
        if len(cur) >= max_words or re.search(r"[.,!?;:]$", w["word"]):
            items.append({"text": " ".join(x["word"] for x in cur),
                          "start": round(start, 3), "end": round(cur[-1]["end"], 3)})
            cur, start = [], None
    if cur:
        items.append({"text": " ".join(x["word"] for x in cur),
                      "start": round(start, 3), "end": round(cur[-1]["end"], 3)})
    return items


@app.post("/render-remotion")
def render_remotion(req: RemotionRenderRequest):
    """Primary render path: build props from facecam (WhisperX captions + impacts),
    call the Remotion service for the format's composition, then ffmpeg-mux the
    facecam audio onto the muted graphic. Returns a Cloudinary URL."""
    comp = FORMAT_COMPOSITION.get(req.format)
    if not comp:
        raise HTTPException(status_code=400,
                            detail=f"unknown format '{req.format}'; known: {list(FORMAT_COMPOSITION)}")
    job_id = str(uuid.uuid4())
    job_dir = Path(f"/tmp/remotion_{job_id}")
    job_dir.mkdir(parents=True, exist_ok=True)
    log.info("[REMOTION] START format=%s comp=%s", req.format, comp)
    try:
        facecam_path = job_dir / "facecam.mp4"
        if not download_file(req.facecam, facecam_path):
            raise HTTPException(status_code=500, detail="facecam download failed")
        duration = probe_duration(facecam_path)
        words = _whisperx_words(facecam_path) or []
        captions = _remotion_captions(words)

        punch = []
        if req.punch_ins:
            punch = [float(x) for x in req.punch_ins]
        elif req.impacts:
            for it in req.impacts:
                t = it.get("time") if isinstance(it, dict) else it
                if t is not None:
                    punch.append(float(t))
        elif captions:
            punch = [c["start"] for c in captions[1::2]][:6]

        base = {"durationInSeconds": round(duration, 3)}
        if comp == "JustusBroll":
            props = {**base, "face_url": req.facecam,
                     "topicLabel": req.topic_label or "AI // AGENTS",
                     "headline":  req.headline or req.hook_text or "",
                     "stats":     req.stats  or [{"value": "3.4x", "label": "schneller"}],
                     "ticker":    req.ticker or ["AI shipping", "automation +240%"]}
            if req.code_lines:
                props["codeLines"] = req.code_lines
        elif comp == "JustusUsecase":
            props = {**base, "screen_url": req.screen_url or req.facecam,
                     "face_url": req.facecam, "hook_text": req.hook_text, "captions": captions}
        else:  # JustusPunches
            props = {**base, "face_url": req.facecam, "hook_text": req.hook_text,
                     "captions": captions, "punch_ins": punch}

        r = requests.post(f"{REMOTION_URL}/render",
                          json={"composition": comp, "inputProps": props}, timeout=600)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise HTTPException(status_code=502, detail=f"remotion error: {data.get('error')}")
        gfx_url = data["url"]
        log.info("[REMOTION] graphic rendered %s (%d frames)", gfx_url, data.get("durationInFrames", 0))

        gfx_path = job_dir / "gfx.mp4"
        if not download_file(gfx_url, gfx_path):
            raise HTTPException(status_code=500, detail="remotion output download failed")

        out = job_dir / "final.mp4"
        run(["ffmpeg", "-y", "-i", str(gfx_path), "-i", str(facecam_path),
             "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-shortest", "-movflags", "+faststart", str(out)], "remotion_mux")
        url = upload_cloudinary(out, f"remotion_{comp}_{job_id}")
        log.info("[REMOTION] DONE %s", url)
        return {"ok": True, "url": url, "composition": comp, "format": req.format,
                "duration": round(duration, 3), "captions": len(captions)}
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("[REMOTION] fail")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/debug/template")
def debug_template(client_id: str = "justus"):
    """Verify Supabase template resolution works from Railway (env + fetch + parse)."""
    _TEMPLATE_CACHE.pop(client_id, None)
    tpl = _load_template(client_id, None)
    return {
        "client_id": client_id,
        "supabase_env_set": bool(SUPABASE_URL and SUPABASE_SERVICE_KEY),
        "template_loaded": bool(tpl),
        "keys": [k for k in tpl] if tpl else [],
        "colors": _tpl_colors(tpl),
        "hud": (tpl or {}).get("hud"),
        "sfx": (tpl or {}).get("sfx"),
        "images_model": ((tpl or {}).get("images") or {}).get("model"),
    }

@app.post("/enrich-image-prompt")
async def enrich_image_prompt(req: EnrichImageRequest):
    """Upscale a raw keyword into a brand-aesthetic image prompt (3-layer). Optionally
    also generate the image via Flux.1 [schnell]. For reuse by any N8N image node."""
    loop = asyncio.get_event_loop()
    enriched = await loop.run_in_executor(
        _html_executor, lambda: _enrich_image_prompt(req.keyword, req.brand_color_primary))
    result = dict(enriched)
    if req.generate:
        try:
            result["image_url"] = await loop.run_in_executor(
                _html_executor, lambda: _call_fal_flux(enriched["prompt"], enriched["negative"]))
        except Exception as exc:
            result["error"] = str(exc)
    return result

@app.post("/trim-silence")
async def trim_silence(req: TrimSilenceRequest):
    """Front-of-pipeline 2-phase auto-cut. Phase 1 kills acoustic filler words
    (äh/ähm) via Whisper forced-alignment; Phase 2 trims remaining dead air with
    auto-editor (waveform). Returns a NEW tightened facecam URL. Run this FIRST in
    N8N — everything downstream then works on the finalized clean timeline."""
    job_id  = str(uuid.uuid4())
    job_dir = Path(f"/tmp/trim_{job_id}")
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        src = job_dir / "facecam.mp4"
        if not download_file(req.facecam, src):
            raise HTTPException(status_code=500, detail="facecam download failed")
        orig_dur = probe_duration(src)

        current = src

        # ── PHASE 0: smart coherence cut (doppelte Takes / Fehlstarts via LLM) ─
        n_coherence = 0
        if getattr(req, "smart_cut", True):
            # filler-prompt transcript keeps disfluencies (äh/Doppler) visible so the LLM can cut them
            cwords = transcribe_audio(current, prompt=WHISPER_FILLER_PROMPT)
            if cwords:
                ckeeps, n_coherence = _coherence_keep_segments(cwords, probe_duration(current))
                if n_coherence > 0 and len(ckeeps) >= 1:
                    p0 = job_dir / "phase0.mp4"
                    if _trim_dead_air(current, ckeeps, p0):
                        current = p0
                        log.info("[TRIM] phase0: smart cut removed %d words (takes/false-starts)", n_coherence)

        # ── PHASE 1: acoustic filler-word killer ──────────────────────────────
        n_fillers = 0
        words = transcribe_audio(current, prompt=WHISPER_FILLER_PROMPT)
        if words:
            keeps, n_fillers = _filler_keep_segments(words, probe_duration(current))
            if n_fillers > 0 and len(keeps) >= 1:
                p1 = job_dir / "phase1.mp4"
                if _trim_dead_air(current, keeps, p1):   # video hard-cut + 15ms edge declick
                    current = p1
                    log.info("[TRIM] phase1: cut %d filler word(s)", n_fillers)
        else:
            log.warning("[TRIM] phase1: no transcript — skipping filler cut")

        # ── PHASE 2: WORD-BASED dead-air trim (replaces energy auto-editor) ───
        # cuts ONLY in pauses between words (never mid-word) → no half words, smooth
        w2 = transcribe_audio(current)
        if w2:
            keeps2 = _compute_keep_segments(w2, probe_duration(current), max_gap=0.6, pad=0.12)
            if len(keeps2) > 1:
                p2 = job_dir / "phase2.mp4"
                if _trim_dead_air(current, keeps2, p2):
                    current = p2
                    log.info("[TRIM] phase2: word-based dead-air trim, %d keep-segments", len(keeps2))
        else:
            log.warning("[TRIM] phase2: no transcript — skipping dead-air trim")

        # ── result ────────────────────────────────────────────────────────────
        if current == src:
            log.info("[TRIM] nothing to cut — returning original")
            return {"url": req.facecam, "trimmed": False,
                    "original_duration": orig_dur, "trimmed_duration": orig_dur,
                    "removed": 0.0, "fillers_cut": n_fillers}

        final_dur = probe_duration(current)
        removed   = round(orig_dur - final_dur, 3)
        if removed < 0.2:
            return {"url": req.facecam, "trimmed": False,
                    "original_duration": orig_dur, "trimmed_duration": orig_dur,
                    "removed": removed, "fillers_cut": n_fillers}

        url = upload_cloudinary(current, f"trimmed_{job_id}")
        log.info("[TRIM] %.2fs -> %.2fs (removed %.2fs, %d fillers) -> %s",
                 orig_dur, final_dur, removed, n_fillers, url)
        return {"url": url, "trimmed": True,
                "original_duration": orig_dur, "trimmed_duration": final_dur,
                "removed": removed, "fillers_cut": n_fillers}
    except HTTPException:
        raise
    except Exception as exc:
        log.error("[TRIM] error: %s", exc)
        raise HTTPException(status_code=500, detail=f"trim-silence failed: {exc}")
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)

@app.get("/debug/last-broll-scripts")
def debug_last_broll_scripts():
    """Return only the <script> blocks from the last broll HTML — for diagnosing JS errors."""
    p = Path("/tmp/last_broll.html")
    if not p.exists():
        return {"error": "No broll HTML saved yet — trigger a /generate-broll-synced run first"}
    html = p.read_text(encoding="utf-8")
    import re as _re
    scripts = _re.findall(r'<script[^>]*>.*?</script>', html, flags=_re.DOTALL | _re.IGNORECASE)
    # Extract scene0 div to see what Sonnet generated
    scene0_match = _re.search(r'(<div[^>]*id=["\']scene0["\'][^>]*>.*?)((?=<div[^>]*id=["\']scene1)|$)', html, flags=_re.DOTALL)
    scene0_preview = scene0_match.group(1)[:4000] if scene0_match else "scene0 NOT FOUND"
    return {
        "total_html_chars": len(html),
        "script_count": len(scripts),
        "scene0_preview": scene0_preview,
        "scripts": [s[:3000] for s in scripts],
    }


@app.get("/debug/gen-anim-script")
def debug_gen_anim_script():
    """Isolated proof of the 2nd-call fix: run _gen_animation_script on a fixed
    representative scene (counter + self-drawing SVG graph + hero rings) and return
    the real Sonnet-generated <script>. Lets us verify the fix without the full pipeline."""
    divs = (
        '<div class="scene" id="scene0" style="background:#0a0910;">'
        '<svg id="heroSvg" viewBox="0 0 180 180" width="180" height="180">'
        '<circle id="ring1" cx="90" cy="90" r="60" fill="none" stroke="#8B5CF6" stroke-width="3"/>'
        '<circle id="ring2" cx="90" cy="90" r="40" fill="none" stroke="#06B6D4" stroke-width="2"/></svg>'
        '<div class="dp" id="counter0">0</div>'
        '<div class="lbl">Milliarden USD Deal-Volumen</div>'
        '<svg id="graphSvg" viewBox="0 0 400 120" width="400" height="120">'
        '<path id="gl" d="M 0,110 L 120,82 L 240,48 L 360,18 L 400,8" fill="none" '
        'stroke="#06B6D4" stroke-width="3"/></svg></div>'
    )
    scenes = [{"start": 0.0, "end": 6.0}]
    try:
        script = _gen_animation_script(divs, scenes, "#8B5CF6")
    except Exception as exc:
        return {"error": str(exc)}
    return {
        "complete": _has_complete_animation_script(script),
        "script_chars": len(script),
        "script": script[:6000],
    }
