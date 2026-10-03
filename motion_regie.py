"""Motion-Regie (29.09.2026, Justus): Motion-Design-Overlays im Papier-Stil, vorab aus dem
Gesprochenen geplant, mit Opus 5.5 gebaut.

Zwei Stufen, beide mit gemessenen Kosten:
  PLANEN  Sonnet liest das Wort-Transkript und waehlt die Momente, an denen ein Overlay dem
          Zuschauer hilft: Zahl, Vergleich, Liste, Begriff, Haken/Kreuz, Frage, Zitat.
          Der Text ist WOERTLICH aus dem Transkript (Wort-Indizes), sonst faellt der Moment.
  BAUEN   Opus schreibt je Moment das Markup (HTML + CSS-Keyframes) in den Papier-Rahmen
          (gerissene Kante, Korn, Klebeband, Kundenfarben). Kein JS, keine Bilder, keine
          fremden Woerter. Faellt die Pruefung durch, kommt der Papierstreifen aus stil.py.

Der Renderer (_kit_direkt) macht daraus ein transparentes WebM, Frame fuer Frame.
"""
import json
import os
import re
import time
import random

import requests

OR_KEY = os.environ.get("OPENROUTER_API_KEY", "")
PLAN_MODELL = "anthropic/claude-sonnet-4.6"
BAU_MODELL = "anthropic/claude-opus-5.5"
ARTEN = ("zahl", "vergleich", "liste", "begriff", "haken", "pfeil", "frage", "zitat")
MOMENT_MIN_S, MOMENT_MAX_S = 1.6, 5.0

# ─────────────────────────────────────────────── Modellaufruf mit Kosten
def _llm(system, user, modell, max_tokens=2500, temperature=0.3, cache=False):
    """Text und Kosten (USD, aus usage.cost). Wirft bei HTTP-Fehler."""
    if not OR_KEY:
        raise RuntimeError("OPENROUTER_API_KEY fehlt")
    sys_inhalt = ([{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
                  if cache else system)
    body = {"model": modell, "temperature": temperature, "max_tokens": max_tokens,
            "usage": {"include": True},
            "messages": [{"role": "system", "content": sys_inhalt}, {"role": "user", "content": user}]}
    # Opus 5.5: "Reasoning is mandatory for this endpoint and cannot be disabled" (400).
    # Dort bleibt es an, knapp gehalten; bei allen anderen aus.
    if "opus-5" in str(modell):
        body["reasoning"] = {"effort": "low"}
    else:
        body["reasoning"] = {"enabled": False}
    r = requests.post("https://openrouter.ai/api/v1/chat/completions", json=body, timeout=240,
                      headers={"Authorization": "Bearer " + OR_KEY, "Content-Type": "application/json"})
    r.raise_for_status()
    d = r.json()
    txt = (((d.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
    u = d.get("usage") or {}
    return txt, float(u.get("cost") or 0), {"ein": u.get("prompt_tokens"), "aus": u.get("completion_tokens"),
                                             "cache": (u.get("prompt_tokens_details") or {}).get("cached_tokens")}


def _json(txt):
    m = re.search(r"\{[\s\S]*\}", txt or "")
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


_ZAHLWORT = {"1": "eins", "2": "zwei", "3": "drei", "4": "vier", "5": "fünf", "6": "sechs", "7": "sieben",
             "8": "acht", "9": "neun", "10": "zehn", "11": "elf", "12": "zwölf", "20": "zwanzig", "30": "dreißig",
             "50": "fünfzig", "100": "hundert", "1000": "tausend"}


def _norm(w):
    n = re.sub(r"[^\wäöüß%€]", "", str(w).lower())
    # "3" im Overlay, "drei" im Transkript: dieselbe Zahl
    return _ZAHLWORT.get(n, n)


# ─────────────────────────────────────────────── Stufe 1: Planen
PLAN_SYS = """Du bist Motion-Designer fuer Talking-Head-Videos (Hochformat, 9:16) eines Beraters, der
zu Selbststaendigen ueber Sichtbarkeit spricht. Du bekommst das Wort-Transkript mit Zeiten
(je Zeile: INDEX<TAB>SEKUNDE<TAB>WORT). Du waehlst die Momente, an denen ein kurzes,
animiertes Papier-Overlay dem Zuschauer hilft, den Gedanken zu SEHEN.

WAS EIN OVERLAY ZEIGT (art):
- zahl: eine Zahl, die er nennt (Zahl gross, Einheit klein)
- vergleich: zwei Dinge gegeneinander (A gegen B, vorher/nachher, Kollege/du)
- liste: zwei bis vier Schritte oder Punkte, die er nacheinander nennt
- begriff: das eine Wort, um das es geht
- haken: etwas, das richtig oder falsch ist (Haken oder Kreuz)
- pfeil: Ursache -> Wirkung, ein Ablauf in zwei bis drei Stationen
- frage: die Frage, die er stellt
- zitat: ein Satz, der stehen bleiben soll (drei bis sieben Woerter)

REGELN
- text ist WOERTLICH aus dem Transkript: gib von_idx und bis_idx der Woerter an, die das
  Overlay zeigt. Du darfst innerhalb dieses Fensters Woerter weglassen, nie welche erfinden.
  Bei liste, vergleich und pfeil: teile als Liste von Strings, jeder Teil woertlich.
- hoechstens MAX Momente, mindestens ABSTAND Sekunden Abstand zwischen zwei Momenten.
- nicht in den ersten 3 Sekunden (dort steht der Hook-Titel).
- PFLICHT: zwischen Sekunde 3 und 10 MINDESTENS ZWEI Momente, dort reichen 2 Sekunden Abstand.
  Der Zuschauer entscheidet in den ersten zehn Sekunden, ob er bleibt; ein Bild ohne Bewegung
  dort kostet ihn. Nimm dort auch einen Begriff oder ein Zitat, wenn keine Zahl kommt.
- diese Zeitfenster sind schon belegt, dort nichts: BELEGT
- ein Moment dauert 1,6 bis 5 Sekunden: er beginnt beim ersten Wort und endet mit dem
  Gedanken, nicht mitten im Satz.
- Lieber vier starke Momente als acht schwache. Ein Overlay, das nur wiederholt, was man
  hoert, ohne etwas sichtbar zu machen, ist keins.

AUSGABE, nur JSON:
{"momente":[{"von_idx":12,"bis_idx":18,"art":"zahl","text":"10.000 Reichweite","teile":[],"grund":"die Zahl traegt den Beweis"}]}
Nichts Sinnvolles: {"momente":[]}"""


# 03.10., Justus: "die Einblendungen sollen coole Flows sein mit Symbolen, Logos wenn LinkedIn
# oder Claude erwaehnt wird, bunte Themen auf Weiss oder Amethyst, diverser". Logos aus dem
# simple-icons-Sprite (vendor/simple-icons, plus eigene fuer LinkedIn, X, Facebook, Telegram,
# Google, die simple-icons aus Markenrecht entfernt hat). Die Form rotiert je Moment, damit
# nicht jedes Overlay derselbe Streifen ist.
_LOGO_TXT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "simple-icons", "logos.txt")
_MARKEN = {}


def _marken_laden():
    if _MARKEN or not os.path.exists(_LOGO_TXT):
        return _MARKEN
    for z in open(_LOGO_TXT, encoding="utf-8").read().splitlines():
        tl = z.split("\t")
        if len(tl) >= 2 and len(tl[1]) > 2:
            _MARKEN[tl[0]] = (tl[1], "#" + (tl[2].strip() if len(tl) > 2 and tl[2].strip() else "111111"))
    return _MARKEN


def marken_im_text(text, hoechstens=4):
    """Welche Marken im Anzeigetext vorkommen: [{slug, titel, farbe}], laengste zuerst."""
    meta = _marken_laden()
    roh = str(text or "")
    aus = []
    for slug, (titel, farbe) in meta.items():
        flags = 0 if len(titel) == 3 else re.I
        if re.search(r"(?<![\w])" + re.escape(titel) + r"(?![\w])", roh, flags):
            aus.append({"slug": slug, "titel": titel, "farbe": farbe})
    aus.sort(key=lambda m: -len(m["titel"]))
    return aus[:hoechstens]


FORMEN = ("weiss", "amethyst", "kacheln", "kette", "badge", "stapel")
# Welche Formen zu welcher Art passen; die Rotation nimmt die erste, die nicht gerade dran war.
FORM_JE_ART = {"zahl": ("badge", "amethyst", "weiss"), "vergleich": ("kacheln", "weiss", "stapel"),
               "liste": ("kacheln", "stapel", "weiss"), "begriff": ("amethyst", "badge", "weiss"),
               "haken": ("weiss", "kacheln", "amethyst"), "pfeil": ("kette", "kacheln", "weiss"),
               "frage": ("amethyst", "weiss", "stapel"), "zitat": ("weiss", "stapel", "amethyst")}


def form_waehlen(art, vorige):
    for f in FORM_JE_ART.get(art, FORMEN):
        if f not in vorige[-2:]:
            return f
    return FORM_JE_ART.get(art, FORMEN)[0]


def planen(words, dauer, max_n=6, abstand=3.0, belegt=None, modell=None, saetze=None):
    """Momente aus dem Transkript. Rueckgabe {"momente":[{von,bis,dauer,art,text,teile,grund}], "kosten", "roh"}."""
    belegt = belegt or []
    if not words or len(words) < 12:
        return {"momente": [], "kosten": 0.0, "grund": "zu wenig Woerter"}
    zeilen = "\n".join("%d\t%.2f\t%s" % (i, float(w.get("start") or 0), str(w.get("word", "")).strip())
                       for i, w in enumerate(words))
    sysm = (PLAN_SYS.replace("MAX", str(max_n)).replace("ABSTAND", "%.0f" % abstand)
            .replace("BELEGT", ", ".join("%.1f-%.1f s" % (a, b) for a, b in belegt) or "keine"))
    user = zeilen[:30000]
    if saetze:
        user += "\n\nZUR ORIENTIERUNG, das geplante Skript (er hat frei gesprochen, das Transkript zaehlt):\n" + "\n".join(str(x) for x in saetze)[:3000]
    user += "\n\nNur das JSON."
    txt, kosten, usage = _llm(sysm, user, modell or PLAN_MODELL, max_tokens=1800, temperature=0.2)
    o = _json(txt) or {}
    aus, letzte_bis, verworfen = [], -99.0, []
    n = len(words)
    for m in (o.get("momente") or [])[: max_n * 2]:
        try:
            a, b = int(m.get("von_idx")), int(m.get("bis_idx"))
        except Exception:
            verworfen.append({"m": m, "grund": "keine Indizes"})
            continue
        if not (0 <= a <= b < n):
            verworfen.append({"m": m, "grund": "Indizes ausserhalb"})
            continue
        art = str(m.get("art") or "").strip().lower()
        if art not in ARTEN:
            verworfen.append({"m": m, "grund": "art unbekannt"})
            continue
        # drei Woerter Luft um das Fenster: das Modell zaehlt Indizes nicht immer exakt
        fenster = {_norm(words[j].get("word", "")) for j in range(max(0, a - 3), min(n, b + 4))}
        fenster.discard("")
        text = str(m.get("text") or "").strip()
        teile = [str(t).strip() for t in (m.get("teile") or []) if str(t).strip()]
        anzeige = " ".join([text] + teile)
        # "3–5 Monate": Bindestrich und Gedankenstrich sind Trenner, im Transkript steht "3 bis 5"
        def _gesagt(w):
            n_ = _norm(w)
            if not n_ or n_ in fenster:
                return True
            # "organisch" gegen "organischen", "schalten" gegen "schaltest": ein Stamm ab fuenf Zeichen reicht
            return len(n_) >= 5 and any(f.startswith(n_[:5]) for f in fenster)
        fremd = [w for w in re.findall(r"[^\s–\-/→]+", anzeige) if not _gesagt(w)]
        if fremd or not anzeige:
            verworfen.append({"m": m, "grund": "nicht gesprochen: " + ", ".join(fremd[:4])})
            continue
        von = max(0.0, float(words[a].get("start") or 0) - 0.15)
        if von < 3.3:
            verworfen.append({"m": m, "grund": "im Hook-Titel"})
            continue
        ende_wort = float(words[b].get("end") or von)
        dauer_m = max(MOMENT_MIN_S, min(MOMENT_MAX_S, ende_wort + 1.2 - von))
        if von + dauer_m > dauer:
            dauer_m = max(0.0, dauer - von)
            if dauer_m < MOMENT_MIN_S:
                verworfen.append({"m": m, "grund": "am Ende zu kurz"})
                continue
        # 03.10.: in den ersten zehn Sekunden reichen zwei Sekunden Abstand, dort muss etwas passieren.
        if von - letzte_bis < (min(abstand, 2.0) if von < 10.0 else abstand):
            verworfen.append({"m": m, "grund": "Abstand zum vorigen"})
            continue
        if any(not (von + dauer_m <= x or von >= y) for x, y in belegt):
            verworfen.append({"m": m, "grund": "Zeitfenster belegt (%.1f-%.1f)" % (von, von + dauer_m)})
            continue
        # Logos nur, wenn er die Marke wirklich sagt (Text ist woertlich aus dem Transkript).
        marken = marken_im_text(anzeige)
        if art == "pfeil" and marken and len(teile) >= 2:
            # Bei einer Kette mit Marken: kette bleibt, Logos werden Knoten.
            pass
        form = form_waehlen(art, [x["form"] for x in aus])
        aus.append({"von": round(von, 2), "bis": round(von + dauer_m, 2), "dauer": round(dauer_m, 2),
                    "art": art, "text": text, "teile": teile[:4], "grund": str(m.get("grund") or "")[:120],
                    "von_idx": a, "bis_idx": b, "marken": marken, "form": form})
        letzte_bis = von + dauer_m
        if len(aus) >= max_n:
            break
    return {"momente": aus, "kosten": round(kosten, 4), "usage": usage, "roh": len(o.get("momente") or []),
            "verworfen": verworfen}


# ─────────────────────────────────────────────── Stufe 2: Bauen (Papier-Rahmen)
def _papier_css(k, fs, winkel, seed):
    rnd = random.Random(seed)
    zo = " ".join("%d%% %.1f%%" % (x, rnd.uniform(0, 9)) for x in range(0, 101, 3))
    zu = " ".join("%d%% %.1f%%" % (x, 100 - rnd.uniform(0, 9)) for x in range(100, -1, -3))
    papier = k.get("surface") or "#FFFFFF"
    return f"""
    .wrap{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;}}
    .karte{{position:relative;max-width:94%;padding:{int(fs*.7)}px {int(fs*.9)}px {int(fs*.75)}px;
            transform:rotate({winkel:.2f}deg);animation:rein .45s cubic-bezier(.2,1.3,.4,1) both;
            filter:drop-shadow(0 14px 22px rgba(0,0,0,.28));}}
    .papier{{position:absolute;inset:0;background:{papier};clip-path:polygon({zo}, {zu});}}
    .papier::after{{content:'';position:absolute;inset:0;opacity:.35;mix-blend-mode:multiply;
      background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 .45  0 0 0 0 .42  0 0 0 0 .38  0 0 0 .55 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>");}}
    .band{{position:absolute;top:-{int(fs*.3)}px;left:{int(fs*.6)}px;width:{int(fs*2.0)}px;height:{int(fs*.65)}px;
           background:rgba(255,255,255,.55);transform:rotate({-winkel*3:.1f}deg);box-shadow:0 2px 6px rgba(0,0,0,.08);}}
    .inhalt{{position:relative;font-size:{fs}px;line-height:1.14;font-weight:800;letter-spacing:-0.02em;color:{k['text']};
             max-width:100%;word-break:normal;overflow-wrap:normal;hyphens:none;}}
    .inhalt *{{word-break:normal;overflow-wrap:normal;hyphens:none;}}
    .akzent{{color:{k['akzent']};}}
    .marker{{background:linear-gradient(transparent 12%, {k['akzent']}88 12%, {k['akzent']}88 92%, transparent 92%) no-repeat left / 0% 100%;
             padding:0 .08em;animation:marker .55s .5s cubic-bezier(.2,.8,.2,1) forwards;}}
    .leise{{font-weight:600;font-size:{int(fs*.62)}px;color:{k.get('muted') or k['text']};opacity:.85;}}
    .stempel{{display:inline-block;opacity:0;animation:stempel .22s cubic-bezier(.2,1.6,.4,1) forwards;}}
    @keyframes stempel{{from{{opacity:0;transform:scale(1.5)}}to{{opacity:1;transform:none}}}}
    @keyframes marker{{to{{background-size:100% 100%}}}}
    @keyframes rein{{from{{opacity:0;transform:rotate({winkel:.2f}deg) translateY(30px) scale(.92)}}to{{opacity:1;transform:rotate({winkel:.2f}deg)}}}}
    @keyframes zeichnen{{from{{stroke-dashoffset:400}}to{{stroke-dashoffset:0}}}}
    @keyframes auf{{from{{transform:scaleX(0)}}to{{transform:scaleX(1)}}}}
    @keyframes hoch{{from{{opacity:0;transform:translateY(14px)}}to{{opacity:1;transform:none}}}}"""


# Bunte Themen (Justus 03.10.): fuenf lebendige Farben fuer Symbole, Balken und Marker. Auf
# Amethyst hellere Toene, damit sie tragen.
PALETTE = {"weiss": ("#F59E0B", "#10B981", "#3B82F6", "#EF4444", "#EC4899"),
           "amethyst": ("#FCD34D", "#6EE7B7", "#93C5FD", "#FCA5A5", "#F9A8D4")}

# Icon-Auswahl aus dem motion-anything-Sprite (1121 Symbole): die 72, die zu Sichtbarkeit,
# Kunden, Geld, Zeit und Technik passen. Der Gestalter darf nur diese nennen.
ICONS = ("chart-trend chart-bar chart-line chart-pie graph-up graph-down presentation award medal star "
         "heart thumbs-up thumbs-down like bookmark briefcase-dollar suitcase-3 nodes sitemap-4 diagram-tree "
         "bulb lightning laptop mobile monitor phone3 send-square reply refresh-2 download upload export "
         "import login logout chat-round chat-square chat-round-like chat-round-money envelope inbox plane "
         "hashtag code terminal-square server cloud-storage calendar calendar-check clock hourglass stopwatch "
         "history user users user-check user-add user-speak user-search banknote bill card dollar euro "
         "money-bag wallet coins tag-price sale verified search camera mic video play clapperboard gallery "
         "globe map-point route flag-7 pin target gear sliders magic-wand layers file-text checklist2 list-check").split()


def _rahmen_css(k, fs, winkel, seed, form):
    """Rahmen fuer die sechs Formen. Opus baut nur den Inhalt."""
    rnd = random.Random(seed)
    papier = k.get("surface") or "#FFFFFF"
    tinte = k.get("text") or "#111111"
    akzent = k.get("akzent") or "#8B5CF6"
    dunkel = form == "amethyst"
    pal = PALETTE["amethyst" if dunkel else "weiss"]
    grund = akzent if dunkel else papier
    schrift = "#FFFFFF" if dunkel else tinte
    akz = "#FDE68A" if dunkel else akzent
    leise = "rgba(255,255,255,.78)" if dunkel else (k.get("muted") or tinte)
    zo = " ".join("%d%% %.1f%%" % (x, rnd.uniform(0, 9)) for x in range(0, 101, 3))
    zu = " ".join("%d%% %.1f%%" % (x, 100 - rnd.uniform(0, 9)) for x in range(100, -1, -3))
    korn = ("url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220'>"
            "<filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3' stitchTiles='stitch'/>"
            "<feColorMatrix values='0 0 0 0 .45  0 0 0 0 .42  0 0 0 0 .38  0 0 0 .55 0'/></filter>"
            "<rect width='100%' height='100%' filter='url(%23n)'/></svg>\")")
    basis = f"""
    .wrap{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:{schrift};}}
    .inhalt{{position:relative;font-size:{fs}px;line-height:1.14;font-weight:800;letter-spacing:-0.02em;color:{schrift};
             max-width:100%;word-break:normal;overflow-wrap:normal;hyphens:none;}}
    .inhalt *{{word-break:normal;overflow-wrap:normal;hyphens:none;}}
    .akzent{{color:{akz};}}
    .c1{{color:{pal[0]}}} .c2{{color:{pal[1]}}} .c3{{color:{pal[2]}}} .c4{{color:{pal[3]}}} .c5{{color:{pal[4]}}}
    .bg1{{background:{pal[0]}}} .bg2{{background:{pal[1]}}} .bg3{{background:{pal[2]}}} .bg4{{background:{pal[3]}}} .bg5{{background:{pal[4]}}}
    .marker{{background:linear-gradient(transparent 12%, {akz}88 12%, {akz}88 92%, transparent 92%) no-repeat left / 0% 100%;
             padding:0 .08em;animation:marker .55s .5s cubic-bezier(.2,.8,.2,1) forwards;}}
    .leise{{font-weight:600;font-size:{int(fs*.62)}px;color:{leise};opacity:.9;}}
    .stempel{{display:inline-block;opacity:0;animation:stempel .22s cubic-bezier(.2,1.6,.4,1) forwards;}}
    .lg,.ic{{display:inline-block;width:1.25em;height:1.25em;vertical-align:-0.22em;fill:currentColor;flex:none;}}
    .lg{{fill:var(--marke,currentColor);}}
    .lg.gross,.ic.gross{{width:2.1em;height:2.1em;vertical-align:middle;}}
    .pille{{display:inline-flex;align-items:center;gap:.35em;padding:.18em .55em;border-radius:999px;
            background:{"rgba(255,255,255,.16)" if dunkel else "rgba(0,0,0,.06)"};font-weight:800;}}
    .knoten{{display:inline-flex;align-items:center;gap:.4em;padding:.28em .6em;border-radius:.45em;
             background:{"rgba(255,255,255,.14)" if dunkel else papier};
             box-shadow:{"none" if dunkel else "0 6px 16px rgba(0,0,0,.14)"};font-weight:800;white-space:nowrap;
             opacity:0;animation:hoch .4s cubic-bezier(.2,1.3,.4,1) forwards;}}
    .verbinder{{display:inline-block;width:1.6em;height:.18em;border-radius:1em;background:{akz};transform-origin:left;
                transform:scaleX(0);animation:auf .3s ease-out forwards;flex:none;}}
    .kachel{{position:relative;padding:.55em .7em;border-radius:.5em;background:{"rgba(255,255,255,.14)" if dunkel else papier};
             box-shadow:{"none" if dunkel else "0 8px 20px rgba(0,0,0,.14)"};opacity:0;animation:hoch .4s cubic-bezier(.2,1.3,.4,1) forwards;}}
    .reihe{{display:flex;align-items:center;justify-content:center;gap:.45em;flex-wrap:wrap;}}
    .spalte{{display:flex;flex-direction:column;align-items:flex-start;gap:.35em;}}
    @keyframes stempel{{from{{opacity:0;transform:scale(1.5)}}to{{opacity:1;transform:none}}}}
    @keyframes marker{{to{{background-size:100% 100%}}}}
    @keyframes rein{{from{{opacity:0;transform:rotate({winkel:.2f}deg) translateY(30px) scale(.92)}}to{{opacity:1;transform:rotate({winkel:.2f}deg)}}}}
    @keyframes zeichnen{{from{{stroke-dashoffset:400}}to{{stroke-dashoffset:0}}}}
    @keyframes auf{{from{{transform:scaleX(0)}}to{{transform:scaleX(1)}}}}
    @keyframes hoch{{from{{opacity:0;transform:translateY(14px)}}to{{opacity:1;transform:none}}}}
    @keyframes pop{{from{{opacity:0;transform:scale(.6)}}60%{{transform:scale(1.08)}}to{{opacity:1;transform:none}}}}"""
    if form in ("weiss", "amethyst"):
        return basis + (".lg{fill:#fff !important;}" if dunkel else "") + f"""
    .karte{{position:relative;max-width:94%;padding:{int(fs*.7)}px {int(fs*.9)}px {int(fs*.75)}px;
            transform:rotate({winkel:.2f}deg);animation:rein .45s cubic-bezier(.2,1.3,.4,1) both;
            filter:drop-shadow(0 14px 22px rgba(0,0,0,.28));}}
    .papier{{position:absolute;inset:0;background:{grund};clip-path:polygon({zo}, {zu});}}
    .papier::after{{content:'';position:absolute;inset:0;opacity:{".22" if dunkel else ".35"};mix-blend-mode:multiply;background-image:{korn};}}
    .band{{position:absolute;top:-{int(fs*.3)}px;left:{int(fs*.6)}px;width:{int(fs*2.0)}px;height:{int(fs*.65)}px;
           background:rgba(255,255,255,.55);transform:rotate({-winkel*3:.1f}deg);box-shadow:0 2px 6px rgba(0,0,0,.08);}}"""
    if form == "badge":
        return basis + f"""
    .karte{{position:relative;display:inline-flex;align-items:center;gap:.5em;padding:{int(fs*.5)}px {int(fs*1.1)}px;
            border-radius:999px;background:{akzent};color:#fff;transform:rotate({winkel*.6:.2f}deg);
            animation:pop .5s cubic-bezier(.2,1.4,.4,1) both;filter:drop-shadow(0 14px 22px rgba(0,0,0,.28));}}
    .karte .inhalt,.karte .akzent{{color:#fff;}} .karte .akzent{{color:#FDE68A;}}
    .karte .lg{{fill:#fff;}} .karte .ic{{color:#fff;}}
    .papier,.band{{display:none;}}"""
    if form == "stapel":
        return basis + f"""
    .karte{{position:relative;max-width:92%;padding:{int(fs*.7)}px {int(fs*.9)}px {int(fs*.75)}px;
            transform:rotate({winkel:.2f}deg);animation:rein .45s cubic-bezier(.2,1.3,.4,1) both;
            filter:drop-shadow(0 14px 22px rgba(0,0,0,.28));}}
    .papier{{position:absolute;inset:0;background:{papier};}}
    .papier::before,.papier::after{{content:'';position:absolute;inset:0;background:{papier};z-index:-1;
            box-shadow:0 4px 10px rgba(0,0,0,.12);}}
    .papier::before{{transform:rotate({-winkel*2.2:.1f}deg) translate(8px,6px);background:{pal[0]}33;}}
    .papier::after{{transform:rotate({winkel*2.6:.1f}deg) translate(-8px,8px);background:{pal[2]}33;}}
    .band{{display:none;}}"""
    # kacheln und kette: kein Papier, die Elemente selbst sind die Flaechen
    return basis + f"""
    .karte{{position:relative;max-width:96%;padding:{int(fs*.2)}px;transform:rotate({winkel*.4:.2f}deg);
            animation:rein .45s cubic-bezier(.2,1.3,.4,1) both;filter:drop-shadow(0 12px 20px rgba(0,0,0,.26));}}
    .papier,.band{{display:none;}}"""


BAU_SYS = """Du bist Motion-Designer und baust EIN animiertes Overlay fuer ein Talking-Head-Video.
Stil: Paper-Motion wie bei Paperclip oder Vox: Papier, Korn, harte Schatten, aber lebendig, mit
Symbolen, Logos und Farbe. Professionell, nicht verspielt. Der Rahmen (Form, Papier, Schrift,
Farben, Einflug) steht schon. Du lieferst NUR den Inhalt und die Keyframes fuer seine Bewegung.

DU BEKOMMST: art, form, den Text (woertlich, unveraenderlich), ggf. teile, die Marken, die er
nennt (slug + Farbe), die Kartenbreite und Hoehe in Pixeln, die Dauer und die Schriftgroesse.

FORMEN (steht fest, du baust passend dazu):
  weiss: Papierkarte, dunkle Schrift, bunte Symbole.   amethyst: Papierkarte in Amethyst, weisse Schrift, helle Symbole.
  kacheln: zwei bis vier weisse Kacheln (.kachel) nebeneinander oder untereinander, jede mit Symbol/Logo + Wort.
  kette: Flow aus Knoten (.knoten mit Symbol/Logo + Wort) und Verbindern (.verbinder), links nach rechts, bei drei Stationen untereinander.
  badge: eine Pille in Amethyst, die Zahl riesig, das Wort klein daneben.   stapel: Karte auf zwei bunten Blaettern dahinter.

VORHANDENE KLASSEN (nutz sie, statt sie neu zu bauen):
  .inhalt (Textblock)  .akzent  .marker (Textmarker zieht ueber ein Wort)  .leise (Nebentext)  .stempel (Wort stempelt sich, animation-delay je Wort)
  .c1 .c2 .c3 .c4 .c5 (fuenf bunte Farben fuer Symbole und einzelne Woerter)  .bg1 bis .bg5 (dieselben als Flaeche, z.B. Balken)
  .pille (runde Kapsel um Symbol+Wort)  .knoten + .verbinder (Flow)  .kachel  .reihe (nebeneinander)  .spalte (untereinander)
  Keyframes: stempel, marker, hoch, auf (scaleX), zeichnen (stroke-dasharray:400), pop (Element ploppt)

SYMBOLE UND LOGOS:
  Logo einer Marke, die er nennt: <svg class="lg" style="--marke:#FARBE"><use href="#lg-SLUG"/></svg>  (nur die gegebenen Marken, nie andere)
  Icon: <svg class="ic c3"><use href="#ic-NAME"/></svg>  mit NAME aus dieser Liste, keine anderen: ICONS
  Gross als Blickfang: class="lg gross" oder "ic gross". Ein Symbol je Gedanke, nicht je Wort. Ein Flow ohne
  Symbole ist nur Text; ein Overlay mit fuenf Symbolen ist Krach. Zwei bis drei sind richtig.

BEWEGUNG (Motion Design, nicht Standbild): jedes sichtbare Element hat seinen eigenen Auftritt,
zeitlich gestaffelt (animation-delay 0.05 bis 0.9 s): Woerter stempeln oder fahren hoch, Zahlen
setzen sich mit Ueberschwingen (scale 1.4 -> 1), Linien und Pfeile zeichnen sich (stroke-dashoffset),
Balken wachsen (scaleX), der Marker zieht 0.4 s nach dem Wort, ein Haken oder Kreuz zeichnet sich
in 0.35 s. Zwei bis vier gestaffelte Auftritte je Karte, nie alles gleichzeitig, nie laenger als
1,2 s bis alles steht. Ruhig und praezise, kein Wackeln, kein Blinken.

WAS DU BAUST, je Art:
  zahl: die Zahl gross (Ziffern zaehlen sich NICHT hoch, sie stempeln sich), Einheit/Wort daneben klein, ein passendes Icon
  vergleich: zwei Kacheln oder Spalten, links das eine, rechts das andere, je ein Symbol, ein Strich oder "vs" dazwischen
  liste: Punkte untereinander (.spalte), jeder kommt 0,35 s nach dem vorigen (hoch), mit Icon oder Ziffer in Farbe davor
  begriff: das Wort gross, der Marker zieht drueber, ein Symbol daneben
  haken: der Text mit grossem Haken (richtig, .c2) oder Kreuz (falsch, .c4) als SVG, das sich zeichnet
  pfeil: Flow: Knoten (Logo oder Icon + Wort) mit Verbindern, die nacheinander wachsen; Stationen sind die teile
  frage: die Frage, das Fragezeichen als Akzent, stempelt sich zuletzt
  zitat: die Woerter stempeln sich einzeln, das wichtigste Wort bekommt den Marker

FREIRAUM (Justus 03.10.: "lass Opus Freiraum"): die Form ist der Rahmen, nicht die Grenze. Du darfst
eigene Flaechen, Linien, Pfeile, Kreise, Balken, Verlaeufe als inline-SVG oder CSS ergaenzen, Elemente
drehen, staffeln, ueberlappen, ein Wort riesig und den Rest klein setzen, Zahlen als Balken oder Kreis
zeigen. Was bleibt: Papier-Charakter (keine Neon-Glows, kein 3D, keine Schlagschatten in Farbe),
Lesbarkeit in 0,3 s, und die Regeln unten.

HARTE REGELN
- Nur die gegebenen Woerter und Zahlen. Keine neuen Woerter, keine Ueberschriften, keine Emojis. Symbole nur ueber <use> aus der Liste, dazu Haken, Kreuz, Pfeil, Strich als inline-SVG.
- Kein <script>, kein <img>, kein url(), keine externen Schriften. Nur HTML und CSS-Keyframes.
- Alles Sichtbare beginnt innerhalb der ersten 1,2 Sekunden und BLEIBT dann stehen (kein Ausflug, die Karte wird hart ausgeblendet).
- Alles muss in die Karte passen: BREITE x HOEHE Pixel, mit dem Padding des Rahmens. Lieber Schrift verkleinern als abschneiden.
- Kein Wort wird je getrennt oder abgeschnitten. Bei pfeil und liste mit drei Stationen oder einer Station ueber 14 Zeichen:
  Stationen UNTEREINANDER (jede eine Zeile, Pfeil oder Ziffer davor), nicht nebeneinander. Zwei kurze Stationen duerfen nebeneinander.
- Die Karte ist breit und flach (etwa 2,5:1): rechne mit hoechstens drei Textzeilen bei Schriftgroesse SCHRIFT.
- NUTZE DIE FLAECHE: der Inhalt fuellt mindestens 70 Prozent der Breite und 60 Prozent der Hoehe. Kein Text
  kleiner als 0,8 x SCHRIFT, Symbole mindestens 1,2 em. Ein kleines Haeufchen Elemente in einer grossen Karte
  (Lauf 1, Kette: ein Drittel der Breite) sieht am Handy nach nichts aus. Lieber zwei Knoten gross als drei klein.
- Farben nur ueber die Klassen .akzent, .leise, .c1 bis .c5, .bg1 bis .bg5 und currentColor. Bei form amethyst keine dunklen Flaechen.

AUSGABE, nur JSON:
{"body":"<div class='inhalt'>...</div>","css":".meine{...} @keyframes ...{...}"}"""


def bauen(moment, k, breit, hoch, dauer, modell=None, hinweis=""):
    """Markup fuer einen Moment. Rueckgabe {"markup", "kosten", "quelle": "opus"|"streifen", "hinweis"}.
    hinweis: Befund aus dem letzten Versuch (z. B. Ueberlauf), geht als Auftrag mit."""
    fs = max(40, int(breit * 0.075))
    if hinweis:
        fs = int(fs * 0.85)
    winkel = random.Random(moment.get("von_idx", 0)).choice([-1, 1]) * random.Random(len(moment.get("text", ""))).uniform(1.2, 2.6)
    form = moment.get("form") or form_waehlen(moment["art"], [])
    kern = _rahmen_css(k, fs, winkel, seed=moment.get("von_idx", 0), form=form)
    marken = moment.get("marken") or marken_im_text(" ".join([moment.get("text", "")] + (moment.get("teile") or [])))
    auftrag = json.dumps({"art": moment["art"], "form": form, "text": moment["text"], "teile": moment.get("teile") or [],
                          "marken": [{"slug": m["slug"], "name": m["titel"], "farbe": m["farbe"]} for m in marken],
                          "breite_px": breit, "hoehe_px": hoch, "dauer_s": dauer, "schrift_px": fs}, ensure_ascii=False)
    if hinweis:
        auftrag += "\n\nBEFUND DES LETZTEN VERSUCHS (unbedingt beheben): " + hinweis + " Stationen untereinander, Schrift kleiner, nichts ragt heraus."
    try:
        txt, kosten, usage = _llm(BAU_SYS.replace("BREITE", str(breit)).replace("HOEHE", str(hoch)).replace("SCHRIFT", str(fs))
                                  .replace("ICONS", ", ".join(ICONS)),
                                  auftrag + "\n\nNur das JSON.", modell or BAU_MODELL, max_tokens=2200,
                                  temperature=0.4, cache=True)
    except Exception as exc:
        return {"markup": _streifen(moment, k, breit, hoch, dauer), "kosten": 0.0, "quelle": "streifen",
                "hinweis": "Modell: %s" % str(exc)[:120]}
    o = _json(txt) or {}
    body, css = str(o.get("body") or ""), str(o.get("css") or "")
    body = _symbole_filtern(body, marken)
    body, css = _schrift_boden(body, css, fs)
    grund = _pruefen(body, css, moment)
    if grund:
        return {"markup": _streifen(moment, k, breit, hoch, dauer), "kosten": round(kosten, 4), "quelle": "streifen",
                "hinweis": "Opus verworfen: " + grund, "usage": usage}
    seite = _seite(k, breit, hoch,
                   "<div class='wrap'><div class='karte'><div class='papier'></div><div class='band'></div>" + body + "</div></div>",
                   kern + "\n" + css)
    return {"markup": seite, "kosten": round(kosten, 4), "quelle": "opus", "hinweis": "", "usage": usage}


def _schrift_boden(body, css, fs):
    """Lauf 1 (03.10.): Opus setzte Knoten und Kacheln auf 38 px in einer 972 px breiten Karte,
    am Handy ein Haeufchen. Keine Schrift unter 0,8 x Ausgangsgroesse; zu kleine Werte werden
    angehoben statt die Karte zu verwerfen."""
    boden = int(fs * 0.8)

    def _px(m):
        v = float(m.group(1))
        return "font-size:%dpx" % boden if v < boden else m.group(0)

    def _em(m):
        v = float(m.group(1))
        return "font-size:.8em" if v < 0.8 else m.group(0)
    fix = lambda s: re.sub(r"font-size:\s*(\d+(?:\.\d+)?)px", _px,
                           re.sub(r"font-size:\s*(0?\.\d+|1(?:\.0+)?)em", _em, s))
    return fix(body), fix(css)


def _symbole_filtern(body, marken):
    """Nur Logos der genannten Marken und Icons aus der Liste bleiben; fremde <use> fliegen
    samt ihrem <svg> raus (lautlos: ein <use> auf ein fehlendes Ziel rendert nichts, und
    dann staende ein leeres Kaestchen im Bild)."""
    erlaubt = {"lg-" + m["slug"] for m in marken} | {"ic-" + n for n in ICONS}

    def _svg(m):
        ziel = re.search(r"href=['\"]#([a-z0-9_\-\.]+)['\"]", m.group(0), re.I)
        if ziel and ziel.group(1) not in erlaubt and ziel.group(1).startswith(("lg-", "ic-")):
            return ""
        return m.group(0)
    return re.sub(r"<svg\b[^>]*>\s*<use\b[^>]*/?>\s*(?:</use>)?\s*</svg>", _svg, body, flags=re.I | re.S)


def _pruefen(body, css, moment):
    ganz = body + css
    for verboten in ("<script", "<img", "url(", "http:", "https:", "@import", "javascript:"):
        if verboten in ganz.lower().replace(" ", ""):
            if verboten == "url(" and "data:image/svg" in ganz:
                continue
            return "verboten: " + verboten
    if not body.strip():
        return "leerer body"
    erlaubt = {_norm(w) for w in re.findall(r"\S+", " ".join([moment.get("text", "")] + (moment.get("teile") or [])))}
    erlaubt.discard("")
    sichtbar = re.sub(r"<[^>]+>", " ", body)
    fremd = [w for w in re.findall(r"[A-Za-zÄÖÜäöüß]{2,}", sichtbar) if _norm(w) not in erlaubt and w.lower() not in ("bis", "vs", "und")]
    if fremd:
        return "fremde Woerter: " + ", ".join(fremd[:5])
    return ""


def _seite(k, breit, hoch, body, css):
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
    html,body{{margin:0;padding:0;width:{breit}px;height:{hoch}px;overflow:hidden;background:transparent;}}
    *{{box-sizing:border-box;}}
    body{{font-family:{k['font']};color:{k['text']};-webkit-font-smoothing:antialiased;}}
    .buehne{{position:absolute;left:0;top:0;width:{breit}px;height:{hoch}px;}}
    {css}
    </style></head><body><div class="buehne">{body}</div></body></html>"""


def _streifen(moment, k, breit, hoch, dauer):
    """Rueckfall: der Papierstreifen aus stil.py mit dem Text des Moments."""
    import stil
    text = moment.get("text") or " ".join(moment.get("teile") or [])
    return stil.vox_zeile(text, stil.schluesselwort(text), k, breit, hoch, dauer, seed=moment.get("von_idx", 0))
