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
            "reasoning": {"enabled": False}, "usage": {"include": True},
            "messages": [{"role": "system", "content": sys_inhalt}, {"role": "user", "content": user}]}
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
- nicht in den ersten 2 Sekunden (der Hook hat sein eigenes Bild).
- diese Zeitfenster sind schon belegt, dort nichts: BELEGT
- ein Moment dauert 1,6 bis 5 Sekunden: er beginnt beim ersten Wort und endet mit dem
  Gedanken, nicht mitten im Satz.
- Lieber vier starke Momente als acht schwache. Ein Overlay, das nur wiederholt, was man
  hoert, ohne etwas sichtbar zu machen, ist keins.

AUSGABE, nur JSON:
{"momente":[{"von_idx":12,"bis_idx":18,"art":"zahl","text":"10.000 Reichweite","teile":[],"grund":"die Zahl traegt den Beweis"}]}
Nichts Sinnvolles: {"momente":[]}"""


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
        fremd = [w for w in re.findall(r"[^\s–\-/]+", anzeige) if _norm(w) and _norm(w) not in fenster]
        if fremd or not anzeige:
            verworfen.append({"m": m, "grund": "nicht gesprochen: " + ", ".join(fremd[:4])})
            continue
        von = max(0.0, float(words[a].get("start") or 0) - 0.15)
        if von < 2.0:
            verworfen.append({"m": m, "grund": "im Hook"})
            continue
        ende_wort = float(words[b].get("end") or von)
        dauer_m = max(MOMENT_MIN_S, min(MOMENT_MAX_S, ende_wort + 1.2 - von))
        if von + dauer_m > dauer:
            dauer_m = max(0.0, dauer - von)
            if dauer_m < MOMENT_MIN_S:
                verworfen.append({"m": m, "grund": "am Ende zu kurz"})
                continue
        if von - letzte_bis < abstand:
            verworfen.append({"m": m, "grund": "Abstand zum vorigen"})
            continue
        if any(not (von + dauer_m <= x or von >= y) for x, y in belegt):
            verworfen.append({"m": m, "grund": "Zeitfenster belegt (%.1f-%.1f)" % (von, von + dauer_m)})
            continue
        aus.append({"von": round(von, 2), "bis": round(von + dauer_m, 2), "dauer": round(dauer_m, 2),
                    "art": art, "text": text, "teile": teile[:4], "grund": str(m.get("grund") or "")[:120],
                    "von_idx": a, "bis_idx": b})
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
    .inhalt{{position:relative;font-size:{fs}px;line-height:1.14;font-weight:800;letter-spacing:-0.02em;color:{k['text']};}}
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


BAU_SYS = """Du bist Motion-Designer und baust EIN animiertes Overlay fuer ein Talking-Head-Video.
Stil: ein gerissener Papierstreifen (Vox-Dokumentation), simpel, professionell, ruhig. Der
Rahmen (Papier, Korn, Klebeband, Schrift, Farben, Einflug) steht schon. Du lieferst NUR den
Inhalt der Karte und die Keyframes fuer seine Bewegung.

DU BEKOMMST: die Art des Overlays, den Text (woertlich, unveraenderlich), die Kartenbreite
und Hoehe in Pixeln, die Dauer in Sekunden und die Schriftgroesse als Ausgang.

VORHANDENE KLASSEN (nutz sie, statt sie neu zu bauen):
  .inhalt (Textblock, fett, Kundenfarbe)  .akzent (Akzentfarbe)  .marker (Textmarker zieht ueber ein Wort)
  .leise (kleiner, ruhiger Nebentext)  .stempel (Wort stempelt sich ein; setz animation-delay je Wort)
  Keyframes: stempel, marker, hoch (Element faehrt auf), auf (Balken waechst scaleX), zeichnen (SVG-Linie zeichnet sich, stroke-dasharray:400)

WAS DU BAUST, je Art:
  zahl: die Zahl gross (Ziffern zaehlen sich NICHT hoch, sie stempeln sich), Einheit/Wort daneben klein
  vergleich: zwei Spalten oder zwei Zeilen, links/oben das eine, rechts/unten das andere, ein duenner Strich dazwischen, der sich zeichnet
  liste: Punkte untereinander, jeder kommt 0,35 s nach dem vorigen (hoch), mit kleinem Haken oder Ziffer davor
  begriff: das Wort gross, der Marker zieht drueber
  haken: der Text mit grossem Haken (richtig) oder Kreuz (falsch) als SVG, das sich zeichnet
  pfeil: Stationen nebeneinander oder untereinander mit Pfeilen (SVG-Linie zeichnet sich)
  frage: die Frage, das Fragezeichen als Akzent, stempelt sich zuletzt
  zitat: die Woerter stempeln sich einzeln, das wichtigste Wort bekommt den Marker

HARTE REGELN
- Nur die gegebenen Woerter und Zahlen. Keine neuen Woerter, keine Ueberschriften, keine Emojis, keine Icons ausser Haken, Kreuz, Pfeil, Strich als inline-SVG.
- Kein <script>, kein <img>, kein url(), keine externen Schriften. Nur HTML und CSS-Keyframes.
- Alles Sichtbare beginnt innerhalb der ersten 1,2 Sekunden und BLEIBT dann stehen (kein Ausflug, die Karte wird hart ausgeblendet).
- Alles muss in die Karte passen: BREITE x HOEHE Pixel, mit dem Padding des Rahmens. Lieber Schrift verkleinern als abschneiden.
- Farben nur ueber die Klassen .akzent und .leise und currentColor.

AUSGABE, nur JSON:
{"body":"<div class='inhalt'>...</div>","css":".meine{...} @keyframes ...{...}"}"""


def bauen(moment, k, breit, hoch, dauer, modell=None):
    """Markup fuer einen Moment. Rueckgabe {"markup", "kosten", "quelle": "opus"|"streifen", "hinweis"}."""
    fs = max(40, int(breit * 0.075))
    winkel = random.Random(moment.get("von_idx", 0)).choice([-1, 1]) * random.Random(len(moment.get("text", ""))).uniform(1.2, 2.6)
    kern = _papier_css(k, fs, winkel, seed=moment.get("von_idx", 0))
    auftrag = json.dumps({"art": moment["art"], "text": moment["text"], "teile": moment.get("teile") or [],
                          "breite_px": breit, "hoehe_px": hoch, "dauer_s": dauer, "schrift_px": fs}, ensure_ascii=False)
    try:
        txt, kosten, usage = _llm(BAU_SYS.replace("BREITE", str(breit)).replace("HOEHE", str(hoch)),
                                  auftrag + "\n\nNur das JSON.", modell or BAU_MODELL, max_tokens=2200,
                                  temperature=0.4, cache=True)
    except Exception as exc:
        return {"markup": _streifen(moment, k, breit, hoch, dauer), "kosten": 0.0, "quelle": "streifen",
                "hinweis": "Modell: %s" % str(exc)[:120]}
    o = _json(txt) or {}
    body, css = str(o.get("body") or ""), str(o.get("css") or "")
    grund = _pruefen(body, css, moment)
    if grund:
        return {"markup": _streifen(moment, k, breit, hoch, dauer), "kosten": round(kosten, 4), "quelle": "streifen",
                "hinweis": "Opus verworfen: " + grund, "usage": usage}
    seite = _seite(k, breit, hoch,
                   "<div class='wrap'><div class='karte'><div class='papier'></div><div class='band'></div>" + body + "</div></div>",
                   kern + "\n" + css)
    return {"markup": seite, "kosten": round(kosten, 4), "quelle": "opus", "hinweis": "", "usage": usage}


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
