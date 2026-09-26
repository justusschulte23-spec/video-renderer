# -*- coding: utf-8 -*-
"""Siebter Stil-Baustein: ausschnitt (Collage-Sticker), 25.09.

Quellen in dieser Reihenfolge:
  1 eigenes Material des Kunden, freigestellt
  2 Pexels-FOTO (kein Video), nur konkrete Gegenstaende, strenge Pruefung (Gegenstand
    ganz im Bild, klar abgegrenzt, Hauptmotiv), dann lokal freigestellt (rembg)
  3 Icon aus der freien SVG-Bibliothek (vendor/motion-anything, Apache-2.0) im
    Papier-Stil: geht immer, kein Risiko

Look: weisser Rand, weicher Schatten, 2 bis 4 Grad gedreht, Papierton aus dem Farbtopf.
Platz: neben dem Gesicht, nie darueber (sticker_platz rechnet das aus der Gesichtsbox).

Freisteller-Guete wird gemessen, nicht behauptet: Anteil deckender Pixel und Anteil
halbtransparenter Randpixel. Faellt sie durch, gilt die Quelle als gescheitert und die
naechste (Icon) kommt dran.
"""
import io
import json
import math
import os
import random
import re
from pathlib import Path

import requests
from PIL import Image, ImageFilter, ImageOps, ImageChops, ImageDraw

PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
ICONS = Path(__file__).parent / "vendor" / "motion-anything" / "icons.svg"

# Guete, gemessen an der OBJEKTBOX (nicht am ganzen Foto: ein kleines Objekt auf viel Weiss
# ist ein guter Fall, kein schlechter; Test 25.09., Miniaturhaus mit 2,8 % Bildanteil):
#   Kante   die kleinere Seite der Box in Pixeln, darunter ist nichts Brauchbares uebrig
#   Fuelle  deckende Pixel in der Box, darunter hat rembg nur Striche behalten
#   Franse  halbtransparente Randpixel im Verhaeltnis zum Objekt, darueber franst es aus
KANTE_MIN = 160
FUELLE_MIN = 0.25
FRANSE_MAX = 0.10


# ─────────────────────────────────────────────── 2 Pexels-Foto
def pexels_fotos(anfrage, n=15):
    if not PEXELS_KEY:
        return []
    r = requests.get("https://api.pexels.com/v1/search", timeout=30,
                     headers={"Authorization": PEXELS_KEY},
                     params={"query": anfrage, "per_page": n})
    r.raise_for_status()
    return [{"id": p["id"], "thumb": p["src"]["medium"], "gross": p["src"]["large2x"],
             "fotograf": p.get("photographer"), "seite": p.get("url")}
            for p in (r.json().get("photos") or [])]


def foto_pruefen(gegenstand, kandidaten, modell="google/gemini-2.5-flash"):
    """Strenge Pruefung wie bei den Einblendungen: der Gegenstand ist das Hauptmotiv,
    GANZ im Bild, klar vom Hintergrund abgegrenzt (sonst misslingt das Freistellen),
    keine Person im Vordergrund. Passt keines: None."""
    if not (OPENROUTER_KEY and kandidaten):
        return None
    teil = kandidaten[:12]
    inhalt = [{"type": "text", "text": (
        "Gesucht ist ein Foto, das GENAU diesen Gegenstand zeigt: " + gegenstand + ".\n"
        "Streng heisst: es ist genau dieser Gegenstand (kein aehnlicher), er ist das Hauptmotiv "
        "und vollstaendig im Bild; keine Person im Vordergrund, kein Schriftzug, der nicht zum "
        "Gegenstand gehoert (Text auf einem Dokument oder Bildschirm gehoert dazu). Ob er sich "
        "freistellen laesst, misst der Code danach, darum musst du dich nicht kuemmern.\n"
        "Vorschaubilder nummeriert ab 0. Beschreibe zuerst jedes Bild in wenigen Worten, dann "
        "waehle das beste passende. Passt keines, index -1. Am Ende JSON: "
        "{\"index\":0,\"begruendung\":\"ein Satz\"}")}]
    for c in teil:
        inhalt.append({"type": "image_url", "image_url": {"url": c["thumb"]}})
    # 25.09.: 300 Token reichten nicht, das Modell verbrauchte sie beim Denken, es kam kein
    # JSON, und das wurde als "kein Foto passt" gebucht. Eine Antwort ohne Urteil ist ein
    # AUSFALL und wird so gemeldet, nie als Ablehnung.
    r = requests.post("https://openrouter.ai/api/v1/chat/completions", timeout=120,
                      headers={"Authorization": "Bearer " + OPENROUTER_KEY, "Content-Type": "application/json"},
                      json={"model": modell, "messages": [{"role": "user", "content": inhalt}], "max_tokens": 2000})
    r.raise_for_status()
    txt = (r.json()["choices"][0]["message"].get("content") or "")
    m = re.search(r"\{[^{}]*\"index\"[^{}]*\}", txt)
    if not m:
        return {"ausfall": True, "grund": "Pruefer ohne Urteil (keine JSON-Antwort)"}
    o = json.loads(m.group())
    i = int(o.get("index", -1))
    if 0 <= i < len(teil):
        return dict(teil[i], begruendung=o.get("begruendung", ""))
    return {"abgelehnt": True, "grund": o.get("begruendung") or "strenge Pruefung: kein Foto passt"}


# ─────────────────────────────────────────────── Freistellen
_SESSION = None


def freistellen(bild: Image.Image) -> Image.Image:
    global _SESSION
    from rembg import remove, new_session
    if _SESSION is None:
        _SESSION = new_session("isnet-general-use")
    return remove(bild.convert("RGB"), session=_SESSION).convert("RGBA")


def guete(rgba: Image.Image) -> dict:
    """Gemessene Freisteller-Guete an der Objektbox: Kante, Fuelle, Franse."""
    a = rgba.getchannel("A")
    box = a.point(lambda v: 255 if v > 20 else 0).getbbox()
    if not box:
        return {"kante": 0, "fuelle": 0.0, "franse": 1.0, "ok": False}
    ab = a.crop(box)
    hist = ab.histogram()
    n = float(sum(hist)) or 1.0
    deckend, halb = sum(hist[200:]) / n, sum(hist[20:200]) / n
    franse = halb / (deckend + halb) if (deckend + halb) else 1.0
    kante = min(box[2] - box[0], box[3] - box[1])
    ok = kante >= KANTE_MIN and deckend >= FUELLE_MIN and franse <= FRANSE_MAX
    return {"kante": kante, "fuelle": round(deckend, 3), "franse": round(franse, 3), "ok": ok}


def zuschneiden(rgba: Image.Image) -> Image.Image:
    box = rgba.getchannel("A").point(lambda v: 255 if v > 20 else 0).getbbox()
    return rgba.crop(box) if box else rgba


# ─────────────────────────────────────────────── 3 Icon im Papier-Stil
def icon_svg(icon_id: str, farbe: str) -> str:
    roh = ICONS.read_text(encoding="utf-8")
    m = re.search(r'<symbol id="%s"([^>]*)>(.*?)</symbol>' % re.escape(icon_id), roh, re.S)
    if not m:
        raise KeyError(icon_id)
    vb = re.search(r'viewBox="([^"]+)"', m.group(1))
    inner = m.group(2).replace("currentColor", farbe)
    # 25.09.: feste 600 px schnitten das Icon ab, sobald die Leinwand kleiner war. Das SVG
    # fuellt jetzt die Leinwand, egal wie gross sie ist.
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="%s" width="100%%" height="100%%" '
            'style="display:block;width:100vw;height:100vh" fill="none" stroke="%s" color="%s">%s</svg>'
            % (vb.group(1) if vb else "0 0 24 24", farbe, farbe, inner))


def icon_bild(icon_id: str, farbe: str, groesse=600, render=None, papier_ton: str = "#FFFFFF",
              linie: str = None) -> Image.Image:
    """Das Icon auf einer Papierkarte im Kundenton (Collage-Stil statt Emoji-Aufkleber).
    render(svg, w, h) -> PNG-Bytes (Playwright im Renderer)."""
    png = render(icon_svg(icon_id, farbe), int(groesse * .62), int(groesse * .62))
    icon = Image.open(io.BytesIO(png)).convert("RGBA")
    karte = _papier((groesse, groesse), papier_ton, seed=3).convert("RGBA")
    maske = Image.new("L", (groesse, groesse), 0)
    ImageDraw.Draw(maske).rounded_rectangle([0, 0, groesse - 1, groesse - 1], radius=int(groesse * .08), fill=255)
    karte.putalpha(maske)
    if linie:
        ImageDraw.Draw(karte).rounded_rectangle([3, 3, groesse - 4, groesse - 4], radius=int(groesse * .08),
                                                outline=linie, width=3)
    karte.alpha_composite(icon, ((groesse - icon.width) // 2, (groesse - icon.height) // 2))
    return karte


# ─────────────────────────────────────────────── Sticker
def _papier(groesse, ton: str, seed=7) -> Image.Image:
    """Papierflaeche im Kundenton mit leiser Koernung (keine fremde Farbe)."""
    rnd = random.Random(seed)
    w, h = groesse
    grund = Image.new("RGB", (w, h), ton)
    rausch = Image.effect_noise((max(1, w // 3), max(1, h // 3)), 18).resize((w, h), Image.BILINEAR)
    rausch = ImageOps.autocontrast(rausch).point(lambda v: 128 + (v - 128) // 9)
    return ImageChops.overlay(grund, Image.merge("RGB", (rausch, rausch, rausch)))


def sticker(motiv: Image.Image, papier_ton: str, winkel: float = None, rand_px: int = 22,
            seed: int = 7) -> Image.Image:
    """Motiv (RGBA) -> Sticker: Papierrand, weicher Schatten, leicht gedreht."""
    rnd = random.Random(seed)
    if winkel is None:
        winkel = rnd.choice([-1, 1]) * rnd.uniform(2.0, 4.0)
    m = zuschneiden(motiv)
    pad = rand_px * 3
    leinwand = Image.new("RGBA", (m.width + 2 * pad, m.height + 2 * pad), (0, 0, 0, 0))
    leinwand.alpha_composite(m, (pad, pad))
    a = leinwand.getchannel("A").point(lambda v: 255 if v > 40 else 0)
    rand = a.filter(ImageFilter.MaxFilter(rand_px * 2 + 1)).filter(ImageFilter.GaussianBlur(1.2))
    papier = _papier(leinwand.size, papier_ton, seed).convert("RGBA")
    papier.putalpha(rand)
    schatten_a = rand.filter(ImageFilter.GaussianBlur(rand_px * 0.9)).point(lambda v: int(v * 0.35))
    schatten = Image.new("RGBA", leinwand.size, (0, 0, 0, 0))
    schatten.putalpha(schatten_a)
    ganz = Image.new("RGBA", (leinwand.width + 40, leinwand.height + 40), (0, 0, 0, 0))
    ganz.alpha_composite(schatten, (22, 30))
    ganz.alpha_composite(papier, (20, 20))
    ganz.alpha_composite(leinwand, (20, 20))
    return ganz.rotate(winkel, resample=Image.BICUBIC, expand=True)


def sticker_platz(bild_w, bild_h, gesicht, st_w, st_h):
    """Position neben dem Gesicht, nie darueber. gesicht = (x, y, w, h) in Pixeln.
    Gewaehlt wird die Seite mit mehr Platz; ist keine Seite breit genug, None."""
    x, y, w, h = gesicht
    luft = int(bild_w * 0.02)     # Abstand zur Gesichtsbox (Haare und Schulter duerfen verdeckt sein)
    rand = int(bild_w * 0.025)    # Abstand zum Bildrand: am Rand klebend wirkt er abgeschnitten
    links, rechts = x - luft - rand, bild_w - (x + w) - luft - rand
    seite = "rechts" if rechts >= links else "links"
    platz = max(links, rechts)
    if platz < st_w * 0.6:
        return None
    skala = min(1.0, platz / float(st_w))
    sw, sh = int(st_w * skala), int(st_h * skala)
    px = x + w + luft if seite == "rechts" else x - luft - sw
    py = max(int(bild_h * 0.06), min(int(bild_h * 0.62) - sh, y + h // 2 - sh // 2))
    return {"x": px, "y": py, "w": sw, "h": sh, "seite": seite, "skala": round(skala, 3)}


def foto_freigestellt(gegenstand, suche, versuche=2, laden=None):
    """Quelle 2 ganz: Pexels-Foto suchen, streng pruefen, freistellen, Guete messen. Faellt
    die Guete durch, wird das Foto ausgeschlossen und EINMAL das naechstbeste versucht.
    Rueckgabe: {"rgba", "foto", "guete"} oder {"grund"} (dann kommt das Icon)."""
    kand = pexels_fotos(suche)
    gruende = []
    for _ in range(versuche):
        wahl = foto_pruefen(gegenstand, kand)
        if not wahl or not wahl.get("gross"):
            gruende.append((wahl or {}).get("grund") or "keine Kandidaten")
            break
        roh = Image.open(io.BytesIO((laden or (lambda u: requests.get(u, timeout=60).content))(wahl["gross"]))).convert("RGB")
        roh.thumbnail((1600, 1600))
        frei = freistellen(roh)
        g = guete(frei)
        if g["ok"]:
            return {"rgba": frei, "roh": roh, "foto": wahl, "guete": g, "versuche": gruende}
        gruende.append("Foto %s: Freisteller durchgefallen %s" % (wahl["id"], g))
        kand = [k for k in kand if k["id"] != wahl["id"]]
    return {"grund": "; ".join(gruende)}


def hinter_platz(bild_w, bild_h, gesicht, st_w, st_h, unten_max=0.62):
    """26.09., Justus: groesser als das Gesicht, ueber die Schulter, HINTER ihm. Er steht
    freigestellt davor (eigene Ebene), deshalb darf der Sticker in seine Silhouette ragen.
    gesicht = (x, y, w, h) in Pixeln. Hoehe etwa 1,7 Gesichtshoehen, Breite 40 bis 60 %
    des Bildes. Die innere Kante sitzt knapp im Kopf, die Mitte auf Kinnhoehe: oben steht
    er neben dem Kopf, unten verschwindet er hinter der Schulter. Unterkante ueber den
    Untertiteln. Mindestens die Haelfte der Breite liegt ausserhalb des Gesichts."""
    x, y, w, h = gesicht
    asp = st_w / float(st_h)
    sh = 1.7 * h
    sw = sh * asp
    if sw > 0.60 * bild_w:
        sw = 0.60 * bild_w
    if sw < 0.40 * bild_w:
        sw = 0.40 * bild_w
    sh = sw / asp
    oben_min, unten = int(bild_h * 0.05), int(bild_h * unten_max)
    if sh > unten - oben_min:
        sh = unten - oben_min
        sw = sh * asp
    mitte = x + w / 2.0
    seite = "rechts" if (bild_w - mitte) >= mitte else "links"
    innen = (x + w - 0.10 * w) if seite == "rechts" else (x + 0.10 * w)
    px = innen if seite == "rechts" else innen - sw
    rand = 0.02 * bild_w
    px = max(rand, min(bild_w - rand - sw, px))
    sichtbar = ((px + sw) - (x + w)) / sw if seite == "rechts" else (x - px) / sw
    if sichtbar < 0.5:
        return None
    py = (y + h) - 0.45 * sh
    py = max(oben_min, min(unten - sh, py))
    return {"x": int(px), "y": int(py), "w": int(sw), "h": int(sh), "seite": seite,
            "sichtbar": round(sichtbar, 2), "art": "hinter"}
