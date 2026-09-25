# -*- coding: utf-8 -*-
"""Stil-Bausteine (25.09.): sechs Einblendungen aus Code, kein Modell, keine Bildquelle.

  typo_minimal       Kernsatz, grosse Schrift, viel Ruhe             (Vorbild Dan Koe)
  grosse_zahl        eine Zahl, zaehlt hoch                          (Apple Keynote)
  daten_chart        Balken oder Linie aus Zahlen des Skripts        (Economist / Bloomberg)
  vox_dokument       nachgebautes Dokument, Textmarker, Zoom         (Vox)
  ui_karte           Satz als Chat, Notiz oder Mail                  (native Karte)
  bildschirm_beweis  hochgeladene Aufnahme mit Zoom                  (Screen Studio) -> main.py

Regeln, die hier im Aufbau stecken und nicht erst geprueft werden:
  - Jeder Text und jede Zahl kommt als Argument herein. Der Aufrufer (main._stil_baustein)
    nimmt sie ausschliesslich aus den gesprochenen Woertern des Abschnitts.
  - Farben und Schrift nur aus kit.kit_fuer (clients.brand_colors, sonst Kunden-Preset).
  - Die Vollbild-Bausteine lassen das untere Drittel frei (dort laufen die Untertitel) und
    decken das Kamerabild fuer ihren Abschnitt ganz ab: nichts steht UEBER dem Gesicht.
  - ui_karte ist transparent und wird von main.py in den Raum oberhalb des Gesichts gesetzt.

Zeit: Der Renderer setzt jede Animation Bild fuer Bild (SEEK_JS: CSS-Animationen ueber
currentTime, dazu window.__pumpe(ms)). Deshalb kein requestAnimationFrame, kein Timer.
"""
import html
import json
import re

import kit

BAUSTEINE = ("typo_minimal", "grosse_zahl", "daten_chart", "vox_dokument", "ui_karte", "bildschirm_beweis")
VOLLBILD = ("typo_minimal", "grosse_zahl", "daten_chart", "vox_dokument")


def _e(t):
    return html.escape(str(t or ""), quote=True)


def _k(client_id, farben):
    k = kit.kit_fuer(client_id, False, farben)
    k.setdefault("silber", "#A8A9AD" if (client_id or "").lower() == "justus" else k.get("muted"))
    return k


def _seite(k, breit, hoch, body, css, js="", transparent=False):
    grund = "transparent" if transparent else k["canvas"]
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
    html,body{{margin:0;padding:0;width:{breit}px;height:{hoch}px;overflow:hidden;background:{grund};}}
    *{{box-sizing:border-box;}}
    body{{font-family:{k['font']};color:{k['text']};-webkit-font-smoothing:antialiased;}}
    .buehne{{position:absolute;left:0;top:0;width:{breit}px;height:{hoch}px;}}
    /* Oberes Feld: 8 % bis 62 % der Hoehe. Darunter laufen die Untertitel. */
    .feld{{position:absolute;left:{int(breit*.09)}px;right:{int(breit*.09)}px;top:{int(hoch*.08)}px;
          height:{int(hoch*.54)}px;display:flex;flex-direction:column;justify-content:center;}}
    @keyframes rein{{from{{opacity:0;transform:translateY(28px)}}to{{opacity:1;transform:none}}}}
    @keyframes blende{{from{{opacity:0}}to{{opacity:1}}}}
    {css}
    </style></head><body><div class="buehne">{body}</div>
    <script>{js}</script></body></html>"""


def _px_fuer(text, breit, max_px, min_px=40, faktor=0.54, zeilen=None):
    """Schriftgroesse, bei der der Text in die Breite passt (grobe Zeichenbreite).
    Am Handy zaehlt Groesse mehr als Zeilenzahl: lange Saetze gehen auf vier Zeilen."""
    worte = str(text).split()
    laengste = max([len(w) for w in worte] + [1])
    z = zeilen or (4 if len(text) > 40 else 3)
    zeichen = max(len(text) / float(z), laengste)
    return int(max(min_px, min(max_px, breit * 0.82 / (zeichen * faktor))))


# ─────────────────────────────────────────────── typo_minimal
def typo_minimal(satz, k, breit, hoch, sekunden=3.0):
    worte = str(satz).split()
    px = _px_fuer(satz, breit, int(breit * 0.14))
    spans = "".join(
        f"<span class='w' style='animation-delay:{0.12 + j * 0.07:.2f}s'>{_e(w)}</span> "
        for j, w in enumerate(worte))
    css = f"""
    .satz{{font-size:{px}px;line-height:1.12;font-weight:{400 if k.get('serif') else 600};
           letter-spacing:{'-0.01em' if k.get('serif') else '-0.03em'};}}
    .w{{display:inline-block;opacity:0;animation:rein .6s cubic-bezier(.2,.8,.2,1) both;}}
    .strich{{width:{int(breit*.12)}px;height:6px;background:{k['akzent']};margin-top:{int(px*.6)}px;
             transform-origin:left;animation:zieh .7s .5s cubic-bezier(.2,.8,.2,1) both;}}
    @keyframes zieh{{from{{transform:scaleX(0)}}to{{transform:scaleX(1)}}}}"""
    body = f"<div class='feld'><div class='satz'>{spans}</div><div class='strich'></div></div>"
    return _seite(k, breit, hoch, body, css)


# ─────────────────────────────────────────────── grosse_zahl
def grosse_zahl(zahl, einheit, stuetze, k, breit, hoch, sekunden=3.0):
    """Die Zahl zaehlt in 1,1 s hoch. Angezeigt wird am Ende GENAU die gesprochene Zahl."""
    ziel_txt = str(zahl)
    try:
        ziel = float(ziel_txt.replace(".", "").replace(",", "."))
    except ValueError:
        ziel = None
    px = _px_fuer(ziel_txt, breit, int(breit * 0.30), faktor=0.6)
    css = f"""
    .zahl{{font-size:{px}px;line-height:1;font-weight:{500 if k.get('serif') else 700};
           letter-spacing:-0.04em;color:{k['text']};font-variant-numeric:tabular-nums;}}
    .einheit{{font-size:{int(px*.24)}px;color:{k['akzent']};font-weight:600;margin-top:{int(px*.08)}px;
              animation:blende .5s .5s both;}}
    .stuetze{{font-size:{int(breit*.058)}px;color:{k['silber']};margin-top:{int(px*.22)}px;line-height:1.3;
              animation:blende .6s .8s both;max-width:90%;}}"""
    body = (f"<div class='feld'><div class='zahl' id='z'>{_e(ziel_txt)}</div>"
            + (f"<div class='einheit'>{_e(einheit)}</div>" if einheit else "")
            + (f"<div class='stuetze'>{_e(stuetze)}</div>" if stuetze else "") + "</div>")
    js = ""
    if ziel is not None and ziel >= 10:
        # Tausenderpunkte wie gesprochen; ohne Nachkommastellen.
        js = ("const Z=%s, T=%s; function fmt(v){return Math.round(v).toLocaleString('de-DE');}"
              "window.__pumpe=function(ms){const t=Math.min(1,ms/1100);const e=1-Math.pow(1-t,3);"
              "document.getElementById('z').textContent=(t>=1?T:fmt(Z*e));};"
              % (json.dumps(ziel), json.dumps(ziel_txt)))
    return _seite(k, breit, hoch, body, css, js)


# ─────────────────────────────────────────────── daten_chart
def daten_chart(werte, titel, k, breit, hoch, sekunden=3.0, variante="klar"):
    """werte: [{"label": str, "wert": float, "text": str}] aus dem Skript. Balken; bei
    Jahreszahlen als Label eine Linie. variante 'economist': Titel links, duenne Rasterlinien,
    Akzentbalken oben links (in der Kundenfarbe, nicht in Economist-Rot)."""
    werte = [w for w in werte if w.get("wert") is not None][:6]
    mx = max([float(w["wert"]) for w in werte] + [1.0])
    linie = len(werte) >= 3 and all(re.fullmatch(r"(19|20)\d\d", str(w.get("label") or "")) for w in werte)
    ch_w, ch_h = int(breit * .82), int(hoch * .32)
    eco = variante == "economist"
    kopf = (f"<div class='eco'></div>" if eco else "") + (f"<div class='titel'>{_e(titel)}</div>" if titel else "")
    raster = "".join(f"<div class='rl' style='bottom:{int(ch_h*f)}px'></div>" for f in (0.25, 0.5, 0.75, 1.0)) if eco else ""
    if linie:
        pts = []
        for j, w in enumerate(werte):
            x = int(ch_w * (j / max(1, len(werte) - 1)))
            y = int(ch_h - ch_h * float(w["wert"]) / mx * .9)
            pts.append((x, y))
        d = "M" + " L".join(f"{x},{y}" for x, y in pts)
        laenge = sum(((pts[j][0]-pts[j-1][0])**2 + (pts[j][1]-pts[j-1][1])**2) ** .5 for j in range(1, len(pts))) + 1
        punkte = "".join(f"<div class='lab' style='left:{x-60}px;top:{y-58}px'>{_e(w.get('text') or w['wert'])}</div>"
                         f"<div class='ach' style='left:{x-60}px'>{_e(w.get('label'))}</div>" for (x, y), w in zip(pts, werte))
        grafik = (f"<svg width='{ch_w}' height='{ch_h}' style='overflow:visible'><path d='{d}' fill='none' "
                  f"stroke='{k['akzent']}' stroke-width='8' stroke-linecap='round' stroke-linejoin='round' "
                  f"style='stroke-dasharray:{laenge:.0f};stroke-dashoffset:{laenge:.0f};animation:strich 1.2s .2s ease-out forwards'/></svg>"
                  + punkte)
    else:
        bw = int(ch_w / max(1, len(werte)) * .62)
        luecke = (ch_w - bw * len(werte)) / max(1, len(werte))
        grafik = "".join(
            f"<div class='bal' style='left:{int(luecke/2 + j*(bw+luecke))}px;width:{bw}px;"
            f"height:{int(ch_h*float(w['wert'])/mx*.92)}px;animation-delay:{0.15+j*0.12:.2f}s;"
            f"background:{k['akzent'] if float(w['wert'])==mx else k['silber']}'>"
            f"<span class='bw'>{_e(w.get('text') or w['wert'])}</span></div>"
            f"<div class='ach' style='left:{int(j*(bw+luecke))}px;width:{int(bw+luecke)}px'>{_e(w.get('label'))}</div>"
            for j, w in enumerate(werte))
    css = f"""
    .titel{{font-size:{int(breit*.062)}px;font-weight:{600 if not k.get('serif') else 700};line-height:1.2;
            margin-bottom:{int(hoch*.04)}px;animation:blende .5s both;}}
    .eco{{width:{int(breit*.08)}px;height:10px;background:{k['akzent']};margin-bottom:{int(hoch*.018)}px;}}
    .chart{{position:relative;width:{ch_w}px;height:{ch_h}px;border-bottom:3px solid {k['text']};}}
    .rl{{position:absolute;left:0;right:0;height:1px;background:{k['linie']};}}
    .bal{{position:absolute;bottom:0;transform-origin:bottom;border-radius:{min(10, k.get('radius', 8))}px {min(10, k.get('radius', 8))}px 0 0;
          animation:wachs .9s cubic-bezier(.2,.8,.2,1) both;}}
    @keyframes wachs{{from{{transform:scaleY(0)}}to{{transform:scaleY(1)}}}}
    @keyframes strich{{to{{stroke-dashoffset:0}}}}
    .bw{{position:absolute;bottom:100%;margin-bottom:12px;left:-60px;right:-60px;text-align:center;font-weight:700;
         white-space:nowrap;font-size:{int(breit*.056)}px;color:{k['text']};font-variant-numeric:tabular-nums;animation:blende .4s .8s both;}}
    .ach{{position:absolute;top:{ch_h+16}px;text-align:center;font-size:{int(breit*.042)}px;line-height:1.2;color:{k['muted']};width:120px;}}
    .lab{{position:absolute;width:120px;text-align:center;font-weight:700;font-size:{int(breit*.042)}px;animation:blende .4s 1s both;}}"""
    body = f"<div class='feld'>{kopf}<div class='chart'>{raster}{grafik}</div></div>"
    return _seite(k, breit, hoch, body, css)


# ─────────────────────────────────────────────── vox_dokument
def vox_dokument(text, markierung, k, breit, hoch, sekunden=3.0):
    """Ein Blatt mit dem gesprochenen Text. Der markierte Teil wird mit Textmarker ueberzogen,
    danach zoomt das Blatt auf die Stelle. markierung ist ein woertlicher Teil von text."""
    t = str(text)
    i = t.find(markierung) if markierung else -1
    if i >= 0:
        inhalt = (_e(t[:i]) + f"<mark>{_e(markierung)}</mark>" + _e(t[i + len(markierung):]))
    else:
        inhalt = _e(t)
    papier = k["surface"]
    marker = k["akzent"]
    css = f"""
    .blatt{{position:absolute;left:{int(breit*.07)}px;right:{int(breit*.07)}px;top:{int(hoch*.07)}px;height:{int(hoch*.56)}px;
            background:{papier};border-radius:6px;box-shadow:0 30px 80px rgba(0,0,0,.18);padding:{int(breit*.08)}px;
            transform-origin:50% 55%;animation:zoom {max(1.6, sekunden):.1f}s 1.1s cubic-bezier(.4,0,.2,1) both;
            background-image:repeating-linear-gradient(to bottom, transparent 0, transparent {int(breit*.074)}px, {k['linie']} {int(breit*.074)}px, {k['linie']} {int(breit*.074)+1}px);}}
    .kopfz{{height:10px;width:30%;background:{k['raised']};margin-bottom:{int(breit*.05)}px;border-radius:3px;}}
    .txt{{font-family:{k['font']};font-size:{int(breit*.068)}px;line-height:1.42;color:{k['text']};}}
    mark{{background:linear-gradient({marker}66,{marker}66) no-repeat left center / 0% 88%;color:inherit;padding:0 2px;
          animation:marker .7s .45s cubic-bezier(.2,.8,.2,1) forwards;}}
    @keyframes marker{{to{{background-size:100% 88%}}}}
    @keyframes zoom{{from{{transform:scale(1)}}to{{transform:scale(1.16)}}}}"""
    body = f"<div class='blatt'><div class='kopfz'></div><div class='txt'>{inhalt}</div></div>"
    return _seite(k, breit, hoch, body, css)


# ─────────────────────────────────────────────── ui_karte
def ui_karte(text, art, k, breit, hoch, sekunden=3.0, absender=""):
    """Transparent. art: chat | notiz | mail. Erst Tipp-Punkte (chat), dann die Karte."""
    r = max(14, int(k.get("radius", 14)))
    fs = int(breit * .066)
    if art == "chat":
        karte = (f"<div class='tipp'><i></i><i></i><i></i></div>"
                 f"<div class='blase'>{_e(text)}</div>")
    elif art == "mail":
        # Kein erfundener Absender: steht keiner im Skript, bleibt die Kopfzeile leer.
        karte = ((f"<div class='mail'><div class='mz'><b>{_e(absender)}</b></div>" if absender else "<div class='mail'>")
                 + f"<div class='mt'>{_e(text)}</div></div>")
    else:
        karte = f"<div class='notiz'><div class='nt'>{_e(text)}</div></div>"
    css = f"""
    .wrap{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:{int(breit*.04)}px;}}
    .blase{{background:{k['akzent']};color:#fff;font-size:{fs}px;line-height:1.3;padding:{int(fs*.7)}px {int(fs*.9)}px;
            border-radius:{r*2}px {r*2}px 6px {r*2}px;max-width:100%;box-shadow:0 18px 50px rgba(0,0,0,.22);
            opacity:0;animation:pop .45s .75s cubic-bezier(.3,1.4,.5,1) forwards;}}
    .tipp{{position:absolute;display:flex;gap:10px;background:{k['surface']};padding:18px 24px;border-radius:30px;
           animation:weg .2s .7s forwards;box-shadow:0 10px 30px rgba(0,0,0,.15);}}
    .tipp i{{width:14px;height:14px;border-radius:50%;background:{k['muted']};animation:hopp .6s infinite;}}
    .tipp i:nth-child(2){{animation-delay:.15s}} .tipp i:nth-child(3){{animation-delay:.3s}}
    @keyframes hopp{{0%,100%{{transform:translateY(0)}}50%{{transform:translateY(-8px)}}}}
    @keyframes weg{{to{{opacity:0}}}}
    @keyframes pop{{from{{opacity:0;transform:scale(.85) translateY(20px)}}to{{opacity:1;transform:none}}}}
    .notiz,.mail{{background:{k['surface']};border-radius:{r}px;padding:{int(fs*.8)}px;width:100%;
                  box-shadow:0 18px 50px rgba(0,0,0,.20);animation:pop .5s .2s cubic-bezier(.3,1.3,.5,1) both;}}
    .notiz{{border-top:10px solid {k['akzent']};}}
    .nk,.mz{{font-size:{int(fs*.55)}px;color:{k['muted']};margin-bottom:{int(fs*.4)}px;letter-spacing:.04em;text-transform:uppercase;}}
    .nt,.mt{{font-size:{fs}px;line-height:1.3;color:{k['text']};}}"""
    body = f"<div class='wrap'>{karte}</div>"
    return _seite(k, breit, hoch, body, css, transparent=True)


def baue(baustein, inhalt, client_id, breit, hoch, farben=None, sekunden=3.0, variante=None):
    """Markup fuer einen Baustein oder None, wenn der Inhalt fehlt."""
    k = _k(client_id, farben)
    if baustein == "typo_minimal" and inhalt.get("satz"):
        return typo_minimal(inhalt["satz"], k, breit, hoch, sekunden)
    if baustein == "grosse_zahl" and inhalt.get("zahl"):
        return grosse_zahl(inhalt["zahl"], inhalt.get("einheit"), inhalt.get("stuetze"), k, breit, hoch, sekunden)
    if baustein == "daten_chart" and len(inhalt.get("werte") or []) >= 2:
        return daten_chart(inhalt["werte"], inhalt.get("titel"), k, breit, hoch, sekunden, variante or "klar")
    if baustein == "vox_dokument" and inhalt.get("text"):
        return vox_dokument(inhalt["text"], inhalt.get("markierung"), k, breit, hoch, sekunden)
    if baustein == "ui_karte" and inhalt.get("text"):
        return ui_karte(inhalt["text"], inhalt.get("art") or "notiz", k, breit, hoch, sekunden, inhalt.get("absender"))
    return None


# ─────────────────────────────────────────────── Inhalt aus den gesprochenen Woertern
_ZAHLWORT = {"zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "fuenf": 5, "sechs": 6, "sieben": 7, "acht": 8,
             "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "zwoelf": 12, "zwanzig": 20, "dreißig": 30,
             "fünfzig": 50, "hundert": 100, "tausend": 1000, "hunderttausend": 100000}


def zahlen_aus(worte):
    """[(index, anzeige, wert)] fuer jede gesprochene Zahl. Ziffern wie gesprochen
    (Whisper schreibt '10.700'), Zahlwoerter in Ziffern. Nichts wird gerechnet."""
    aus = []
    for i, w in enumerate(worte):
        t = str(w).strip(" ,.;:!?\"'„“()")
        m = re.fullmatch(r"\d{1,3}(\.\d{3})+|\d+(,\d+)?", t)
        if m:
            try:
                aus.append((i, t, float(t.replace(".", "").replace(",", "."))))
            except ValueError:
                pass
        elif t.lower() in _ZAHLWORT:
            v = _ZAHLWORT[t.lower()]
            aus.append((i, f"{v:,}".replace(",", "."), float(v)))
    return aus


def kernsatz(worte, max_worte=9):
    """Der laengste zusammenhaengende Teil bis max_worte, an Satzzeichen geschnitten.
    Woertlich aus dem Gesprochenen, keine Umformulierung."""
    teile, cur = [], []
    for w in worte:
        cur.append(str(w).strip())
        if re.search(r"[.!?,;:]$", str(w).strip()):
            teile.append(cur)
            cur = []
    if cur:
        teile.append(cur)
    kand = [t for t in teile if 3 <= len(t) <= max_worte] or [t[:max_worte] for t in teile if t]
    if not kand:
        return ""
    bester = max(kand, key=len)
    return re.sub(r"[,;:]$", "", " ".join(bester)).strip()


def nur_gesprochen(anzeige, worte):
    """Pruefung: jedes Wort und jede Zahl der Anzeige steht in den gesprochenen Woertern."""
    def norm(x):
        return re.sub(r"[^\wäöüß.]", "", str(x).lower()).strip(".")
    gesagt = {norm(w) for w in worte}
    fremd = [w for w in re.findall(r"\S+", str(anzeige)) if norm(w) and norm(w) not in gesagt]
    return fremd


# ─────────────────────────────────────────────── Thumbnail
def thumbnail(hook, client_id, breit, hoch, farben=None):
    """Standbild fuer das Cover: der Hook-Text im Kundenstil, MITTIG gesetzt, damit er
    auch im 3:4-Ausschnitt des Instagram-Rasters (Mitte, 1080x1440) ganz steht. Keine
    Animation: das Bild ist der Endzustand."""
    k = _k(client_id, farben)
    px = _px_fuer(hook, breit, int(breit * 0.13), zeilen=4)
    css = f"""
    .mitte{{position:absolute;left:{int(breit*.09)}px;right:{int(breit*.09)}px;top:{int(hoch*.27)}px;
            height:{int(hoch*.46)}px;display:flex;flex-direction:column;justify-content:center;}}
    .satz{{font-size:{px}px;line-height:1.1;font-weight:{500 if k.get('serif') else 700};
           letter-spacing:{'-0.01em' if k.get('serif') else '-0.035em'};}}
    .strich{{width:{int(breit*.14)}px;height:8px;background:{k['akzent']};margin-top:{int(px*.55)}px;}}"""
    body = f"<div class='mitte'><div class='satz'>{_e(hook)}</div><div class='strich'></div></div>"
    return _seite(k, breit, hoch, body, css)
