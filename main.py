import os
import hashlib
import uuid
import shutil
import logging
import subprocess
import math
import asyncio
import json
import copy
from datetime import datetime, timezone
import re
import time
import contextvars
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import httpx
import requests
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from PIL import Image, ImageDraw, ImageFont
import cloudinary
import cloudinary.uploader
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Cloudinary (LEGACY — uploads moved to Supabase Storage; only the static SFX
#    library is still served from res.cloudinary.com. Config is now optional so the
#    service boots even after the Cloudinary env vars are removed). ──────────────
if os.environ.get("CLOUDINARY_CLOUD_NAME"):
    cloudinary.config(
        cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME", ""),
        api_key=os.environ.get("CLOUDINARY_API_KEY", ""),
        api_secret=os.environ.get("CLOUDINARY_API_SECRET", ""),
    )

openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
GOOGLE_AI_KEY = os.environ.get("GOOGLE_AI_KEY", "")
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
SUPABASE_BUCKET      = os.environ.get("SUPABASE_BUCKET", "media")  # Storage bucket for all generated media (replaces Cloudinary)
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
    # Der alte Standalone-Dienst (…-4e7d) schläft mit altem Code und antwortet
    # nicht mehr. Der Default zeigte trotzdem noch dorthin — eine Mine für den
    # Tag, an dem die Env-Variable mal fehlt.
    "REMOTION_URL", "https://remotion-production-6381.up.railway.app"
).rstrip("/")
FORMAT_COMPOSITION = {
    "broll_automated":      "JustusBroll",
    "usecase_bubble":       "JustusUsecase",
    "talking_head_punches": "JustusPunches",
    # Quadrant format keys (Supabase clients.formats / used_topics.format) map
    # straight onto a composition, so the render-engine can pass the format key
    # it already has instead of a second translation table in n8n.
    "tool_drop":            "JustusUsecase",   # Q1 tool_reveal
    "tool_reveal":          "JustusUsecase",
    "build_story":          "JustusBroll",     # Q2 builder-in-the-trenches
    "business_result":      "JustusPunches",   # Q3
    "hot_take":             "JustusPunches",   # Q4
    # Tim Kemper quadrant keys → his own compositions (TIM_BRAND petrol/gold)
    "freiheits_werkzeug":   "TimTalkingHead",  # Q1
    "bollerwagen":          "TimTalkingHead",  # Q2
    "news_impact":          "TimBroll",        # Q3 (the one gold-stat)
    "klartext":             "TimTalkingHead",  # Q4
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
    impacts:     Optional[list] = None   # SFX cues + punch source; detected if omitted
    sfx:         bool = True             # mix the SFX library into the final audio
    overlays:    bool = True             # fly-in stock cards (punches only)
    trim:        bool = True             # auto-cut before rendering (skip if N8N already did)
    grade:       bool = True             # cinematic colour grade on the facecam
    briefing:    Optional[dict] = None   # production_briefing.segments → regie drives the render
    regie_hints: Optional[dict] = None   # used_topics.regie_hints — punch_words/lower_thirds/
                                         # cta_keyword/Blockgrenzen. Nie im Kunden-Chat; wird
                                         # gegen das echte WhisperX-Transkript remappt.
    qa:          bool = True             # Gemini QA gate on the final render
    bg_mode:     str = "original"        # original | canvas — replace bg with a studio canvas + matte
    thumbnail:   bool = True             # append a 0.2s thumbnail end-card
    thumbnail_url:     Optional[str] = None   # pre-made end-card image; else auto-generated
    thumbnail_concept: Optional[str] = None   # fal.ai prompt (defaults to hook_text/headline)
    flow_diagram: Optional[dict] = None  # force a node-graph {nodes,chips,startFrame,endFrame} (else director)
    cta_word:     Optional[dict] = None  # force a CTA word {word,startFrame,endFrame} (else director)
    scenes:       Optional[list] = None  # force full-screen cutaway scenes (else director/vscript)
    music_url:    Optional[str] = None   # background bed, sidechain-ducked under the voice (§4)
    metaphern:    bool = True            # Anker→Entfaltung→Rang→Stock-Router als echte Bildebene
    contact_sheet: bool = True           # Kontaktblatt (Filmstreifen+Wellenform+Wortraster) an die Regie
    layer_stage:  bool = True            # R3 Teil 1: über die generische Ebenen-Composition rendern.
                                         # Pixelgleich zum alten Weg (SSIM-Kontrolle 2026-07-28),
                                         # Adapter in remotion-renderer/src/layers/legacy.ts.
                                         # false = zurück auf JustusPunches, falls etwas klemmt.


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
    # 0.30/0.05 hat jede Atempause gefressen und mit nur 50ms Rand die Wortenden
    # angeschnitten. Es soll geschnitten werden, aber fluessig klingen.
    max_gap: float = 0.45   # gaps <= this stay (natural cadence)
    pad:     float = 0.12   # silence kept on each side of a trimmed gap
    # AUS. Phase 0 entfernt gesprochene Woerter und hat dabei die Pointe des Hooks
    # gefressen. Der Trimmer schneidet Stille, er redigiert nicht.
    smart_cut: bool = False  # phase 0: LLM coherence cut (duplicate takes / false starts)


class ImageRequest(BaseModel):
    prompt:       str
    aspect_ratio: str = "1:1"
    reference:    list[str] = []   # image URLs -> nano-banana-pro/edit (keeps the character)
    num_images:   int = 1


class TranscribeRequest(BaseModel):
    audio:    str          # any downloadable URL — Telegram voice notes are .oga
    language: str = "de"
    prompt:   str = ""     # optional vocabulary hint for Whisper


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


CACHE_MARK = {"cache_control": {"type": "ephemeral"}}


# ── Kosten-Messung ────────────────────────────────────────────────────────────
# Erst messen, dann optimieren. Jeder bezahlte Aufruf schreibt eine Zeile in
# run_log: Modelle mit Token-Zahlen, alles andere mit einer Menge (Bild, Minute,
# Sekunde Renderzeit). Solange diese Zeilen fehlen, ist jede Aussage darueber,
# welcher Posten der groesste ist, geraten — und man optimiert den falschen.
#
# Preise in USD, Stand 2026-07-29. Bewusst im Code und nicht in der DB: eine
# Preisaenderung ist ein Deploy wert, sonst sehen alte Auswertungen rueckwirkend
# anders aus. LLM-Tupel: (ein, aus, cache_write, cache_read) je 1 Mio Token.
PREISE_LLM = {
    "anthropic/claude-sonnet-4.6":  (3.00, 15.00, 3.75, 0.30),
    "anthropic/claude-sonnet-4.5":  (3.00, 15.00, 3.75, 0.30),
    "anthropic/claude-haiku-4.5":   (1.00,  5.00, 1.25, 0.10),
    "z-ai/glm-4.6":                 (0.40,  1.75, 0.50, 0.04),
    "google/gemini-2.5-flash":      (0.30,  2.50, 0.38, 0.075),
    "perplexity/sonar":             (1.00,  1.00, 1.00, 1.00),
    "perplexity/sonar-pro":         (3.00, 15.00, 3.00, 3.00),
}
# Unbekanntes Modell teuer rechnen. Eine zu hohe Schaetzung faellt auf und wird
# korrigiert; eine zu niedrige laesst den Posten unsichtbar bleiben.
PREIS_LLM_UNBEKANNT = (3.00, 15.00, 3.75, 0.30)

PREISE_EINHEIT = {                  # USD je Einheit
    "fal-nano-banana-pro": 0.140,   # ein Bild
    "fal-flux-2-flash":    0.015,   # ein Bild
    "fal-sonstige":        0.050,   # ein Bild, unbekannter Endpunkt
    "replicate-whisperx":  0.0060,  # eine Minute Audio
    "openai-whisper-1":    0.0060,  # eine Minute Audio
    "railway-render":      0.00040, # eine Sekunde Renderzeit (vCPU + RAM)
}

# Wer gerade bedient wird. Die Detektoren tief im Renderer bekommen die client_id
# nicht durchgereicht — ohne sie faellt jede Zeile auf "unknown" und die
# Auswertung pro Kunde ist wertlos. ContextVar statt Parameter durch 20 Ebenen.
AKTIVER_CLIENT = contextvars.ContextVar("aktiver_client", default="")
_kosten_pool = ThreadPoolExecutor(max_workers=2)


def _wer() -> str:
    try:
        return AKTIVER_CLIENT.get() or ""
    except Exception:
        return ""


def _log_kosten(client_id: str, tool: str, status: str, detail: dict) -> None:
    """Im Hintergrund schreiben. Messung darf den gemessenen Weg nicht bremsen —
    sonst misst man am Ende die Messung mit."""
    cid = client_id or _wer()
    try:
        _kosten_pool.submit(_log_run, cid, tool, status, detail)
    except Exception as exc:
        log.warning("[KOSTEN] %s", exc)


def _log_llm(tool: str, model: str, usage: dict, dauer_ms: int,
             client_id: str = "", status: str = "ok", extra: Optional[dict] = None) -> None:
    u = usage or {}
    ein = int(u.get("prompt_tokens") or 0)
    aus = int(u.get("completion_tokens") or 0)
    cached = int(((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
    _log_kosten(client_id, tool, status,
                {"art": "llm", "modell": model, "ein": ein, "aus": aus,
                 "cached": cached, "dauer_ms": int(dauer_ms), **(extra or {})})


def _log_einheit(tool: str, einheit: str, menge: float, dauer_ms: int,
                 client_id: str = "", status: str = "ok",
                 extra: Optional[dict] = None) -> None:
    """Bilder, Audio-Minuten, Renderzeit. Alles, was nach Stueck abgerechnet wird."""
    _log_kosten(client_id, tool, status,
                {"art": "einheit", "einheit": einheit, "menge": round(float(menge), 4),
                 "dauer_ms": int(dauer_ms), **(extra or {})})


def _audio_minuten(pfad: Path) -> float:
    """Laenge aus der Dateigroesse. Das Audio wird immer mit 64 kbit/s mono
    extrahiert, also 8 kB je Sekunde — genau genug fuer eine Kostenzeile, und
    kostet keinen ffprobe-Aufruf."""
    try:
        return round(Path(pfad).stat().st_size / 8000.0 / 60.0, 3)
    except Exception:
        return 0.0


def call_openrouter(system_prompt: str, user_message: str,
                    model: str = "anthropic/claude-haiku-4.5",
                    max_tokens: int = 6000,
                    image_path: Optional[Path] = None,
                    cache_system: bool = False,
                    cache_prefix: Optional[str] = None,
                    tool: str = "llm-unbenannt",
                    client_id: str = "") -> str:
    """image_path haengt EIN Bild an die User-Nachricht. Ohne Bild bleibt der
    Content ein String — Modelle, die keine Bilder koennen, sehen keinen Unterschied.

    `tool` ist der Name des Postens in der Kostenauswertung. Default absichtlich
    haesslich: eine Zeile "llm-unbenannt" in /tool/stats/kosten zeigt sofort, wo
    noch ein Aufruf ohne Namen sitzt.

    cache_system / cache_prefix setzen einen Cache-Punkt auf den STATISCHEN Teil
    (Systemprompt, Few-Shots, Kontaktblatt, Transkript). Das zahlt sich erst aus,
    wenn derselbe Praefix mehrfach geschickt wird — heute bei den wiederholten
    Aufrufen der Anker-Maschine, ab dem Tool-Loop bei jedem einzelnen Turn.
    Anthropic ignoriert einen Cache-Punkt unter ~1024 Token stillschweigend; das
    ist kein Fehler, es passiert dann nur nichts.
    """
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://schultensolutions.app.n8n.cloud",
        "X-Title": "Schulten Solutions Video Renderer",
    }
    parts: list = []
    if cache_prefix:
        parts.append({"type": "text", "text": cache_prefix, **CACHE_MARK})
    parts.append({"type": "text", "text": user_message})
    if image_path and Path(image_path).exists():
        import base64
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
        parts.append({"type": "image_url",
                      "image_url": {"url": "data:image/jpeg;base64," + b64}})
    # Ohne Bild und ohne Praefix bleibt es ein schlichter String — so wie vorher.
    user_content = parts[0]["text"] if len(parts) == 1 and not cache_prefix else parts
    system_content = ([{"type": "text", "text": system_prompt, **CACHE_MARK}]
                      if cache_system else system_prompt)
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user",   "content": user_content},
        ],
    }
    if "glm" in model.lower():
        payload["reasoning"] = {"enabled": False}   # GLM burns tokens on reasoning otherwise
    t0 = time.time()
    try:
        resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=180)
        resp.raise_for_status()
    except Exception:
        # Auch der Fehlschlag kostet Zeit und manchmal Tokens. Ohne Zeile sieht
        # ein Posten, der staendig scheitert und wiederholt wird, billig aus.
        _log_llm(tool, model, {}, int((time.time() - t0) * 1000), client_id, "fehler")
        raise
    dauer_ms = int((time.time() - t0) * 1000)
    try:
        d = resp.json()
    except Exception as exc:
        _log_llm(tool, model, {}, dauer_ms, client_id, "fehler")
        log.error("OpenRouter parse error. status=%d body=%s", resp.status_code, resp.text[:400])
        raise RuntimeError(f"OpenRouter non-JSON response: {resp.text[:200]}") from exc
    _log_llm(tool, model, d.get("usage") or {}, dauer_ms, client_id)
    try:
        return d["choices"][0]["message"]["content"]
    except Exception as exc:
        log.error("OpenRouter parse error. status=%d body=%s", resp.status_code, resp.text[:400])
        raise RuntimeError(f"OpenRouter non-JSON response: {resp.text[:200]}") from exc


HTML_TOOL_MAX_S = 8.0        # ein vom Agenten gebautes Element, kein Film
HTML_TOOL_MAX_PX = 1080 * 1920


async def _render_html_alpha(markup: str, width: int, height: int, seconds: float,
                             job_dir: Path, fps: int = FPS) -> tuple:
    """HTML/CSS/GSAP → transparentes WebM (vp9, yuva420p).

    NICHT der Aufnahme-Weg von `render_html_to_video`: der nimmt mit der Wanduhr
    auf, liefert kein Alpha und muss hinterher graue Startframes wegschneiden.
    Hier wird Frame fuer Frame gestellt — GSAP und die CSS-Animationen werden auf
    t = f/fps gesetzt, dann ein Screenshot mit `omit_background`. Deterministisch:
    derselbe Markup ergibt zweimal dieselben Pixel.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.error("[HTMLTOOL] Playwright fehlt")
        return None, ["Playwright fehlt"]

    seconds = max(0.3, min(float(seconds), HTML_TOOL_MAX_S))
    if width * height > HTML_TOOL_MAX_PX:
        log.warning("[HTMLTOOL] %dx%d ueber der Grenze — auf Leinwandmass gekappt", width, height)
        width, height = W, H
    frames = int(round(seconds * fps))
    shots = job_dir / "htmlshots"
    shots.mkdir(parents=True, exist_ok=True)

    page_html = (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        "html,body{margin:0;padding:0;background:transparent;overflow:hidden;"
        f"width:{width}px;height:{height}px}}*{{box-sizing:border-box}}</style></head>"
        f"<body>{markup}</body></html>"
    )
    src = job_dir / "tool.html"
    src.write_text(page_html, encoding="utf-8")

    t0 = time.time()
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = await browser.new_context(viewport={"width": width, "height": height},
                                        device_scale_factor=1)
        if GSAP_LOCAL.exists():
            await ctx.add_init_script(path=str(GSAP_LOCAL))
        page = await ctx.new_page()
        errs: list = []
        page.on("pageerror", lambda e: errs.append(str(e)[:160]))
        await page.goto(f"file://{src.absolute()}", wait_until="load", timeout=20000)
        # Beide Uhren anhalten, damit NICHTS von der Wanduhr abhaengt.
        await page.evaluate("""() => {
            if (window.gsap) { gsap.globalTimeline.pause(); }
            document.getAnimations().forEach(a => { a.pause(); });
        }""")
        # Passt der Inhalt ueberhaupt in die angeforderte Flaeche? Der Wrapper
        # setzt overflow:hidden — was darueber hinausragt, wird still
        # abgeschnitten und faellt erst im fertigen Video auf.
        ueberlauf = await page.evaluate(
            """() => ({ b: document.body.scrollWidth, h: document.body.scrollHeight })""")
        for f in range(frames):
            t = f / fps
            await page.evaluate(
                """(t) => {
                    if (window.gsap) gsap.globalTimeline.seek(t);
                    document.getAnimations().forEach(a => { a.currentTime = t * 1000; });
                }""", t)
            await page.screenshot(path=str(shots / f"{f:05d}.png"), omit_background=True)
        await ctx.close()
        await browser.close()
    if errs:
        log.warning("[HTMLTOOL] %d JS-Fehler, erster: %s", len(errs), errs[0])
    ueber = []
    if ueberlauf.get("b", 0) > width + 2:
        ueber.append(f"Inhalt ist {ueberlauf['b']}px breit, die Leinwand nur {width}px — "
                     f"links/rechts wird abgeschnitten")
    if ueberlauf.get("h", 0) > height + 2:
        ueber.append(f"Inhalt ist {ueberlauf['h']}px hoch, die Leinwand nur {height}px — "
                     f"unten wird abgeschnitten")
    if ueber:
        log.warning("[HTMLTOOL] Ueberlauf: %s", "; ".join(ueber))

    # Prüfen, ob die Screenshots ueberhaupt Alpha tragen. Ohne diese Zeile sieht
    # ein verlorener Alpha-Kanal aus wie ein gelungener Render — dieselbe
    # Fehlerklasse wie der tote Face-Track.
    probe = shots / "00000.png"
    try:
        _a = Image.open(str(probe)).convert("RGBA").getchannel("A")
        alpha_min, alpha_max = _a.getextrema()
    except Exception:
        alpha_min = alpha_max = 255
    if alpha_min == 255:
        log.warning("[HTMLTOOL] Screenshots sind deckend (Alpha %d-%d) — "
                    "das Markup malt einen eigenen Hintergrund", alpha_min, alpha_max)

    out = job_dir / f"htmltool_{uuid.uuid4().hex[:8]}.webm"
    try:
        run(["ffmpeg", "-y", "-framerate", str(fps), "-i", str(shots / "%05d.png"),
             "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "28",
             "-auto-alt-ref", "0", str(out)], "htmltool_webm")
    except Exception as exc:
        log.error("[HTMLTOOL] ffmpeg: %s", exc)
        return None, ueber

    # WebM legt Alpha NICHT im Pixelformat des Hauptstroms ab, sondern als
    # Nebenspur. ffprobe meldet fuer den Hauptstrom deshalb weiter yuv420p, auch
    # wenn alles stimmt — der Beweis steht im Tag alpha_mode, und dekodieren
    # laesst sich die Spur nur mit `-c:v libvpx-vp9` VOR dem Input.
    # Wer hier auf pix_fmt prueft, baut sich einen Fehlalarm.
    try:
        tag = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                              "-show_entries", "stream_tags=alpha_mode",
                              "-of", "csv=p=0", str(out)],
                             capture_output=True, text=True).stdout.strip()
    except Exception:
        tag = ""
    if tag != "1":
        log.error("[HTMLTOOL] keine Alpha-Spur (alpha_mode=%r)", tag)
        return None, ueber
    log.info("[HTMLTOOL] %d Frames %dx%d in %.1fs -> %.1f KB, alpha_mode=1 (Screenshot-Alpha %d-%d)",
             frames, width, height, time.time() - t0, out.stat().st_size / 1024,
             alpha_min, alpha_max)
    return out, ueber


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


def _audio_onsets(src: Path, job_dir: Path) -> list:
    """§1 transient detection — times (s) where the audio energy jumps (hard
    consonants / stress). Used to snap visual events onto the beat so nothing
    lands a couple frames off. Lightweight energy-flux, no librosa."""
    try:
        import numpy as np
        import wave
        wav = job_dir / "onset.wav"
        subprocess.run(["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "22050", str(wav)],
                       check=True, capture_output=True)
        with wave.open(str(wav), "rb") as w:
            sr = w.getframerate()
            sig = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
        if sig.size < sr:
            return []
        hop = 512
        n = sig.size // hop
        env = np.array([np.sqrt(np.mean(sig[i * hop:(i + 1) * hop] ** 2) + 1) for i in range(n)])
        flux = np.diff(env, prepend=env[0])
        flux[flux < 0] = 0
        thr = flux.mean() + 1.4 * flux.std()
        onsets = []
        last = -999
        for i in range(1, n - 1):
            if flux[i] > thr and flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1]:
                t = i * hop / sr
                if t - last > 0.12:
                    onsets.append(round(t, 3))
                    last = t
        return onsets
    except Exception as exc:
        log.warning("[ONSET] detection failed: %s", exc)
        return []


def _snap_frame(f: int, onsets: list, fps: int, max_shift: float = 0.12) -> int:
    """Snap a frame to the nearest audio onset within max_shift seconds."""
    if not onsets:
        return f
    t = f / fps
    best = min(onsets, key=lambda o: abs(o - t))
    return int(round(best * fps)) if abs(best - t) <= max_shift else f


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
    t0 = time.time()
    minuten = _audio_minuten(audio_path)
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
        _log_einheit("transkript-whisperx", "replicate-whisperx", minuten,
                     int((time.time() - t0) * 1000),
                     status="ok" if words else "warn", extra={"woerter": len(words)})
        if words:
            log.info("[WHISPERX] %d words (precise align)", len(words))
            return words
        log.warning("[WHISPERX] no words in output — falling back")
        return None
    except Exception as exc:
        # Fehlgeschlagen heisst nicht kostenlos: die GPU-Zeit bis zum Abbruch ist
        # bezahlt, und danach laeuft zusaetzlich whisper-1. Doppelt bezahlt.
        _log_einheit("transkript-whisperx", "replicate-whisperx", minuten,
                     int((time.time() - t0) * 1000), status="fehler")
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
        _t0 = time.time()
        with open(audio_path, "rb") as af:
            resp = openai_client.audio.transcriptions.create(file=af, **kwargs)
        _log_einheit("transkript-whisper1", "openai-whisper-1", _audio_minuten(audio_path),
                     int((time.time() - _t0) * 1000),
                     extra={"fuellwoerter_pass": bool(prompt)})
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


def _silence_intervals(audio_path: Path, noise_db: int = -30, min_silence: float = 0.35) -> list:
    """Real silence intervals from ffmpeg silencedetect — the hard audio signal
    that Whisper word-gaps miss (loose word ends, breaths inside word spans)."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-i", str(audio_path), "-af",
             f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"],
            capture_output=True, text=True).stderr
        starts = [float(x) for x in re.findall(r"silence_start:\s*([0-9.]+)", out)]
        ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", out)]
        return list(zip(starts, ends))
    except Exception as exc:
        log.warning("[TRIM] silencedetect failed: %s", exc)
        return []


def _silence_keep_segments(silences: list, duration: float, pad: float = 0.09) -> list:
    """Keep everything except the interior of each real silence (minus a small pad
    so speech onsets/tails aren't clipped)."""
    keeps, cur = [], 0.0
    for s, e in silences:
        a, b = s + pad, e - pad
        if b - a <= 0.06:
            continue
        if a > cur:
            keeps.append((round(cur, 3), round(a, 3)))
        cur = b
    if cur < duration:
        keeps.append((round(cur, 3), round(duration, 3)))
    return [(s, e) for s, e in keeps if e - s > 0.05]


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
        parts.append(f"[{cur_a}][a{i}]acrossfade=d={dd:.3f}:c1=tri:c2=tri[ax{i}];")
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
# The opening line IS the hook — the single most retention-critical moment. The
# coherence pass (whose whole job is deleting sentence-beginnings/restarts) kept
# eating it, so no removal is allowed to touch the first HOOK_GUARD_S seconds.
HOOK_GUARD_S = float(os.getenv("HOOK_GUARD_S", "3.0"))
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
    "- HOOK IST HEILIG: Der ALLERERSTE gesprochene Satz (die Anfangssekunden) ist der Hook und wird NIE "
    "entfernt, auch wenn er wie ein Anlauf/Wiederholung wirkt. Schneide erst AB dem zweiten Satz.\n"
    "- IM ZWEIFEL NICHT SCHNEIDEN: Entferne NUR was EINDEUTIG ein doppelter Take/Abbruch ist (derselbe Satz "
    "zweimal, klarer Versprecher mit Neustart). Lieber ein Wort zu viel behalten als einen echten Satz "
    "zerschneiden. Konservativ bleiben.\n"
    'OUTPUT NUR JSON: {"remove": [[startIdx, endIdx], ...]} - Indizes inklusive, auf die Wort-Indizes '
    'bezogen, aufsteigend, ohne Ueberlappung. Nichts zu entfernen -> {"remove": []}.'
)


def _hook_guard_end(words: list) -> float:
    """Ende des ERSTEN Satzes, nicht eine feste Sekundenzahl. Ein fixer 3s-Guard
    schneidet mitten in den Hook, wenn der Satz laenger ist — und genau das ist
    passiert ('...und auf einmal | war meine Kampagne weg' → Pointe weg).
    Satzgrenze = erste echte Pause nach mindestens 6 Woertern."""
    if not words:
        return HOOK_GUARD_S
    for i in range(len(words) - 1):
        if i < 5:
            continue
        gap = float(words[i + 1]["start"]) - float(words[i]["end"])
        if gap >= 0.35:
            return max(HOOK_GUARD_S, min(12.0, float(words[i]["end"]) + 0.15))
    return max(HOOK_GUARD_S, min(12.0, float(words[-1]["end"])))


def _coherence_keep_segments(words: list, duration: float, pad: float = 0.12):
    """LLM analysiert das Wort-Transkript und entfernt doppelte Takes / Fehlstarts.
    Returns (keeps, n_removed_words). Faellt sicher auf 'alles behalten' zurueck."""
    if not words or len(words) < 8:
        return [(0.0, duration)], 0
    transcript = "\n".join(f"{i}\t{w.get('word','')}" for i, w in enumerate(words))
    try:
        raw = call_openrouter(COHERENCE_SYS, transcript,
                              model="anthropic/claude-sonnet-4.5", max_tokens=1500,
                              tool="schnitt-kohaerenz")
        m = re.search(r"\{[\s\S]*\}", raw)
        remove = json.loads(m.group(0)).get("remove", []) if m else []
    except Exception as exc:
        log.warning("[SMART] coherence analysis failed: %s", exc)
        return [(0.0, duration)], 0
    n = len(words)
    guard = _hook_guard_end(words)
    removes, removed_words = [], 0
    for pair in remove:
        try:
            a, b = int(pair[0]), int(pair[1])
        except Exception:
            continue
        a, b = max(0, a), min(n - 1, b)
        if b < a:
            continue
        # HOOK GUARD: der erste Satz ist unantastbar. Frueher wurde ein
        # ueberlappender Schnitt auf den Guard GEKAPPT — das garantiert einen
        # Schnitt mitten im Hooksatz. Jetzt faellt die Entfernung ganz weg.
        if float(words[a]["start"]) < guard:
            log.info("[SMART] hook-guard: removal %d-%d verworfen (Hook bis %.2fs)", a, b, guard)
            continue
        # Nur an echten Pausen schneiden. Liegt eine Kante mitten im Redefluss,
        # hoert man den Schnitt als abgehackten Halbsatz — dann lieber nicht.
        gap_vor  = float(words[a]["start"]) - float(words[a - 1]["end"]) if a > 0 else 99.0
        gap_nach = float(words[b + 1]["start"]) - float(words[b]["end"]) if b < n - 1 else 99.0
        if gap_vor < 0.18 or gap_nach < 0.18:
            log.info("[SMART] removal %d-%d verworfen (keine Pause: vor %.2fs, nach %.2fs)",
                     a, b, gap_vor, gap_nach)
            continue
        s = max(0.0, float(words[a]["start"]) - pad)
        e = min(duration, float(words[b]["end"]) + pad)
        if e > s:
            removes.append((s, e)); removed_words += (b - a + 1)
    # Reissleine: wer ein Viertel des Textes wegwirft, hat den Take nicht
    # verstanden. Dann lieber gar kein Phase-0-Schnitt.
    if removed_words > 0.25 * n:
        log.warning("[SMART] %d/%d Woerter vorgeschlagen (>25%%) — Phase 0 komplett verworfen",
                    removed_words, n)
        return [(0.0, duration)], 0
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
        raw = call_openrouter(sys_p, json.dumps(wl), model=CHEAP_MODEL, max_tokens=500,
                              tool="wort-klassen")
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


# ── Supabase Storage (replaces Cloudinary for all generated media) ────────────
_CONTENT_TYPES = {
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".pdf": "application/pdf", ".html": "text/html", ".mp3": "audio/mpeg",
    ".wav": "audio/wav", ".json": "application/json",
}
_BUCKET_READY = False


def _ensure_bucket() -> None:
    """Create the public Storage bucket once (idempotent — 'already exists' is fine)."""
    global _BUCKET_READY
    if _BUCKET_READY:
        return
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY not set — cannot upload media")
    hdr = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
           "Content-Type": "application/json"}
    # No file_size_limit: it must stay under the project's global cap or the
    # create is rejected 413; omitting it inherits the project default.
    r = requests.post(f"{SUPABASE_URL}/storage/v1/bucket", headers=hdr, timeout=30,
                      json={"id": SUPABASE_BUCKET, "name": SUPABASE_BUCKET, "public": True})
    if r.status_code in (200, 201):
        log.info("[STORAGE] created bucket '%s'", SUPABASE_BUCKET)
    elif "exist" in r.text.lower():
        pass  # already there
    else:
        # hard failure — don't cache success, surface it so the upload doesn't 404
        raise RuntimeError(f"bucket create failed {r.status_code}: {r.text[:300]}")
    _BUCKET_READY = True


def upload_supabase(path: Path, key: str, folder: str = "renders",
                    content_type: str = None) -> str:
    """Upload a file to Supabase Storage and return its public URL.
    Object path = <folder>/<key><ext>. Replaces the old Cloudinary uploader —
    same call shape as upload_cloudinary(path, public_id)."""
    _ensure_bucket()
    ext = path.suffix.lower() or ""
    ctype = content_type or _CONTENT_TYPES.get(ext, "application/octet-stream")
    obj = f"{folder}/{key}{ext}" if not key.endswith(ext) else f"{folder}/{key}"
    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{obj}"
    hdr = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
           "Content-Type": ctype, "x-upsert": "true", "cache-control": "3600"}
    log.info("[STORAGE] uploading %s → %s", path.name, obj)
    with open(path, "rb") as fh:
        r = requests.post(url, headers=hdr, data=fh, timeout=300)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Supabase upload failed {r.status_code}: {r.text[:400]}")
    return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{obj}"


def upload_cloudinary(path: Path, public_id: str) -> str:
    """Back-compat shim — all media now goes to Supabase Storage."""
    return upload_supabase(path, public_id, folder="renders")


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
    _t0 = time.time()
    resp = requests.post(FAL_THUMBNAIL_ENDPOINT, headers=headers, json=payload, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    # Ein Bild, egal ob direkt oder ueber die Warteschlange. Gezaehlt wird der
    # abgeschickte Auftrag — fal rechnet ab da ab, auch wenn das Polling scheitert.
    _log_einheit("bild-thumbnail", "fal-nano-banana-pro", 1,
                 int((time.time() - _t0) * 1000))

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
            sys_p, f"Keyword: {keyword}", model=CHEAP_MODEL, max_tokens=120,
            tool="bild-enricher"))).strip() \
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
        raw = call_openrouter(sys_p, user, model="anthropic/claude-sonnet-4.6", max_tokens=2500,
                              tool="bild-prompts")
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
    _t0 = time.time()
    resp = requests.post(endpoint, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    _log_einheit("bild-cutaway",
                 "fal-flux-2-flash" if "flux-2/flash" in endpoint else "fal-sonstige",
                 1, int((time.time() - _t0) * 1000), extra={"endpunkt": endpoint})
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
                              model=CHEAP_MODEL, max_tokens=400, tool="bild-schnitte")
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
                              model=CHEAP_MODEL, max_tokens=700, tool="stock-schnitte")
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
                              model=CHEAP_MODEL, max_tokens=300, tool="logo-erkennung")
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
            tool="broll-szenen",
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
        model="anthropic/claude-sonnet-4.6", max_tokens=8000, tool="broll-script")))
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
                                   max_tokens=32000, tool="broll-html")

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
        url = upload_supabase(broll_final, f"broll_{req.topic_slug}_{job_id[:8]}",
                              folder="broll_synced")
        log.info("[BROLL_SYNC] uploaded: %s", url)
        return {"broll_video_url": url, "scenes": scenes, "duration": duration}

    except HTTPException:
        raise
    except Exception as exc:
        log.error("[BROLL_SYNC] Error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Broll sync failed: {exc}")
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def _broll_job(job_id: str, req: "BrollSyncedRequest"):
    RENDER_JOBS[job_id] = {"status": "processing"}
    try:
        # eigener Event-Loop im Worker-Thread: der Impl ist async und nutzt
        # run_in_executor fuer den LLM-Call
        res = asyncio.run(_generate_broll_synced_impl(req))
        RENDER_JOBS[job_id] = {"status": "done", **(res or {})}
    except HTTPException as exc:
        RENDER_JOBS[job_id] = {"status": "error", "error": str(exc.detail)}
    except Exception as exc:
        log.exception("[BROLL_SYNC] job %s failed", job_id)
        RENDER_JOBS[job_id] = {"status": "error", "error": str(exc)}


@app.post("/generate-broll-synced")
async def generate_broll_synced(req: BrollSyncedRequest, wait: bool = False):
    """Async by default: liefert {job_id, status} und arbeitet im Hintergrund.
    Abruf ueber /render-status/{job_id} — dieselbe Registry wie /render-remotion.
    ?wait=true blockiert (kurze Clips / Tests).

    Warum nicht mehr synchron: der Aufruf haelt die Verbindung ueber vier Minuten
    ohne ein einziges Antwort-Byte (ein Sonnet-Call ueber 32k Token fuer neun
    Szenen). Irgendein Hop dazwischen kappt sie bei exakt 180s — n8n meldet
    `Error: aborted`, obwohl sein eigener Timeout auf 900s stand. Der Server
    baute die B-Roll danach fertig und lud sie hoch; nur wusste das niemand mehr.
    Der Keepalive-Stream davor war der Versuch, das zu ueberleben, und hat nicht
    gehalten. Also gar keine lange Verbindung mehr — wie beim Render auch.
    """
    if wait:
        try:
            return await _generate_broll_synced_impl(req)
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("[BROLL_SYNC] sync fail")
            raise HTTPException(status_code=500, detail=str(exc))
    job_id = str(uuid.uuid4())
    RENDER_JOBS[job_id] = {"status": "queued"}
    _broll_executor.submit(_broll_job, job_id, req)
    return {"job_id": job_id, "status": "processing"}


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
            tool="broll-html-alt",
        )

        html_content = _strip_fences(html_content)
        html_path.write_text(html_content, encoding="utf-8")
        log.info("[BROLL] HTML saved: %s", html_path)

        html_url = upload_supabase(html_path, req.topic_slug, folder="broll_html")
        log.info("[BROLL] HTML uploaded: %s", html_url)

        return {"html_url": html_url, "topic_slug": req.topic_slug}

    except Exception as exc:
        log.error("[BROLL] Error: %s", exc)
        raise HTTPException(status_code=500, detail=f"B-Roll generation failed: {exc}")
    finally:
        html_path.unlink(missing_ok=True)


# ── POST /detect-impacts ──────────────────────────────────────────────────────
def _llm_impacts(words: list) -> list:
    """Place SFX-library cues on the transcript. Returns [{time,category,asset,...}]."""
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
    raw = call_openrouter(system_prompt, json.dumps(words),
                          model=CHEAP_MODEL, max_tokens=1000, tool="impacts")
    try:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        impacts = json.loads(m.group()).get("impacts", []) if m else []
    except (json.JSONDecodeError, AttributeError):
        log.warning("[IMPACTS] Could not parse JSON: %s", raw[:200])
        impacts = []
    log.info("[IMPACTS] Detected %d moments", len(impacts))
    return impacts


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

        impacts = _llm_impacts(words)

        return {
            "impacts":        impacts,
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

        # ── Upload to Supabase Storage ────────────────────────────────────────
        url = upload_supabase(tmp_final, f"thumb_{job_id}", folder="thumbnails")
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

        url = upload_supabase(pdf_path, f"infosheet_{job_id}", folder="infosheets")
        log.info("[INFOSHEET] uploaded: %s", url)
        return {"url": url}

    except HTTPException:
        raise
    except Exception as exc:
        log.error("[INFOSHEET] Error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Infosheet generation failed: {exc}")
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


REMOTION_HOOK_MAX_S   = 3.5   # hook scene never runs longer than this
REMOTION_OUTRO_S      = 5.0   # trailing CTA scene
REMOTION_PUNCH_GAP_S  = 2.5   # minimum spacing between camera jumps
REMOTION_MAX_OVERLAYS = 3

# Words the caption layer should hit harder. Kept deliberately small — the
# alternative is an LLM pass per video, and this is a styling decision, not a
# creative one.
_HOT_WORD_RE = re.compile(
    r"^(nie|niemand|alles|nichts|jeder|sofort|brutal|krass|null|"
    r"\d+[.,]?\d*\s*(%|x|€|k|mio)?|\d+)$", re.IGNORECASE)


def _remotion_chunks(words: list, max_words: int = 3) -> list:
    """Word-level caption chunks: [{start,end,words:[{text,start,end,hot}]}].

    JustusPunches reveals one word at a time, so unlike _remotion_captions the
    per-word timings have to survive into the props."""
    chunks, cur = [], []
    for w in words:
        clean = w["word"].strip()
        if not clean:
            continue
        cur.append({
            "text":  clean,
            "start": round(float(w["start"]), 3),
            "end":   round(float(w["end"]), 3),
            "hot":   bool(_HOT_WORD_RE.match(clean.strip(".,!?;:"))),
        })
        # Frueher wurde zusaetzlich an Satzzeichen umgebrochen — dann standen
        # zwischendurch ein oder zwei Woerter statt drei. Justus' Caption-Stil
        # verlangt IMMER drei gleichzeitig, also entscheidet allein die Zahl.
        if len(cur) >= max_words:
            chunks.append({"start": cur[0]["start"], "end": cur[-1]["end"], "words": cur})
            cur = []
    if cur:
        chunks.append({"start": cur[0]["start"], "end": cur[-1]["end"], "words": cur})
    # A chunk must stay on screen until the next one starts, otherwise the band
    # blinks out between phrases.
    for i, c in enumerate(chunks[:-1]):
        c["end"] = chunks[i + 1]["start"]
    if chunks:
        chunks[-1]["end"] += 0.4
    return chunks


def _norm_token(s: str) -> str:
    return re.sub(r"[^0-9a-zäöüß]", "", s.lower())


def _find_word_time(words: list, target: str, after: float = 0.0) -> Optional[float]:
    """Start time of the first transcript word matching `target` (its first
    meaningful token), searched from `after` seconds on. None if not found."""
    toks = [_norm_token(t) for t in re.split(r"\s+", target) if _norm_token(t)]
    if not toks:
        return None
    key = toks[0]
    for w in words:
        if float(w["start"]) < after:
            continue
        ww = _norm_token(w["word"])
        if ww and (ww == key or (len(key) > 3 and key in ww)):
            return float(w["start"])
    return None


def _block_grenzen(hints: dict, words: list, duration: float) -> dict:
    """Blockgrenzen gegen das ECHTE Transkript remappen.

    Die Dauern im Skript sind eine Schaetzung des Modells — was zaehlt, ist was
    er wirklich gesagt hat. Jeder Block wird ueber seine laengsten, seltensten
    Woerter im Wort-Transkript gesucht, in der Reihenfolge der Bloecke und immer
    NACH dem Vorgaenger. Findet sich ein Block nicht wieder, bleibt er ohne
    Zeit — er wird dann nicht geraten, sondern uebersprungen.
    """
    out, cursor = {}, 0.0
    for b in (hints or {}).get("blocks", []) or []:
        rolle = b.get("rolle")
        text = str(b.get("text") or "")
        # seltene Woerter zuerst: lange Tokens tragen mehr Information als "und"
        kandidaten = sorted(
            {_norm_token(w) for w in re.findall(r"\w+", text) if len(w) > 5},
            key=len, reverse=True)[:6]
        treffer = None
        for k in kandidaten:
            t = _find_word_time(words, k, after=cursor)
            if t is not None and (treffer is None or t < treffer):
                treffer = t
        if treffer is not None:
            out[rolle] = treffer
            cursor = treffer
    # Ende eines Blocks = Anfang des naechsten gefundenen
    zeiten = sorted(out.items(), key=lambda kv: kv[1])
    grenzen = {}
    for i, (rolle, start) in enumerate(zeiten):
        ende = zeiten[i + 1][1] if i + 1 < len(zeiten) else duration
        grenzen[rolle] = (start, ende)
    return grenzen


def _briefing_props(briefing: dict, words: list, hook_end_s: float, duration: float,
                    hints: dict = None) -> tuple:
    """Map the regie onto the ACTUAL transcript timeline.
    Returns (punch_frames, lower_thirds). Script timings are ignored — the words
    people actually spoke drive where punches/cards land.

    Quelle ist `used_topics.regie_hints` (getrennt vom Kunden-Spickzettel).
    Aeltere Zeilen haben die Regie noch in production_briefing.segments[].regie —
    die werden hier auf dieselbe Form gebracht.
    """
    hints = hints or {}
    if not hints.get("punch_words") and not hints.get("lower_thirds"):
        alt_p, alt_l, alt_cta = [], [], None
        for s in (briefing or {}).get("segments", []) or []:
            r = s.get("regie", {}) or {}
            for pw in (r.get("punch_words") or []):
                alt_p.append({"rolle": s.get("rolle"), "wort": pw})
            lt = r.get("lower_third") or {}
            if lt.get("title"):
                alt_l.append({"rolle": s.get("rolle"), "title": lt["title"],
                              "subtitle": lt.get("subtitle") or ""})
            alt_cta = alt_cta or r.get("cta_keyword")
        hints = {"blocks": [{"rolle": s.get("rolle"), "text": s.get("text")}
                            for s in (briefing or {}).get("segments", []) or []],
                 "punch_words": alt_p, "lower_thirds": alt_l, "cta_keyword": alt_cta}

    grenzen = _block_grenzen(hints, words, duration)
    punch, lowers = [], []
    verworfen = 0

    for p in hints.get("punch_words") or []:
        # nur innerhalb des Blocks suchen, in dem das Wort laut Skript stand —
        # sonst trifft ein haeufiges Wort irgendwo im Video
        von, bis = grenzen.get(p.get("rolle"), (hook_end_s, duration))
        t = _find_word_time(words, str(p.get("wort")), after=von)
        if t is not None and t <= bis and hook_end_s < t < duration - 1.0:
            punch.append(int(t * FPS))
        else:
            verworfen += 1

    cta = hints.get("cta_keyword")
    if cta:
        t = _find_word_time(words, str(cta))
        if t is not None and t < duration - 0.3:
            punch.append(int(t * FPS))
        else:
            verworfen += 1

    for l in hints.get("lower_thirds") or []:
        von, bis = grenzen.get(l.get("rolle"), (None, None))
        if von is None:
            verworfen += 1      # Block nicht gesprochen -> keine Einblendung
            continue
        ende = min(von + 4.5, bis - 0.2 if bis else duration - 0.2, duration - 0.2)
        if ende <= von:
            verworfen += 1
            continue
        lowers.append({
            "startFrame": int(von * FPS),
            "endFrame": int(ende * FPS),
            "title": str(l.get("title"))[:42],
            "subtitle": str(l.get("subtitle") or "")[:70],
        })

    if verworfen:
        log.info("[REGIE] %d Hinweise verworfen — im Skript, aber nicht gesprochen",
                 verworfen)
    log.info("[REGIE] %d/%d Bloecke im Transkript wiedergefunden",
             len(grenzen), len(hints.get("blocks") or []))
    # dedupe + keep punches spaced apart
    punch = sorted(set(punch))
    spaced, last = [], -999
    for f in punch:
        if f - last >= int(REMOTION_PUNCH_GAP_S * FPS):
            spaced.append(f)
            last = f
    return spaced, lowers


def _visual_director(words: list, briefing: dict, duration: float,
                     hook_end_s: float, brand_accent: str = "#8B5CF6") -> dict:
    """Smart visual layer. One LLM plans the whole timeline from the REAL
    transcript + the script intent — which moments get a clean glass text card
    vs a real stock scene, where lower-thirds/stats sit, where captions go so
    they don't cover the face, and a brightness curve. Returns {} on failure so
    the caller falls back to the dumb derivation."""
    if not words or duration < 4:
        return {}
    transcript = " ".join(w["word"] for w in words)
    seg_intent = ""
    for s in (briefing or {}).get("segments", []) or []:
        r = s.get("regie", {}) or {}
        seg_intent += f"- {s.get('rolle')}: {s.get('text','')[:160]}"
        if r.get("lower_third", {}).get("title"):
            seg_intent += f" [Kernbegriff: {r['lower_third']['title']}]"
        seg_intent += "\n"

    system = (
        "Du bist VISUAL DIRECTOR fuer einen 9:16 Talking-Head-Clip (Gesicht mittig, darf NIE "
        "verdeckt werden). Du bekommst das echte Wort-Transkript mit Zeiten und die Skript-Absicht. "
        "Entwirf eine GESCHMACKVOLLE visuelle Timeline die die Aussage verstaerkt — NICHT jede Sekunde "
        "ein Overlay, das Gesicht muss atmen. Entscheide pro starkem Moment das BESTE Mittel:\n"
        "- overlay kind 'glass' = kurze, knackige TEXT-Karte (2-5 Woerter) fuer Konzept/Claim/Begriff. "
        "Bevorzugt fuer abstrakte/Tech-/Aussage-Momente. IMMER sauber, nie sloppy.\n"
        "- overlay kind 'stock' = echte reale Szene, NUR wenn ein woertliches Realbild wirklich hilft. "
        "Gib eine 3-5 Woerter ENGLISCHE Suchquery.\n"
        "- position IMMER 'upper_third' oder 'lower_third', NIE 'center' (verdeckt das Gesicht).\n"
        "- lower_thirds: fuer EINEN Definitions-/Label-Moment (title 2-4 Woerter + kurzer subtitle).\n"
        "- stats: NUR fuer eine echte METRIK MIT EINHEIT (%, x-fach, €, Std, min) — z.B. '90%','3x','40 Std'. "
        "NIEMALS fuer eine blosse Anzahl ('3 Aenderungen','5 Schritte' → kein stat, hoechstens glass/lower_third). value MUSS die Einheit enthalten.\n"
        "- caption_y: wo die Captions sitzen (0.60-0.72) damit sie das Gesicht nicht verdecken.\n"
        "- brightness: leicht abdunkeln (0.86) bei Spannung/Problem, voll (1.0) bei Hook/Payoff.\n"
        "- washes: dramatischer FARB-Wash auf emotionalen Beats. color = 'red' (Spannung/Aggro), "
        "'amethyst'/'cyan' (Tech/Fokus), 'warm' (Payoff/Aufloesung), 'blue' (Ruhe). strength 0.2-0.35. Sparsam, 1-3 total.\n"
        "- flow: EIN animierter Node-Graph fuer EINEN System-/Architektur-/Datenfluss-/Vergleichs-Moment "
        "(wenn ueber einen ABLAUF, eine Pipeline, ein 'A wird zu B wird zu C' geredet wird). nodes = 2-4 KURZE "
        "Labels (1-2 Woerter) in Fluss-Reihenfolge [Quelle, Transformation/Kern, Output, (optionale 2. Quelle)]. "
        "chips = 0-2 winzige Code-/Status-Chips (z.B. 'POST /infer','200 OK · 12ms'). start/end Sekunden. NUR wenn "
        "wirklich ein Ablauf beschrieben wird — sonst ganz weglassen. Das ist der Keynote-Blickfang, sparsam einsetzen.\n"
        "- cta: das EINE Call-to-Action-Wort am Schluss (z.B. 'ENGINE','JA','START') wenn der Sprecher zum "
        "Kommentieren/Handeln auffordert. {word, time}. Sonst weglassen.\n"
        "REGELN: max ~1 Overlay pro 4-5 Sekunden. Glass-Text max 5 Woerter. Zeiten in Sekunden. "
        "Waehle bewusst Abwechslung (mal glass, mal stock, mal flow, mal wash, mal nichts). NUR JSON zurueck:\n"
        '{"overlays":[{"start":4.2,"end":6.2,"kind":"glass","text":"kostet echtes Geld","position":"upper_third","query":""}],'
        '"lower_thirds":[{"start":12,"end":16,"title":"Workflow > Agent","subtitle":"fuer 90% der Faelle"}],'
        '"stats":[{"time":20,"value":"90%","label":"der Faelle"}],'
        '"flow":{"nodes":["Prompt","Agent","Output"],"chips":["POST /infer","200 OK"],"start":11,"end":17},'
        '"cta":{"word":"ENGINE","time":26},'
        '"washes":[{"start":3,"end":6,"color":"red","strength":0.3}],'
        '"caption_y":0.65,"brightness":[{"t":0,"level":1.0},{"t":5,"level":0.88}]}'
    )
    user = (f"Dauer: {duration:.1f}s. Hook endet bei {hook_end_s:.1f}s (davor keine Overlays).\n\n"
            f"SKRIPT-ABSICHT:\n{seg_intent}\n\nWORT-TRANSKRIPT (mit Zeiten):\n"
            + json.dumps([{"w": w["word"], "t": round(float(w["start"]), 2)} for w in words], ensure_ascii=False))
    try:
        raw = call_openrouter(system, user, model="anthropic/claude-sonnet-4.5",
                              max_tokens=2000, tool="regie-visuell")
        m = re.search(r"\{[\s\S]*\}", raw)
        plan = json.loads(m.group()) if m else {}
    except Exception as exc:
        log.warning("[DIRECTOR] failed: %s", exc)
        return {}
    log.info("[DIRECTOR] %d overlays, %d lower-thirds, %d stats, flow=%s, cta=%s",
             len(plan.get("overlays", [])), len(plan.get("lower_thirds", [])), len(plan.get("stats", [])),
             bool(plan.get("flow")), bool(plan.get("cta")))
    return plan


def _director_to_props(plan: dict, duration: float, hook_end_s: float, face: dict = None) -> dict:
    """Turn the director's plan into Remotion props: overlays (glass text stay as
    text; stock get a Pexels link), lowerThirds, statPops, captionY, brightness.
    Overlays are pinned to face-aware SAFE RAILS (above face.top / below face.bottom)
    so a centred, punch-zoomed face is never covered — this is what the QA gate was
    nuking. Cards stay small ('third')."""
    face = face or {}
    ft = face.get("top", 0.15)
    fb = face.get("bottom", 0.66)
    upper_rail = round(max(0.05, ft - 0.16), 3)   # card ends above the forehead
    lower_rail = round(min(0.80, fb + 0.04), 3)    # card starts below the chin
    has_upper = ft > 0.22                           # real room above the face?
    # lower-third occupies the bottom band — keep overlays off it (no bottom pile-up)
    lt_ivals = []
    for lt in (plan.get("lower_thirds", []) or []):
        try:
            lt_ivals.append((float(lt["start"]), float(lt["end"])))
        except (KeyError, TypeError, ValueError):
            continue
    def _hits_lt(a, b):
        return any(a < e and b > s for s, e in lt_ivals)
    overlays, lowers, stats = [], [], []
    for i, o in enumerate(plan.get("overlays", []) or []):
        try:
            st, en = float(o["start"]), float(o["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if st < hook_end_s or st >= duration - 0.5:
            continue
        en = min(en, duration - 0.2)
        want_upper = o.get("position") == "upper_third" and has_upper
        # a lower-third owns the bottom band → force overlay up, or drop if no room
        if _hits_lt(st, en):
            if not has_upper:
                continue
            want_upper = True
        rail = upper_rail if want_upper else lower_rail
        pos = "upper_third" if want_upper else "lower_third"
        frm = "left" if i % 2 == 0 else "right"
        kind = o.get("kind")
        if kind == "glass" and o.get("text"):
            overlays.append({"startFrame": int(st * FPS), "endFrame": int(en * FPS),
                             "kind": "glass", "text": str(o["text"])[:60], "size": "third",
                             "position": pos, "topRatio": rail, "from": frm, "asset_url": ""})
        elif kind == "stock" and o.get("query"):
            if want_upper:   # stock is big; only place it below the face, never on the top rail
                continue
            link = _pexels_link(str(o["query"]))
            if link:
                overlays.append({"startFrame": int(st * FPS), "endFrame": int(en * FPS),
                                 "kind": "video", "asset_url": link, "size": "half",
                                 "position": "lower_third", "topRatio": lower_rail,
                                 "from": frm, "text": ""})
    for lt in (plan.get("lower_thirds", []) or []):
        try:
            st = float(lt["start"]); en = float(lt["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if lt.get("title"):
            lowers.append({"startFrame": int(st * FPS), "endFrame": int(min(en, duration - 0.2) * FPS),
                           "title": str(lt["title"])[:42], "subtitle": str(lt.get("subtitle", ""))[:70]})
    for s in (plan.get("stats", []) or []):
        try:
            t = float(s["time"])
        except (KeyError, TypeError, ValueError):
            continue
        if s.get("value") and hook_end_s < t < duration - 1.0:
            stats.append({"frame": int(t * FPS), "value": str(s["value"])[:8], "label": str(s.get("label", ""))[:24]})
    cap_y = plan.get("caption_y")
    try:
        cap_y = float(cap_y) if cap_y is not None else None
    except (TypeError, ValueError):
        cap_y = None

    washes = []
    for w in (plan.get("washes", []) or [])[:3]:
        try:
            washes.append({"start": float(w["start"]), "end": float(min(float(w["end"]), duration)),
                           "color": str(w.get("color", "amethyst")), "strength": float(w.get("strength", 0.28))})
        except (KeyError, TypeError, ValueError):
            continue
    callouts = []
    for c in (plan.get("callouts", []) or [])[:2]:
        try:
            st = float(c["start"])
        except (KeyError, TypeError, ValueError):
            continue
        if st < hook_end_s:
            continue
        callouts.append({"start": st, "end": float(min(float(c.get("end", st + 3)), duration)),
                         "position": c.get("position", "mid") if c.get("position") in ("upper", "mid", "lower") else "mid",
                         "size": c.get("size", "half") if c.get("size") in ("third", "half") else "half",
                         "label": str(c.get("label", ""))[:20]})
    # flow node-graph (one keynote centrepiece) + CTA word — the premium layer
    flow_diagram = None
    fl = plan.get("flow") or {}
    fl_nodes = [str(n)[:18] for n in (fl.get("nodes") or []) if str(n).strip()][:4]
    try:
        fl_s, fl_e = float(fl["start"]), float(fl["end"])
    except (KeyError, TypeError, ValueError):
        fl_s = fl_e = None
    if len(fl_nodes) >= 2 and fl_s is not None and fl_s >= hook_end_s and fl_s < duration - 1.0:
        flow_diagram = {"nodes": fl_nodes,
                        "chips": [str(c)[:18] for c in (fl.get("chips") or [])][:2],
                        "startFrame": int(fl_s * FPS),
                        "endFrame": int(min(fl_e, duration - 0.2) * FPS)}

    cta_word = None
    ct = plan.get("cta") or {}
    try:
        ct_t = float(ct["time"])
    except (KeyError, TypeError, ValueError):
        ct_t = None
    if ct.get("word") and ct_t is not None and ct_t > hook_end_s:
        cta_word = {"word": str(ct["word"]).split()[0][:16],
                    "startFrame": int(ct_t * FPS),
                    "endFrame": int(min(ct_t + 2.4, duration) * FPS)}

    return {"overlays": overlays, "lowerThirds": lowers, "statPops": stats,
            "captionY": cap_y, "brightness": plan.get("brightness") or [],
            "washes": washes, "callouts": [],  # callouts killed — the dashed "HIER" box landed on the face and read cheap
            "flowDiagram": flow_diagram, "ctaWord": cta_word}


# ── Visual-script engine (anchor → transcript mapper) ─────────────────────────
# Two stages, the way the user framed it: a VISUAL SCRIPT sets the rough intent
# ("here a flow of X, here the concept 'Y', the CTA word is 'ENGINE'") anchored
# to phrases the speaker will say; then the DIRECTOR maps each anchor onto the
# ACTUAL Whisper transcript so every element lands EXACTLY where its words fall.
def _find_phrase_time(words: list, phrase: str, after: float = 0.0) -> Optional[float]:
    """Start time of where `phrase` is actually spoken, matched on its most
    distinctive tokens (not just the first, which is often a stopword)."""
    toks = [t for t in (_norm_token(x) for x in re.split(r"\s+", phrase)) if len(t) > 2]
    if not toks:
        return None
    keys = sorted(set(toks), key=len, reverse=True)[:3]
    for w in words:
        if float(w["start"]) < after:
            continue
        ww = _norm_token(w["word"])
        if ww and any(ww == k or (len(k) > 3 and k in ww) for k in keys):
            return float(w["start"])
    return None


VISUAL_SCRIPT_SYS = (
    "Du bist Bildregisseur fuer ein 9:16-Video. Du bekommst das Skript in sechs "
    "Bloecken und das wortgenaue Transkript der echten Aufnahme.\n"
    "EISERNE REGEL: Das Transkript ist die Wahrheit. Er hat nicht wortwoertlich das "
    "gesagt, was im Skript steht. Ordne die Bloecke den tatsaechlich gesprochenen "
    "Stellen zu. Findest du einen Block nicht wieder, lass ihn visuell leer.\n"
    "DEINE AUFGABE: EIN visueller Zustand pro Block. Nicht mehr. Du platzierst keine "
    "Effekte, du triffst Regieentscheidungen.\n"
    "PCI — VERBINDLICH, NICHT VERHANDELBAR\n"
    "P Precision   Maximal 2 Elemente gleichzeitig im Bild. Weissraum ist Koenig. "
    "Keine Partikel, kein Flimmern, keine Deko.\n"
    "C Cleverness  Niemals Wort-fuer-Wort-Bebilderung. Kein Haus-Icon bei 'Haus'. Du "
    "uebersetzt die BEDEUTUNG einer Aussage in ein UI-Primitive: concept_card, stat, "
    "flow, lower_third, scene. Wenn dir nur ein illustratives Bild einfaellt: lass es weg.\n"
    "I Intuitive   Unter 100ms erfassbar. Wer eine Sekunde hinsehen muss, um es zu "
    "verstehen, sieht das Falsche.\n"
    "DIE SECHS ZUSTAENDE\n"
    "HOOK     Vollbild. Titel ein. Kein zweites Element. Der Satz muss allein tragen.\n"
    "SZENE    Vollbild. Hoechstens EIN Element, seitlich, nie ueber dem Gesicht. Nur "
    "wenn eine Zahl, ein Name oder ein Produkt faellt.\n"
    "WENDUNG  Dein einziger grosser Wechsel. Layout darf kippen. Maximal 2 Elemente. "
    "Das ist der teuerste Moment im Video — verschiess ihn nicht woanders.\n"
    "HALTUNG  Vollbild. NICHTS. Kein Overlay, keine Karte. Das ist eine Anweisung, "
    "keine Auslassung. Die Stille hier ist der Grund, warum die WENDUNG davor knallt.\n"
    "MITNAHME Was der Zuschauer mitnimmt. HIER darf wieder ein Element stehen, genau "
    "EINES: das Werkzeug beim Namen, die Zahl zum Nachrechnen, der Handgriff als "
    "kurze Liste. Das ist der Block, den man abfotografiert — er darf sichtbar sein.\n"
    "CTA      Ein Wort, gross, kurz. Sonst nichts.\n"
    "HANDWERK — PFLICHT: Weniger Elemente heisst NICHT weniger Aufwand. Was steht, "
    "muss gebaut aussehen. MINDESTENS 2 der Zustaende sind full-screen 'scene'-Cutaways, "
    "und MINDESTENS EINER davon hat scene_type 'image' (photoreale, gebrandete Szene, "
    "concept = 3-6 Woerter Englisch, EIN Hero-Objekt, kein Text, kein Gesicht). "
    "Eine nackte Textkarte ist die Ausnahme, nicht die Regel — sie ist erlaubt, wenn ein "
    "Satz allein staerker ist als jedes Bild. Nutze 'flow' wenn ein Ablauf beschrieben "
    "wird und 'stat' wenn eine echte Zahl faellt. 5-8 Beats insgesamt.\n"
    "RHYTHMUS: Zwischen zwei Zustaenden mindestens 3 Sekunden. Gleichmaessige "
    "Verteilung ist verboten — Wechsel liegen dort, wo der Inhalt kippt, nicht im Takt.\n"
    "BEAT-TYPEN (typ):\n"
    "- hook_title: der Hook oben, anchor='__hook__', content = 3-7 Woerter.\n"
    "- scene: FULL-SCREEN Cutaway, der staerkste Move. content={scene_type: "
    "'card'|'statement'|'stat'|'quote'|'image', title, subtitle?, lines?(2-4 kurz), "
    "value?+label?, concept?(3-6 WOERTER ENGLISCH, ein Hero-Objekt, kein Text/Gesicht)}.\n"
    "- concept_card: Textkarte fuer EINE Kernaussage. content={kicker,headline}.\n"
    "- stat: NUR echte Metrik mit Einheit. content={value,label}.\n"
    "- flow: Node-Graph NUR bei einem echten Ablauf. content={nodes[2-4],chips[0-2]}.\n"
    "- lower_third: Label unten. content={title,subtitle}.\n"
    "- cta: das eine CTA-Wort am Schluss. content='ENGINE'.\n"
    "- none: kein Bild. Ein gueltiges und oft das beste Ergebnis.\n"
    "ANKER: jeder Beat braucht anchor = eine WOERTLICHE, seltene Phrase (2-5 Woerter) "
    "aus dem WORT-TRANSKRIPT, kopiert, kein Fuellwort wie 'und/das/ich'. Dort landet "
    "der Beat. Findest du keine passende Stelle: typ 'none'.\n"
    "block = hook|szene|wendung|haltung|mitnahme|cta. prio 1-5 (5 = darf nie fliegen). "
    "hold_s = Standzeit 2-6.\n"
    "NUR JSON:\n"
    '{"beats":['
    '{"block":"hook","typ":"hook_title","content":"40 Euro, Kampagne weg","anchor":"__hook__","prio":5,"hold_s":2},'
    '{"block":"szene","typ":"stat","content":{"value":"40 EUR","label":"pro Monat"},"anchor":"Starter-Abo","prio":3,"hold_s":3},'
    '{"block":"wendung","typ":"scene","content":{"scene_type":"statement","title":"Selbst gebaut","subtitle":"in zwei Abenden"},"anchor":"selber bauen","prio":5,"hold_s":3.5},'
    '{"block":"haltung","typ":"none","content":null,"anchor":"","prio":1,"hold_s":0},'
    '{"block":"mitnahme","typ":"scene","content":{"scene_type":"card","title":"Heute noch machen","lines":["Abos auflisten","Doppelte streichen"]},"anchor":"einmal durchgehen","prio":4,"hold_s":3.5},'
    '{"block":"cta","typ":"cta","content":"ABO","anchor":"Kommentare","prio":4,"hold_s":2.5}'
    '],"leer_gelassen":["haltung: bewusst still"]}'
)


ANKER_SYS = """Du suchst in einem gesprochenen Transkript nach Wörtern, unter denen
ein Bild liegt.

WAS EIN ANKER IST
Ein Wort oder eine Wendung, die abstrakt gemeint ist, aber eine
körperliche Ursprungsbedeutung hat.
  "Grind"            → mahlen, abtragen
  "hängengeblieben"  → ein Haken, der etwas festhält
  "durchgerutscht"   → eine Lücke im Sieb
  "aufgebaut"        → Stein auf Stein
  "verbrannt"        → Geld als Material, das nicht wiederkommt

WAS KEIN ANKER IST
- Konkrete Substantive. "Depot", "Handy", "Kunde" sind schon Dinge.
  Ein Bild vom Ding ist keine Metapher, das ist Illustration.
- Wörter ohne körperliche Wurzel: "Struktur", "Möglichkeit", "System"
- Blasse Allerweltsverben. "packen", "nehmen", "machen", "schaffen",
  "haben", "geben", "gehen" sind KEINE Anker. Sie haben zwar eine
  körperliche Bedeutung, aber keine, die ein bestimmtes Bild erzwingt.
- Füllwörter und Verstärker

"traegt":"hoch" vergibst du NUR, wenn die körperliche Ursprungsbedeutung
so eindeutig ist, dass sie einen konkreten Vorgang erzwingt — Reibung,
ein Haken, eine Lücke, ein Riss. Im Zweifel "mittel".

NULL ANKER IST EIN GUELTIGES ERGEBNIS. Fuell nicht auf, nur damit
etwas dasteht. Lieber leer als beliebig.

WIE VIELE
Ein 50-Sekunden-Video hat 2 bis 4 Anker. Nicht mehr.
Wenn du zehn findest, hast du die Regel gebrochen.
Lieber zwei starke als sechs mittelmäßige.

AUSGABE — nur JSON:
{ "anker": [ { "wort":"Grind", "wort_index":47,
  "woertlich":"mahlen, Material abtragen", "traegt":"hoch|mittel" } ] }

Alles mit "traegt":"mittel" fliegt später raus. Sei streng."""

ENTFALTUNG_SYS = """Du entwickelst aus einem Anker eine visuelle Idee.

VORGEHEN
1. Nenne den physikalischen Vorgang hinter der wörtlichen Bedeutung.
2. Entwickle DREI Umsetzungen in drei Registern:
   NATUR      etwas aus der physischen Welt, ohne Technik
   MENSCH     Werkzeug, Handwerk, Alltag
   SCHIEF     unerwartet, humorvoll, oder aus einer ganz anderen Domäne
3. Bewerte jede selbst.

DIE BEWERTUNG IST DER PUNKT
naheliegend = das Erste, was jedem einfällt. Bei "Grind" ist das eine
Person am Schreibtisch nachts. Das ist wertlos.
Du bewertest von 1 (jedem sofort klar) bis 5 (überraschend, aber
sofort verständlich, sobald man es sieht).

Alles unter 4 wird verworfen. Wenn alle drei unter 4 liegen, gib
"kein_bild": true zurück. Kein Bild ist besser als ein plattes Bild.

VERSTÄNDLICHKEITS-GRENZE
Eine 5, die man erklären muss, ist eine 1. Der Zuschauer hat
100 Millisekunden. Wenn der Sprung zu weit ist, ist er falsch.

VERB
Wähle für die beste Idee EIN Verb aus dieser Liste. Nur diese:
rollen · abtragen · füllen · leerlaufen · spalten · verbinden ·
trennen · stapeln · zerbrechen · verformen · wachsen · kippen

Passt kein Verb, ist die Idee nicht renderbar. Nimm die nächstbeste.

AUSGABE — nur JSON:
{ "vorgang":"...", "ideen":[{"register":"natur","bild":"...","score":5,"warum":"..."}],
  "gewaehlt":0, "verb":"abtragen", "objekt":"Polygon",
  "zustand_von":"12 Ecken", "zustand_nach":"Kreis",
  "asset_typ":"prozedural|stock|generiert", "kein_bild":false }"""

VERB_LISTE = {"rollen", "abtragen", "füllen", "leerlaufen", "spalten", "verbinden",
              "trennen", "stapeln", "zerbrechen", "verformen", "wachsen", "kippen"}

# Zweiter Call, der die Ideen NICHT selbst erzeugt hat. Ein Modell, das seine
# eigenen Einfaelle benotet, benotet sie gut — deshalb Rangfolge statt Note.
RANG_SYS = """Du bekommst drei Bilder für ein Wort. Du hast sie nicht erfunden.

Ordne sie danach, wie NAHELIEGEND sie sind: Welches würde jedem sofort
einfallen, wenn er das Wort hört? Das ist Platz 1 der naheliegenden.
Welches ist überraschend, aber sofort verständlich, sobald man es sieht?

Ein Bild, das man erklären muss, ist nicht überraschend — es ist daneben.
Ein Bild, das man schon hundertmal gesehen hat, ist naheliegend, auch wenn
es gut gemacht ist.

Zusaetzlich: Ist das bestplatzierte Bild ueberraschend GENUG, oder sind
alle drei naheliegend? Antworte binaer. Im Zweifel "alle_naheliegend": true —
kein Bild ist besser als ein plattes.

AUSGABE — nur JSON, Indizes der Eingabe:
{ "von_naheliegend_nach_ueberraschend": [0,2,1],
  "alle_naheliegend": false,
  "begruendung": "ein Satz, warum das letzte das stärkste ist" }"""

# Bilder, die jeder schon hundertmal gesehen hat. Der Prompt allein haelt sie
# nicht: das Modell hat den Schneeball trotz Gegenbeispiel mit 5 bewertet.
ANTI_BLOCKLIST = ["schneeball", "marionette", "glühbirne", "gluehbirne",
                  "hamsterrad", "hamster im rad", "zug fährt", "zug faehrt",
                  "sisyphos", "brennende geldschein", "geldschein brennt",
                  "brennender geldschein", "puzzleteil", "eisberg"]


def _blockiert(bild: str) -> str:
    b = str(bild or "").lower()
    for w in ANTI_BLOCKLIST:
        if w in b:
            return w
    return ""


def _rang(wort: str, ideen: list) -> list:
    """Gibt die Reihenfolge von naheliegend nach ueberraschend zurueck."""
    if len(ideen) < 2:
        return list(range(len(ideen))), False
    liste = "\n".join(f"{i}: {x.get('bild','')}" for i, x in enumerate(ideen))
    try:
        raw = call_openrouter(RANG_SYS, f"WORT: {wort}\n\n{liste}",
                              model="anthropic/claude-sonnet-4.5", max_tokens=300,
                              cache_system=True, tool="rang")
        m = re.search(r"\{[\s\S]*\}", raw)
        o = json.loads(m.group()) if m else {}
        order = [int(i) for i in o.get("von_naheliegend_nach_ueberraschend", [])
                 if isinstance(i, (int, float)) and 0 <= int(i) < len(ideen)]
        order += [i for i in range(len(ideen)) if i not in order]
        return order, o.get("alle_naheliegend") is True
    except Exception as exc:
        log.warning("[META] Rang-Call fehlgeschlagen: %s", exc)
        return list(range(len(ideen))), False


def _few_shot_metaphern() -> str:
    """8 Positive + 3 Anti, rotierend. Die Seeds bleiben immer im Topf — fuettert
    man nur Akzeptiertes zurueck, lernt das System den eigenen Durchschnitt und
    hoert auf zu ueberraschen."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        return ""
    hdr = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    try:
        pos = requests.get(f"{SUPABASE_URL}/rest/v1/metaphern", timeout=20, headers=hdr,
                           params={"select": "anker,bild,score,begruendung", "score": "gte.4",
                                   "quelle": "in.(seed,akzeptiert)", "limit": "40"}).json()
        neg = requests.get(f"{SUPABASE_URL}/rest/v1/metaphern", timeout=20, headers=hdr,
                           params={"select": "anker,bild,score,begruendung",
                                   "quelle": "in.(anti,verworfen)", "limit": "20"}).json()
    except Exception as exc:
        log.warning("[META] Few-Shot nicht lesbar: %s", exc)
        return ""
    import random as _r
    pos = _r.sample(pos, min(8, len(pos))) if isinstance(pos, list) else []
    neg = _r.sample(neg, min(3, len(neg))) if isinstance(neg, list) else []
    out = ["BEISPIELE — so sieht ein Treffer aus:"]
    for p in pos:
        out.append(f"  [{p.get('score')}] {p.get('anker')} → {p.get('bild')} ({p.get('begruendung','')})")
    if neg:
        out.append("GEGENBEISPIELE — genau das ist naheliegend:")
        for n in neg:
            out.append(f"  [{n.get('score')}] {n.get('anker')} → {n.get('bild')} ({n.get('begruendung','')})")
    return "\n".join(out)


PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")

ROUTER_SYS = """Du entscheidest, wie ein visuelles Bild umgesetzt wird.

"prozedural" NUR bei abstrakter Geometrie ohne benannten Gegenstand:
Polygon, Linie, Balken, Kurve, Raster, Punktwolke.
Sobald ein echtes Objekt vorkommt — Kerze, Seife, Wurzel, Sieb, Hammer,
Wasser, Hand, Werkzeug — ist es "stock".

Für "stock" baust du DREI englische Pexels-Suchanfragen:
1. die genaueste: Objekt + Aktion (2-4 Wörter, z.B. "candle wax dripping")
2. ein Fallback: nur das Objekt in Bewegung
3. ein weiter gefasster Fallback: der Vorgang ohne das konkrete Objekt

Keine Adjektive, keine Stimmung, keine Marken. Nur was zu sehen ist.

AUSGABE — nur JSON:
{ "asset_typ":"stock|prozedural", "anfragen":["...","...","..."] }"""


def _pexels(query: str, n: int = 15) -> list:
    if not PEXELS_API_KEY:
        return []
    try:
        r = requests.get("https://api.pexels.com/videos/search", timeout=25,
                         headers={"Authorization": PEXELS_API_KEY},
                         # orientation-Filter raus: fuer Vorgaenge wie "Wurzel hebt
                         # Asphalt" gibt es fast nur Querformat, damit kam nie ein
                         # Kandidat zurueck und der Vision-Check lief nie.
                         params={"query": query, "per_page": n})
        r.raise_for_status()
        out = []
        for v in (r.json() or {}).get("videos", []):
            files = sorted(v.get("video_files") or [],
                           key=lambda f: abs((f.get("height") or 0) - 1280))
            out.append({"id": v.get("id"), "dauer": v.get("duration"),
                        "thumb": v.get("image"),
                        "url": (files[0].get("link") if files else ""),
                        "seite": v.get("url")})
        return [c for c in out if c["url"] and c["thumb"]]
    except Exception as exc:
        log.warning("[STOCK] Pexels '%s': %s", query, exc)
        return []


def _vision_pick(beschreibung: str, kandidaten: list, streng: bool = True) -> dict:
    """Prueft die VORSCHAUBILDER, nicht die ganzen Clips — ein Standbild sagt,
    ob der Gegenstand da ist, nicht ob die Bewegung stimmt. Ehrliche Grenze.

    Genau deshalb gibt es zwei Durchgaenge: streng verlangt Gegenstand UND
    Vorgang, locker nur noch den Gegenstand. Der strenge Pass hat in drei
    Produktionslaeufen 45 Kandidaten gesehen und keinen genommen — ein Filter,
    der nie etwas durchlaesst, filtert nicht, er blockiert."""
    if not (OPENROUTER_API_KEY and kandidaten):
        return {}
    teil = kandidaten[:12]
    frage = ("Welches zeigt den beschriebenen Gegenstand und Vorgang tatsaechlich? "
             if streng else
             "Welches zeigt den beschriebenen GEGENSTAND oder ein sehr aehnliches "
             "Material klar erkennbar? Die Bewegung muss nicht exakt stimmen — ein "
             "Standbild zeigt sie ohnehin nicht. ")
    content = [{"type": "text", "text": (
        "Gesucht ist ein Clip, der DIESEN Vorgang zeigt: " + beschreibung +
        "\nDu siehst Vorschaubilder von Stock-Clips, nummeriert ab 0. " + frage +
        "Deko, Menschen im Buero oder abstrakte Muster zaehlen NICHT. "
        "Passt keines, gib index -1. Nur JSON: "
        "{\"index\":0,\"begruendung\":\"ein Satz\"}")}]
    for c in teil:
        content.append({"type": "image_url", "image_url": {"url": c["thumb"]}})
    t0 = time.time()
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", timeout=120,
                          headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                                   "Content-Type": "application/json"},
                          json={"model": "google/gemini-2.5-flash",
                                "messages": [{"role": "user", "content": content}],
                                "max_tokens": 300})
        r.raise_for_status()
        d = r.json()
        # Bis zu 12 Vorschaubilder pro Aufruf, und der Check laeuft zweimal
        # (streng, dann locker). Das ist der teuerste Bild-Posten im Stock-Weg —
        # er gehoert einzeln gezaehlt, nicht unter "stock-router" versteckt.
        _log_llm("stock-vision", "google/gemini-2.5-flash", d.get("usage") or {},
                 int((time.time() - t0) * 1000),
                 extra={"bilder": len(teil), "streng": streng})
        txt = d["choices"][0]["message"]["content"]
        m = re.search(r"\{[\s\S]*\}", txt)
        o = json.loads(m.group()) if m else {}
        i = int(o.get("index", -1))
        if 0 <= i < len(teil):
            return {"clip": teil[i], "begruendung": o.get("begruendung", "")}
    except Exception as exc:
        _log_llm("stock-vision", "google/gemini-2.5-flash", {},
                 int((time.time() - t0) * 1000), status="fehler")
        log.warning("[STOCK] Vision-Check: %s", exc)
    return {}


def _stock_fuer(bild: str) -> dict:
    """Router + Suche + Vision-Check fuer EIN Bild."""
    try:
        raw = call_openrouter(ROUTER_SYS, bild, model="anthropic/claude-sonnet-4.5",
                              max_tokens=300, cache_system=True, tool="stock-router")
        m = re.search(r"\{[\s\S]*\}", raw)
        o = json.loads(m.group()) if m else {}
    except Exception as exc:
        log.warning("[STOCK] Router: %s", exc)
        return {"asset_typ": "prozedural", "anfragen": [], "grund": f"router:{exc}"}
    typ = str(o.get("asset_typ", "prozedural")).lower()
    anfragen = [str(q) for q in (o.get("anfragen") or [])][:3]
    if typ != "stock":
        return {"asset_typ": "prozedural", "anfragen": anfragen}
    spuren, cache = [], {}
    for streng in (True, False):
        for q in anfragen:
            if q not in cache:
                cache[q] = _pexels(q)
                spuren.append({"anfrage": q, "kandidaten": len(cache[q])})
            kand = cache[q]
            if not kand:
                continue
            treffer = _vision_pick(bild, kand, streng=streng)
            if treffer:
                log.info("[STOCK] '%s' → Clip %s (%s, %s)", q, treffer["clip"]["id"],
                         "streng" if streng else "locker", treffer["begruendung"][:50])
                return {"asset_typ": "stock", "anfragen": anfragen, "anfrage_treffer": q,
                        "spuren": spuren, "clip": treffer["clip"],
                        "streng": streng, "vision": treffer["begruendung"]}
    gefunden = sum(x["kandidaten"] for x in spuren)
    grund = ("Pexels lieferte 0 Kandidaten" if gefunden == 0
             else "Vision-Check: kein Clip zeigt den Vorgang (streng UND locker)")
    if not PEXELS_API_KEY:
        grund = "PEXELS_API_KEY fehlt im Renderer"
    _log_run("", "stock-router", "warn", {"grund": grund, "anfragen": anfragen,
                                          "spuren": spuren})
    return {"asset_typ": "stock", "anfragen": anfragen, "clip": None,
            "spuren": spuren, "key_da": bool(PEXELS_API_KEY), "grund": grund}


def _metaphern(words: list, max_anker: int = 4) -> dict:
    """Anker finden, dann entfalten. Gibt nur JSON zurueck — die Verb-Bibliothek
    und der Asset-Router kommen spaeter, asset_typ wird vorerst nur geloggt."""
    text = " ".join(str(w.get("word", "")) for w in (words or [])).strip()
    if len(text) < 40:
        return {"anker": [], "konzepte": [], "_diag": "zu kurz"}
    idx = "\n".join(f"{i}\t{w.get('word','')}" for i, w in enumerate(words))
    try:
        raw = call_openrouter(ANKER_SYS, idx[:6000], model="anthropic/claude-sonnet-4.5",
                              max_tokens=900, tool="anker")
        m = re.search(r"\{[\s\S]*\}", raw)
        anker = (json.loads(m.group()).get("anker", []) if m else [])
    except Exception as exc:
        log.warning("[META] Anker-Pass fehlgeschlagen: %s", exc)
        return {"anker": [], "konzepte": [], "_diag": f"anker:{exc}"}

    # Ankerzahl skaliert mit der Textmenge: ein kurzes Transkript hat nicht
    # vier Bilder in sich, egal wie willig das Modell ist.
    # 150 Woerter pro Anker war strenger als der Prompt selbst ("ein 50-Sekunden-
    # Video hat 2 bis 4 Anker"): 239 Woerter ergaben genau EINEN Kandidaten, und
    # faellt der durch den Stock-Router, ist die ganze Ebene leer.
    budget = min(max_anker, max(1, round(len(words) / 90)))
    # Dasselbe Wort an drei Stellen ist ein Anker, nicht drei.
    rang_t = {"hoch": 2, "mittel": 1}
    beste = {}
    for a in anker:
        w = str(a.get("wort", "")).strip().lower()
        if not w:
            continue
        if w not in beste or rang_t.get(str(a.get("traegt", "")).lower(), 0) >            rang_t.get(str(beste[w].get("traegt", "")).lower(), 0):
            beste[w] = a
    stark = [a for a in beste.values() if str(a.get("traegt", "")).lower() == "hoch"][:budget]
    shots = _few_shot_metaphern()
    konzepte, verworfen, blocks = [], [], []
    for a in stark:
        i = int(a.get("wort_index") or 0)
        satz = " ".join(str(w.get("word", "")) for w in words[max(0, i - 12):i + 12])
        # Die Few-Shots sind bei jedem Anker identisch — sie gehoeren vor den
        # Cache-Punkt, der wechselnde Anker dahinter.
        user = (f"ANKER: {a.get('wort')} — wörtlich: {a.get('woertlich')}\n"
                f"SATZ: {satz}")
        try:
            raw = call_openrouter(ENTFALTUNG_SYS, user, model="anthropic/claude-sonnet-4.5",
                                  max_tokens=1200, cache_system=True,
                                  cache_prefix=shots, tool="entfaltung")
            m = re.search(r"\{[\s\S]*\}", raw)
            k = json.loads(m.group()) if m else {}
        except Exception as exc:
            log.warning("[META] Entfaltung '%s' fehlgeschlagen: %s", a.get("wort"), exc)
            continue
        ideen = k.get("ideen") or []
        verb_ok = str(k.get("verb", "")).strip().lower() in VERB_LISTE
        if k.get("kein_bild") is True or not ideen or not verb_ok:
            verworfen.append({"wort": a.get("wort"), "verb": k.get("verb"),
                              "verb_bekannt": verb_ok,
                              "kein_bild": k.get("kein_bild") is True})
            continue
        # Fremdbewertung: Rangfolge statt Selbstnote. Das naheliegendste fliegt.
        order, alle_platt = _rang(a.get("wort", ""), ideen)
        if alle_platt:
            verworfen.append({"wort": a.get("wort"), "grund": "Ranker: alle drei naheliegend"})
            continue
        kandidaten = list(reversed(order))[:-1] if len(order) > 1 else order
        gewaehlt = None
        for ci in kandidaten:
            treffer = _blockiert((ideen[ci] or {}).get("bild", ""))
            if treffer:
                blocks.append({"anker": a.get("wort"), "begriff": treffer,
                               "bild": (ideen[ci] or {}).get("bild", "")})
                _log_run("", "metaphern", "warn",
                         {"grund": "anti_blocklist", "anker": a.get("wort"),
                          "begriff": treffer, "bild": (ideen[ci] or {}).get("bild", "")})
                continue
            gewaehlt = ci
            break
        if gewaehlt is None:
            verworfen.append({"wort": a.get("wort"), "grund": "alle Ideen geblockt"})
            continue
        # Stock-Router: kein Treffer -> naechstbeste Idee, sonst kein Bild
        stock, ok_idx = {}, None
        for ci in kandidaten:
            if _blockiert((ideen[ci] or {}).get("bild", "")):
                continue
            st = _stock_fuer((ideen[ci] or {}).get("bild", ""))
            if st.get("asset_typ") == "prozedural" or st.get("clip"):
                stock, ok_idx = st, ci
                break
        if ok_idx is None:
            verworfen.append({"wort": a.get("wort"),
                              "grund": "kein Stock-Clip zeigt den Vorgang"})
            continue
        gewaehlt = ok_idx
        k["stock"] = stock
        k["asset_typ"] = stock.get("asset_typ")
        k["gewaehlt"] = gewaehlt
        k["rangfolge_naheliegend_zuerst"] = order
        k["wort"] = a.get("wort")
        k["wort_index"] = i
        k["zeit_s"] = round(float(words[i].get("start", 0.0)), 2) if i < len(words) else None
        konzepte.append(k)
        log.info("[META] '%s' → %s (%s, asset_typ=%s)", a.get("wort"),
                 (ideen[int(k.get('gewaehlt') or 0)] or {}).get("bild", "")[:60]
                 if ideen else "", k.get("verb"), k.get("asset_typ"))
    return {"anker": anker, "budget": budget, "stark": len(stark),
            "konzepte": konzepte, "verworfen": verworfen,
            "blocklist_treffer": blocks,
            "_diag": f"ok:{len(konzepte)} blocked:{len(blocks)}"}


class MetaphernRequest(BaseModel):
    text:     str = ""
    audio:    str = ""      # alternativ: URL, wird transkribiert
    max_anker: int = 4


@app.post("/metaphern")
def metaphern_debug(req: MetaphernRequest):
    """Nur JSON, kein Render. Zeigt welche Anker gefunden, welche drei Ideen je
    Anker, welche Scores, was verworfen wurde."""
    words = []
    if req.text:
        words = [{"word": w, "start": i * 0.35, "end": i * 0.35 + 0.3}
                 for i, w in enumerate(req.text.split())]
    elif req.audio:
        job = Path(f"/tmp/meta_{uuid.uuid4()}")
        job.mkdir(parents=True, exist_ok=True)
        try:
            src = job / "in"
            if download_file(req.audio, src):
                words = transcribe_audio(src) or []
        finally:
            shutil.rmtree(job, ignore_errors=True)
    if not words:
        raise HTTPException(status_code=400, detail="weder text noch audio verwertbar")
    return _metaphern(words, max_anker=req.max_anker)


def _timed_transcript(words: list, max_chars: int = 4200) -> str:
    """Wort-Transkript MIT Zeiten, in Zeilen zu 8 Woertern. Der Regie fehlte
    bisher jede Zeitachse — sie bekam Fliesstext und musste raten, wie eng die
    Saetze stehen. Die Zeit steht am Zeilenanfang, NIE im Wort selbst, damit
    eine woertlich kopierte Anker-Phrase sauber bleibt."""
    out, cur, start = [], [], None
    for w in (words or []):
        if start is None:
            start = float(w.get("start") or 0.0)
        cur.append(str(w.get("word", "")))
        if len(cur) >= 8:
            out.append(f"[{start:6.2f}] " + " ".join(cur))
            cur, start = [], None
    if cur:
        out.append(f"[{start or 0.0:6.2f}] " + " ".join(cur))
    txt = "\n".join(out)
    return txt[:max_chars]


def _contact_sheet(video: Path, words: list, face: dict, duration: float,
                   onsets: list, job_dir: Path,
                   von: float = 0.0, bis: float = 0.0) -> Optional[Path]:
    """EIN Kontaktblatt fuer die Regie: Filmstreifen mit eingezeichneter
    Gesichtsbox, Wellenform mit Transienten, Sprechbalken mit Pausen.
    Loest die drei Dinge, die der Director bisher blind geraten hat — wo das
    Gesicht ist, wo Luft ist, wie eng die Betonungen liegen. None bei Fehler:
    die Regie laeuft dann wie vorher, nur ohne Augen."""
    try:
        # Fenster: ohne Angabe das ganze Video. Der Agent kann sich eine
        # Passage groesser ansehen, ohne den ganzen Clip auf acht Kacheln zu
        # quetschen.
        t0_s = max(0.0, float(von))
        t1_s = float(bis) if bis and bis > t0_s else duration
        t1_s = min(t1_s, duration)
        span = max(0.01, t1_s - t0_s)
        SHOTS, TW = 8, 150
        TH = int(TW * 16 / 9)                      # 9:16 Kachel
        W_SHEET = SHOTS * TW + (SHOTS + 1) * 8
        WAVE_H, BAR_H, PAD = 150, 74, 8
        H_SHEET = PAD + TH + 26 + WAVE_H + 12 + BAR_H + 30

        img = Image.new("RGB", (W_SHEET, H_SHEET), (16, 16, 22))
        d = ImageDraw.Draw(img)
        try:
            f_sm = ImageFont.truetype(str(FONT_SEMIBOLD), 17)
            f_xs = ImageFont.truetype(str(FONT_SEMIBOLD), 14)
        except Exception:
            f_sm = f_xs = ImageFont.load_default()

        # ── Filmstreifen + Gesichtsbox ───────────────────────────────────────
        ft, fb = float(face.get("top", 0.0) or 0.0), float(face.get("bottom", 0.0) or 0.0)
        for i in range(SHOTS):
            t = t0_s + span * (i + 0.5) / SHOTS
            fp = job_dir / f"sheet_{i}.jpg"
            subprocess.run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(video),
                            "-frames:v", "1", "-vf", f"scale={TW}:{TH}", str(fp)],
                           check=True, capture_output=True)
            x = PAD + i * (TW + PAD)
            img.paste(Image.open(str(fp)).convert("RGB"), (x, PAD))
            if fb > ft > 0:
                d.rectangle([x, PAD + int(ft * TH), x + TW - 1, PAD + int(fb * TH)],
                            outline=(255, 92, 92), width=3)
            d.text((x + 4, PAD + TH + 4), f"{t:.1f}s", font=f_xs, fill=(190, 190, 200))
        if not (fb > ft > 0):
            d.text((PAD + 60, PAD + TH + 4), "GESICHT NICHT GETRACKT — schaetz es aus den Bildern",
                   font=f_sm, fill=(255, 92, 92))
        y = PAD + TH + 26

        # ── Wellenform (ffmpeg) + Transienten ────────────────────────────────
        wav_png = job_dir / "sheet_wave.png"
        inner_w = W_SHEET - 2 * PAD
        subprocess.run(["ffmpeg", "-y", "-ss", f"{t0_s:.2f}", "-t", f"{span:.2f}",
                        "-i", str(video), "-filter_complex",
                        f"showwavespic=s={inner_w}x{WAVE_H}:colors=0x8B5CF6",
                        "-frames:v", "1", str(wav_png)], check=True, capture_output=True)
        img.paste(Image.open(str(wav_png)).convert("RGB"), (PAD, y))
        for o in (onsets or []):
            if t0_s <= o <= t1_s:
                ox = PAD + int(inner_w * (o - t0_s) / span)
                d.line([ox, y + WAVE_H - 16, ox, y + WAVE_H], fill=(255, 200, 60), width=2)
        d.text((PAD + 6, y + 3), "WELLENFORM · gelb = Betonung", font=f_xs, fill=(150, 150, 165))
        y += WAVE_H + 12

        # ── Sprechbalken: wo geredet wird, wo Luft ist ───────────────────────
        d.rectangle([PAD, y, PAD + inner_w, y + BAR_H], fill=(30, 30, 40))
        for w in (words or []):
            try:
                a, b = float(w["start"]), float(w.get("end") or w["start"])
            except (KeyError, TypeError, ValueError):
                continue
            if b < t0_s or a > t1_s:
                continue
            x0 = PAD + int(inner_w * (max(a, t0_s) - t0_s) / span)
            x1 = max(x0 + 1, PAD + int(inner_w * (min(b, t1_s) - t0_s) / span))
            d.rectangle([x0, y + 20, x1, y + BAR_H - 20], fill=(139, 92, 246))
        schritt = 5 if span > 20 else (2 if span > 8 else 1)
        for s in range(int(t0_s), int(t1_s) + 1, schritt):
            sx = PAD + int(inner_w * (s - t0_s) / span)
            d.line([sx, y, sx, y + BAR_H], fill=(70, 70, 88), width=1)
            d.text((sx + 3, y + BAR_H - 18), f"{s}s", font=f_xs, fill=(130, 130, 150))
        d.text((PAD + 6, y + 2), "SPRECHBALKEN · Luecke = Pause", font=f_xs, fill=(150, 150, 165))

        out = job_dir / "contact_sheet.jpg"
        img.save(str(out), "JPEG", quality=78)
        log.info("[SHEET] Kontaktblatt %dx%d, %d Kacheln, %.1f-%.1fs, Gesicht %.2f-%.2f",
                 W_SHEET, H_SHEET, SHOTS, t0_s, t1_s, ft, fb)
        return out
    except Exception as exc:
        log.warning("[SHEET] Kontaktblatt fehlgeschlagen: %s", exc)
        return None


def _metaphern_overlays(words: list, duration: float, hook_end_s: float,
                        face: dict = None) -> list:
    """Haengt die Anker-Maschine an den Render-Pfad. Sie war gebaut, getestet
    und nur an /metaphern verdrahtet — jedes Konzept mit echtem Stock-Clip wird
    jetzt eine Bildebene. Prozedurale Konzepte fallen raus, dafuer gibt es
    keinen Renderer; das steht im Log, statt still zu verschwinden."""
    face = face or {}
    lower_rail = round(min(0.80, float(face.get("bottom", 0.66)) + 0.04), 3)
    try:
        meta = _metaphern(words)
    except Exception as exc:
        log.warning("[META] Pass fehlgeschlagen: %s", exc)
        return []
    overlays, prozedural = [], 0
    for i, k in enumerate(meta.get("konzepte") or []):
        clip = ((k.get("stock") or {}).get("clip")) or {}
        t = k.get("zeit_s")
        if not clip.get("url"):
            prozedural += 1
            continue
        if t is None or not (hook_end_s <= float(t) < duration - 1.5):
            continue
        st = float(t)
        en = min(st + 2.6, duration - 0.3)
        overlays.append({"startFrame": int(st * FPS), "endFrame": int(en * FPS),
                         "asset_url": clip["url"], "kind": "video", "size": "half",
                         "position": "lower_third", "topRatio": lower_rail,
                         "from": "left" if i % 2 == 0 else "right", "text": "",
                         "quelle": "metapher"})
    log.info("[META] %s → %d Bildebenen (%d prozedural verworfen, %d Anker geprueft)",
             meta.get("_diag"), len(overlays), prozedural, len(meta.get("anker") or []))
    if not overlays:
        _log_run("", "metaphern-render", "warn",
                 {"grund": "keine Bildebene", "diag": meta.get("_diag"),
                  "verworfen": meta.get("verworfen") or []})
    return overlays


def _gen_visual_script(briefing: dict, words: list, duration: float, hook_end_s: float,
                       sheet: Optional[Path] = None) -> dict:
    """Stage 1 — the visual SCRIPT (rough intent, anchored to script phrases).
    Built from the briefing's argument structure. {} if no briefing to ground it."""
    # Works from the briefing's argument structure if present, ELSE straight from
    # the transcript — so the planner ALWAYS fires (no briefing-only dependency
    # that dropped prod into the dumb fallback). Anchors always come from the real
    # transcript below, so they always resolve.
    transcript = " ".join(str(w.get("word", "")) for w in (words or [])).strip()
    b = briefing or {}
    # MITNAHME ist ein eigener Block (Kalle Patch 8) und darf einen visuellen Beat
    # bekommen — HALTUNG bleibt weiterhin bewusst leer.
    bloecke = [(k, str(b.get(k) or "").strip())
               for k in ("hook", "szene", "wendung", "haltung", "mitnahme", "cta")]
    if any(t for _, t in bloecke):
        intent = "".join(f"[{k.upper()}] {t[:400]}\n" for k, t in bloecke if t)
        src = ("SKRIPT IN SECHS BLOECKEN (das ist die visuelle Struktur — "
               "ein Zustand pro Block):\n" + intent)
        if b.get("hook_formel"):
            src += f"HOOK-FORMEL: {b['hook_formel']}\n"
        if b.get("avatar_pain"):
            src += f"SCHMERZ DER ZIELGRUPPE: {str(b['avatar_pain'])[:200]}\n"
    elif (briefing or {}).get("segments"):
        intent = "".join(f"[{sg.get('rolle') or sg.get('role') or '?'}] {sg.get('text','')[:300]}\n"
                         for sg in briefing["segments"])
        src = "SKRIPT-ABSICHT (Argument-Struktur):\n" + intent
    elif transcript:
        src = "TRANSKRIPT (was der Sprecher sagt, plane die Visuals dazu):\n" + transcript[:2600]
    else:
        return {"beats": [], "_diag": "no-input"}
    # Der statische Praefix: Skript-Absicht und Wort-Transkript aendern sich
    # innerhalb eines Videos nie. Er steht getrennt, damit er VOR dem Cache-Punkt
    # liegt — heute ein Call, ab dem Loop dreissig.
    prefix = (f"Dauer ~{duration:.0f}s.\n{src}\n\n"
              "WORT-TRANSKRIPT MIT ZEITEN (nimm die anchor-Phrasen WOERTLICH hieraus — "
              "die Zeit in eckigen Klammern ist NUR Orientierung und gehoert NIE in den "
              "anchor):\n" + _timed_transcript(words))
    user = "Plane jetzt die Bildregie fuer genau diesen Clip."
    if sheet:
        user += ("\n\nKONTAKTBLATT (Bild): oben acht Standbilder ueber die Laufzeit, das "
                 "rote Rechteck ist das getrackte Gesicht — was du darueber oder darunter "
                 "legst, deckt es nicht. Mitte die Wellenform, gelbe Striche sind Betonungen. "
                 "Unten der Sprechbalken: Luecken sind Pausen. Setz deine Zustaende dorthin, "
                 "wo Luft ist, nicht mitten in einen Satz.")
    try:
        # Der Regie-Systemprompt ist bei jedem Video derselbe. Heute ein einziger
        # Call, also ohne Wirkung — ab dem Tool-Loop ist genau das der Praefix,
        # der sonst bei jedem Turn neu bezahlt wird.
        raw = call_openrouter(VISUAL_SCRIPT_SYS, user, model="anthropic/claude-sonnet-4.5",
                              max_tokens=2500, image_path=sheet, cache_system=True,
                              cache_prefix=prefix, tool="regie-briefing")
    except Exception as exc:
        log.warning("[VSCRIPT] gen call failed: %s", exc)
        return {"beats": [], "_diag": f"call:{exc}"}
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        log.warning("[VSCRIPT] no JSON in response: %s", raw[:200])
        return {"beats": [], "_diag": f"nojson:{raw[:120]}"}
    try:
        vs = json.loads(m.group())
    except Exception as exc:
        log.warning("[VSCRIPT] JSON parse failed: %s | %s", exc, m.group()[:200])
        return {"beats": [], "_diag": f"parse:{exc}"}
    beats = vs.get("beats") if isinstance(vs, dict) else None
    log.info("[VSCRIPT] %d beats", len(beats or []))
    return {"beats": beats or [], "_diag": f"ok:{len(beats or [])}"}


                                                                                  # noqa
# Maximal zwei Elemente gleichzeitig, mindestens vier Sekunden zwischen zwei
# Zustaenden. Der Prompt allein haelt das nicht — sobald ein Modell zwoelf gute
# Ideen hat, legt es sie uebereinander.
PCI_MAX_GLEICHZEITIG = 2
PCI_MIN_ABSTAND_S = 3.0   # 4s liess das Bild zu lange leer — Ruhe ja, Leere nein
# Prioritaet nach Gewicht des Elements: was die Aussage traegt, bleibt.
PCI_PRIO = {"scene": 5, "metapher": 4, "flow": 4, "lower_third": 3, "overlay": 2, "stat": 2}


def _pci_gate(dp: dict, duration: float) -> dict:
    """Entzerrt die gemappten Beats: hoechstens zwei gleichzeitig, Mindestabstand
    zwischen den Einsaetzen. Captions, Hook-Titel und CTA zaehlen nicht mit —
    die sind Text, keine Flaeche. Wirft das Gate regelmaessig die Haelfte weg,
    ist der Prompt kaputt und nicht das Gate: deshalb das Logging."""
    items = []
    for key, kind in (("scenes", "scene"), ("flowDiagram", "flow"),
                      ("lowerThirds", "lower_third"), ("overlays", "overlay"),
                      ("statPops", "stat")):
        val = dp.get(key)
        if not val:
            continue
        for el in (val if isinstance(val, list) else [val]):
            start = el.get("startFrame", el.get("frame"))
            if start is None:
                continue
            end = el.get("endFrame", start + int(2.0 * FPS))
            # Eine Metapher ist ein echtes Bild, keine Textkarte — sie faellt
            # nicht in dieselbe Klasse wie ein generisches Overlay.
            k = "metapher" if el.get("quelle") == "metapher" else kind
            items.append({"key": key, "kind": k, "el": el,
                          "start": int(start), "end": int(end),
                          "prio": PCI_PRIO.get(k, 1)})
    if not items:
        return dp
    rein = len(items)
    items.sort(key=lambda x: (x["start"], -x["prio"]))

    behalten = []
    for it in items:
        parallel = [b for b in behalten if it["start"] < b["end"] and it["end"] > b["start"]]
        if len(parallel) >= PCI_MAX_GLEICHZEITIG:
            schwaechster = min(parallel, key=lambda b: b["prio"])
            if it["prio"] <= schwaechster["prio"]:
                continue
            behalten.remove(schwaechster)
        # Mindestabstand: zwei Einsaetze duerfen nicht aufeinander kleben
        if behalten and (it["start"] - behalten[-1]["start"]) / FPS < PCI_MIN_ABSTAND_S:
            if it["prio"] <= behalten[-1]["prio"]:
                continue
            behalten.pop()
        behalten.append(it)

    out = dict(dp)
    for key in ("scenes", "lowerThirds", "overlays", "statPops"):
        if isinstance(dp.get(key), list):
            keep = [b["el"] for b in behalten if b["key"] == key]
            out[key] = keep
    if dp.get("flowDiagram") and not any(b["key"] == "flowDiagram" for b in behalten):
        out["flowDiagram"] = None
    log.info("[PCI] %d Elemente rein, %d raus (max %d gleichzeitig, min %.1fs Abstand)",
             rein, len(behalten), PCI_MAX_GLEICHZEITIG, PCI_MIN_ABSTAND_S)
    return out


def _map_visual_script(beats: list, words: list, duration: float,
                       hook_end_s: float, face: dict = None) -> dict:
    """Stage 2 — the DIRECTOR maps each anchored beat onto the ACTUAL transcript:
    find where the anchor phrase is really spoken → that is the exact frame. Same
    return shape as _director_to_props so the render path consumes it identically."""
    face = face or {}
    ft, fb = face.get("top", 0.15), face.get("bottom", 0.66)
    upper_rail = round(max(0.05, ft - 0.16), 3)
    lower_rail = round(min(0.80, fb + 0.04), 3)
    has_upper = ft > 0.22

    overlays, lowers, stats, washes, scenes = [], [], [], [], []
    flow_diagram, cta_word, hook_title = None, None, None
    lt_ivals, busy = [], []  # busy = occupied [start,end] frames for heavy visuals

    def _overlaps(a, b, ivals):
        return any(a < e and b > s for s, e in ivals)

    ci = 0
    for beat in beats:
        typ = str(beat.get("typ") or beat.get("type") or "").strip()
        content = beat.get("content")
        anchor = str(beat.get("anchor", "")).strip()
        hold = float(beat.get("hold_s") or 3.0)

        if typ == "hook_title":
            if isinstance(content, str) and content.strip():
                hook_title = content.strip()[:60]
            continue

        # resolve the exact spoken time of the anchor
        if anchor in ("__hook__", "hook"):
            st = 0.0
        else:
            t = _find_phrase_time(words, anchor, after=hook_end_s)
            if t is None:
                log.info("[VSCRIPT] anchor not found, skip %s: %r", typ, anchor[:40])
                continue
            st = t
        en = min(st + hold, duration - 0.2)
        sf, ef = int(st * FPS), int(en * FPS)

        if typ == "flow" and flow_diagram is None and st >= hook_end_s and isinstance(content, dict):
            nodes = [str(n)[:18] for n in (content.get("nodes") or []) if str(n).strip()][:4]
            if len(nodes) >= 2:
                flow_diagram = {"nodes": nodes,
                                "chips": [str(c)[:18] for c in (content.get("chips") or [])][:2],
                                "startFrame": sf, "endFrame": ef}
                busy.append((sf, ef))
        elif typ == "cta" and cta_word is None and st > hook_end_s:
            word = content if isinstance(content, str) else (content or {}).get("word", "")
            if word:
                cta_word = {"word": str(word).split()[0][:16], "startFrame": sf,
                            "endFrame": int(min(st + max(hold, 2.2), duration) * FPS)}
        elif typ == "lower_third" and isinstance(content, dict) and content.get("title"):
            ef = int(min(st + max(hold, 4.0), duration - 0.2) * FPS)
            lowers.append({"startFrame": sf, "endFrame": ef,
                           "title": str(content["title"])[:42],
                           "subtitle": str(content.get("subtitle") or "")[:70]})
            lt_ivals.append((sf, ef))
        elif typ == "stat" and isinstance(content, dict) and content.get("value") and st > hook_end_s:
            stats.append({"frame": sf, "value": str(content["value"])[:8],
                          "label": str(content.get("label") or "")[:24]})
        elif typ == "wash" and isinstance(content, dict):
            washes.append({"start": st, "end": en, "color": str(content.get("color", "amethyst")),
                           "strength": 0.28})
        elif typ == "scene" and isinstance(content, dict) and st >= hook_end_s and len(scenes) < 3:
            stype = content.get("scene_type") or "card"
            if stype not in ("card", "statement", "stat", "quote", "image"):
                stype = "card"
            sc = {"type": stype, "startFrame": sf,
                  "endFrame": int(min(st + max(hold, 2.5), duration - 0.2) * FPS),
                  "title": str(content.get("title") or "")[:48] or None,
                  "subtitle": str(content.get("subtitle") or "")[:60] or None,
                  "lines": [str(x)[:40] for x in (content.get("lines") or [])][:4] or None,
                  "value": str(content.get("value") or "")[:8] or None,
                  "label": str(content.get("label") or "")[:24] or None,
                  "concept": str(content.get("concept") or "")[:80] or None}  # image scenes
            scenes.append({k: v for k, v in sc.items() if v is not None})
            busy.append((sf, sc["endFrame"]))
        elif typ == "concept_card" and st >= hook_end_s:
            headline = content.get("headline") if isinstance(content, dict) else content
            if not headline:
                continue
            # a lower-third owns the bottom band → force the card up (or drop)
            want_upper = _overlaps(sf, ef, lt_ivals) or (ci % 2 == 0 and has_upper)
            if want_upper and not has_upper:
                if _overlaps(sf, ef, lt_ivals):
                    continue
                want_upper = False
            rail = upper_rail if want_upper else lower_rail
            overlays.append({"startFrame": sf, "endFrame": ef, "kind": "glass",
                             "text": str(headline)[:60],
                             "kicker": str((content or {}).get("kicker") or "")[:24] if isinstance(content, dict) else "",
                             "size": "third", "position": "upper_third" if want_upper else "lower_third",
                             "topRatio": rail, "from": "left" if ci % 2 == 0 else "right", "asset_url": ""})
            ci += 1

    washes = washes[:3]
    return {"overlays": overlays, "lowerThirds": lowers, "statPops": stats,
            "captionY": None, "brightness": [], "washes": washes, "callouts": [],
            "flowDiagram": flow_diagram, "ctaWord": cta_word, "hookTitle": hook_title,
            "scenes": scenes}


def _remotion_scenes(chunks: list, duration: float) -> tuple:
    """(hookEndFrame, outroStartFrame). Both snap to chunk boundaries so the
    layout never changes mid-sentence."""
    hook_end = 2.0
    for c in chunks:
        if c["end"] <= REMOTION_HOOK_MAX_S:
            hook_end = c["end"]
        else:
            break
    outro_start = 0.0
    target = duration - REMOTION_OUTRO_S
    if target > hook_end + 4.0:
        for c in chunks:
            if c["start"] >= target:
                outro_start = c["start"]
                break
    return int(hook_end * FPS), int(outro_start * FPS)


def _remotion_punch_frames(impacts: list, chunks: list, hook_end_s: float, duration: float) -> list:
    """Camera jump frames. Prefer the LLM's impact/transition cues — those are
    where a new argument actually lands. Fall back to an even chunk cadence."""
    times = [float(i["time"]) for i in (impacts or [])
             if i.get("time") is not None
             and i.get("category") in ("impact", "transition")]
    if not times:
        times = [c["start"] for c in chunks[2::3]]

    out, last = [], -999.0
    for t in sorted(times):
        if t <= hook_end_s or t >= duration - 1.5 or (t - last) < REMOTION_PUNCH_GAP_S:
            continue
        out.append(int(t * FPS))
        last = t
    return out


# A spoken number is the one thing worth putting on screen without asking an LLM:
# it is unambiguous, and the viewer cannot re-listen to a stat they missed.
_STAT_RE = re.compile(
    r"^(?:(?P<cur>[€$])\s?)?(?P<num>\d{1,3}(?:[.,]\d+)?)(?P<suf>%|x|k|K|€|mio|Mio)?$")
_STAT_MIN_GAP_S = 6.0


def _remotion_stat_pops(words: list, hook_end_s: float, duration: float,
                        max_pops: int = 4) -> list:
    """Chips for numbers spoken aloud. Bare integers under 10 are usually
    counting ('drei Schritte'), not a stat, so they stay in the caption band."""
    pops, last = [], -999.0
    for w in words:
        raw = w["word"].strip().strip(".,!?;:")
        m = _STAT_RE.match(raw)
        if not m:
            continue
        t = float(w["start"])
        if t <= hook_end_s or t >= duration - 2.0 or (t - last) < _STAT_MIN_GAP_S:
            continue
        suffix = m.group("suf")
        num = m.group("num")
        if not suffix and not m.group("cur") and "," not in num and "." not in num:
            if int(num) < 10:
                continue
        pops.append({"frame": int(t * FPS),
                     "value": (m.group("cur") or "") + num + (suffix or ""),
                     "label": ""})
        last = t
        if len(pops) >= max_pops:
            break
    log.info("[REMOTION] %d stat pops", len(pops))
    return pops


def _pexels_link(query: str) -> Optional[str]:
    """Direct portrait clip URL. Remotion streams it, so nothing is downloaded here."""
    if not PEXELS_API_KEY:
        return None
    try:
        r = requests.get("https://api.pexels.com/videos/search",
                         headers={"Authorization": PEXELS_API_KEY},
                         params={"query": query, "per_page": 12,
                                 "orientation": "portrait", "size": "large"},
                         timeout=30)
        r.raise_for_status()
        vids = r.json().get("videos", [])
        if not vids:
            return None
        best = max(vids, key=lambda v: (1 if (v.get("height") or 0) >= (v.get("width") or 1) else 0,
                                        min(v.get("height") or 0, 2400), v.get("id") or 0))
        files = sorted(best["video_files"], key=lambda f: abs((f.get("height") or 0) - 1920))
        return files[0]["link"]
    except Exception as exc:
        log.warning("[VID] pexels link failed (%s): %s", query, exc)
        return None


def _remotion_overlays(words: list, duration: float, hook_end_s: float) -> list:
    """Stock clips as fly-in cards. Size and position come from an enum the
    composition owns — the LLM only picks the moment and the search query."""
    cuts = _detect_video_cuts(words, duration, max_cuts=REMOTION_MAX_OVERLAYS)
    overlays = []
    for i, c in enumerate(cuts):
        if c["time"] <= hook_end_s:
            continue
        link = _pexels_link(c["query"])
        if not link:
            continue
        overlays.append({
            "startFrame": int(c["time"] * FPS),
            "endFrame":   int(min(c["end"], duration - 0.5) * FPS),
            "asset_url":  link,
            "kind":       "video",
            "size":       "half",
            "position":   "upper_third" if i % 2 == 0 else "lower_third",
            "from":       "left" if i % 2 == 0 else "right",
        })
    log.info("[REMOTION] %d fly-in overlays", len(overlays))
    return overlays


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


def _face_track_mediapipe(video_path: Path, duration: float, samples: int = 24) -> dict:
    """§3 precise face track via MediaPipe Face Mesh — origin locked on the nose
    bridge (landmark 168), box from the full mesh. {} if mediapipe unavailable."""
    try:
        import cv2
        import numpy as np
        import mediapipe as mp
        mesh = mp.solutions.face_mesh.FaceMesh(static_image_mode=True, max_num_faces=1,
                                               refine_landmarks=True, min_detection_confidence=0.5)
        cap = cv2.VideoCapture(str(video_path))
        cxs, cys, tops, bots, lefts, rights = [], [], [], [], [], []
        for i in range(samples):
            cap.set(cv2.CAP_PROP_POS_MSEC, (duration * 1000) * (i + 0.5) / samples)
            ok, frame = cap.read()
            if not ok:
                continue
            res = mesh.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            if not res.multi_face_landmarks:
                continue
            lm = res.multi_face_landmarks[0].landmark
            ys = [p.y for p in lm]
            xs = [p.x for p in lm]
            cxs.append(lm[168].x)   # nose bridge
            cys.append(lm[168].y)
            tops.append(max(0.0, min(ys)))
            bots.append(min(1.0, max(ys)))
            lefts.append(max(0.0, min(xs)))
            rights.append(min(1.0, max(xs)))
        cap.release()
        mesh.close()
        if len(cxs) < 3:
            return {}
        med = lambda a: float(np.median(a))
        track = {"origin_x": round(med(cxs), 3), "origin_y": round(med(cys), 3),
                 "top": round(med(tops), 3), "bottom": round(med(bots), 3),
                 "left": round(med(lefts), 3), "right": round(med(rights), 3)}
        log.info("[FACE] mediapipe tracked %d/%d, nose=(%.2f,%.2f) bottom=%.2f",
                 len(cxs), samples, track["origin_x"], track["origin_y"], track["bottom"])
        return track
    except Exception as exc:
        log.info("[FACE] mediapipe unavailable (%s) → OpenCV", str(exc)[:80])
        return {}


def _face_track(video_path: Path, duration: float, samples: int = 24) -> dict:
    """Track the speaker's face. MediaPipe Face Mesh first (nose-bridge locked),
    OpenCV Haar cascade fallback. Returns {origin_x, origin_y, top, bottom} 0..1 —
    the median face box so Remotion locks punch-in zoom on the face and keeps
    captions off it. Empty on failure (composition falls back to its centre)."""
    mp_track = _face_track_mediapipe(video_path, duration, samples)
    if mp_track:
        return mp_track
    try:
        import cv2
        import numpy as np
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        cap = cv2.VideoCapture(str(video_path))
        vw = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1080
        vh = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1920
        cxs, cys, tops, bots, lefts, rights = [], [], [], [], [], []
        for i in range(samples):
            cap.set(cv2.CAP_PROP_POS_MSEC, (duration * 1000) * (i + 0.5) / samples)
            ok, frame = cap.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, 1.1, 5, minSize=(int(vw * 0.12), int(vh * 0.08)))
            if len(faces) == 0:
                continue
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])  # biggest face
            cxs.append((x + w / 2) / vw)
            cys.append((y + h / 2) / vh)
            tops.append(y / vh)
            bots.append((y + h) / vh)
            lefts.append(x / vw)
            rights.append((x + w) / vw)
        cap.release()
        if len(cxs) < 3:
            return {}
        med = lambda a: float(np.median(a))
        track = {"origin_x": round(med(cxs), 3), "origin_y": round(med(cys), 3),
                 "top": round(med(tops), 3), "bottom": round(med(bots), 3),
                 "left": round(med(lefts), 3), "right": round(med(rights), 3)}
        log.info("[FACE] tracked %d/%d frames, centre=(%.2f,%.2f) bottom=%.2f",
                 len(cxs), samples, track["origin_x"], track["origin_y"], track["bottom"])
        return track
    except Exception as exc:
        log.warning("[FACE] tracking failed: %s", exc)
    # Ein toter Face-Track sieht aus wie ein funktionierender: alles rechnet mit
    # den Default-Rails 0.15/0.66 weiter und niemand merkt, dass nie ein Gesicht
    # gefunden wurde. Deshalb laut.
    log.error("[FACE] KEIN Face-Track — Rails laufen auf Annahme 0.15/0.66")
    _log_run("", "face-track", "error",
             {"grund": "weder mediapipe noch OpenCV lieferten ein Gesicht",
              "datei": str(video_path.name)})
    return {}


_REMBG_SESSION = None


def _matte_video(facecam_path: Path, job_dir: Path) -> str:
    """Self-hosted background removal (rembg, CPU — no API cost): matte the speaker
    onto transparency and return a Cloudinary alpha-webm URL. Rendered at reduced
    width for speed; Remotion upscales it over the generated canvas. '' on failure
    → caller keeps the original background."""
    global _REMBG_SESSION
    try:
        from rembg import remove, new_session
        import cv2
        if _REMBG_SESSION is None:
            _REMBG_SESSION = new_session("u2netp")  # light + fast
        cap = cv2.VideoCapture(str(facecam_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        W = 480
        frames = job_dir / "matte_frames"
        frames.mkdir(exist_ok=True)
        idx = 0
        log.info("[MATTE] start: %d frames @ %dfps, W=%d", total, int(round(fps)), W)
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            frame = cv2.resize(frame, (W, int(h * W / w)))
            cut = remove(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), session=_REMBG_SESSION)  # RGBA
            cv2.imwrite(str(frames / f"{idx:05d}.png"), cv2.cvtColor(cut, cv2.COLOR_RGBA2BGRA))
            idx += 1
            del frame, cut
            if idx % 100 == 0:
                import gc as _gc
                _gc.collect()
                log.info("[MATTE] %d/%d frames", idx, total)
        cap.release()
        if idx < 5:
            return ""
        webm = job_dir / "matte.webm"
        subprocess.run(["ffmpeg", "-y", "-framerate", str(int(round(fps))), "-i", str(frames / "%05d.png"),
                        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "2M", str(webm)],
                       check=True, capture_output=True)
        url = upload_cloudinary(webm, f"matte_{uuid.uuid4().hex[:8]}")
        log.info("[MATTE] %d frames → %s", idx, url)
        return url
    except Exception as exc:
        log.warning("[MATTE] failed: %s", exc)
        return ""


def _gemini_qa(mp4_path: Path, moments: list, duration: float) -> dict:
    """QA gate: sample frames at the risky moments (overlays/captions active) and
    ask Gemini vision (via OpenRouter) whether anything covers the face or clips
    off-frame. Returns {overall: 'OK'|'ISSUES'|'SKIP', frames:[{ok, issue}]}."""
    if not OPENROUTER_API_KEY:
        return {"overall": "SKIP", "reason": "no OPENROUTER_API_KEY"}
    import base64
    ts = sorted(set(round(m, 2) for m in moments if 0.5 < m < duration - 0.3))
    if len(ts) > 6:
        ts = ts[:: max(1, len(ts) // 6)][:6]
    if not ts:
        ts = [round(duration * 0.3, 2), round(duration * 0.6, 2)]
    content = [{"type": "text", "text": (
        "Du bist QA fuer 9:16 Short-Form-Video-Frames. Untertitel/Lower-Thirds/Karten im UNTEREN Drittel "
        "und Titel/Grafik im OBEREN Drittel sind ABSICHT und voellig OK — das ist Standard-Short-Form-Layout, "
        "NICHT flaggen. Flagge NUR echte Fehler: "
        "(1) Text/Grafik/Karte liegt direkt AUF dem GESICHT (Augen/Nase/Mund) und verdeckt es — "
        "ein Untertitel UNTER dem Kinn ist KEIN Fehler. "
        "(2) Ein Element ist am Bildrand HART abgeschnitten sodass Sinn verloren geht. "
        "(3) Zwei Text-Elemente liegen SO uebereinander dass beide unlesbar werden (blosse Naehe ist ok). "
        "(4) PCI-Budget: mehr als ZWEI UI-Elemente gleichzeitig im Bild (Karten, Overlays, Lower-Thirds, "
        "Stat-Blocks, Flow-Graphen). Untertitel zaehlen NICHT mit. Mehr als zwei -> ok:false. "
        "(5) Lesbarkeit unter einer Sekunde: ein Element, dessen Bedeutung sich nicht sofort erschliesst. "
        "Das NUR im issue-Text melden mit Praefix 'HINWEIS:' und ok:true lassen — das ist Geschmack, "
        "keine Maschinenentscheidung. "
        "Im Zweifel ok:true. Antworte NUR mit JSON: "
        "{\"frames\":[{\"ok\":true,\"issue\":\"\"}],\"overall\":\"OK\"|\"ISSUES\"} "
        "— ein frames-Eintrag pro Bild, gleiche Reihenfolge."
    )}]
    tmp = mp4_path.parent
    for i, t in enumerate(ts):
        fp = tmp / f"qa_{i}.jpg"
        try:
            subprocess.run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(mp4_path),
                            "-frames:v", "1", "-vf", "scale=540:-1", str(fp)],
                           check=True, capture_output=True)
            b64 = base64.b64encode(fp.read_bytes()).decode()
            content.append({"type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64," + b64}})
        except Exception:
            continue
    if len(content) < 2:
        return {"overall": "SKIP", "reason": "no frames"}
    t0 = time.time()
    try:
        r = requests.post(OPENROUTER_URL, headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://schultensolutions.app.n8n.cloud",
            "X-Title": "Schulten Solutions Video Renderer",
        }, json={"model": "google/gemini-2.5-flash", "temperature": 0,
                 "messages": [{"role": "user", "content": content}]}, timeout=90)
        r.raise_for_status()
        d = r.json()
        _log_llm("gemini-qa", "google/gemini-2.5-flash", d.get("usage") or {},
                 int((time.time() - t0) * 1000), extra={"frames": len(ts)})
        txt = d["choices"][0]["message"]["content"]
        m = re.search(r"\{[\s\S]*\}", txt)
        qa = json.loads(m.group()) if m else {"overall": "SKIP"}
    except Exception as exc:
        _log_llm("gemini-qa", "google/gemini-2.5-flash", {},
                 int((time.time() - t0) * 1000), status="fehler")
        log.warning("[QA] gemini(openrouter) failed: %s", exc)
        return {"overall": "SKIP", "reason": str(exc)}
    qa["checked_at"] = ts
    issues = [f for f in qa.get("frames", []) if not f.get("ok", True)]
    log.info("[QA] overall=%s, %d/%d frames flagged", qa.get("overall"), len(issues), len(ts))
    return qa


# ── Async render jobs ─────────────────────────────────────────────────────────
# A full render is minutes long and blows Railway's ~5-min HTTP edge timeout, so
# /render-remotion returns a job id immediately and renders in a worker thread;
# the caller polls /render-status/{job_id}.
RENDER_JOBS: dict = {}
_render_executor = ThreadPoolExecutor(max_workers=2)
# eigener Worker fuer die B-Roll: sonst kann ein 5-Minuten-B-Roll-Job beide
# Render-Slots blockieren und der eigentliche Render wartet hinter ihm
_broll_executor = ThreadPoolExecutor(max_workers=1)


def _make_thumbnail_image(concept: str, client_id: str, out_path: Path) -> bool:
    """Generate a branded 1080×1920 thumbnail jpg via fal.ai (dark fallback on
    failure), center-cropped. Sync — mirrors /generate-thumbnail minus the upload."""
    tpl  = _load_template(client_id, None)
    cols = _tpl_colors(tpl)
    timg = ((tpl or {}).get("images") or {})
    raw  = out_path.with_suffix(".raw.jpg")
    ok = False
    try:
        img_url = _call_fal_thumbnail(concept, cols.get("primary") or "#8B5CF6",
                                      cols.get("bg") or "#12101a",
                                      timg.get("glow_word") or "amethyst purple",
                                      timg.get("thumbnail_vibe"))
        log.info("[THUMB] fal.ai returned: %s", img_url)
        ok = download_file(img_url, raw)
    except Exception as exc:
        log.warning("[THUMB] fal.ai failed — dark fallback: %s", exc)
    if not ok:
        Image.new("RGB", (W, H), (18, 16, 26)).save(str(raw), "JPEG", quality=95)
    img = Image.open(str(raw)).convert("RGB")
    if img.width / img.height > W / H:
        new_h, new_w = H, int(img.width * H / img.height)
    else:
        new_w, new_h = W, int(img.height * W / img.width)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left, top = (new_w - W) // 2, (new_h - H) // 2
    img.crop((left, top, left + W, top + H)).save(str(out_path), "JPEG", quality=95)
    return True


def _append_end_thumbnail(video_path: Path, req: RemotionRenderRequest, job_dir: Path) -> Path:
    """Append a 0.2s thumbnail freeze-frame to the END of the render (never the
    start — a still on frame 0 kills the hook). Returns the original path on any
    failure so a thumbnail problem never drops the video."""
    try:
        thumb_img = job_dir / "thumb.jpg"
        if req.thumbnail_url:
            if not download_file(req.thumbnail_url, thumb_img):
                log.warning("[REMOTION] thumbnail_url download failed — skipping end-card")
                return video_path
        else:
            concept = req.thumbnail_concept or req.hook_text or req.headline or req.topic_label or ""
            if not concept:
                return video_path
            _make_thumbnail_image(concept, req.client_id or "justus", thumb_img)
        thumb_clip = job_dir / "thumb_clip.mp4"
        run(["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(thumb_img),
             "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
             "-t", "0.2", "-vf",
             f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1",
             "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-c:a", "aac", "-b:a", "192k",
             "-pix_fmt", "yuv420p", "-shortest", str(thumb_clip)], "thumb_clip")
        out = job_dir / "with_thumb.mp4"
        run(["ffmpeg", "-y", "-i", str(video_path), "-i", str(thumb_clip),
             "-filter_complex", "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vout][aout]",
             "-map", "[vout]", "-map", "[aout]",
             "-c:v", "libx264", "-crf", "20", "-preset", "medium",
             "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(out)], "thumb_concat")
        log.info("[REMOTION] appended 0.2s thumbnail end-card")
        return out
    except Exception as exc:
        log.warning("[REMOTION] thumbnail end-card failed, shipping without: %s", exc)
        return video_path


def _fit_size(path: Path, job_dir: Path, target_mb: float = 47.0, name: str = "fit.mp4") -> Path:
    """Guarantee the file fits the 50MB ceiling (Supabase bucket + Telegram
    multipart). Re-encode to a duration-derived bitrate only if it's over. For a
    long clip it also downscales to 720p wide so the bitrate stays sharp."""
    try:
        if path.stat().st_size / 1e6 <= target_mb:
            return path
        dur = probe_duration(path)
        vbit = int((target_mb * 8 * 1_000_000) / max(dur, 1.0)) - 160_000  # leave room for audio
        vbit = max(1_500_000, vbit)
        out = job_dir / name
        # long clip → downscale to 720x1280 so the fewer pixels keep a crisp bitrate
        scale = ["-vf", "scale=-2:1280"] if dur > 70 else []
        run(["ffmpeg", "-y", "-i", str(path), *scale, "-c:v", "libx264", "-b:v", str(vbit),
             "-maxrate", str(int(vbit * 1.15)), "-bufsize", str(int(vbit * 2)),
             "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
             "-movflags", "+faststart", str(out)], "fit_size")
        if out.exists() and out.stat().st_size < path.stat().st_size:
            log.info("[REMOTION] size-guard %.1fMB → %.1fMB", path.stat().st_size / 1e6, out.stat().st_size / 1e6)
            return out
    except Exception as exc:
        log.warning("[REMOTION] size-guard failed: %s", exc)
    return path


MUSIC_SELECTOR_URL = os.environ.get(
    "MUSIC_SELECTOR_URL", "https://ad-music-selector-production.up.railway.app/select-music")


def _pick_music(client_id: str, briefing: dict, duration: float) -> str:
    """Waehlt das Musikbett aus der Bibliothek des Kunden (clients.music_tracks).
    Ohne Bibliothek gibt es kein Bett — es wird keine erfunden. Die Auswahl macht
    der ad-music-selector (Gemini hoert die Previews)."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY and client_id):
        return ""
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/clients", timeout=20,
                         headers={"apikey": SUPABASE_SERVICE_KEY,
                                  "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
                         params={"client_id": f"eq.{client_id}", "select": "music_tracks"})
        rows = r.json() if r.ok else []
        tracks = (rows[0].get("music_tracks") if rows else None) or []
    except Exception as exc:
        log.warning("[MUSIC] Bibliothek nicht lesbar: %s", exc)
        return ""
    if not tracks:
        log.info("[MUSIC] keine Tracks fuer %s hinterlegt — Render laeuft ohne Bett", client_id)
        return ""
    b = briefing or {}
    try:
        sel = requests.post(MUSIC_SELECTOR_URL, timeout=180, json={
            "tracks": tracks[:8],
            "adCtx": {"stil": b.get("hook_formel", ""), "mood": "",
                      "hook_audio": b.get("hook", ""),
                      "zielgruppe": b.get("avatar_pain", "")}})
        sel.raise_for_status()
        data = sel.json() or {}
        url = data.get("best_track_url") or data.get("url") or ""
        log.info("[MUSIC] gewaehlt: %s", url[:80] or "nichts")
        return url
    except Exception as exc:
        log.warning("[MUSIC] Auswahl fehlgeschlagen: %s", exc)
        return ""


def _add_music_ducked(video: Path, music_url: str, job_dir: Path) -> Path:
    """§4 mix a background music bed UNDER the voice with sidechain ducking — the
    music drops ~8dB while the speaker talks, breathes back in the pauses. Returns
    the original path on any failure so music never breaks the render."""
    try:
        mus = job_dir / "music.mp3"
        if not download_file(music_url, mus):
            return video
        out = job_dir / "with_music.mp4"
        filt = (
            "[1:a]volume=0.18,aloop=loop=-1:size=2000000000[m];"
            "[0:a]asplit=2[v0][key];"
            "[m][key]sidechaincompress=threshold=0.03:ratio=8:attack=5:release=250[md];"
            "[v0][md]amix=inputs=2:duration=first:dropout_transition=0,alimiter=limit=0.95[aout]"
        )
        run(["ffmpeg", "-y", "-i", str(video), "-i", str(mus), "-filter_complex", filt,
             "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-shortest", "-movflags", "+faststart", str(out)], "music_duck")
        return out if out.exists() else video
    except Exception as exc:
        log.warning("[MUSIC] ducking failed: %s", exc)
        return video


def _gen_scene_image(concept: str, client_id: str, job_dir: Path) -> str:
    """Generate an on-brand PHOTOREAL scene image (fal nano-banana, no text) for a
    cutaway and return its public Supabase URL. '' on failure → caller drops the
    image scene. ~$0.03/image. Brand palette from the client template."""
    try:
        tpl = _load_template(client_id, None)
        cols = _tpl_colors(tpl)
        timg = ((tpl or {}).get("images") or {})
        img_url = _call_fal_thumbnail(concept, cols.get("primary") or "#8B5CF6",
                                      cols.get("bg") or "#0A0A0F",
                                      timg.get("glow_word") or "amethyst purple",
                                      timg.get("thumbnail_vibe"))
        raw = job_dir / f"scene_{uuid.uuid4().hex[:8]}.jpg"
        if not download_file(img_url, raw):
            return ""
        # center-crop to exact 1080x1920
        img = Image.open(str(raw)).convert("RGB")
        if img.width / img.height > W / H:
            nh, nw = H, int(img.width * H / img.height)
        else:
            nw, nh = W, int(img.height * W / img.width)
        img = img.resize((nw, nh), Image.LANCZOS)
        left, top = (nw - W) // 2, (nh - H) // 2
        fin = job_dir / raw.name.replace(".jpg", "_c.jpg")
        img.crop((left, top, left + W, top + H)).save(str(fin), "JPEG", quality=92)
        return upload_supabase(fin, fin.stem, folder="scenes")
    except Exception as exc:
        log.warning("[SCENE-IMG] failed: %s", exc)
        return ""


def _log_run(client_id: str, tool: str, status: str, detail: dict) -> None:
    """Ein Eintrag in run_log. Fehlschlaege, die still weiterlaufen, merkt sonst
    niemand — und ein gerissener Durchstich sieht aus wie ein schwaches Modell."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        return
    try:
        requests.post(f"{SUPABASE_URL}/rest/v1/run_log", timeout=15,
                      headers={"apikey": SUPABASE_SERVICE_KEY,
                               "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                               "Content-Type": "application/json"},
                      json=[{"client_id": client_id or "unknown", "tool": tool,
                             "status": status, "detail": detail}])
    except Exception as exc:
        log.warning("[RUNLOG] %s", exc)


def _render_remotion_impl(req: RemotionRenderRequest) -> dict:
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
    # Ohne Briefing erfindet die Regie die Bildsprache aus dem Transkript. Das ist
    # ein Notbetrieb, kein Normalzustand — laut protokollieren, sonst faellt der
    # gerissene Durchstich nie auf.
    if not req.briefing:
        log.warning("[REMOTION] render_ohne_briefing — Regie raet aus dem Transkript")
        _log_run(req.client_id or "", "render-remotion", "warn",
                 {"grund": "render_ohne_briefing", "format": req.format,
                  "hook_text": bool(req.hook_text)})
    try:
        facecam_path = job_dir / "facecam.mp4"
        if not download_file(req.facecam, facecam_path):
            raise HTTPException(status_code=500, detail="facecam download failed")

        # Cut first. Every timestamp downstream — captions, punches, SFX cues —
        # is measured against the finished timeline, so the transcript has to be
        # taken from the trimmed clip, not the raw one.
        face_url = req.facecam
        if req.trim:
            trimmed, _ = _trim_pipeline(facecam_path, job_dir)
            if trimmed != facecam_path:
                facecam_path = trimmed
        # the facecam source (raw or trimmed) may be >50MB on a long clip → compress
        # it under the Supabase ceiling so remotion can fetch it (the big RAW itself
        # arrives via the self-hosted bot-api server, not Supabase).
        facecam_path = _fit_size(facecam_path, job_dir, target_mb=46, name="facecam_fit.mp4")
        face_url = upload_supabase(facecam_path, f"facecam_{job_id}", folder="uploads")
        log.info("[REMOTION] facecam source → %s", face_url)

        duration = probe_duration(facecam_path)
        # transcribe_audio extracts the audio track first, then tries WhisperX and
        # falls back to whisper-1. Calling _whisperx_words on the mp4 directly
        # skipped both the extraction and the fallback, so a missing
        # REPLICATE_API_TOKEN silently produced zero captions.
        words = transcribe_audio(facecam_path) or []

        impacts = req.impacts
        if impacts is None and words:
            impacts = _llm_impacts(words)
        impacts = impacts or []

        qa_moments = []
        visual_diag = {"path": "n/a"}
        onsets = _audio_onsets(facecam_path, job_dir)  # §1 transients for beat-sync
        base = {"durationInSeconds": round(duration, 3)}
        if comp == "JustusBroll":
            props = {**base, "face_url": face_url,
                     "topicLabel": req.topic_label or "AI // AGENTS",
                     "headline":  req.headline or req.hook_text or "",
                     "stats":     req.stats  or [{"value": "3.4x", "label": "schneller"}],
                     "ticker":    req.ticker or ["AI shipping", "automation +240%"]}
            if req.code_lines:
                props["codeLines"] = req.code_lines
        elif comp == "JustusUsecase":
            captions = _remotion_captions(words)
            props = {**base, "screen_url": req.screen_url or face_url,
                     "face_url": face_url, "hook_text": req.hook_text, "captions": captions}
        else:  # JustusPunches
            face = _face_track(facecam_path, duration)
            matte_url = _matte_video(facecam_path, job_dir) if req.bg_mode == "canvas" else ""
            chunks = _remotion_chunks(words)
            hook_end_f, outro_start_f = _remotion_scenes(chunks, duration)
            hook_end_s = hook_end_f / FPS
            # punches (audio hits) from the briefing/impacts; lands on real words.
            lower_thirds = []
            if req.punch_ins:
                punch_frames = [int(float(x) * FPS) for x in req.punch_ins]
            elif req.briefing or req.regie_hints:
                punch_frames, lower_thirds = _briefing_props(
                    req.briefing, words, hook_end_s, duration, hints=req.regie_hints)
                if not punch_frames:
                    punch_frames = _remotion_punch_frames(impacts, chunks, hook_end_s, duration)
            else:
                punch_frames = _remotion_punch_frames(impacts, chunks, hook_end_s, duration)

            # VISUAL SCRIPT (anchor intent) → DIRECTOR maps it onto the real
            # transcript for exact placement. Preferred when a briefing exists;
            # falls back to the invent-from-transcript director, then the dumb layer.
            dp = None
            hook_override = None
            visual_diag = {"path": "dumb", "beats": 0, "vs_raw": None}
            # Kontaktblatt: die Regie hat bisher kein einziges Bild gesehen.
            sheet = (_contact_sheet(facecam_path, words, face, duration, onsets, job_dir)
                     if (req.overlays and req.contact_sheet) else None)
            # Anker-Maschine: laeuft parallel zur Regie, liefert eigene Bildebenen.
            meta_overlays = (_metaphern_overlays(words, duration, hook_end_s, face)
                             if (req.overlays and req.metaphern) else [])
            if req.overlays:
                vs = (req.briefing or {}).get("visual_script")
                if not vs:
                    vs = _gen_visual_script(req.briefing or {}, words, duration, hook_end_s,
                                            sheet=sheet)
                beats = vs.get("beats") if isinstance(vs, dict) else (vs if isinstance(vs, list) else None)
                visual_diag["vs_raw"] = (vs or {}).get("_diag") if isinstance(vs, dict) else None
                if beats:
                    dp = _map_visual_script(beats, words, duration, hook_end_s, face)
                    # Metaphern-Ebenen VOR dem Gate dazu: sie konkurrieren mit den
                    # Regie-Elementen um denselben Platz, statt oben draufzuliegen.
                    dp["overlays"] = (dp.get("overlays") or []) + meta_overlays
                    # PCI: entzerren BEVOR gesnappt wird — sonst rasten zwei
                    # Elemente auf denselben Transienten und stehen doch zusammen.
                    dp = _pci_gate(dp, duration)
                    hook_override = dp.get("hookTitle")
                    _tc = {}
                    for _b in beats:
                        _t = str(_b.get("typ") or _b.get("type"))
                        _tc[_t] = _tc.get(_t, 0) + 1
                    visual_diag = {"path": "vscript", "beats": len(beats), "emitted": _tc,
                                   "scenes": len(dp.get("scenes") or []),
                                   "overlays": len(dp["overlays"]), "lowers": len(dp["lowerThirds"]),
                                   "flow": bool(dp["flowDiagram"]), "cta": bool(dp["ctaWord"]),
                                   "hook": bool(hook_override)}
                    log.info("[VSCRIPT] mapped → %d overlays, %d lowers, flow=%s cta=%s hook=%s",
                             len(dp["overlays"]), len(dp["lowerThirds"]), bool(dp["flowDiagram"]),
                             bool(dp["ctaWord"]), bool(hook_override))
                if dp is None:
                    plan = _visual_director(words, req.briefing or {}, duration, hook_end_s)
                    if plan:
                        dp = _director_to_props(plan, duration, hook_end_s, face)
                        dp["overlays"] = (dp.get("overlays") or []) + meta_overlays
                        dp = _pci_gate(dp, duration)
                        visual_diag = {**visual_diag, "path": "director",
                                       "overlays": len(dp["overlays"]), "lowers": len(dp["lowerThirds"]),
                                       "flow": bool(dp["flowDiagram"]), "callouts": len(dp["callouts"])}
            caption_y, brightness, washes, callouts = None, [], [], []
            flow_diagram, cta_word = None, None
            scenes_layer = []
            if dp:
                overlays, stat_pops = dp["overlays"], dp["statPops"]
                if dp["lowerThirds"]:
                    lower_thirds = dp["lowerThirds"]
                caption_y, brightness = dp["captionY"], dp["brightness"]
                washes, callouts = dp["washes"], dp["callouts"]
                flow_diagram, cta_word = dp.get("flowDiagram"), dp.get("ctaWord")
                scenes_layer = dp.get("scenes") or []
            else:
                overlays = (_remotion_overlays(words, duration, hook_end_s) + meta_overlays
                            if req.overlays else [])
                stat_pops = _remotion_stat_pops(words, hook_end_s, duration)
            visual_diag = {**visual_diag, "meta": len(meta_overlays), "sheet": bool(sheet)}
            # §1 snap visual events onto the nearest audio transient
            if onsets:
                punch_frames = sorted({_snap_frame(int(f), onsets, FPS) for f in punch_frames})
                for _o in overlays:
                    _o["startFrame"] = _snap_frame(_o["startFrame"], onsets, FPS)
                if flow_diagram:
                    flow_diagram["startFrame"] = _snap_frame(flow_diagram["startFrame"], onsets, FPS)
                if cta_word:
                    cta_word["startFrame"] = _snap_frame(cta_word["startFrame"], onsets, FPS)
                for _sc in (scenes_layer or []):
                    _sc["startFrame"] = _snap_frame(_sc["startFrame"], onsets, FPS)
            log.info("[REMOTION] %d words → %d chunks, hook@%df outro@%df, %d punches, %d overlays, %d lower-thirds, flow=%s cta=%s %d onsets (director=%s)",
                     len(words), len(chunks), hook_end_f, outro_start_f, len(punch_frames), len(overlays), len(lower_thirds),
                     bool(flow_diagram), bool(cta_word), len(onsets), bool(dp))
            props = {**base, "face_url": face_url, "hook_text": req.hook_text,
                     "chunks": chunks, "punchFrames": punch_frames, "overlays": overlays,
                     "statPops": stat_pops, "lowerThirds": lower_thirds,
                     "hookEndFrame": hook_end_f, "outroStartFrame": outro_start_f,
                     "grade": req.grade}
            if caption_y is not None:
                props["captionY"] = caption_y
            # ALWAYS send these — Remotion merges missing keys from defaultProps
            # (the Studio demo), so an unsent key leaks a demo overlay/callout/flow
            # into production. Send explicit values (empty = off).
            props["brightness"] = brightness or []
            props["washes"] = washes or []
            props["callouts"] = callouts or []
            props["floatingQuotes"] = []
            # explicit request override (manual test / n8n) wins over the director
            flow_diagram = req.flow_diagram or flow_diagram
            cta_word = req.cta_word or cta_word
            # CTA aus dem Skript: ein Wort, gross, am Ende. Nur wenn die Regie
            # selbst keins gesetzt hat — das Skript kennt den Abbinder, das
            # Transkript-Raten nicht.
            if not cta_word and (req.briefing or {}).get("cta"):
                _cw = re.sub(r"[^\wÄÖÜäöüß]", "",
                             str(req.briefing["cta"]).strip().split()[0] if
                             str(req.briefing["cta"]).strip() else "")
                _start = outro_start_f or max(0, int((duration - 2.2) * FPS))
                if _cw:
                    cta_word = {"word": _cw.upper()[:14],
                                "startFrame": _start,
                                "endFrame": int(duration * FPS)}
            scenes_layer = req.scenes if req.scenes is not None else scenes_layer
            # generate photoreal on-brand images for 'image' scene cutaways
            for _sc in (scenes_layer or []):
                if _sc.get("type") == "image" and _sc.get("concept") and not _sc.get("imageUrl"):
                    _u = _gen_scene_image(_sc["concept"], req.client_id or "justus", job_dir)
                    if _u:
                        _sc["imageUrl"] = _u
                    else:
                        _sc["type"] = "statement"  # fallback keeps a scene if fal fails
                _sc.pop("concept", None)
            # always send (null/[] override the Studio demo defaults)
            props["flowDiagram"] = flow_diagram
            props["ctaWord"] = cta_word
            props["scenes"] = scenes_layer or []
            # the visual script can supply a sharper hook title than req.hook_text
            if hook_override:
                props["hook_text"] = hook_override
            # face-lock the punch-in origin + keep captions below the face
            if face:
                props["faceOriginX"] = face["origin_x"]
                props["faceOriginY"] = face["origin_y"]
                props["faceBottom"] = face["bottom"]
                if caption_y is None:
                    props["captionY"] = min(0.68, face["bottom"] + 0.05)
            # bg replace: studio canvas behind the matted speaker (opt-in)
            if matte_url:
                props["speakerMatteUrl"] = matte_url
                props["bgMode"] = req.bg_mode
            # QA samples the risky moments — where an overlay/lower-third/stat is up
            qa_moments = ([o["startFrame"] / FPS + 0.4 for o in overlays]
                          + [lt["startFrame"] / FPS + 0.6 for lt in lower_thirds]
                          + [s["frame"] / FPS + 0.3 for s in stat_pops]
                          + [c["start"] + 0.5 for c in callouts])

        LUT = "lut/cinematic.cube"
        use_lut = req.grade and os.path.exists(LUT)

        def _render_once(_props, _tag):
            """Render the composition + mux the facecam audio (+ cinematic LUT).
            No SFX here — that's applied once to the chosen render."""
            # Fit the 50MB ceiling (Supabase + Telegram) WITHOUT the blur. Long clips
            # render at 720x1280 (scale 0.667) so every pixel keeps a high bitrate =
            # razor sharp, instead of a soft full-1080p at a crushed bitrate.
            target_mb = 42
            vkbit = int(target_mb * 8 * 1000 / max(duration, 1.0))
            # low floor so a LONG clip's graphic still fits <50MB (the 4000 floor made
            # a 125s graphic ~62MB → Supabase 413). Short clips still get a high bitrate.
            vkbit = max(800, min(vkbit, 12000))
            gfx_scale = 0.75 if duration > 55 else 1.0   # 0.75 → exact 810x1440 (both even; H264 needs even dims)
            # R3 Teil 1: dieselben Props, andere Composition. LayerStage baut die
            # Ebenenliste aus `legacy` und fuehrt sie aus — identische Bausteine,
            # identische Geometrie, nur ist die Reihenfolge jetzt Daten.
            if req.layer_stage and comp == "JustusPunches":
                _comp, _send = "LayerStage", {"legacy": _props,
                                              "face_url": _props.get("face_url", ""),
                                              "durationInSeconds": _props["durationInSeconds"]}
            else:
                _comp, _send = comp, _props
            _body = {"composition": _comp, "inputProps": _send, "videoBitrate": f"{vkbit}k"}
            if gfx_scale < 1.0:
                _body["scale"] = gfx_scale
            r = requests.post(f"{REMOTION_URL}/render",
                              json={**_body,
                                    # remotion uploads its graphic to the same Supabase
                                    # bucket; pass creds so it needs no env of its own.
                                    "supabase": {"url": SUPABASE_URL, "key": SUPABASE_SERVICE_KEY,
                                                 "bucket": SUPABASE_BUCKET}}, timeout=600)
            r.raise_for_status()
            data = r.json()
            if not data.get("ok"):
                raise HTTPException(status_code=502, detail=f"remotion error: {data.get('error')}")
            log.info("[REMOTION] graphic rendered %s (%d frames)", data.get("url"), data.get("durationInFrames", 0))
            gfx_path = job_dir / f"gfx_{_tag}.mp4"
            if not download_file(data["url"], gfx_path):
                raise HTTPException(status_code=500, detail="remotion output download failed")
            _out = job_dir / f"final_{_tag}.mp4"
            # "8000€-camera" grade: crisp detail (unsharp) + a lifted pop (eq) so
            # it reads sharp + rich, not dark + soft. Always re-encode so it applies.
            # Subtle, natural cinematic grade — NOT a crunchy over-sharpened HDR
            # filter. Light unsharp (no halos), gentle contrast, skin stays real.
            SHARP = "unsharp=3:3:0.35:3:3:0.0,eq=contrast=1.03:saturation=1.05:brightness=0.012"
            vf = f"lut3d={LUT},{SHARP}" if use_lut else SHARP
            run(["ffmpeg", "-y", "-i", str(gfx_path), "-i", str(facecam_path),
                 "-map", "0:v:0", "-map", "1:a:0?", "-vf", vf,
                 "-c:v", "libx264", "-crf", "19", "-preset", "medium", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(_out)],
                "remotion_mux")
            return _out

        out = _render_once(props, "a")
        qa = _gemini_qa(out, qa_moments, duration) if req.qa else {"overall": "SKIP"}

        # ── QA auto-fix: one corrective re-render if the gate flags issues ──
        # DON'T nuke the graphics (that shipped bare-captions videos). Instead pin
        # every overlay to the safe rails (small 'third', above/below the face) and
        # push captions lower. Drop only the callouts (they point INTO the frame).
        if req.qa and qa.get("overall") == "ISSUES" and comp == "JustusPunches":
            _ft = (face or {}).get("top", 0.15)
            _fb = (face or {}).get("bottom", 0.66)
            _upper = round(max(0.05, _ft - 0.16), 3)
            _lower = round(min(0.82, _fb + 0.06), 3)
            safe_overlays = []
            for k, o in enumerate(props.get("overlays", [])):
                rail = _upper if (o.get("position") == "upper_third" and _ft > 0.22) else _lower
                safe_overlays.append({**o, "size": "third", "topRatio": rail})
            # keep captions ABOVE the lower-third band (0.82) — don't shove them down
            fixed = {**props, "overlays": safe_overlays, "callouts": [],
                     "captionY": min(0.70, (props.get("captionY") or 0.66) + 0.03)}
            log.info("[QA] ISSUES → corrective re-render (rail overlays, clean face)")
            out2 = _render_once(fixed, "b")
            qa2 = _gemini_qa(out2, qa_moments, duration)
            n1 = sum(1 for f in qa.get("frames", []) if not f.get("ok", True))
            n2 = sum(1 for f in qa2.get("frames", []) if not f.get("ok", True))
            if n2 <= n1:
                out, qa = out2, qa2
                qa["auto_fixed"] = True

        # SFX onto the chosen render (audio-only, doesn't affect visual QA)
        if req.sfx:
            mixed = mix_sfx_into_video(out, impacts, job_dir, duration)
            if mixed:
                out = mixed
            else:
                log.warning("[REMOTION] SFX mix produced nothing — shipping dry audio")

        # §4 background music bed, sidechain-ducked under the voice. Ohne
        # explizite URL waehlt der Selector aus der Bibliothek des Kunden.
        _music = req.music_url or _pick_music(req.client_id or "", req.briefing or {}, duration)
        if _music:
            out = _add_music_ducked(out, _music, job_dir)

        # thumbnail end-card (0.2s freeze at the very end, after everything else)
        if req.thumbnail:
            out = _append_end_thumbnail(out, req, job_dir)

        out = _fit_size(out, job_dir)   # never exceed the 50MB ceiling
        url = upload_supabase(out, f"remotion_{comp}_{job_id}", folder="renders")
        log.info("[REMOTION] DONE %s visual=%s", url, visual_diag)
        return {"ok": True, "url": url, "composition": comp, "format": req.format,
                "duration": round(duration, 3), "words": len(words),
                "impacts": len(impacts), "qa": qa, "visual": visual_diag}
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def _render_job(job_id: str, req: RemotionRenderRequest):
    RENDER_JOBS[job_id] = {"status": "processing"}
    # Ab hier haengt die client_id am Thread. Alles, was der Render darunter an
    # Modellen und Bildern zieht, landet mit dem richtigen Kunden im run_log.
    AKTIVER_CLIENT.set(req.client_id or "")
    t0 = time.time()
    try:
        res = _render_remotion_impl(req)
        RENDER_JOBS[job_id] = {"status": "done", **res}
        _log_einheit("render-laufzeit", "railway-render", time.time() - t0,
                     int((time.time() - t0) * 1000), extra={"format": req.format})
    except Exception as exc:
        _log_einheit("render-laufzeit", "railway-render", time.time() - t0,
                     int((time.time() - t0) * 1000), status="fehler",
                     extra={"format": req.format})
        log.exception("[REMOTION] job %s failed", job_id)
        RENDER_JOBS[job_id] = {"status": "error", "error": str(exc)}


@app.post("/render-remotion")
def render_remotion(req: RemotionRenderRequest, wait: bool = False):
    """Async by default: returns {job_id, status} and renders in the background.
    Poll /render-status/{job_id}. Pass ?wait=true to block and return the URL
    directly (short clips / testing only)."""
    if not FORMAT_COMPOSITION.get(req.format):
        raise HTTPException(status_code=400,
                            detail=f"unknown format '{req.format}'; known: {list(FORMAT_COMPOSITION)}")
    if wait:
        try:
            return _render_remotion_impl(req)
        except Exception as exc:
            log.exception("[REMOTION] sync fail")
            raise HTTPException(status_code=500, detail=str(exc))
    job_id = str(uuid.uuid4())
    RENDER_JOBS[job_id] = {"status": "queued"}
    _render_executor.submit(_render_job, job_id, req)
    return {"job_id": job_id, "status": "processing"}


@app.get("/render-status/{job_id}")
def render_status(job_id: str):
    j = RENDER_JOBS.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="unknown job_id")
    return {"job_id": job_id, **j}


# ── Werkzeuge für den Agenten ─────────────────────────────────────────────────
# Noch kein Loop — erst die Werkzeuge, die er später greifen soll. Jedes ist ein
# eigener Endpoint, damit es einzeln testbar bleibt und nicht erst im Agenten
# das erste Mal läuft.

class RenderHtmlRequest(BaseModel):
    markup:  str
    width:   int = W
    height:  int = 420
    seconds: float = 3.0


@app.post("/tool/render-html")
async def tool_render_html(req: RenderHtmlRequest):
    """Der Agent schreibt die Animation selbst und bekommt sie als transparentes
    Video zurück. Das ist der Unterschied zwischen Auswählen und Schneiden: er ist
    nicht mehr auf die Bausteine beschränkt, die zufällig schon existieren."""
    if not req.markup.strip():
        raise HTTPException(status_code=400, detail="markup ist leer")
    job_dir = Path(f"/tmp/htmltool_{uuid.uuid4()}")
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        out, ueber = await _render_html_alpha(req.markup, req.width, req.height,
                                              req.seconds, job_dir)
        if not out:
            raise HTTPException(status_code=500,
                                detail={"text": "HTML-Render fehlgeschlagen", "ueberlauf": ueber})
        url = upload_supabase(out, out.stem, folder="htmltool", content_type="video/webm")
        return {"ok": True, "url": url, "ueberlauf": ueber,
                "hinweis": ("Inhalt passt in die Leinwand" if not ueber else
                            "ABGESCHNITTEN — schreib den Inhalt schmaler oder fordere "
                            "eine groessere Leinwand an"),
                "seconds": min(req.seconds, HTML_TOOL_MAX_S),
                # direkt als Ebene verwendbar — transparent muss gesetzt sein,
                # sonst rendert Chromium den Alpha-Kanal schwarz
                "layer_source": {"kind": "video", "url": url, "transparent": True}}
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


class PreviewFrameRequest(BaseModel):
    session_id:        str = ""     # bevorzugt: rendert den Stand der Sitzung
    layers:            list = []
    legacy:            Optional[dict] = None
    face_url:          str = ""
    durationInSeconds: float = 15.0
    frame:             int = 0
    scale:             float = 0.5


@app.post("/tool/preview-frame")
def tool_preview_frame(req: PreviewFrameRequest):
    """EIN Frame als PNG. Ohne das plant der Agent weiter blind, nur mit mehr
    Freiheitsgraden — und mehr Freiheit ohne Kontrolle wird schlechter."""
    layers, legacy = req.layers, req.legacy
    face_url, secs = req.face_url, req.durationInSeconds
    brand = None
    if req.session_id:
        s = _sess(req.session_id)
        layers, legacy = s["layers"], None
        face_url, secs = s["face_url"], s["frames"] / FPS
        brand = s.get("brand")
    body = {"composition": "LayerStage", "frame": req.frame, "scale": req.scale,
            "inputProps": {"layers": layers, "legacy": legacy,
                           "face_url": face_url,
                           "durationInSeconds": secs,
                           **({"brand": brand} if brand else {})},
            "supabase": {"url": SUPABASE_URL, "key": SUPABASE_SERVICE_KEY,
                         "bucket": SUPABASE_BUCKET}}
    try:
        r = requests.post(f"{REMOTION_URL}/still", json=body, timeout=180)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"remotion /still: {exc}")
    if not data.get("ok"):
        raise HTTPException(status_code=502, detail=str(data.get("error"))[:300])
    return data


class SearchStockRequest(BaseModel):
    beschreibung: str


@app.post("/tool/search-stock")
def tool_search_stock(req: SearchStockRequest):
    """Suche + Vision-Prüfung in einem. Die Maschine dahinter (_stock_fuer) gab es
    längst, sie hing nur am Debug-Endpoint. Der Agent bekommt hier auch die
    Begründung und die Fehlschläge — ein Werkzeug, das nur 'nichts gefunden'
    sagt, kann er nicht anders bedienen."""
    if not req.beschreibung.strip():
        raise HTTPException(status_code=400, detail="beschreibung ist leer")
    res = _stock_fuer(req.beschreibung)
    clip = res.get("clip") or {}
    return {"ok": bool(clip.get("url")), **res,
            "layer_source": ({"kind": "video", "url": clip["url"], "transparent": False}
                             if clip.get("url") else None)}


# ── Bau-Sitzung ───────────────────────────────────────────────────────────────
# Die Bau-Werkzeuge brauchen etwas, worauf sie wirken. Eine Sitzung haelt den
# teuren Teil (Transkript, Face-Track, Transienten, Kontaktblatt) EINMAL und die
# Ebenenliste, die dazwischen waechst. Ohne sie waere jedes place_layer ein
# eigener Render mit eigenem Whisper-Lauf.
BUILD_SESSIONS: dict = {}
SESSION_TTL_S = 3 * 3600


def _sess(sid: str) -> dict:
    s = BUILD_SESSIONS.get(sid)
    if not s:
        # Container neu gestartet oder TTL abgelaufen: aus dem Checkpoint holen,
        # statt den Aufrufer bei null anfangen zu lassen.
        s = _rehydrate(sid)
    if not s:
        raise HTTPException(status_code=404, detail=f"Sitzung {sid} unbekannt oder abgelaufen")
    s["touched"] = time.time()
    return s


def _sess_gc() -> None:
    now = time.time()
    for sid in [k for k, v in BUILD_SESSIONS.items() if now - v.get("touched", 0) > SESSION_TTL_S]:
        shutil.rmtree(BUILD_SESSIONS[sid]["dir"], ignore_errors=True)
        BUILD_SESSIONS.pop(sid, None)
        log.info("[SESSION] %s abgeraeumt", sid)


# ── Teil 3: die harten Regeln ─────────────────────────────────────────────────
# Nicht verhandelbar, weil messbar. Sie werden ERZWUNGEN, nicht gemeldet: eine
# Regel, die nur im Bericht steht, ist eine Bitte. place-layer und move-layer
# lehnen ab, statt den Fehler bis zum Render mitzuschleppen.
SAFE_MARGIN = 0.06
MAX_EXTRA_LAYERS = 3
MIN_LAYER_S = 0.8
MOTION_WINDOW_F = 15        # in den ersten 15 Frames muss eine Kurve laufen
TURN_BUDGET = 30
SEKUNDEN_PRO_EBENE = 15.0   # grosszuegig — die Lueckenregel macht die Arbeit
MIN_LAYERS_ABSOLUT = 3


def _min_ebenen(duration: float) -> int:
    """Boden, nicht Ziel. Fuenf Ebenen alle im ersten Drittel fallen trotzdem
    durch, drei gleichmaessig verteilte kommen durch — das entscheidet die
    Lueckenregel. Die Zahl hier verhindert nur, dass ein 80-Sekunden-Video mit
    drei Elementen als fertig gilt."""
    return max(MIN_LAYERS_ABSOLUT, int(duration // SEKUNDEN_PRO_EBENE))


# Ebenen, die immer da sind und dem Agenten nicht gehoeren. Sie zaehlen weder
# beim Ebenen-Boden noch beim Gleichzeitig-Budget mit — sonst waere das Budget
# von Haus aus zur Haelfte verbraucht.
PFLICHT_KINDS = ("facecam", "captions")


def _ist_pflicht(l: dict) -> bool:
    return l.get("source", {}).get("kind") in PFLICHT_KINDS


def _layer_rule_errors(lay: dict, face: dict) -> list:
    """Regeln, die eine EINZELNE Ebene verletzen kann."""
    t = lay["transform"]
    ft, fb = float(face.get("top", 0.15)), float(face.get("bottom", 0.66))
    out = []
    if _ist_pflicht(lay):
        return out                      # Facecam und Captions duerfen ueberall stehen
    if (t["x"] < SAFE_MARGIN - 1e-6 or t["y"] < SAFE_MARGIN - 1e-6
            or t["x"] + t["w"] > 1 - SAFE_MARGIN + 1e-6
            or t["y"] + t["h"] > 1 - SAFE_MARGIN + 1e-6):
        out.append({"ebene": lay["id"], "regel": "safe_area",
                    "text": f"ragt in die aeusseren {int(SAFE_MARGIN * 100)}%"})
    if (lay["to"] - lay["from"]) / FPS < MIN_LAYER_S:
        out.append({"ebene": lay["id"], "regel": "mindestdauer",
                    "text": f"{(lay['to'] - lay['from']) / FPS:.2f}s, mindestens {MIN_LAYER_S}s"})
    # Das Gesicht ist eine FLAECHE, keine Zeile. Die erste Fassung dieser Regel
    # verglich nur die Hoehe — damit war jedes schmale Element NEBEN dem Gesicht
    # verboten, obwohl links und rechts Platz ist. Verdeckt ist nur, was sich in
    # BEIDEN Achsen mit dem Gesicht schneidet.
    fl, fr = float(face.get("left", 0.28)), float(face.get("right", 0.72))
    vollflaechig = t["w"] > 0.95 and t["h"] > 0.95
    senkrecht = t["y"] < fb and t["y"] + t["h"] > ft
    waagrecht = t["x"] < fr and t["x"] + t["w"] > fl
    if not vollflaechig and senkrecht and waagrecht:
        out.append({"ebene": lay["id"], "regel": "gesicht",
                    "text": f"verdeckt das Gesicht (x {fl:.2f}-{fr:.2f}, y {ft:.2f}-{fb:.2f}). "
                            f"Vier Auswege: darueber (y+h <= {ft:.2f}), darunter "
                            f"(y >= {fb:.2f}), links (x+w <= {fl:.2f}) oder rechts "
                            f"(x >= {fr:.2f}). Vollflaechig (w und h > 0.95) waere ein "
                            f"Cutaway und ist erlaubt."})
    return out


def _doppelte_pflicht(layers: list) -> list:
    """Von Facecam und Captions gibt es genau eine. Der Agent hat in zwei von
    drei Laeufen eine zweite Facecam angelegt statt die vorhandene zu
    verschieben — das dekodiert das Video doppelt und macht move_layer auf die
    erste wirkungslos, ohne dass irgendwo ein Fehler steht."""
    out = []
    for kind in PFLICHT_KINDS:
        ids = [l["id"] for l in layers if l["source"].get("kind") == kind]
        if len(ids) > 1:
            out.append({"ebene": ",".join(ids), "regel": "doppelte_pflichtebene",
                        "text": f"{len(ids)} Ebenen vom Typ '{kind}' ({', '.join(ids)}). "
                                f"Es gibt genau eine — sie liegt seit dem Oeffnen der "
                                f"Sitzung da. Verschieb sie mit move_layer, statt eine "
                                f"zweite anzulegen."})
    return out


def _budget_errors(layers: list) -> list:
    """Hoechstens drei Ebenen ausser der Facecam gleichzeitig."""
    deko = [l for l in layers if not _ist_pflicht(l)]
    for f in sorted({x for l in deko for x in (l["from"], l["to"])}):
        gleich = [l["id"] for l in deko if l["from"] <= f < l["to"]]
        if len(gleich) > MAX_EXTRA_LAYERS:
            return [{"ebene": ",".join(gleich), "regel": "budget",
                     "text": f"{len(gleich)} Ebenen gleichzeitig ab Frame {f}, "
                             f"erlaubt sind {MAX_EXTRA_LAYERS}"}]
    return []


MAX_GAP_S = 20.0            # Obergrenze fuer eine Strecke ohne sichtbares Ereignis


def _max_gap(layers: list, frames: int) -> tuple:
    """Laengste Strecke ohne EINE Ebene ausser der Facecam. Kopf- und
    Schlussstueck zaehlen mit: wer bei Sekunde 15 anfaengt, hat 15 Sekunden
    nichts gezeigt, und wer bei 60 aufhoert, laesst zwanzig liegen.

    Gibt (Laenge in Frames, Startframe) zurueck."""
    deko = sorted([(l["from"], l["to"]) for l in layers if not _ist_pflicht(l)])
    luecke, wo, cursor = 0, 0, 0
    for a, b in deko:
        if a > cursor and a - cursor > luecke:
            luecke, wo = a - cursor, cursor
        cursor = max(cursor, b)
    if frames - cursor > luecke:
        luecke, wo = frames - cursor, cursor
    return luecke, wo


def _gap_grenze(duration: float) -> float:
    """Zwanzig Sekunden Stille sind bei 80 Sekunden Material ein Signal. Bei
    einem 30-Sekunden-Clip waeren zwanzig Sekunden fast alles — deshalb greift
    dort ein Viertel der Laufzeit, mit sechs Sekunden als Boden."""
    return min(MAX_GAP_S, max(6.0, duration * 0.25))


def _motion_at_zero(layers: list) -> bool:
    """Ab Frame 0 laeuft eine Bewegung. Handheld zaehlt NICHT — das ist
    Subpixel-Atmen, kein Anfang."""
    return any(int(a.get("start", 0)) < MOTION_WINDOW_F and int(a.get("end", 0)) > 0
               for l in layers for a in (l.get("animate") or []))


def _hard_check(layers: list, face: dict) -> list:
    fehler = [e for l in layers for e in _layer_rule_errors(l, face)]
    return fehler + _budget_errors(layers) + _doppelte_pflicht(layers)


# Spiegel von remotion-renderer/src/theme.ts. Die Ebenen-Composition hat den
# Justus-Brand als Default — wer nichts schickt, bekommt Amethyst. Fuer Tim ist
# das kein Detail, sondern das falsche Video.
BRAND_PRESETS = {
    "justus": {"bg": "#09090B", "bgGlow": "#140F22", "accent": "#8B5CF6",
               "accent2": "#06B6D4", "text": "#FFFFFF", "muted": "#A1A1AA"},
    "tim":    {"bg": "#0A1F19", "bgGlow": "#0F2A22", "accent": "#C9A24B",
               "accent2": "#3FB89B", "text": "#F5F2EC", "muted": "#9DB3AC"},
}
# Justus: harte Wortkarten, drei gleichzeitig, das gesprochene ploppt.
# Tim: ruhige Editorial-Zeile. Vertrauen vor Reiz, ausdruecklich sein Stil.
CAPTION_STIL = {"justus": "hormozi", "tim": "editorial"}


def _brand_fuer(client_id: str, tpl: dict) -> dict:
    """Markenfarben des Kunden. Das Template darf die Akzente ueberschreiben,
    der Rest kommt aus dem Preset."""
    b = dict(BRAND_PRESETS.get((client_id or "justus").lower(), BRAND_PRESETS["justus"]))
    c = _tpl_colors(tpl) or {}
    if c.get("primary"):
        b["accent"] = c["primary"]
    if c.get("secondary"):
        b["accent2"] = c["secondary"]
    if c.get("bg"):
        b["bg"] = c["bg"]
    return b


def _herkunft(source: dict) -> str:
    """Woher die Ebene stammt — aus der Quelle abgeleitet, nicht vom Agenten
    erfragt. Ein Feld, das jemand ausfuellen MUSS, ist ein Feld, das irgendwann
    'agent' enthaelt und damit nichts mehr sagt."""
    kind = str(source.get("kind") or "")
    if kind == "facecam":
        return "facecam"
    url = str(source.get("url") or "")
    if "/htmltool/" in url:
        return "html"
    if "/scenes/" in url:
        return "generiert"
    if "pexels" in url or "pixabay" in url:
        return "stock"
    if kind in ("video", "image") and url:
        return "extern"
    return "baustein"


def _layer_hints(lay: dict) -> list:
    """Fussangeln, die kein Fehler sind, aber fast immer ungewollt. w und h sind
    Anteile VERSCHIEDENER Kanten (1080 bzw. 1920) — wer beide gleich setzt und
    einen Kreis erwartet, bekommt ein Ei."""
    h = []
    t = lay["transform"]
    px_w, px_h = t["w"] * W, t["h"] * H
    if lay["mask"] == "circle" and px_h > 0 and not (0.9 < px_w / px_h < 1.11):
        h.append(f"mask 'circle' bei {px_w:.0f}x{px_h:.0f}px ergibt eine Ellipse. "
                 f"Fuer einen Kreis h = w * {W}/{H} = {t['w'] * W / H:.3f} setzen.")
    if t["w"] > 0.999 and t["h"] > 0.999 and lay["source"].get("kind") != "facecam":
        h.append("vollflaechig — das deckt das Gesicht ab, also ein Cutaway. "
                 "Wenn das nicht gewollt ist, kleiner machen.")
    return h


ANIM_PROPS = ("x", "y", "w", "h", "scale", "opacity", "rotate")
ANIM_EASE = ("linear", "spring", "easeOut", "easeInOut")


def _clean_animate(raw_list, frames: int) -> tuple:
    """Kurven pruefen, statt sie durchzureichen.

    Der erste Loop-Lauf hat hier 20 Turns verbrannt: das Modell hat ein eigenes
    Keyframe-Format erfunden ({at, transform}), weil das Werkzeugschema die Form
    nicht beschrieb. Python liess es durch, die Fertig-Pruefung sagte weiter
    'keine Kurve', und Remotion ist am Ende an `inputRange must contain only
    numbers` gestorben. Drei Stellen, an denen es haette auffallen muessen.
    """
    clean, fehler = [], []
    for i, a in enumerate(raw_list or []):
        if not isinstance(a, dict):
            fehler.append(f"animate[{i}] ist kein Objekt")
            continue
        if "at" in a or "transform" in a:
            fehler.append(
                f"animate[{i}] benutzt ein Keyframe-Format. Richtig ist EINE Kurve pro "
                f"Eintrag: {{property, from, to, start, end, easing}} — Frames, keine Sekunden.")
            continue
        p = str(a.get("property", ""))
        if p not in ANIM_PROPS:
            fehler.append(f"animate[{i}].property '{p}' unbekannt, erlaubt: {list(ANIM_PROPS)}")
            continue
        try:
            von, bis = float(a["from"]), float(a["to"])
            st, en = int(a["start"]), int(a["end"])
        except (KeyError, TypeError, ValueError):
            fehler.append(f"animate[{i}]: from/to muessen Zahlen sein, start/end Frames "
                          f"(ganze Zahlen). Bekommen: {json.dumps(a, ensure_ascii=False)[:120]}")
            continue
        if en <= st:
            fehler.append(f"animate[{i}]: end ({en}) muss groesser als start ({st}) sein")
            continue
        e = a.get("easing", "easeOut")
        clean.append({"property": p, "from": von, "to": bis,
                      "start": max(0, st), "end": min(frames, en),
                      "easing": e if e in ANIM_EASE else "easeOut"})
    return clean, fehler


def _layer_defaults(raw: dict, frames: int) -> dict:
    """Fuellt eine Ebene auf die Form, die LayerStage erwartet. Der Agent soll
    nicht jedes Feld kennen muessen — was er nicht sagt, ist Vollbild, sichtbar,
    ohne Maske."""
    t = dict(raw.get("transform") or {})
    tr = {"x": float(t.get("x", 0)), "y": float(t.get("y", 0)),
          "w": float(t.get("w", 1)), "h": float(t.get("h", 1)),
          "scale": float(t.get("scale", 1)), "rotate": float(t.get("rotate", 0)),
          "opacity": float(t.get("opacity", 1)),
          "origin": list(t.get("origin", [0.5, 0.5]))[:2] or [0.5, 0.5]}
    m = dict(raw.get("modifiers") or {})
    mods = {"handheld": bool(m.get("handheld", False)),
            "grade": bool(m.get("grade", False)),
            "punch": m.get("punch")}
    anim, anim_fehler = _clean_animate(raw.get("animate"), frames)
    if anim_fehler:
        raise HTTPException(status_code=422, detail={
            "abgelehnt": raw.get("id"), "fehler": [{"regel": "animate", "text": t}
                                                   for t in anim_fehler]})
    return {
        "id": str(raw.get("id") or f"L{uuid.uuid4().hex[:6]}"),
        "source": raw.get("source") or {"kind": "text", "content": ""},
        "from": max(0, int(raw.get("from", 0))),
        "to": min(frames, int(raw.get("to", frames))),
        "z": int(raw.get("z", 20)),
        "transform": tr, "animate": anim,
        "modifiers": mods,
        "mask": raw.get("mask") if raw.get("mask") in ("none", "circle", "rounded", "speaker") else "none",
        "blend": str(raw.get("blend") or "normal"),
        "herkunft": str(raw.get("herkunft") or _herkunft(raw.get("source") or {})),
        "konzept": str(raw.get("konzept") or ""),
    }


def _build_prefix(duration: float, frames_gesamt: int, face: dict, words: list,
                  onsets: list, style: str) -> str:
    """Der STATISCHE Block, der in jedem Turn identisch mitginge. Er wird EINMAL
    beim Oeffnen der Sitzung gebaut und danach nie wieder berechnet.

    Hier darf NICHTS stehen, was sich waehrend der Sitzung aendert. Die erste
    Fassung enthielt die aktuelle Framezahl — und `set-duration` schreibt genau
    die um. Der Praefix waere mitten im Loop ein anderer gewesen, der Cache
    stillschweigend gefallen, und sichtbar geworden waere das erst auf der
    Rechnung. Deshalb steht hier die Laenge des MATERIALS, nicht die des
    aktuellen Schnitts."""
    peaks = ", ".join(f"{o:.2f}" for o in (onsets or [])[:60])
    return (
        "STIL DES KUNDEN\n" + (style or "-") + "\n\n"
        f"MATERIAL: {duration:.2f}s, {frames_gesamt} Frames bei {FPS} fps, 1080x1920.\n"
        f"GESICHT (Anteile der Hoehe): oben {face.get('top', '?')}, unten {face.get('bottom', '?')}, "
        f"Nase bei x={face.get('origin_x', '?')} y={face.get('origin_y', '?')}.\n\n"
        "TRANSKRIPT MIT ZEITEN\n" + _timed_transcript(words or []) + "\n\n"
        "BETONUNGEN (Sekunden)\n" + (peaks or "-") + "\n"
    )


class OpenSessionRequest(BaseModel):
    facecam:      str
    client_id:    str = "justus"
    briefing:     Optional[dict] = None
    trim:         bool = True
    turn_budget:  int = TURN_BUDGET


@app.post("/tool/session/open")
def tool_session_open(req: OpenSessionRequest):
    """Einmal lesen, dann bauen. Alles Teure passiert hier."""
    _sess_gc()
    sid = uuid.uuid4().hex[:12]
    job = Path(f"/tmp/session_{sid}")
    job.mkdir(parents=True, exist_ok=True)
    cam = job / "facecam.mp4"
    if not download_file(req.facecam, cam):
        shutil.rmtree(job, ignore_errors=True)
        raise HTTPException(status_code=400, detail="facecam nicht ladbar")
    if req.trim:
        trimmed, _ = _trim_pipeline(cam, job)
        if trimmed != cam:
            cam = trimmed
    cam = _fit_size(cam, job, target_mb=46, name="facecam_fit.mp4")
    face_url = upload_supabase(cam, f"facecam_{sid}", folder="uploads")

    duration = probe_duration(cam)
    words = transcribe_audio(cam) or []
    face = _face_track(cam, duration)
    onsets = _audio_onsets(cam, job)
    sheet = _contact_sheet(cam, words, face, duration, onsets, job)
    sheet_url = upload_supabase(sheet, f"sheet_{sid}", folder="preview") if sheet else ""

    tpl = _load_template(req.client_id, None)
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/clients",
                         params={"client_id": f"eq.{req.client_id}", "select": "edit_style"},
                         headers={"apikey": SUPABASE_SERVICE_KEY,
                                  "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}, timeout=20)
        style = ((r.json() or [{}])[0] or {}).get("edit_style") or ""
    except Exception:
        style = ""

    frames = int(round(duration * FPS))
    # Einmal bauen, danach nie wieder anfassen — der Cache haengt an der
    # Byte-Gleichheit dieses Strings.
    prefix = _build_prefix(duration, frames, face, words, onsets, style)
    s = {
        "prefix": prefix,
        "prefix_sha": hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:16],
        "id": sid, "dir": job, "client_id": req.client_id,
        "facecam_path": cam, "face_url": face_url,
        "duration": duration, "frames": frames,
        "words": words, "face": face, "onsets": onsets,
        "sheet": sheet, "sheet_url": sheet_url,
        "style_guide": style, "colors": _tpl_colors(tpl),
        "brand": _brand_fuer(req.client_id, tpl),
        "caption_stil": CAPTION_STIL.get((req.client_id or "justus").lower(), "hormozi"),
        "briefing": req.briefing, "sfx": [], "music": None,
        "touched": time.time(),
        "turns_used": 0, "turn_budget": req.turn_budget, "abbruch_grund": "",
        "gelesen": set(), "verlauf": [],
        "tokens": {"ein": 0, "aus": 0, "cached": 0, "abgeschnitten": 0,
                   "pro_werkzeug": {}},
        # Die Facecam liegt von Anfang an als Ebene da — der Agent soll sie
        # verschieben koennen, nicht erst erfinden muessen.
        "layers": [_layer_defaults({
            "id": "facecam", "z": 10,
            "source": {"kind": "facecam", "url": face_url},
            "from": 0, "to": frames,
            "transform": {"origin": [face.get("origin_x", 0.5), face.get("origin_y", 0.42)]},
            "modifiers": {"handheld": True, "grade": True,
                          "punch": {"frames": [], "hookEndFrame": 0,
                                    "outroStartFrame": 0, "base": 1.04}},
            "herkunft": "facecam",
        }, frames),
            # Captions sind keine Agentenentscheidung. Sie waren im alten
            # Stapel immer da; ein Video ohne sie ist ein Rueckschritt, kein
            # Stilmittel. Verschieben darf er sie, wegnehmen nicht.
            _layer_defaults({
                "id": "captions", "z": 29,
                "source": {"kind": "captions", "chunks": _remotion_chunks(words),
                           "stil": CAPTION_STIL.get((req.client_id or "justus").lower(), "hormozi"),
                           "y": round(min(0.68, float(face.get("bottom", 0.63)) + 0.05), 3),
                           "fontSize": 66, "duckFor": [], "duckY": 0.62,
                           "duckFontSize": 58, "hookEndFrame": 0, "hookY": 0.68,
                           "hookFontSize": 62, "outroStartFrame": 0, "allAccent": False},
                "from": 0, "to": frames, "herkunft": "captions",
            }, frames)],
    }
    BUILD_SESSIONS[sid] = s
    log.info("[SESSION] %s offen: %.1fs, %d Woerter, Gesicht %s, %d Transienten",
             sid, duration, len(words), bool(face), len(onsets))
    return {"ok": True, "session_id": sid, "duration": round(duration, 3), "frames": frames,
            "fps": FPS, "face_url": face_url, "contact_sheet": sheet_url,
            "words": len(words), "face": face, "onsets": len(onsets),
            "layers": [l["id"] for l in s["layers"]]}


@app.get("/tool/session/{sid}")
def tool_session_state(sid: str):
    s = _sess(sid)
    return {"ok": True, "session_id": sid, "frames": s["frames"],
            "duration": round(s["duration"], 3), "layers": s["layers"],
            "sfx": s["sfx"], "music": s["music"],
            "turns_used": s["turns_used"], "turn_budget": s["turn_budget"],
            "abbruch_grund": s["abbruch_grund"]}


class SessionRef(BaseModel):
    session_id: str


# Felder, die in den Checkpoint gehoeren. Der Rest (Pfade, Timestamps) ist
# entweder wiederherstellbar oder gehoert nicht in eine Datenbank.
_CKPT_FIELDS = ("id", "client_id", "face_url", "duration", "frames", "words", "face",
                "onsets", "sheet_url", "style_guide", "colors", "briefing", "sfx",
                "music", "layers", "turns_used", "turn_budget", "abbruch_grund",
                "prefix", "prefix_sha", "gelesen", "verlauf", "tokens",
                "brand", "caption_stil")


def _checkpoint(s: dict, status: str = "offen") -> None:
    """Nach JEDEM Turn. Stuerzt der Loop bei Turn 22 ab, faengt ein Neustart
    nicht bei null an. Die Sitzung hielt den Zustand schon — sie hat ihn nur
    nirgends abgelegt, wo ein anderer Prozess ihn findet."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        return
    state = {k: (sorted(s[k]) if isinstance(s.get(k), set) else s.get(k))
             for k in _CKPT_FIELDS}
    try:
        requests.post(f"{SUPABASE_URL}/rest/v1/agent_sessions", timeout=20,
                      headers={"apikey": SUPABASE_SERVICE_KEY,
                               "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                               "Content-Type": "application/json",
                               "Prefer": "resolution=merge-duplicates"},
                      json=[{"session_id": s["id"], "client_id": s["client_id"],
                             "status": status, "turns_used": s["turns_used"],
                             "turn_budget": s["turn_budget"], "state": state,
                             "updated_at": datetime.now(timezone.utc).isoformat()}])
    except Exception as exc:
        log.warning("[CKPT] %s nicht geschrieben: %s", s["id"], exc)


def _rehydrate(sid: str) -> Optional[dict]:
    """Sitzung aus dem Checkpoint zurueckholen. Die Facecam liegt in Supabase,
    also laesst sie sich neu laden — Transkript, Face-Track und Transienten
    stehen im Zustand und werden NICHT neu berechnet."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        return None
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/agent_sessions", timeout=25,
                         params={"session_id": f"eq.{sid}", "select": "state,status"},
                         headers={"apikey": SUPABASE_SERVICE_KEY,
                                  "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
        r.raise_for_status()
        rows = r.json() or []
    except Exception as exc:
        log.warning("[CKPT] %s nicht lesbar: %s", sid, exc)
        return None
    if not rows:
        return None
    st = rows[0]["state"]
    job = Path(f"/tmp/session_{sid}")
    job.mkdir(parents=True, exist_ok=True)
    cam = job / "facecam.mp4"
    if not cam.exists() and not download_file(st["face_url"], cam):
        log.error("[CKPT] %s: Facecam nicht wiederherstellbar", sid)
        return None
    s = dict(st)
    s["dir"] = job
    s["facecam_path"] = cam
    s["sheet"] = None
    s["gelesen"] = set(st.get("gelesen") or [])
    s["touched"] = time.time()
    BUILD_SESSIONS[sid] = s
    log.info("[CKPT] %s wiederaufgenommen bei Turn %d/%d, %d Ebenen",
             sid, s["turns_used"], s["turn_budget"], len(s["layers"]))
    return s


PFLICHTLEKTUERE = {"contact-sheet", "transcript"}


def _lesen_guard(s: dict) -> None:
    """Erst lesen, dann bauen. Ein Agent, der sofort place_layer ruft, ohne das
    Kontaktblatt angesehen zu haben, produziert genau das, was diese ganze
    Umbaureihe abstellen soll: Elemente, die irgendwo sitzen.

    Geprueft wird, DASS er gelesen hat — ob er hingesehen hat, kann niemand
    pruefen. Deshalb legt der Loop das Kontaktblatt zusaetzlich ungefragt in den
    ersten Turn."""
    fehlt = PFLICHTLEKTUERE - s.get("gelesen", set())
    if fehlt:
        raise HTTPException(status_code=428, detail={
            "grund": "nicht_gelesen",
            "text": "Erst lesen, dann bauen. Noch nicht aufgerufen: "
                    + ", ".join(f"/tool/read/{x}" for x in sorted(fehlt)),
            "fehlt": sorted(fehlt)})


def _budget_guard(s: dict) -> None:
    """Harter Abbruch. Ein Agent, der nicht konvergiert, dreht sonst still
    Runden — sichtbar wird das erst auf der Rechnung. Nach dem Budget nimmt die
    Sitzung keine Aenderung mehr an; gerendert wird mit dem, was steht."""
    if s["turns_used"] >= s["turn_budget"]:
        raise HTTPException(status_code=409, detail={
            "grund": "turn_budget",
            "text": f"{s['turn_budget']} Turns verbraucht. Keine Aenderungen mehr — "
                    f"jetzt /tool/session/render mit dem, was steht.",
            "turns_used": s["turns_used"]})


def _fertig(s: dict) -> dict:
    """Objektive Fertig-Definition. NICHT der Agent entscheidet das: Modelle
    hoeren entweder zu frueh auf oder polieren ewig. Drei messbare Bedingungen,
    alle drei muessen stimmen."""
    face = s.get("face") or {}
    deko = [l for l in s["layers"] if not _ist_pflicht(l)]
    fehler = _hard_check(s["layers"], face)
    bewegung = _motion_at_zero(s["layers"])
    luecke_f, luecke_ab = _max_gap(s["layers"], s["frames"])
    grenze = _gap_grenze(s["frames"] / FPS)
    offen = []
    if fehler:
        offen.append(f"{len(fehler)} Regelverstoesse offen")
    noetig = _min_ebenen(s["frames"] / FPS)
    if len(deko) < noetig:
        offen.append(f"{len(deko)} von {noetig} Ebenen (Facecam und Captions zaehlen nicht)")
    if not bewegung:
        offen.append(f"keine animate-Kurve in den ersten {MOTION_WINDOW_F} Frames")
    # Vierte Bedingung: drei Ebenen im ersten Drittel und danach eine Minute
    # nichts erfuellen die ersten drei Bedingungen — und sind trotzdem kein
    # fertiges Video. Das ist der Unterschied zwischen "aufgehoert zu arbeiten"
    # und "fertig".
    if luecke_f / FPS > grenze:
        offen.append(f"{luecke_f / FPS:.1f}s ohne sichtbares Ereignis ab "
                     f"{luecke_ab / FPS:.1f}s (erlaubt {grenze:.1f}s)")
    return {"fertig": not offen, "offen": offen, "fehler": fehler,
            "ebenen_ohne_facecam": len(deko), "bewegung_ab_null": bewegung,
            "groesste_luecke_s": round(luecke_f / FPS, 1),
            "luecke_ab_s": round(luecke_ab / FPS, 1),
            "luecke_grenze_s": round(grenze, 1)}


@app.post("/tool/session/tick")
def tool_session_tick(req: SessionRef):
    """Einmal pro Turn vom Loop aufzurufen. Zaehlt den Turn, prueft die
    Fertig-Definition und sagt, ob weitergebaut werden darf.

    `weiter: false` heisst in beiden Faellen dasselbe fuer den Aufrufer —
    rendern. Der Unterschied steht im Grund: `fertig` ist ein Ergebnis,
    `turn_budget` ist ein Abbruch."""
    s = _sess(req.session_id)
    s["turns_used"] += 1
    stand = _fertig(s)
    rest = s["turn_budget"] - s["turns_used"]
    if stand["fertig"]:
        grund = "fertig"
    elif rest <= 0:
        grund = "turn_budget"
    else:
        grund = ""
    s["abbruch_grund"] = grund or s.get("abbruch_grund", "")
    s["verlauf"].append({"turn": s["turns_used"], "ebenen": len(s["layers"]),
                         "offen": stand["offen"]})
    _checkpoint(s, "fertig" if grund == "fertig" else
                ("abgebrochen" if grund == "turn_budget" else "offen"))
    return {"ok": True, "turns_used": s["turns_used"], "turns_left": max(0, rest),
            "weiter": not (stand["fertig"] or rest <= 0), "grund": grund, **stand}


# ── Lesen ─────────────────────────────────────────────────────────────────────
@app.post("/tool/read/transcript")
def tool_read_transcript(req: SessionRef):
    s = _sess(req.session_id)
    s["gelesen"].add("transcript")
    return {"ok": True, "text": _timed_transcript(s["words"], max_chars=20000),
            "words": [{"w": w["word"], "t": round(float(w["start"]), 2),
                       "e": round(float(w.get("end") or w["start"]), 2)} for w in s["words"]]}


class ContactSheetRequest(BaseModel):
    session_id: str
    von: float = 0.0
    bis: float = 0.0   # 0 = bis zum Ende


@app.post("/tool/read/contact-sheet")
def tool_read_contact_sheet(req: ContactSheetRequest):
    """Kontaktblatt fuer einen Ausschnitt — acht Standbilder, Wellenform,
    Sprechbalken. Ohne Ausschnitt das ganze Video (dann das aus der Sitzung)."""
    s = _sess(req.session_id)
    s["gelesen"].add("contact-sheet")
    bis = req.bis if req.bis > req.von else s["duration"]
    if req.von <= 0 and bis >= s["duration"] and s.get("sheet_url"):
        return {"ok": True, "url": s["sheet_url"], "von": 0.0, "bis": round(s["duration"], 2)}
    sub = _contact_sheet(s["facecam_path"], s["words"], s["face"], s["duration"],
                         s["onsets"], s["dir"], von=req.von, bis=bis)
    if not sub:
        raise HTTPException(status_code=500, detail="Kontaktblatt fehlgeschlagen")
    url = upload_supabase(sub, f"sheet_{s['id']}_{int(req.von)}_{int(bis)}", folder="preview")
    return {"ok": True, "url": url, "von": round(req.von, 2), "bis": round(bis, 2)}


@app.post("/tool/read/context")
def tool_read_context(req: SessionRef):
    """Der statische Block, den ein Loop in JEDEM Turn mitschicken wuerde: Stil,
    Masse, Gesicht, Transkript, Betonungen. Er kommt hier fertig heraus, damit er
    im Loop unveraendert vor den Cache-Punkt gelegt werden kann — veraendert man
    ihn zwischen zwei Turns auch nur um ein Zeichen, faellt der Cache."""
    s = _sess(req.session_id)
    pre = s["prefix"]
    return {"ok": True, "prefix": pre, "zeichen": len(pre),
            "sha": s["prefix_sha"],
            "contact_sheet": s.get("sheet_url", ""),
            "hinweis": "beim Oeffnen der Sitzung eingefroren. Unveraendert als "
                       "cache_prefix senden; die sha ist dieselbe, solange der "
                       "Block derselbe ist."}


@app.post("/tool/read/face-track")
def tool_read_face_track(req: SessionRef):
    s = _sess(req.session_id)
    s["gelesen"].add("face-track")
    if not s["face"]:
        return {"ok": False, "grund": "kein Face-Track — Rails laufen auf Annahme 0.15/0.66",
                "face": {"top": 0.15, "bottom": 0.66, "origin_x": 0.5, "origin_y": 0.42}}
    f = s["face"]
    return {"ok": True, "face": f,
            "frei_links": round(float(f.get("left", 0.28)), 3),
            "frei_rechts": round(float(f.get("right", 0.72)), 3),
            "hinweis": "Anteile der Kanten (x von 1080, y von 1920). Verdeckt ist nur, "
                       "was sich in BEIDEN Achsen mit der Box schneidet — ein schmales "
                       f"Element links von {float(f.get('left', 0.28)):.2f} oder rechts von "
                       f"{float(f.get('right', 0.72)):.2f} darf auf Augenhoehe stehen."}


@app.post("/tool/read/audio-peaks")
def tool_read_audio_peaks(req: SessionRef):
    s = _sess(req.session_id)
    s["gelesen"].add("audio-peaks")
    return {"ok": True, "peaks": s["onsets"], "anzahl": len(s["onsets"]),
            "hinweis": "Sekunden. Ein Einsatz auf einem Peak sitzt auf der Betonung."}


class HistoryRequest(BaseModel):
    client_id: str = "justus"
    n:         int = 5


@app.post("/tool/read/history")
def tool_read_history(req: HistoryRequest):
    """Was in den letzten Videos schon dran war. Ohne das kann eine Regie nicht
    variieren wollen — sie weiss ja nicht, was sie schon gemacht hat."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        return {"ok": False, "grund": "Supabase nicht konfiguriert", "renders": []}
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/render_layers", timeout=25,
                         params={"client_id": f"eq.{req.client_id}",
                                 "select": "render_id,source_kind,konzept,z,from_frame,to_frame,transform,herkunft,created_at",
                                 "order": "created_at.desc", "limit": str(max(1, req.n) * 40)},
                         headers={"apikey": SUPABASE_SERVICE_KEY,
                                  "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
        r.raise_for_status()
        rows = r.json() or []
    except Exception as exc:
        return {"ok": False, "grund": str(exc)[:200], "renders": []}
    renders: list = []
    seen: dict = {}
    for row in rows:
        rid = row["render_id"]
        if rid not in seen:
            if len(renders) >= req.n:
                continue
            seen[rid] = {"render_id": rid, "wann": row["created_at"], "elemente": []}
            renders.append(seen[rid])
        seen[rid]["elemente"].append({"art": row["source_kind"], "konzept": row["konzept"],
                                      "von": row["from_frame"], "bis": row["to_frame"],
                                      "transform": row.get("transform") or {},
                                      "herkunft": row.get("herkunft")})
    konzepte = [e["konzept"] for r_ in renders for e in r_["elemente"] if e["konzept"]]
    # Die drei Fragen, fuer die diese Tabelle gebaut wurde. Rohdaten
    # zurueckzugeben und den Agenten rechnen zu lassen waere billiger und
    # schlechter — er soll die Antwort sehen, nicht die Zahlen.
    alle = [e for r_ in renders for e in r_["elemente"]]
    cam = [e for e in alle if e["art"] == "facecam"]
    in_der_ecke = sum(1 for e in cam
                      if (e.get("transform") or {}).get("w", 1) < 0.9)
    cutaways = [e for e in alle if e["art"] != "facecam"
                and (e.get("transform") or {}).get("w", 0) > 0.95
                and (e.get("transform") or {}).get("h", 0) > 0.95]
    letzter_cut = max((e["bis"] - e["von"] for e in cutaways), default=0)
    herkunft: dict = {}
    for e in alle:
        if e["art"] != "facecam":
            herkunft[e.get("herkunft") or "?"] = herkunft.get(e.get("herkunft") or "?", 0) + 1
    return {"ok": True, "renders": renders,
            "schon_benutzt": sorted(set(konzepte)),
            "facecam_in_der_ecke": f"{in_der_ecke} von {len(cam)} Videos",
            "laengster_cutaway_s": round(letzter_cut / FPS, 1),
            "herkunft_verteilung": herkunft,
            "hinweis": "Was hier steht, war schon dran. Wiederholung nur, wenn sie gewollt ist."}


class StyleGuideRequest(BaseModel):
    client_id: str = "justus"


@app.post("/tool/read/style-guide")
def tool_read_style_guide(req: StyleGuideRequest):
    tpl = _load_template(req.client_id, None)
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/clients", timeout=20,
                         params={"client_id": f"eq.{req.client_id}", "select": "edit_style,name"},
                         headers={"apikey": SUPABASE_SERVICE_KEY,
                                  "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
        row = (r.json() or [{}])[0] or {}
    except Exception:
        row = {}
    return {"ok": True, "client_id": req.client_id, "name": row.get("name") or req.client_id,
            "edit_style": row.get("edit_style") or "", "farben": _tpl_colors(tpl)}


# ── Beschaffen ────────────────────────────────────────────────────────────────
class InspectClipRequest(BaseModel):
    url:          str
    beschreibung: str


@app.post("/tool/inspect-clip")
def tool_inspect_clip(req: InspectClipRequest):
    """Zeigt der Clip wirklich, was er soll? Prueft ein Standbild — was er ueber
    die BEWEGUNG sagt, ist geraten. Das ist die ehrliche Grenze des Verfahrens."""
    job = Path(f"/tmp/inspect_{uuid.uuid4().hex[:8]}")
    job.mkdir(parents=True, exist_ok=True)
    try:
        src = job / "clip"
        if not download_file(req.url, src):
            raise HTTPException(status_code=400, detail="Clip nicht ladbar")
        shot = job / "shot.jpg"
        subprocess.run(["ffmpeg", "-y", "-ss", "0.5", "-i", str(src), "-frames:v", "1",
                        "-vf", "scale=640:-1", str(shot)], check=True, capture_output=True)
        import base64
        b64 = base64.b64encode(shot.read_bytes()).decode()
        treffer = _vision_pick(req.beschreibung, [{"thumb": "data:image/jpeg;base64," + b64,
                                                   "id": "clip", "url": req.url}])
        return {"ok": bool(treffer), "zeigt_es": bool(treffer),
                "begruendung": (treffer or {}).get("begruendung", "kein Treffer"),
                "grenze": "geprueft wurde EIN Standbild, nicht die Bewegung"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)[:200])
    finally:
        shutil.rmtree(job, ignore_errors=True)


class GenerateImageRequest(BaseModel):
    prompt:    str
    client_id: str = "justus"


@app.post("/tool/generate-image")
def tool_generate_image(req: GenerateImageRequest):
    job = Path(f"/tmp/img_{uuid.uuid4().hex[:8]}")
    job.mkdir(parents=True, exist_ok=True)
    try:
        url = _gen_scene_image(req.prompt, req.client_id, job)
        if not url:
            raise HTTPException(status_code=502, detail="Bildgenerierung fehlgeschlagen")
        return {"ok": True, "url": url,
                "layer_source": {"kind": "image", "url": url}}
    finally:
        shutil.rmtree(job, ignore_errors=True)


# ── Bauen ─────────────────────────────────────────────────────────────────────
class PlaceLayerRequest(BaseModel):
    session_id: str
    layer:      dict


@app.post("/tool/place-layer")
def tool_place_layer(req: PlaceLayerRequest):
    s = _sess(req.session_id)
    _budget_guard(s)
    _lesen_guard(s)
    lay = _layer_defaults(req.layer, s["frames"])
    kandidat = [l for l in s["layers"] if l["id"] != lay["id"]] + [lay]
    fehler = _hard_check(kandidat, s.get("face") or {})
    if fehler:
        # Ablehnen, nicht mitschleppen. Eine Regel, die man erst im Bericht
        # sieht, ist eine Bitte.
        raise HTTPException(status_code=422, detail={"abgelehnt": lay["id"], "fehler": fehler})
    s["layers"] = kandidat
    hin = _layer_hints(lay)
    # Drei gleiche Formate hintereinander sind kein Regelverstoss, aber der
    # deutlichste Hinweis darauf, dass jemand nur noch ablegt statt zu setzen.
    deko = [l for l in s["layers"] if not _ist_pflicht(l)]
    letzte = sorted(deko, key=lambda l: l["from"])[-3:]
    if len(letzte) == 3 and len({(round(l["transform"]["w"], 2),
                                  round(l["transform"]["h"], 2)) for l in letzte}) == 1:
        hin.append(f"drei Ebenen in Folge mit derselben Breite und Hoehe "
                   f"({letzte[-1]['transform']['w']:.2f} x {letzte[-1]['transform']['h']:.2f}). "
                   f"Ein schmales Element (w 0.25-0.40) passt neben das Gesicht.")
    return {"ok": True, "layer": lay, "layers": len(s["layers"]), "hinweise": hin}


class MoveLayerRequest(BaseModel):
    session_id: str
    id:         str
    transform:  Optional[dict] = None
    animate:    Optional[list] = None
    z:          Optional[int] = None
    from_frame: Optional[int] = None
    to_frame:   Optional[int] = None
    mask:       Optional[str] = None


@app.post("/tool/move-layer")
def tool_move_layer(req: MoveLayerRequest):
    s = _sess(req.session_id)
    lay = next((l for l in s["layers"] if l["id"] == req.id), None)
    if not lay:
        raise HTTPException(status_code=404, detail=f"Ebene '{req.id}' gibt es nicht")
    _budget_guard(s)
    vorher = copy.deepcopy(lay)
    if req.transform:
        lay["transform"].update({k: v for k, v in req.transform.items()
                                 if k in lay["transform"]})
        # Die Captions haengen ihre Hoehe an source.y, nicht am Transform-Kasten.
        # Ohne diese Zeile waere ein move_layer darauf ein Aufruf, der nichts tut.
        if lay["source"].get("kind") == "captions" and "y" in req.transform:
            lay["source"]["y"] = float(req.transform["y"])
    if req.animate is not None:
        anim, anim_fehler = _clean_animate(req.animate, s["frames"])
        if anim_fehler:
            raise HTTPException(status_code=422, detail={
                "abgelehnt": req.id, "fehler": [{"regel": "animate", "text": t}
                                                for t in anim_fehler]})
        lay["animate"] = anim
    if req.z is not None:
        lay["z"] = int(req.z)
    if req.from_frame is not None:
        lay["from"] = max(0, int(req.from_frame))
    if req.to_frame is not None:
        lay["to"] = min(s["frames"], int(req.to_frame))
    if req.mask in ("none", "circle", "rounded", "speaker"):
        lay["mask"] = req.mask
    fehler = _hard_check(s["layers"], s.get("face") or {})
    if fehler:
        s["layers"] = [vorher if l["id"] == req.id else l for l in s["layers"]]
        raise HTTPException(status_code=422, detail={"abgelehnt": req.id, "fehler": fehler,
                                                     "zurueckgesetzt": True})
    return {"ok": True, "layer": lay, "hinweise": _layer_hints(lay)}


class RemoveLayerRequest(BaseModel):
    session_id: str
    id:         str


@app.post("/tool/remove-layer")
def tool_remove_layer(req: RemoveLayerRequest):
    s = _sess(req.session_id)
    _budget_guard(s)
    ziel = next((l for l in s["layers"] if l["id"] == req.id), None)
    if ziel and _ist_pflicht(ziel):
        raise HTTPException(status_code=422, detail={
            "abgelehnt": req.id,
            "text": f"'{req.id}' ist eine Pflichtebene. Verschieben, skalieren und "
                    f"umzeiten geht (move_layer), wegnehmen nicht."})
    vorher = len(s["layers"])
    s["layers"] = [l for l in s["layers"] if l["id"] != req.id]
    if len(s["layers"]) == vorher:
        raise HTTPException(status_code=404, detail=f"Ebene '{req.id}' gibt es nicht")
    return {"ok": True, "layers": len(s["layers"])}


class CutRequest(BaseModel):
    session_id: str
    at_frame:   int


@app.post("/tool/cut")
def tool_cut(req: CutRequest):
    """Harter Schnitt auf der Facecam — Sprung auf die naechste Brennweite, nie
    geeased. Rastet auf die naechste Betonung, wenn eine in Reichweite liegt."""
    s = _sess(req.session_id)
    _budget_guard(s)
    cam = next((l for l in s["layers"] if l["source"].get("kind") == "facecam"), None)
    if not cam or not cam["modifiers"].get("punch"):
        raise HTTPException(status_code=400, detail="keine Facecam-Ebene in der Sitzung")
    f = _snap_frame(max(0, min(int(req.at_frame), s["frames"] - 1)), s["onsets"], FPS)
    frames = sorted(set(cam["modifiers"]["punch"]["frames"] + [f]))
    cam["modifiers"]["punch"]["frames"] = frames
    return {"ok": True, "at_frame": f, "gesnappt": f != int(req.at_frame), "schnitte": frames}


class SetDurationRequest(BaseModel):
    session_id: str
    frames:     int


@app.post("/tool/set-duration")
def tool_set_duration(req: SetDurationRequest):
    """Der Agent bestimmt die Laenge. Laenger als das Material geht nicht — die
    Tonspur ist die Grenze, nicht der Wunsch."""
    s = _sess(req.session_id)
    _budget_guard(s)
    hart = int(round(s["duration"] * FPS))
    f = max(FPS, min(int(req.frames), hart))
    s["frames"] = f
    for l in s["layers"]:
        l["to"] = min(l["to"], f)
        l["from"] = min(l["from"], max(0, f - 1))
    return {"ok": True, "frames": f, "sekunden": round(f / FPS, 2),
            "gekappt_bei": hart if req.frames > hart else None}


class AddSfxRequest(BaseModel):
    session_id: str
    asset:      str          # asset_id aus der Bibliothek
    at_frame:   int
    gain:       float = 1.0


@app.post("/tool/add-sfx")
def tool_add_sfx(req: AddSfxRequest):
    s = _sess(req.session_id)
    _budget_guard(s)
    if req.asset not in SFX_LIBRARY:
        raise HTTPException(status_code=400,
                            detail=f"unbekannter Sound. Verfuegbar: {sorted(SFX_LIBRARY)[:30]}")
    ev = {"asset": req.asset, "time": round(max(0, req.at_frame) / FPS, 3),
          "gain": float(req.gain)}
    s["sfx"].append(ev)
    return {"ok": True, "sfx": s["sfx"]}


@app.get("/tool/sfx-library")
def tool_sfx_library():
    return {"ok": True, "assets": sorted(SFX_LIBRARY)}


class SetMusicRequest(BaseModel):
    session_id: str
    url:        str = ""
    ducking:    bool = True


@app.post("/tool/set-music")
def tool_set_music(req: SetMusicRequest):
    s = _sess(req.session_id)
    _budget_guard(s)
    s["music"] = {"url": req.url, "ducking": req.ducking} if req.url else None
    return {"ok": True, "music": s["music"]}


# ── Prüfen ────────────────────────────────────────────────────────────────────
@app.post("/tool/validate")
def tool_validate(req: SessionRef):
    """Die harten Regeln. Nicht Geschmack — messbare Fehler.

    place-layer und move-layer lehnen dieselben Regeln schon beim Setzen ab; was
    hier noch auftaucht, sind Verletzungen, die erst durch eine Aenderung
    ANDERSWO entstanden sind (etwa set-duration, das Ebenen kuerzt)."""
    s = _sess(req.session_id)
    face = s.get("face") or {}
    fehler = _hard_check(s["layers"], face)
    hinweise = []
    if not _motion_at_zero(s["layers"]):
        fehler.append({"ebene": "-", "regel": "erster_frame",
                       "text": f"keine animate-Kurve in den ersten {MOTION_WINDOW_F} Frames. "
                               f"Handheld zaehlt nicht."})
    deko = [l for l in s["layers"] if not _ist_pflicht(l)]
    if not deko:
        hinweise.append("keine einzige Ebene ausser der Facecam")
    return {"ok": not fehler, "fehler": fehler, "hinweise": hinweise,
            "ebenen": len(s["layers"])}


# ── Rendern ───────────────────────────────────────────────────────────────────
@app.post("/tool/session/render")
def tool_session_render(req: SessionRef):
    """Die Sitzung zu Ende bringen: Ebenen → Remotion, Ton drunter, SFX, Musik.
    Schreibt danach jede Ebene nach render_layers — das ist die Quelle, aus der
    read_history spaeter liest."""
    s = _sess(req.session_id)
    # KEIN Budget-Guard: rendern ist der Ausweg aus dem Abbruch, nicht noch
    # eine Aenderung. Der Grund wandert in die Telemetrie.
    stand = _fertig(s)
    log.info("[SESSION] %s rendert nach %d/%d Turns — %s%s", s["id"], s["turns_used"],
             s["turn_budget"], s.get("abbruch_grund") or "manuell",
             "" if stand["fertig"] else f", offen: {stand['offen']}")
    job = s["dir"]
    props = {"layers": s["layers"], "legacy": None, "face_url": s["face_url"],
             "durationInSeconds": round(s["frames"] / FPS, 3),
             # OHNE das rendert jeder Kunde in Justus' Amethyst — die Composition
             # hat den Justus-Brand als Default.
             "brand": s.get("brand") or BRAND_PRESETS["justus"]}
    vkbit = max(800, min(int(42 * 8 * 1000 / max(s["frames"] / FPS, 1.0)), 12000))
    body = {"composition": "LayerStage", "inputProps": props, "videoBitrate": f"{vkbit}k",
            "supabase": {"url": SUPABASE_URL, "key": SUPABASE_SERVICE_KEY,
                         "bucket": SUPABASE_BUCKET}}
    if s["frames"] / FPS > 55:
        body["scale"] = 0.75
    try:
        r = requests.post(f"{REMOTION_URL}/render", json=body, timeout=900)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"remotion: {exc}")
    if not data.get("ok"):
        raise HTTPException(status_code=502, detail=str(data.get("error"))[:300])

    gfx = job / "gfx_session.mp4"
    if not download_file(data["url"], gfx):
        raise HTTPException(status_code=500, detail="Grafik nicht ladbar")
    out = job / "final_session.mp4"
    LUT = "lut/cinematic.cube"
    sharp = "unsharp=3:3:0.35:3:3:0.0,eq=contrast=1.03:saturation=1.05:brightness=0.012"
    vf = f"lut3d={LUT},{sharp}" if os.path.exists(LUT) else sharp
    run(["ffmpeg", "-y", "-i", str(gfx), "-i", str(s["facecam_path"]),
         "-map", "0:v:0", "-map", "1:a:0?", "-vf", vf,
         "-c:v", "libx264", "-crf", "19", "-preset", "medium", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out)],
        "session_mux")
    if s["sfx"]:
        mixed = mix_sfx_into_video(out, s["sfx"], job, s["frames"] / FPS)
        if mixed:
            out = mixed
    if s.get("music") and s["music"].get("url"):
        out = _add_music_ducked(out, s["music"]["url"], job)
    out = _fit_size(out, job)
    url = upload_supabase(out, f"session_{s['id']}", folder="renders")

    # Telemetrie: was TATSAECHLICH gerendert wurde, nicht was geplant war.
    rid = f"{s['id']}"
    rows = [{"client_id": s["client_id"], "render_id": rid, "layer_id": l["id"],
             "source_kind": l["source"].get("kind"), "z": l["z"],
             "from_frame": l["from"], "to_frame": l["to"],
             "transform": l["transform"], "herkunft": l["herkunft"],
             "konzept": l["konzept"]} for l in s["layers"]]
    try:
        requests.post(f"{SUPABASE_URL}/rest/v1/render_layers", timeout=25, json=rows,
                      headers={"apikey": SUPABASE_SERVICE_KEY,
                               "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                               "Content-Type": "application/json"})
    except Exception as exc:
        log.warning("[SESSION] render_layers nicht geschrieben: %s", exc)
    _log_run(s["client_id"], "session-render", "ok" if stand["fertig"] else "warn",
             {"render_id": rid, "turns": s["turns_used"], "budget": s["turn_budget"],
              "grund": s.get("abbruch_grund") or "manuell", "offen": stand["offen"],
              "ebenen": len(s["layers"])})
    log.info("[SESSION] %s gerendert: %d Ebenen → %s", s["id"], len(s["layers"]), url)
    return {"ok": True, "url": url, "render_id": rid, "layers": len(s["layers"]),
            "turns_used": s["turns_used"], "grund": s.get("abbruch_grund") or "manuell",
            "fertig": stand["fertig"], "offen": stand["offen"]}


class TurnStatsRequest(BaseModel):
    client_id: str = ""
    n:         int = 30


@app.post("/tool/stats/turns")
def tool_stats_turns(req: TurnStatsRequest):
    """Wie viele Turns ein Video gekostet hat, ueber die letzten Laeufe.

    Zwei Befunde, die man nur sieht, wenn man sie misst:
    laeuft der Agent regelmaessig ins Budget, konvergiert er nicht — dann fehlt
    ihm Information, nicht Zeit. Meldet er nach sechs Turns fertig, ist er zu
    genuegsam. Beides ist eine Aussage ueber den Prompt, nicht ueber das Budget."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        return {"ok": False, "grund": "Supabase nicht konfiguriert"}
    params = {"tool": "eq.session-render", "select": "client_id,status,detail,created_at",
              "order": "created_at.desc", "limit": str(max(1, min(req.n, 200)))}
    if req.client_id:
        params["client_id"] = f"eq.{req.client_id}"
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/run_log", params=params, timeout=25,
                         headers={"apikey": SUPABASE_SERVICE_KEY,
                                  "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
        r.raise_for_status()
        rows = r.json() or []
    except Exception as exc:
        return {"ok": False, "grund": str(exc)[:200]}
    laeufe = [{"wann": x["created_at"], "client": x["client_id"],
               "turns": (x.get("detail") or {}).get("turns"),
               "budget": (x.get("detail") or {}).get("budget"),
               "grund": (x.get("detail") or {}).get("grund"),
               "ebenen": (x.get("detail") or {}).get("ebenen"),
               "offen": (x.get("detail") or {}).get("offen") or []}
              for x in rows if (x.get("detail") or {}).get("turns") is not None]
    if not laeufe:
        return {"ok": True, "laeufe": [], "befund": "noch keine Laeufe aufgezeichnet"}
    turns = [l["turns"] for l in laeufe]
    ins_budget = sum(1 for l in laeufe if l["grund"] == "turn_budget")
    genuegsam = sum(1 for l in laeufe if l["grund"] == "fertig" and l["turns"] <= 6)
    n = len(laeufe)
    schnitt = sum(turns) / n
    befund = []
    if ins_budget / n > 0.3:
        befund.append(f"{ins_budget} von {n} Laeufen ins Budget gerannt — der Agent "
                      f"konvergiert nicht. Ihm fehlt Information, nicht Zeit.")
    if genuegsam / n > 0.3:
        befund.append(f"{genuegsam} von {n} Laeufen nach hoechstens 6 Turns fertig — "
                      f"zu genuegsam. Die Fertig-Schwelle ist zu leicht zu erreichen.")
    if not befund:
        befund.append(f"unauffaellig: Schnitt {schnitt:.1f} Turns, "
                      f"{ins_budget} im Budget, {genuegsam} unter sieben Turns")
    return {"ok": True, "laeufe_gesamt": n, "turns_schnitt": round(schnitt, 1),
            "turns_min": min(turns), "turns_max": max(turns),
            "ins_budget": ins_budget, "unter_sieben": genuegsam,
            "befund": befund, "laeufe": laeufe[:20]}


# ── Kostenauswertung ──────────────────────────────────────────────────────────
class KostenRequest(BaseModel):
    tage:      int = 7
    client_id: str = ""
    limit:     int = 5000


class LlmLogRequest(BaseModel):
    """Eingang fuer alles, was ausserhalb dieses Dienstes ein Modell ruft —
    Kalle-Interview, Skript-Bauer, Recherche. Ein HTTP-Node hinter dem LLM-Node,
    und der Posten taucht in derselben Auswertung auf wie die Renderer-Posten."""
    tool:      str
    modell:    str = ""
    ein:       int = 0
    aus:       int = 0
    cached:    int = 0
    dauer_ms:  int = 0
    client_id: str = ""
    status:    str = "ok"


class EinheitLogRequest(BaseModel):
    tool:      str
    einheit:   str
    menge:     float = 1.0
    dauer_ms:  int = 0
    client_id: str = ""
    status:    str = "ok"


@app.post("/tool/log/llm")
def tool_log_llm(req: LlmLogRequest):
    _log_llm(req.tool, req.modell,
             {"prompt_tokens": req.ein, "completion_tokens": req.aus,
              "prompt_tokens_details": {"cached_tokens": req.cached}},
             req.dauer_ms, req.client_id, req.status)
    return {"ok": True}


@app.post("/tool/log/einheit")
def tool_log_einheit(req: EinheitLogRequest):
    _log_einheit(req.tool, req.einheit, req.menge, req.dauer_ms,
                 req.client_id, req.status)
    return {"ok": True}


def _kosten_zeile(d: dict) -> float:
    """USD fuer EINE run_log-Zeile.

    Ehrliche Grenze: OpenRouter meldet nur `cached_tokens`, nicht wie viele der
    frischen Token in den Cache GESCHRIEBEN wurden. Ein Schreibvorgang kostet
    aber das 1,25-fache. Die Zahl hier ist also am Anfang einer Sitzung leicht zu
    niedrig — bei Sonnet um hoechstens 25 % der Praefix-Token, nicht der Summe."""
    art = d.get("art")
    if art == "llm":
        ein_p, aus_p, _w_p, read_p = PREISE_LLM.get(d.get("modell") or "",
                                                    PREIS_LLM_UNBEKANNT)
        ein    = int(d.get("ein") or 0)
        cached = min(int(d.get("cached") or 0), ein)
        frisch = ein - cached
        return (frisch * ein_p + cached * read_p
                + int(d.get("aus") or 0) * aus_p) / 1_000_000.0
    if art == "einheit":
        return float(d.get("menge") or 0.0) * PREISE_EINHEIT.get(d.get("einheit") or "", 0.0)
    return 0.0


@app.post("/tool/stats/kosten")
def tool_stats_kosten(req: KostenRequest):
    """Welcher Posten macht welchen Anteil. Sieben Tage, nach tool gruppiert.

    Zeilen ohne `art` sind Ereignis-Zeilen aus der Zeit vor der Messung — sie
    werden mitgezaehlt, aber mit 0 USD und getrennt ausgewiesen. Sonst sieht
    eine Luecke in der Messung wie ein billiger Posten aus."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        return {"ok": False, "grund": "Supabase nicht konfiguriert"}
    tage = max(1, min(int(req.tage), 90))
    seit = datetime.now(timezone.utc).timestamp() - tage * 86400
    seit_iso = datetime.fromtimestamp(seit, timezone.utc).isoformat()
    params = {"select": "client_id,tool,status,detail,created_at",
              "created_at": f"gte.{seit_iso}",
              "order": "created_at.desc", "limit": str(max(1, min(req.limit, 20000)))}
    if req.client_id:
        params["client_id"] = f"eq.{req.client_id}"
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/run_log", params=params, timeout=40,
                         headers={"apikey": SUPABASE_SERVICE_KEY,
                                  "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
        r.raise_for_status()
        rows = r.json() or []
    except Exception as exc:
        return {"ok": False, "grund": str(exc)[:200]}

    posten: dict = {}
    ohne_messung = 0
    for x in rows:
        d = x.get("detail") or {}
        art = d.get("art")
        if art not in ("llm", "einheit"):
            ohne_messung += 1
            continue
        p = posten.setdefault(x.get("tool") or "?", {
            "tool": x.get("tool") or "?", "art": art, "modelle": set(),
            "aufrufe": 0, "fehler": 0, "ein": 0, "aus": 0, "cached": 0,
            "menge": 0.0, "dauer_ms": 0, "usd": 0.0})
        p["aufrufe"] += 1
        if x.get("status") == "fehler":
            p["fehler"] += 1
        p["dauer_ms"] += int(d.get("dauer_ms") or 0)
        p["usd"] += _kosten_zeile(d)
        if art == "llm":
            p["ein"]    += int(d.get("ein") or 0)
            p["aus"]    += int(d.get("aus") or 0)
            p["cached"] += int(d.get("cached") or 0)
            if d.get("modell"):
                p["modelle"].add(d["modell"])
        else:
            p["menge"] += float(d.get("menge") or 0.0)
            if d.get("einheit"):
                p["modelle"].add(d["einheit"])

    gesamt = sum(p["usd"] for p in posten.values()) or 0.0
    liste = []
    for p in sorted(posten.values(), key=lambda y: y["usd"], reverse=True):
        z = {k: v for k, v in p.items() if k != "modelle"}
        z["modelle"] = sorted(p["modelle"])
        z["usd"] = round(p["usd"], 4)
        z["anteil_pct"] = round(100.0 * p["usd"] / gesamt, 1) if gesamt else 0.0
        z["dauer_s"] = round(p["dauer_ms"] / 1000.0, 1)
        z.pop("dauer_ms", None)
        if p["art"] == "llm":
            z["cache_quote_pct"] = round(100.0 * p["cached"] / p["ein"], 1) if p["ein"] else 0.0
        else:
            z.pop("ein", None); z.pop("aus", None); z.pop("cached", None)
        liste.append(z)

    llm = [p for p in posten.values() if p["art"] == "llm"]
    ein_ges = sum(p["ein"] for p in llm)
    cached_ges = sum(p["cached"] for p in llm)
    befund = []
    if liste:
        top = liste[0]
        befund.append(f"groesster Posten: {top['tool']} mit {top['anteil_pct']}% "
                      f"({top['usd']} USD in {tage} Tagen)")
    if ein_ges:
        q = 100.0 * cached_ges / ein_ges
        befund.append(f"Cache-Quote gesamt {q:.1f}% ueber {ein_ges} Eingabe-Token"
                      + ("" if q >= 80 else " — unter 80%, der Praefix wird zu oft neu bezahlt"))
    if ohne_messung:
        befund.append(f"{ohne_messung} Zeilen ohne Kostenmessung (Ereignis-Zeilen oder "
                      f"noch nicht instrumentierte Aufrufe) — nicht in der Summe")
    return {"ok": True, "tage": tage, "zeilen": len(rows),
            "usd_gesamt": round(gesamt, 4),
            "usd_pro_tag": round(gesamt / tage, 4),
            "ohne_messung": ohne_messung, "befund": befund, "posten": liste}


# ── R4: der HTML-Subagent ─────────────────────────────────────────────────────
# Zwei Gruende, ein Umbau. Erstens Kontext: geschriebenes Markup stand als
# Assistant-Nachricht in der Hauptschleife und wurde jeden weiteren Turn
# mitgeschickt. Zweitens Qualitaet: der Hauptagent hat gebaut und ist
# weitergegangen, ohne das Element je anzusehen. Ueberlappender Text und
# abgeschnittene Woerter kamen beide daher.
#
# Kein Kostenargument. Der Subagent iteriert, also wird HTML teurer. Das ist
# beabsichtigt.

HTML_AGENT_RUNDEN   = 3     # gebaute Fassungen, danach wird abgegeben
HTML_AGENT_TURNS    = 10    # harte Bremse, falls er sich im Ansehen verliert
HTML_AGENT_MIN_PX   = 26    # kleiner ist auf dem Handy unlesbar

# Skelette: welche Knoten in welcher Verschachtelung. KEINE Gestaltung —
# Struktur ist wiederholbar, Aussehen nicht. Ohne das erfindet er bei jedem
# Element die Struktur neu und trifft dabei jedes Mal andere Fehler.
SKELETTE = {
    "stat": "<div class='wrap'><div class='wert'></div><div class='label'></div>"
            "<div class='linie'></div><div class='einordnung'></div></div>",
    "zitat": "<div class='wrap'><div class='balken'></div><div class='text'></div>"
             "<div class='quelle'></div></div>",
    "vergleich": "<div class='wrap'><div class='links'><div class='wert'></div>"
                 "<div class='label'></div></div><div class='trenner'></div>"
                 "<div class='rechts'><div class='wert'></div><div class='label'></div></div>"
                 "<div class='differenz'></div></div>",
    "ablauf": "<div class='wrap'><div class='schritt aktiv'></div><div class='schritt'></div>"
              "<div class='schritt'></div></div>",
    "titel": "<div class='wrap'><div class='zeile e1'></div><div class='zeile e2'></div></div>",
}

HTML_AGENT_SYS = """Du baust EIN einzelnes Grafikelement als HTML mit CSS und GSAP.
Es wird auf transparentem Grund gerendert und ueber ein Video gelegt.

Du bekommst einen Auftrag mit den Bestandteilen und die Masse.
Du entscheidest, wie es aussieht — innerhalb der Gestaltungsregeln.

DEIN ABLAUF
1 Bauen
2 preview_frame auf die Mitte der Standzeit
3 Ansehen: passt alles rein, ueberlappt nichts, ist die Hierarchie sichtbar
4 Korrigieren
5 Nach spaetestens 3 Runden abgeben, auch wenn nicht perfekt

DU GIBST ERST AB, WENN
- ueberlauf ist false
- die Liste "abgeschnitten" ist leer. Sie meint EINZELNE Knoten: ein Kasten
  kann in die Leinwand passen und trotzdem seinen eigenen Inhalt beschneiden.
  Genau so verschwindet das Prozentzeichen hinter der Zahl.
- "fehlender_inhalt" ist leer. Jeder Text aus dem Auftrag muss am ENDE der
  Standzeit SICHTBAR dastehen — nicht nur im DOM. Geprueft wird, was man sieht:
  opacity 0, Groesse 0 und versteckte Knoten zaehlen als nicht da.
  Zaehlt ein Wert hoch, zaehlt er auf den bestellten Wert hoch, und die Einheit
  ("%", "x", "Mio") gehoert dazu — sie wird nicht weggelassen, nur weil sie
  nicht mitzaehlt.
  Ein Zaehler laeuft ueber die GSAP-Zeitleiste (gsap.to auf ein Objekt mit
  onUpdate, das den Text schreibt). Setzt du Ziffern per opacity oder Versatz
  ein, muessen ALLE am Ende sichtbar sein — nicht nur die erste.
- kein Text beruehrt einen anderen
- die drei Bestandteile sind auf den ersten Blick unterscheidbar
- etwas bewegt sich ab dem ersten Frame

WENN ETWAS NICHT PASST
Kuerzen, nicht verkleinern. Schrift unter {min_px}px ist auf dem Handy
unlesbar — dann lieber weniger Text.

MESSEN STATT RATEN
messe_text gibt dir je Textknoten Inhalts- und Kastenmass. Ein Bild zeigt dir,
DASS etwas abgeschnitten ist, nicht um wie viel. Bei Ueberlauf misst du zuerst
und aenderst dann gezielt — nicht alle Groessen auf Verdacht.

DIE TECHNIK
- Der Grund ist transparent. Mal keinen eigenen Vollflaechen-Hintergrund;
  Karten und Balken duerfen Flaeche haben, die Leinwand nicht.
- GSAP ist geladen. Die Uhr wird angehalten und auf Zeitpunkte gesetzt, also
  bau Bewegung ueber gsap-Tweens oder CSS-Animationen, nie ueber setTimeout
  oder requestAnimationFrame — beides steht still.
- Ab Frame 0 muss etwas in Bewegung sein. Ein Element, das erst nach einer
  Sekunde anfaengt, sieht im Schnitt aus wie ein Standbild.
- Keine externen Schriften, Bilder oder Skripte. Was nicht im Markup steht,
  ist beim Rendern nicht da.

DAS SKELETT
Du bekommst eine Knotenstruktur fuer deine Art. Sie legt fest, WAS es gibt und
wie es verschachtelt ist. Farben, Groessen, Abstaende, Bewegung schreibst du
selbst. Halte dich an die Klassennamen, aber nicht an eine Gestaltung — es gibt
keine.

Du arbeitest still. Kein Bericht. Wenn du fertig bist, ruf fertig auf."""

HTML_AGENT_GESTALTUNG = """GESTALTUNGSREGELN
- Eine Karte traegt EINE Aussage. Drei gleichrangige Zeilen sind keine
  Hierarchie, sondern eine Liste.
- Der Hauptwert dominiert deutlich. Wenn Haupt- und Nebenzeile aehnlich gross
  sind, hat der Zuschauer nichts, woran sein Auge zuerst haengenbleibt.
- Weissraum ist ein Mittel, kein Rest. Fuell die Leinwand nicht aus, nur weil
  sie da ist.
- Rahmen, Schatten und Verlaeufe zurueckhaltend. Sie sollen die Karte vom Video
  trennen, nicht selbst auffallen.
- Grossbuchstaben nur fuer kurze Beschriftungen, nie fuer ganze Saetze.
- Die Bewegung dient der Aussage: ein Wert zaehlt hoch, eine Zeile schiebt sich
  ein, ein Balken waechst. Kein Dauerpulsieren, kein Rotieren."""


def _fehlender_inhalt(auftrag: dict, text: str) -> list:
    """Steht im Element wirklich drin, was bestellt wurde?

    Die Geometrie kann das nicht sehen: der erste saubere Lauf hat '247 %'
    bekommen und '247' gebaut. Kein Ueberlauf, kein abgeschnittener Knoten,
    trotzdem falsch. Geprueft wird am ENDE der Standzeit — waehrend ein Wert
    hochzaehlt, steht in der Mitte zurecht etwas anderes."""
    def norm(s: str) -> str:
        return re.sub(r"\s+", "", str(s)).casefold()
    hay = norm(text)
    fehlt = []
    for k, v in (auftrag or {}).items():
        if k in ("art", "akzent_auf", "bewegung") or not isinstance(v, str) or not v.strip():
            continue
        if norm(v) not in hay:
            fehlt.append({"feld": k, "erwartet": v})
    return fehlt


async def _html_pruefstand(markup: str, width: int, height: int, t_s: float,
                           mit_bild: bool = False, t_ende: Optional[float] = None) -> dict:
    """Ein Playwright-Durchgang: Ueberlauf, Textmasse, Ueberlappungen, optional
    ein Standbild auf neutralem Grund.

    Bewusst DIESELBE Seitenhuelle wie im echten Alpha-Render. Misst man in einer
    anderen Huelle, misst man ein anderes Layout — und der Agent bekommt gruenes
    Licht fuer etwas, das im Video abgeschnitten ist."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"ok": False, "grund": "Playwright fehlt"}
    page_html = (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        "html,body{margin:0;padding:0;background:transparent;overflow:hidden;"
        f"width:{width}px;height:{height}px}}*{{box-sizing:border-box}}</style></head>"
        f"<body>{markup}</body></html>"
    )
    job = Path(f"/tmp/htmlagent_{uuid.uuid4().hex[:8]}")
    job.mkdir(parents=True, exist_ok=True)
    src = job / "pruef.html"
    src.write_text(page_html, encoding="utf-8")
    bild_b64, fehler = "", []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
            ctx = await browser.new_context(viewport={"width": width, "height": height},
                                            device_scale_factor=1)
            if GSAP_LOCAL.exists():
                await ctx.add_init_script(path=str(GSAP_LOCAL))
            page = await ctx.new_page()
            page.on("pageerror", lambda e: fehler.append(str(e)[:160]))
            await page.goto(f"file://{src.absolute()}", wait_until="load", timeout=20000)
            await page.evaluate("""() => {
                if (window.gsap) { gsap.globalTimeline.pause(); }
                document.getAnimations().forEach(a => { a.pause(); });
            }""")
            await page.evaluate("""(t) => {
                if (window.gsap) gsap.globalTimeline.seek(t);
                document.getAnimations().forEach(a => { a.currentTime = t * 1000; });
            }""", float(t_s))
            mess = await page.evaluate(MESS_JS)
            text_ende = ""
            if t_ende is not None:
                # Einmal ans Ende springen und nur den Text lesen. Kein zweiter
                # Browserstart: der kostet mehr als dieser Sprung.
                await page.evaluate("""(t) => {
                    if (window.gsap) gsap.globalTimeline.seek(t);
                    document.getAnimations().forEach(a => { a.currentTime = t * 1000; });
                }""", float(t_ende))
                text_ende = await page.evaluate(SICHTBARER_TEXT_JS)
                await page.evaluate("""(t) => {
                    if (window.gsap) gsap.globalTimeline.seek(t);
                    document.getAnimations().forEach(a => { a.currentTime = t * 1000; });
                }""", float(t_s))
            if mit_bild:
                # Neutraler Grund: auf transparentem Screenshot sieht helle
                # Schrift auf hellem Grund gleich aus wie auf dunklem.
                await page.evaluate(
                    "() => { document.documentElement.style.background = '#33333a'; }")
                png = job / "vorschau.png"
                await page.screenshot(path=str(png))
                import base64 as _b64
                bild_b64 = _b64.b64encode(png.read_bytes()).decode()
            await ctx.close()
            await browser.close()
    except Exception as exc:
        shutil.rmtree(job, ignore_errors=True)
        return {"ok": False, "grund": str(exc)[:200]}
    shutil.rmtree(job, ignore_errors=True)

    knoten = mess.get("knoten") or []
    breite, hoehe = int(mess.get("b") or 0), int(mess.get("h") or 0)
    ueberlauf = breite > width + 2 or hoehe > height + 2
    abgeschnitten = [k for k in knoten if k.get("abgeschnitten")]
    return {"ok": True, "ueberlauf": ueberlauf, "breite": breite, "hoehe": hoehe,
            "leinwand": [width, height], "knoten": knoten,
            "abgeschnitten": abgeschnitten,
            "ueberlappungen": mess.get("ueberlappungen") or [],
            "zu_klein": [k for k in knoten if k.get("schrift_px", 99) < HTML_AGENT_MIN_PX],
            "js_fehler": fehler[:3], "bild_b64": bild_b64,
            "text_ende": text_ende}


# innerText reicht nicht: es meldet auch Text mit opacity:0 oder auf null
# skaliert. Der dritte Probelauf hatte '247 %' im DOM und zeigte in JEDEM Frame
# '2 %' — Inhalt da, Ziffern unsichtbar. Geprueft wird, was man SIEHT.
SICHTBARER_TEXT_JS = """() => {
  const teile = [];
  document.querySelectorAll('*').forEach(el => {
    const eigen = Array.from(el.childNodes)
      .filter(n => n.nodeType === 3 && n.textContent.trim());
    if (!eigen.length) return;
    let sichtbar = true;
    try {
      sichtbar = el.checkVisibility({opacityProperty: true, visibilityProperty: true,
                                     contentVisibilityAuto: true});
    } catch (e) { sichtbar = getComputedStyle(el).visibility !== 'hidden'; }
    const r = el.getBoundingClientRect();
    if (!sichtbar || r.width < 1 || r.height < 1) return;
    teile.push(eigen.map(n => n.textContent).join(''));
  });
  return teile.join('\\n');
}"""


MESS_JS = """() => {
  const el_mit_text = [];
  document.querySelectorAll('*').forEach(el => {
    const eigen = Array.from(el.childNodes)
      .filter(n => n.nodeType === 3 && n.textContent.trim()).length;
    if (!eigen) return;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') return;
    const kastenB = el.clientWidth  || Math.round(r.width);
    const kastenH = el.clientHeight || Math.round(r.height);
    el_mit_text.push({el: el, rect: r, d: {
      knoten: el.tagName.toLowerCase() +
              (el.className ? '.' + String(el.className).trim().split(/\\s+/).join('.') : ''),
      text: el.textContent.trim().slice(0, 48),
      schrift_px: Math.round(parseFloat(cs.fontSize) || 0),
      breite_inhalt: Math.round(el.scrollWidth), breite_kasten: Math.round(kastenB),
      hoehe_inhalt: Math.round(el.scrollHeight), hoehe_kasten: Math.round(kastenH),
      x: Math.round(r.x), y: Math.round(r.y),
      b: Math.round(r.width), h: Math.round(r.height),
      abgeschnitten: (el.scrollWidth > kastenB + 1) || (el.scrollHeight > kastenH + 1)
    }});
  });
  const ueberlappungen = [];
  for (let i = 0; i < el_mit_text.length; i++) {
    for (let j = i + 1; j < el_mit_text.length; j++) {
      const a = el_mit_text[i], b = el_mit_text[j];
      if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
      const x = Math.max(0, Math.min(a.rect.right, b.rect.right) - Math.max(a.rect.left, b.rect.left));
      const y = Math.max(0, Math.min(a.rect.bottom, b.rect.bottom) - Math.max(a.rect.top, b.rect.top));
      if (x * y > 16) ueberlappungen.push({
        a: a.d.knoten, b: b.d.knoten, flaeche_px: Math.round(x * y)});
    }
  }
  return {b: document.body.scrollWidth, h: document.body.scrollHeight,
          knoten: el_mit_text.map(e => e.d), ueberlappungen: ueberlappungen};
}"""


def _letzte_html_elemente(client_id: str, n: int = 5) -> list:
    """Was zuletzt gebaut wurde — als Ausschlussliste, nicht als Vorlage."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        return []
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/render_layers", timeout=20,
                         params={"client_id": f"eq.{client_id}", "herkunft": "eq.html",
                                 "select": "konzept,transform,created_at",
                                 "order": "created_at.desc", "limit": str(n)},
                         headers={"apikey": SUPABASE_SERVICE_KEY,
                                  "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"})
        r.raise_for_status()
        return [{"konzept": x.get("konzept") or "?",
                 "breite": round(float((x.get("transform") or {}).get("w") or 0), 2)}
                for x in (r.json() or [])]
    except Exception as exc:
        log.warning("[HTMLAGENT] Historie: %s", exc)
        return []


def _html_agent_prompt(art: str, client_id: str) -> str:
    teile = [HTML_AGENT_SYS.format(min_px=HTML_AGENT_MIN_PX), "", HTML_AGENT_GESTALTUNG]
    skelett = SKELETTE.get(art)
    if skelett:
        teile += ["", f"SKELETT FUER '{art}' — Struktur, keine Gestaltung:", skelett]
    try:
        sg = tool_read_style_guide(StyleGuideRequest(client_id=client_id))
        farben = sg.get("farben") or {}
        if farben:
            teile += ["", "MARKEN-TOKEN DES KUNDEN — benutz diese Farben, erfinde keine:",
                      json.dumps(farben, ensure_ascii=False)]
        if sg.get("edit_style"):
            teile += ["", "SCHNITTSTIL DES KUNDEN:", str(sg["edit_style"])[:600]]
    except Exception as exc:
        log.warning("[HTMLAGENT] Style-Guide: %s", exc)
    bsp = _few_shot_overlays()
    if bsp:
        teile += ["", bsp]
    letzte = _letzte_html_elemente(client_id)
    if letzte:
        teile += ["", "ZULETZT GEBAUT — keine dieser Anordnungen nochmal:",
                  json.dumps(letzte, ensure_ascii=False)]
    return "\n".join(teile)


def _html_agent_tools() -> list:
    def T(name, desc, props, req):
        return {"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": req}}}
    return [
        T("schreibe_html", "Markup rendern und pruefen. Gibt Ueberlauf, Masse, "
          "Ueberlappungen und zu kleine Schrift zurueck — kein Bild.",
          {"markup": {"type": "string"}}, ["markup"]),
        T("preview_frame", "Standbild des Elements auf neutralem Grund, zum Zeitpunkt t "
          "in Sekunden.", {"t": {"type": "number"}}, ["t"]),
        T("messe_text", "Je Textknoten Inhalts- und Kastenmass, Schriftgroesse und Lage. "
          "Zahlen sind hier zuverlaessiger als Sehen.", {}, []),
        T("fertig", "Abgeben. Kurze Begruendung, warum es steht.",
          {"begruendung": {"type": "string"}}, ["begruendung"]),
    ]


def _html_subagent(auftrag: dict, w_px: int, h_px: int, dauer_s: float,
                   client_id: str, model: str, debug: bool = False) -> dict:
    """Eigener Kontext, eigene kurze Schleife. Der Hauptagent sieht davon nur
    das Ergebnis — genau das ist der Punkt."""
    art = str(auftrag.get("art") or "stat")
    w_px, h_px = max(80, int(w_px)), max(60, int(h_px))
    dauer_s = max(0.8, min(float(dauer_s), HTML_TOOL_MAX_S))
    t_mitte = round(dauer_s / 2.0, 2)

    tools = _html_agent_tools()
    sys_p = _html_agent_prompt(art, client_id)
    auftrag_text = (
        "AUFTRAG\n" + json.dumps(auftrag, ensure_ascii=False, indent=1) +
        f"\n\nLEINWAND {w_px}x{h_px} px, Standzeit {dauer_s:.1f} s."
        f"\nDie Mitte der Standzeit liegt bei t={t_mitte}."
        "\nBau die erste Fassung und sieh sie dir an.")
    messages = [{"role": "system", "content": [{"type": "text", "text": sys_p, **CACHE_MARK}]},
                {"role": "user", "content": auftrag_text}]

    zustand = {"markup": "", "pruefung": None, "runden": 0, "vorschau_b64": "",
               "begruendung": ""}
    tok = {"ein": 0, "aus": 0, "cached": 0}
    t0 = time.time()

    def werkzeug(name: str, args: dict) -> dict:
        if name == "schreibe_html":
            mk = str(args.get("markup") or "").strip()
            if not mk:
                return {"ok": False, "fehler": "markup ist leer"}
            if zustand["runden"] >= HTML_AGENT_RUNDEN:
                return {"ok": False, "abgelehnt": True,
                        "fehler": f"{HTML_AGENT_RUNDEN} Runden sind aufgebraucht. "
                                  f"Ruf fertig auf — die letzte Fassung wird genommen."}
            pr = asyncio.run(_html_pruefstand(mk, w_px, h_px, t_mitte, t_ende=dauer_s))
            if not pr.get("ok"):
                return {"ok": False, "fehler": pr.get("grund", "Pruefstand kaputt")}
            pr["fehlender_inhalt"] = _fehlender_inhalt(auftrag, pr.get("text_ende", ""))
            zustand["markup"] = mk
            zustand["pruefung"] = pr
            zustand["runden"] += 1
            return {"ok": True, "runde": zustand["runden"],
                    "ueberlauf": pr["ueberlauf"], "inhalt_px": [pr["breite"], pr["hoehe"]],
                    "leinwand_px": pr["leinwand"],
                    # Mit Zahlen, nicht nur mit Namen: "fehlt 34px" ist eine
                    # Anweisung, "abgeschnitten" ist eine Vermutung.
                    "abgeschnitten": [
                        {"knoten": k["knoten"], "text": k["text"],
                         "fehlt_breite_px": max(0, k["breite_inhalt"] - k["breite_kasten"]),
                         "fehlt_hoehe_px": max(0, k["hoehe_inhalt"] - k["hoehe_kasten"])}
                        for k in pr["abgeschnitten"]],
                    "ueberlappungen": pr["ueberlappungen"],
                    "schrift_zu_klein": [f"{k['knoten']} {k['schrift_px']}px"
                                         for k in pr["zu_klein"]],
                    "js_fehler": pr["js_fehler"],
                    "fehlender_inhalt": pr["fehlender_inhalt"],
                    "runden_uebrig": HTML_AGENT_RUNDEN - zustand["runden"]}
        if name == "preview_frame":
            if not zustand["markup"]:
                return {"ok": False, "fehler": "noch nichts gebaut"}
            t = float(args.get("t", t_mitte))
            pr = asyncio.run(_html_pruefstand(zustand["markup"], w_px, h_px, t, mit_bild=True))
            if not pr.get("ok"):
                return {"ok": False, "fehler": pr.get("grund", "")}
            zustand["vorschau_b64"] = pr["bild_b64"]
            return {"ok": True, "t": t, "bild": "folgt als naechste Nachricht"}
        if name == "messe_text":
            pr = zustand["pruefung"]
            if not pr:
                return {"ok": False, "fehler": "noch nichts gebaut"}
            return {"ok": True, "leinwand_px": pr["leinwand"], "knoten": pr["knoten"][:20]}
        if name == "fertig":
            # Ein Tor, das nur fragt, ob er fertig sein WILL, ist keins. Der
            # erste Lauf gab mit abgeschnittenem Prozentzeichen ab, obwohl der
            # Pruefstand es gemeldet hatte.
            pr = zustand["pruefung"] or {}
            maengel = []
            if pr.get("ueberlauf"):
                maengel.append(f"Inhalt {pr['breite']}x{pr['hoehe']}px passt nicht in "
                               f"{pr['leinwand'][0]}x{pr['leinwand'][1]}px")
            for k in pr.get("abgeschnitten", []):
                maengel.append(
                    f"{k['knoten']} schneidet '{k['text'][:20]}' ab "
                    f"(fehlen {max(0, k['breite_inhalt'] - k['breite_kasten'])}px breit, "
                    f"{max(0, k['hoehe_inhalt'] - k['hoehe_kasten'])}px hoch)")
            if pr.get("ueberlappungen"):
                maengel.append(", ".join(f"{u['a']} beruehrt {u['b']}"
                                         for u in pr["ueberlappungen"][:3]))
            for f in pr.get("fehlender_inhalt", []):
                maengel.append(f"'{f['erwartet']}' aus dem Feld {f['feld']} steht am Ende "
                               f"der Standzeit nicht im Element")
            uebrig = HTML_AGENT_RUNDEN - zustand["runden"]
            if maengel and uebrig > 0:
                return {"ok": False, "abgelehnt": True,
                        "fehler": "Noch nicht abgabereif: " + "; ".join(maengel),
                        "runden_uebrig": uebrig,
                        "hinweis": "Kuerzen oder den Kasten weiten. Nicht die Schrift "
                                   f"unter {HTML_AGENT_MIN_PX}px druecken."}
            zustand["begruendung"] = str(args.get("begruendung") or "")[:300]
            return {"ok": True, "abgegeben": True}
        return {"ok": False, "fehler": f"unbekanntes Werkzeug {name}"}

    for _ in range(HTML_AGENT_TURNS):
        try:
            msg, usage, finish = _openrouter_turn(messages, tools, model)
        except Exception as exc:
            log.error("[HTMLAGENT] Modellaufruf: %s", exc)
            break
        tok["ein"] += int(usage.get("prompt_tokens") or 0)
        tok["aus"] += int(usage.get("completion_tokens") or 0)
        tok["cached"] += int(((usage.get("prompt_tokens_details") or {})
                              .get("cached_tokens")) or 0)
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            messages.append({"role": "user", "content":
                             "Kein Werkzeugaufruf. Bau die naechste Fassung oder ruf fertig."})
            continue
        abgegeben = False
        for c in calls:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            name = fn.get("name", "")
            res = werkzeug(name, args)
            messages.append({"role": "tool", "tool_call_id": c.get("id"),
                             "content": json.dumps(res, ensure_ascii=False)[:2000]})
            # Ein Bild passt nicht in eine tool-Nachricht. Es kommt als eigener
            # User-Turn hinterher, sonst sieht er nur die Zusage, nie das Bild.
            if name == "preview_frame" and res.get("ok") and zustand["vorschau_b64"]:
                messages.append({"role": "user", "content": [
                    {"type": "text", "text": "Das ist dein Element auf neutralem Grund."},
                    {"type": "image_url", "image_url": {
                        "url": "data:image/png;base64," + zustand["vorschau_b64"]}}]})
            if name == "fertig" and res.get("ok"):
                abgegeben = True
        if abgegeben:
            break
        if zustand["runden"] >= HTML_AGENT_RUNDEN and zustand["pruefung"]:
            # Runden aufgebraucht und eine Fassung steht: abgeben, statt ihn
            # weiter gegen die abgelehnte schreibe_html laufen zu lassen.
            break

    dauer_ms = int((time.time() - t0) * 1000)
    _log_llm("html-subagent", model,
             {"prompt_tokens": tok["ein"], "completion_tokens": tok["aus"],
              "prompt_tokens_details": {"cached_tokens": tok["cached"]}},
             dauer_ms, client_id,
             status="ok" if zustand["markup"] else "fehler",
             extra={"runden": zustand["runden"], "art": art})

    if not zustand["markup"]:
        raise HTTPException(status_code=502,
                            detail="HTML-Subagent hat nichts gebaut")

    pr = zustand["pruefung"] or {}
    job = Path(f"/tmp/htmlagent_out_{uuid.uuid4().hex[:8]}")
    job.mkdir(parents=True, exist_ok=True)
    try:
        out, ueber = asyncio.run(
            _render_html_alpha(zustand["markup"], w_px, h_px, dauer_s, job))
        if not out:
            raise HTTPException(status_code=500,
                                detail={"text": "Alpha-Render fehlgeschlagen",
                                        "ueberlauf": ueber})
        url = upload_supabase(out, out.stem, folder="htmltool", content_type="video/webm")
        vorschau = ""
        if zustand["vorschau_b64"]:
            import base64 as _b64
            # Eindeutiger Name. Mit festem "vorschau.png" ueberschreibt jeder
            # Lauf den vorherigen, und zwei Elemente zeigen dasselbe Bild.
            png = job / f"vorschau_{uuid.uuid4().hex[:8]}.png"
            png.write_bytes(_b64.b64decode(zustand["vorschau_b64"]))
            vorschau = upload_supabase(png, png.stem, folder="preview",
                                       content_type="image/png")
    finally:
        shutil.rmtree(job, ignore_errors=True)

    hinweise = []
    if pr.get("abgeschnitten"):
        hinweise.append("abgeschnitten: " +
                        ", ".join(k["knoten"] for k in pr["abgeschnitten"][:3]))
    if pr.get("ueberlappungen"):
        hinweise.append(f"{len(pr['ueberlappungen'])} Textueberlappung(en)")
    if zustand["runden"] >= HTML_AGENT_RUNDEN and (pr.get("ueberlauf")
                                                   or pr.get("ueberlappungen")):
        hinweise.append("Rundenbudget aufgebraucht, Rest steht so")
    return {"ok": True, "url": url, "runden": zustand["runden"],
            "ueberlauf": bool(pr.get("ueberlauf")), "vorschau": vorschau,
            "hinweis": "; ".join(hinweise) or (zustand["begruendung"] or "passt"),
            "art": art, "seconds": dauer_s,
            "layer_source": {"kind": "video", "url": url, "transparent": True},
            **({"markup": zustand["markup"],
                "text_ende": (pr.get("text_ende") or "")[:400]} if debug else {})}


class HtmlAgentRequest(BaseModel):
    auftrag:   dict
    w_px:      int = 620
    h_px:      int = 420
    dauer_s:   float = 3.0
    client_id: str = "justus"
    model:     str = "anthropic/claude-sonnet-4.5"
    debug:     bool = False   # gibt das gebaute Markup mit zurueck


@app.post("/tool/html-agent")
def tool_html_agent(req: HtmlAgentRequest):
    """Ein Element isoliert bauen lassen. Der Weg, auf dem der Subagent geprueft
    wird, bevor der Hauptagent auf ihn umgestellt wird."""
    if not req.auftrag:
        raise HTTPException(status_code=400, detail="auftrag fehlt")
    return _html_subagent(req.auftrag, req.w_px, req.h_px, req.dauer_s,
                          req.client_id, req.model, req.debug)


# ── Teil 4: der Loop ──────────────────────────────────────────────────────────
# Werkzeuge, Grenzen und Abbruch stehen. Was hier dazukommt, ist die Anleitung,
# wie ein Editor denkt — und die Schleife, die sie ausfuehrt.
EDITOR_SYS = """Du bist Cutter. Du schneidest ein 9:16-Video aus einer Aufnahme,
die schon existiert.

WIE DU ARBEITEST — IN DIESER REIHENFOLGE
1 LESEN      Kontaktblatt ansehen, Transkript lesen, Gesicht und Betonungen
             holen, Historie pruefen. Du baust nichts, bevor du das Material
             kennst. Das Kontaktblatt ist ein Bild: acht Standbilder, das rote
             Rechteck ist das getrackte Gesicht, darunter die Wellenform mit den
             Betonungen und der Sprechbalken, dessen Luecken die Pausen sind.
2 PLANEN     Sag in drei Saetzen, was das Video zeigt und wo die Wendepunkte
             liegen. Grob. Keine Frames.
3 BESCHAFFEN Erst jetzt Material holen: Stock suchen und pruefen, ein Bild
             erzeugen, oder eine Animation selbst schreiben.
4 BAUEN      Ebenen setzen, Schnitte, Dauer, Ton.
5 PRUEFEN    preview_frame an den kritischen Stellen. Sieh dir an, was du gebaut
             hast, bevor du es fuer fertig haeltst.

Diese Reihenfolge ist keine Empfehlung. place_layer verweigert, solange du
Kontaktblatt und Transkript nicht gelesen hast.

WAS EIN GUTER SCHNITT IST
- Ein Element steht, weil es die Aussage traegt. Nicht, weil an der Stelle
  gerade nichts war.
- Elemente sitzen dort, wo der Inhalt kippt, nicht im gleichmaessigen Takt.
- Ein Einsatz auf einer Betonung sitzt. Nimm die Zahlen aus read_audio_peaks.
- Verteil die Elemente ueber die GANZE Laufzeit. Drei Karten in den ersten
  fuenfzehn Sekunden und danach eine Minute nichts ist kein fertiges Video.
- Wiederhol nicht, was in den letzten Videos schon dran war. read_history sagt
  dir, was das ist.

BREITE BESTIMMT, WO DU STEHEN KANNST
Ein Element ueber 0.6 Breite passt nur ueber oder unter das Gesicht. Darunter
wird es eng, und alle deine Elemente landen im selben Band.

Ein schmales Element (0.25-0.40) passt NEBEN das Gesicht. Das Gesicht belegt
nur einen Streifen in der Mitte — links und rechts ist Flaeche, ueber die ganze
Hoehe. read_face_track gibt dir left und right; alles links von left oder
rechts von right ist frei, auch auf Augenhoehe.

Nutze beides. Wenn drei Elemente hintereinander dieselbe Breite und dieselbe
Hoehe haben, hast du aufgehoert zu komponieren.

DIE BEISPIELE SIND EINE LATTE, KEINE VORLAGE
Du siehst Beispiel-Overlays. Sie zeigen das NIVEAU, das erwartet wird — nicht
das Layout, das du bauen sollst.

Hat dein Element dieselbe Anordnung wie eines der Beispiele, hast du
abgeschrieben statt gestaltet. Bau es um.

Was du aus ihnen ziehst:
  wie viel leere Flaeche ein Element braucht
  wie stark Haupt- und Nebenzeile sich unterscheiden duerfen
  wie zurueckhaltend Rahmen und Schatten sein muessen
  dass eine Karte EINE Aussage traegt, nicht drei

Was du NICHT uebernimmst:
  Anordnung, Seitenverhaeltnis, Anzahl der Zeilen, Position

NACH JEDEM render_html: EINMAL HINSEHEN
Setz die Ebene, dann preview_frame auf einen Frame, an dem sie steht — BEVOR
du die naechste baust. Ueberlappender Text, abgeschnittene Zeilen und
Elemente, die sich gegenseitig verdecken, sieht man nur so. Du hast das
Werkzeug; ein Cutter, der sein Bild nicht ansieht, ist keiner.

WENN DU HTML SCHREIBST
Die Leinwand ist genau so gross, wie du sie anforderst — was darueber
hinausragt, wird abgeschnitten. Schmale Kaesten brauchen kurze Zeilen und
kleinere Schrift, keine Tabelle mit festen Pixelbreiten. Das Werkzeug sagt dir
im Feld "ueberlauf", ob es gepasst hat. Passt es nicht, schreib den Inhalt
schmaler statt ihn stehen zu lassen.

DEINE MITTEL
Die Facecam ist eine gewoehnliche Ebene. Vollbild ist ein Transform-Wert; in die
Ecke ruecken ist derselbe Layer mit anderem x/y/w/h und mask 'circle'. Du kannst
Text, Bilder, Videos, Stock-Clips und selbstgeschriebene HTML-Animationen als
Ebenen setzen, jede mit freier Position, Groesse, Ebene und Dauer.

Reicht dir kein vorhandener Baustein, schreib die Animation selbst
(render_html, HTML+CSS+GSAP, kommt als transparentes Video zurueck).

DIE HARTEN GRENZEN
Sie werden erzwungen, nicht erbeten. Ein Verstoss wird abgelehnt, die Aenderung
zaehlt nicht:
- nichts in den aeusseren 6 Prozent
- nichts auf dem Gesicht, ausser die Ebene ist vollflaechig (dann ist es ein
  Cutaway und das Gesicht ist bewusst weg)
- jede Ebene mindestens 0.8 Sekunden
- hoechstens drei Ebenen gleichzeitig ausser der Facecam
Die Fehlermeldung nennt dir die erlaubten Werte. Lies sie, statt zu raten.

WANN DU FERTIG BIST
Das entscheidest nicht du. session_tick sagt es dir: keine offenen Verstoesse,
mindestens drei Ebenen ausser der Facecam, eine Bewegung in den ersten 15
Frames, und keine Strecke ohne sichtbares Ereignis ueber der Grenze.
Ruf session_tick nach jedem Arbeitsschritt. Sagt es weiter=false, hoerst du auf.

Du arbeitest still. Kein Bericht, keine Zwischenmeldung — ausser dem
Dreisatz-Plan in Schritt 2."""


OVERLAY_BEISPIELE = Path("design/overlay_beispiele.json")


def _few_shot_overlays() -> str:
    """Beispiel-Overlays als Latte. Sie liegen als Datei daneben, nicht im Code:
    wer Beispiele nachlegt, soll dafuer nicht Python anfassen muessen.

    Fehlt die Datei, bleibt die Beschreibung ohne Beispiele — dann baut der
    Agent schlechter, aber er baut."""
    try:
        bsp = json.loads(OVERLAY_BEISPIELE.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not bsp:
        return ""
    kopf = ("\n\nBEISPIELE — das ist die LATTE, nicht die Vorlage. Uebernimm "
            "das Niveau (Weissraum, Kontrast zwischen Haupt- und Nebenzeile, "
            "zurueckhaltende Rahmen, EINE Aussage pro Karte), NICHT die Anordnung.")
    teile = [kopf]
    for b in bsp[:5]:
        teile.append(
            "\n--- {} ({}x{}px) — {}\n{}".format(
                b.get("name", "?"), b.get("width", "?"), b.get("height", "?"),
                b.get("warum", ""), b.get("markup", "")))
    return "".join(teile)


def _tool_specs() -> list:
    """Was der Agent greifen darf. Bewusst knapp gehalten: jedes Werkzeug mehr
    ist ein Weg mehr, den er falsch nehmen kann."""
    def T(name, desc, props, req):
        return {"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": req}}}
    # Die Form der Ebene GEHOERT ins Schema. Beschreibt man sie nur in Prosa,
    # erfindet das Modell ein plausibles eigenes Format — und verbrennt daran
    # sein halbes Budget.
    ANIM = {"type": "object", "description": "EINE Kurve. Frames, keine Sekunden.",
            "properties": {
                "property": {"type": "string", "enum": list(ANIM_PROPS)},
                "from": {"type": "number"}, "to": {"type": "number"},
                "start": {"type": "integer"}, "end": {"type": "integer"},
                "easing": {"type": "string", "enum": list(ANIM_EASE)}},
            "required": ["property", "from", "to", "start", "end"]}
    L = {"type": "object", "required": ["source", "from", "to"], "properties": {
        "id": {"type": "string"},
        "source": {"type": "object", "description":
                   "kind facecam|video|image|text|card|stat|flow|lower|scene|cta|hook. "
                   "Bei video/image zusaetzlich url (und transparent:true fuer "
                   "render_html-Ergebnisse), bei text content."},
        "from": {"type": "integer", "description": "Startframe"},
        "to": {"type": "integer", "description": "Endframe"},
        "z": {"type": "integer", "description": "Reihenfolge, Facecam liegt auf 10"},
        "transform": {"type": "object", "description":
                      "x,y,w,h als Anteile — x/w von 1080, y/h von 1920. "
                      "Ausserdem scale, rotate, opacity, origin[2].",
                      "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                                     "w": {"type": "number"}, "h": {"type": "number"},
                                     "scale": {"type": "number"}, "rotate": {"type": "number"},
                                     "opacity": {"type": "number"}}},
        "animate": {"type": "array", "items": ANIM},
        "mask": {"type": "string", "enum": ["none", "circle", "rounded", "speaker"]},
        "konzept": {"type": "string", "description": "kurzes Schlagwort fuer die Historie"}}}
    return [
        T("read_transcript", "Wort-Transkript mit Zeiten.", {}, []),
        T("read_contact_sheet", "Kontaktblatt als Bild-URL. Ohne Grenzen das ganze Video.",
          {"von": {"type": "number"}, "bis": {"type": "number"}}, []),
        T("read_face_track", "Gesichtsposition als Anteile der Bildhoehe.", {}, []),
        T("read_audio_peaks", "Betonungen in Sekunden.", {}, []),
        T("read_history", "Was in den letzten Videos schon dran war.",
          {"n": {"type": "integer"}}, []),
        T("read_style_guide", "Stil und Farben des Kunden.", {}, []),
        T("search_stock", "Stock-Clip zu einer Bildbeschreibung suchen und pruefen.",
          {"beschreibung": {"type": "string"}}, ["beschreibung"]),
        T("generate_image", "Photoreales, gebrandetes Bild erzeugen.",
          {"prompt": {"type": "string", "description": "3-6 Woerter Englisch, EIN Hero-Objekt"}},
          ["prompt"]),
        T("render_html", "Eigene Animation als HTML/CSS/GSAP; kommt als transparentes "
          "Video. Die Leinwand ist so gross wie width/height; was darueber hinausragt, "
          "wird abgeschnitten (Feld 'ueberlauf' in der Antwort)." + _few_shot_overlays(),
          {"markup": {"type": "string"}, "width": {"type": "integer"},
           "height": {"type": "integer"}, "seconds": {"type": "number"}}, ["markup"]),
        T("place_layer", "Ebene setzen.", {"layer": L}, ["layer"]),
        T("move_layer", "Ebene verschieben, skalieren, maskieren, umzeiten.",
          {"id": {"type": "string"}, "transform": {"type": "object"},
           "animate": {"type": "array", "items": {"type": "object"}},
           "z": {"type": "integer"}, "from_frame": {"type": "integer"},
           "to_frame": {"type": "integer"}, "mask": {"type": "string"}}, ["id"]),
        T("remove_layer", "Ebene entfernen.", {"id": {"type": "string"}}, ["id"]),
        T("cut", "Harter Schnitt auf der Facecam.", {"at_frame": {"type": "integer"}},
          ["at_frame"]),
        T("set_duration", "Laenge des Videos in Frames.", {"frames": {"type": "integer"}},
          ["frames"]),
        T("add_sfx", "Sound setzen.", {"asset": {"type": "string"},
                                       "at_frame": {"type": "integer"}}, ["asset", "at_frame"]),
        T("preview_frame", "EINEN Frame ansehen.", {"frame": {"type": "integer"}}, ["frame"]),
        T("session_tick", "Turn zaehlen und fragen, ob weitergebaut werden darf.", {}, []),
    ]


def _tool_call(name: str, args: dict, s: dict) -> dict:
    """Werkzeugaufruf auf die vorhandenen Endpoints abbilden. Fehler werden
    ZURUeCKGEGEBEN, nicht geworfen — eine abgelehnte Ebene ist eine Information
    fuer den Agenten, kein Absturz des Loops."""
    sid = s["id"]
    try:
        if name == "read_transcript":
            return tool_read_transcript(SessionRef(session_id=sid))
        if name == "read_contact_sheet":
            return tool_read_contact_sheet(ContactSheetRequest(
                session_id=sid, von=args.get("von", 0.0), bis=args.get("bis", 0.0)))
        if name == "read_face_track":
            return tool_read_face_track(SessionRef(session_id=sid))
        if name == "read_audio_peaks":
            r = tool_read_audio_peaks(SessionRef(session_id=sid))
            return {**r, "peaks": r["peaks"][:80]}
        if name == "read_history":
            return tool_read_history(HistoryRequest(client_id=s["client_id"],
                                                    n=args.get("n", 5)))
        if name == "read_style_guide":
            return tool_read_style_guide(StyleGuideRequest(client_id=s["client_id"]))
        if name == "search_stock":
            r = tool_search_stock(SearchStockRequest(beschreibung=args["beschreibung"]))
            return {k: v for k, v in r.items() if k in ("ok", "layer_source", "grund", "vision")}
        if name == "generate_image":
            return tool_generate_image(GenerateImageRequest(prompt=args["prompt"],
                                                            client_id=s["client_id"]))
        if name == "render_html":
            return asyncio.run(tool_render_html(RenderHtmlRequest(
                markup=args["markup"], width=args.get("width", W),
                height=args.get("height", 420), seconds=args.get("seconds", 3.0))))
        if name == "place_layer":
            return tool_place_layer(PlaceLayerRequest(session_id=sid, layer=args["layer"]))
        if name == "move_layer":
            return tool_move_layer(MoveLayerRequest(session_id=sid, **{
                k: v for k, v in args.items()
                if k in ("id", "transform", "animate", "z", "from_frame", "to_frame", "mask")}))
        if name == "remove_layer":
            return tool_remove_layer(RemoveLayerRequest(session_id=sid, id=args["id"]))
        if name == "cut":
            return tool_cut(CutRequest(session_id=sid, at_frame=args["at_frame"]))
        if name == "set_duration":
            return tool_set_duration(SetDurationRequest(session_id=sid, frames=args["frames"]))
        if name == "add_sfx":
            return tool_add_sfx(AddSfxRequest(session_id=sid, asset=args["asset"],
                                              at_frame=args["at_frame"]))
        if name == "preview_frame":
            return tool_preview_frame(PreviewFrameRequest(session_id=sid,
                                                          frame=args.get("frame", 0)))
        if name == "session_tick":
            return tool_session_tick(SessionRef(session_id=sid))
        return {"ok": False, "fehler": f"unbekanntes Werkzeug {name}"}
    except HTTPException as exc:
        d = exc.detail
        return {"ok": False, "abgelehnt": True,
                "fehler": d if isinstance(d, dict) else str(d)}
    except Exception as exc:
        log.warning("[LOOP] %s fehlgeschlagen: %s", name, exc)
        return {"ok": False, "fehler": str(exc)[:300]}


# 3000 war zu knapp: EIN render_html mit reichhaltigem CSS liegt allein bei
# 1500-3000 Ausgabe-Tokens, und ein Turn enthaelt oft mehrere Aufrufe. Bricht
# der Turn an der Grenze ab, kommt ein halbes HTML zurueck — und das faellt
# nirgends auf, weil ein abgeschnittener Tool-Call einfach fehlt.
TURN_MAX_TOKENS = 8000


def _openrouter_turn(messages: list, tools: list, model: str) -> tuple:
    body = {"model": model, "max_tokens": TURN_MAX_TOKENS, "tools": tools,
            "messages": messages}
    r = requests.post(OPENROUTER_URL, timeout=300, json=body, headers={
        "Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json",
        "HTTP-Referer": "https://schulten-ai.de", "X-Title": "Selfbrand Cutter"})
    r.raise_for_status()
    d = r.json()
    ch = d["choices"][0]
    return ch["message"], (d.get("usage") or {}), ch.get("finish_reason", "")


def _loop_impl(s: dict, model: str) -> dict:
    """Die Schleife. Sie ruft keine Regeln auf und trifft keine Geschmacks-
    entscheidung — beides steckt in den Werkzeugen. Sie sorgt nur dafuer, dass
    nach jedem Turn gezaehlt, gesichert und gefragt wird."""
    tools = _tool_specs()
    _t_loop = time.time()
    # Der statische Block liegt EINMAL im ersten User-Turn und ist ab da Praefix
    # der gesamten Historie — genau das cached Anthropic.
    erst = [{"type": "text", "text": s["prefix"], **CACHE_MARK}]
    if s.get("sheet") and Path(s["sheet"]).exists():
        import base64
        erst.append({"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + base64.b64encode(Path(s["sheet"]).read_bytes()).decode()}})
    erst.append({"type": "text", "text":
                 "Das Kontaktblatt liegt bei. Fang mit Schritt 1 an."})
    messages = [{"role": "system", "content": [{"type": "text", "text": EDITOR_SYS, **CACHE_MARK}]},
                {"role": "user", "content": erst}]

    plan, letzter = "", {}
    while True:
        if s["turns_used"] >= s["turn_budget"]:
            s["abbruch_grund"] = "turn_budget"
            break
        try:
            msg, usage, finish = _openrouter_turn(messages, tools, model)
        except Exception as exc:
            log.error("[LOOP] %s Modellaufruf: %s", s["id"], exc)
            s["abbruch_grund"] = "modellfehler"
            break
        ein = int(usage.get("prompt_tokens") or 0)
        aus = int(usage.get("completion_tokens") or 0)
        cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
        s["tokens"]["ein"] += ein
        s["tokens"]["aus"] += aus
        s["tokens"]["cached"] += cached
        werkzeuge = [((c.get("function") or {}).get("name") or "?")
                     for c in (msg.get("tool_calls") or [])]
        # Ausgabe-Tokens dem Turn zuschreiben, nicht dem einzelnen Aufruf — feiner
        # geht es nicht, die API rechnet pro Turn ab. Bei einem Turn mit genau
        # einem render_html ist die Zahl exakt.
        for wz in set(werkzeuge):
            b = s["tokens"]["pro_werkzeug"].setdefault(wz, {"turns": 0, "aus": 0})
            b["turns"] += 1
            b["aus"] += aus // max(1, len(set(werkzeuge)))
        if finish == "length":
            s["tokens"]["abgeschnitten"] += 1
            log.error("[LOOP] %s Turn an der Token-Grenze abgeschnitten (%d aus, Limit %d) "
                      "— Werkzeuge: %s", s["id"], aus, TURN_MAX_TOKENS, werkzeuge)
        messages.append(msg)
        txt = (msg.get("content") or "")
        if isinstance(txt, str) and txt.strip() and not plan:
            plan = txt.strip()[:600]
        calls = msg.get("tool_calls") or []
        if not calls:
            # Kein Werkzeug, nur Text: einen Turn zaehlen, sonst dreht er leer.
            letzter = _tool_call("session_tick", {}, s)
            messages.append({"role": "user", "content":
                             "Kein Werkzeugaufruf. Stand: " + json.dumps(letzter, ensure_ascii=False)[:900]})
            if not letzter.get("weiter", True):
                break
            continue
        for c in calls:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            res = _tool_call(fn.get("name", ""), args, s)
            if fn.get("name") == "session_tick":
                letzter = res
            messages.append({"role": "tool", "tool_call_id": c.get("id"),
                             "content": json.dumps(res, ensure_ascii=False)[:2500]})
        if letzter and not letzter.get("weiter", True):
            break
        # Turn ist vorbei: zaehlen und sichern, auch wenn der Agent nicht
        # getickt hat. Sonst laeuft das Budget nie ab.
        if not any((c.get("function") or {}).get("name") == "session_tick" for c in calls):
            letzter = _tool_call("session_tick", {}, s)
            if not letzter.get("weiter", True):
                break
    _checkpoint(s, "rendert")
    erg = tool_session_render(SessionRef(session_id=s["id"]))
    _checkpoint(s, "fertig")
    t = s["tokens"]
    log.info("[LOOP] %s Tokens: %d ein (%d aus Cache), %d aus, %d Turns abgeschnitten. "
             "Pro Werkzeug: %s", s["id"], t["ein"], t["cached"], t["aus"],
             t["abgeschnitten"], t["pro_werkzeug"])
    # art/modell/dauer_ms machen die Zeile fuer /tool/stats/kosten lesbar. Die
    # Summe ueber alle Turns reicht: abgerechnet wird ohnehin pro Turn, und der
    # Cache-Anteil ist erst ueber den ganzen Lauf aussagekraeftig.
    _log_run(s["client_id"], "loop-tokens", "warn" if t["abgeschnitten"] else "ok",
             {"art": "llm", "modell": model, "dauer_ms": int((time.time() - _t_loop) * 1000),
              "session": s["id"], "turns": s["turns_used"], **t})
    return {**erg, "plan": plan, "turns": s["turns_used"],
            "grund": s.get("abbruch_grund") or "fertig", "tokens": t}


class LoopRequest(BaseModel):
    facecam:     str = ""
    session_id:  str = ""       # vorhandene Sitzung fortsetzen
    client_id:   str = "justus"
    briefing:    Optional[dict] = None
    trim:        bool = True
    turn_budget: int = TURN_BUDGET
    model:       str = "anthropic/claude-sonnet-4.5"


LOOP_JOBS: dict = {}
_loop_executor = ThreadPoolExecutor(max_workers=1)


def _loop_job(job_id: str, req: LoopRequest):
    AKTIVER_CLIENT.set(req.client_id or "")
    try:
        if req.session_id:
            s = _sess(req.session_id)
        else:
            opened = tool_session_open(OpenSessionRequest(
                facecam=req.facecam, client_id=req.client_id,
                briefing=req.briefing, trim=req.trim, turn_budget=req.turn_budget))
            s = _sess(opened["session_id"])
        LOOP_JOBS[job_id] = {"status": "processing", "session_id": s["id"]}
        LOOP_JOBS[job_id] = {"status": "done", "session_id": s["id"],
                             **_loop_impl(s, req.model)}
    except Exception as exc:
        log.exception("[LOOP] %s", job_id)
        LOOP_JOBS[job_id] = {"status": "error", "error": str(exc)[:400]}


@app.post("/tool/loop/run")
def tool_loop_run(req: LoopRequest):
    if not (req.facecam or req.session_id):
        raise HTTPException(status_code=400, detail="facecam oder session_id noetig")
    job_id = str(uuid.uuid4())
    LOOP_JOBS[job_id] = {"status": "processing"}
    _loop_executor.submit(_loop_job, job_id, req)
    return {"ok": True, "job_id": job_id, "status": "processing"}


@app.get("/tool/loop/status/{job_id}")
def tool_loop_status(job_id: str):
    j = LOOP_JOBS.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="unbekannter Job")
    return j


@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/debug/storage-test")
def debug_storage_test():
    """Verify Supabase Storage upload end-to-end: create the bucket, upload a tiny
    file, return the public URL + whether it is publicly fetchable. Also reports
    the raw bucket-create response for diagnosis."""
    global _BUCKET_READY
    _BUCKET_READY = False
    hdr = {"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
           "Content-Type": "application/json"}
    cr = requests.post(f"{SUPABASE_URL}/storage/v1/bucket", headers=hdr, timeout=30,
                       json={"id": SUPABASE_BUCKET, "name": SUPABASE_BUCKET, "public": True})
    lst = requests.get(f"{SUPABASE_URL}/storage/v1/bucket", headers=hdr, timeout=30)
    out = {"bucket": SUPABASE_BUCKET,
           "key_len": len(SUPABASE_SERVICE_KEY),
           "create_status": cr.status_code, "create_body": cr.text[:300],
           "list_status": lst.status_code, "list_body": lst.text[:400]}
    tmp = Path(f"/tmp/storage_test_{uuid.uuid4().hex[:8]}.txt")
    tmp.write_text(f"ok {time.time()}", encoding="utf-8")
    try:
        _BUCKET_READY = True  # bucket handled above; skip re-create in helper
        url = upload_supabase(tmp, tmp.stem, folder="debug")
        out.update(ok=True, url=url, public_fetch_status=requests.get(url, timeout=15).status_code)
    except Exception as exc:
        out.update(ok=False, error=str(exc))
    finally:
        tmp.unlink(missing_ok=True)
    return out

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

def _trim_pipeline(src: Path, job_dir: Path, smart_cut: bool = False) -> tuple:
    """3-phase auto-cut. Returns (path, n_fillers); path is `src` if nothing was cut.

    Phase 0 drops repeated takes and false starts (LLM over a disfluency-preserving
    transcript). Phase 1 kills acoustic filler words. Phase 2 removes the remaining
    dead air, cutting only in the pauses between words so no word is halved."""
    current = src

    n_coherence = 0
    if smart_cut:
        cwords = transcribe_audio(current, prompt=WHISPER_FILLER_PROMPT)
        if cwords:
            ckeeps, n_coherence = _coherence_keep_segments(cwords, probe_duration(current))
            if n_coherence > 0 and len(ckeeps) >= 1:
                p0 = job_dir / "phase0.mp4"
                if _trim_dead_air(current, ckeeps, p0):
                    current = p0
                    log.info("[TRIM] phase0: smart cut removed %d words (takes/false-starts)", n_coherence)

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

    # ── PHASE 3: real-silence tightening (ffmpeg silencedetect) ───────────────
    # catches the "dumb pauses" that word-gaps miss — the actual audio silence.
    try:
        a3 = job_dir / "audio3.mp3"
        subprocess.run(["ffmpeg", "-y", "-i", str(current), "-vn", "-ar", "16000",
                        "-ac", "1", str(a3)], check=True, capture_output=True)
        sil = _silence_intervals(a3)
        if sil:
            dur3 = probe_duration(current)
            keeps3 = _silence_keep_segments(sil, dur3)
            # guard: don't nuke the clip if detection is pathological
            kept = sum(e - s for s, e in keeps3)
            if len(keeps3) > 1 and kept > dur3 * 0.5:
                p3 = job_dir / "phase3.mp4"
                if _trim_dead_air(current, keeps3, p3):
                    current = p3
                    log.info("[TRIM] phase3: removed %d real-silence gaps", len(sil))
    except Exception as exc:
        log.warning("[TRIM] phase3 skipped: %s", exc)

    return current, n_fillers


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

        current, n_fillers = _trim_pipeline(src, job_dir, getattr(req, "smart_cut", False))

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

@app.post("/image")
def generate_image(req: ImageRequest):
    """Raw prompt -> fal.ai nano-banana-pro. No brand template, no crop — for
    character sheets and other one-off assets (e.g. the Kalle interviewer skins).
    Pass `reference` image URLs to run the /edit model instead, which keeps the
    same face across variants."""
    if not FAL_API_KEY:
        raise HTTPException(status_code=500, detail="FAL_API_KEY not set")
    endpoint = FAL_THUMBNAIL_ENDPOINT + ("/edit" if req.reference else "")
    payload = {"prompt": req.prompt, "aspect_ratio": req.aspect_ratio,
               "num_images": max(1, min(4, req.num_images))}
    if req.reference:
        payload["image_urls"] = req.reference
    try:
        r = requests.post(endpoint, timeout=180,
                          headers={"Authorization": f"Key {FAL_API_KEY}",
                                   "Content-Type": "application/json"},
                          json=payload)
        r.raise_for_status()
        data = r.json()
        urls = [i["url"] for i in (data.get("images") or [])]
        if urls:
            return {"images": urls}

        status_url = (data.get("status_url") or data.get("response_url")
                      or (f"https://queue.fal.run/fal-ai/nano-banana-pro/requests/{data['request_id']}"
                          if data.get("request_id") else None))
        if not status_url:
            raise RuntimeError(f"fal.ai unexpected response: {data}")
        for _ in range(90):
            time.sleep(2)
            poll = requests.get(status_url, headers={"Authorization": f"Key {FAL_API_KEY}"},
                                timeout=30)
            poll.raise_for_status()
            res = poll.json()
            if res.get("status") == "COMPLETED":
                imgs = (res.get("output") or res).get("images", [])
                return {"images": [i["url"] for i in imgs]}
            if res.get("status") in ("FAILED", "ERROR"):
                raise RuntimeError(f"fal.ai failed: {res}")
        raise RuntimeError("fal.ai polling timed out")
    except HTTPException:
        raise
    except Exception as exc:
        log.error("[IMAGE] %s", exc)
        raise HTTPException(status_code=500, detail=f"image generation failed: {exc}")


@app.post("/transcribe")
async def transcribe(req: TranscribeRequest):
    """Plain speech-to-text for the Kalle interview loop. Takes any audio or video
    URL (Telegram voice notes arrive as .oga) and returns the spoken text. No word
    timestamps — the interview only needs the words, not the timeline."""
    job_id  = str(uuid.uuid4())
    job_dir = Path(f"/tmp/stt_{job_id}")
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        src = job_dir / "input"
        if not download_file(req.audio, src):
            raise HTTPException(status_code=500, detail="audio download failed")

        mp3 = job_dir / "audio.mp3"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(src),
            "-vn", "-ar", "16000", "-ac", "1", "-b:a", "64k", str(mp3),
        ], check=True, capture_output=True)

        kwargs = {"model": "whisper-1", "response_format": "verbose_json",
                  "language": req.language}
        if req.prompt:
            kwargs["prompt"] = req.prompt
        with open(mp3, "rb") as af:
            resp = openai_client.audio.transcriptions.create(file=af, **kwargs)

        text = (getattr(resp, "text", "") or "").strip()
        dur  = float(getattr(resp, "duration", 0.0) or 0.0)
        log.info("[STT] %.1fs audio -> %d chars", dur, len(text))
        return {"text": text, "duration": dur, "words": len(text.split())}
    except HTTPException:
        raise
    except Exception as exc:
        log.error("[STT] error: %s", exc)
        raise HTTPException(status_code=500, detail=f"transcribe failed: {exc}")
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
