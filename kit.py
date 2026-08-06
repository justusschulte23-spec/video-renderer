"""DER BAUKASTEN — neun Komponenten je Kunde, aus den Brand Kits gebaut.

Warum das den Gestalter ersetzt: er hat bei jedem Element frei HTML geschrieben.
Kein Schnitt-Team der Welt arbeitet so — ein Motion Designer fuellt Vorlagen aus
einem Kit, er erfindet nicht pro Shot ein Layout. Genau daher kamen die leeren,
kahlen Karten: freies Erfinden unter Zeitdruck ergibt Durchschnitt.

Hier steht das Layout fest, der Plan liefert nur noch die Worte. Kosten pro
Element: null Modell-Aufrufe.

Zwei Kits, weil zwei Kanaele:
  justus  dunkel, technisch, Amethyst, harte kurze Bewegungen (320ms rein)
  tim     Papier, warm, Petrol mit Gold fuer Gutes, langsame Blenden (600ms)
Die Zeiten stehen so in den Motion-Blaettern der Brand Kits.
"""
from typing import Optional

# ── Die Kits ────────────────────────────────────────────────────────────────
KITS = {
    "justus": {
        "canvas": "#0B0B12", "surface": "#12121C", "raised": "#1B1B29",
        "akzent": "#8B5CF6", "akzent_soft": "#A78BFA", "text": "#F4F4F8",
        "muted": "#8A8AA3", "linie": "rgba(255,255,255,.10)", "signal": "#F43F5E",
        # Zustandsfarben. Bewusst NICHT die Akzentfarbe: der Akzent sagt
        # "hierher sehen", gruen und rot sagen "das geht" und "das geht nicht".
        # Wer beides in eine Farbe legt, hat weder Betonung noch Befund.
        "gut": "#34D399", "grenze": "#F43F5E", "warnung": "#FBBF24",
        "serif": False,
        "font": "'Inter','Helvetica Neue',system-ui,sans-serif",
        "radius": 18, "rein_ms": 320, "raus_ms": 240, "versatz_ms": 80,
        "glow": "0 0 60px rgba(139,92,246,.35)",
        "grund": "radial-gradient(120% 80% at 50% 0%, #17172A 0%, #0B0B12 60%)",
        "raster": "rgba(255,255,255,.05)",
    },
    "tim": {
        "canvas": "#FDFCF9", "surface": "#FDFCF9", "raised": "#ECE9E2",
        "akzent": "#0F766E", "akzent_soft": "#B8860B", "text": "#1F2421",
        "muted": "#5F6B66", "linie": "#D8D4CC", "signal": "#B8860B",
        # Bei Tim gedaempft — Neongruen auf Papier ist ein Fremdkoerper.
        "gut": "#2F7D57", "grenze": "#A8443C", "warnung": "#B8860B",
        "serif": True,
        "font": "'Georgia','Iowan Old Style',serif",
        "radius": 4, "rein_ms": 600, "raus_ms": 400, "versatz_ms": 220,
        "glow": "none",
        "grund": "#FDFCF9",
        "raster": "rgba(31,36,33,.05)",
    },
}


# ── DIE GEWENDETE FASSUNG ───────────────────────────────────────────────────
# Dasselbe Kit in der anderen Helligkeit. Nicht ein zweites Design: Akzent,
# Radius, Schrift und Zeiten bleiben, nur der Grund kippt. Genau das macht in
# der Referenz den Rhythmus — nach zwanzig Sekunden Dunkel ist ein heller
# Abschnitt ein Ereignis, ohne dass irgendetwas Neues erklaert werden muesste.
WENDE = {
    "justus": {
        "canvas": "#F4F3F8", "surface": "#FFFFFF", "raised": "#EAE8F2",
        "text": "#0B0B12", "muted": "#5B5B70", "linie": "rgba(11,11,18,.14)",
        "glow": "0 24px 60px rgba(88,60,180,.18)",
        "grund": "radial-gradient(120% 80% at 50% 0%, #FFFFFF 0%, #EDEBF5 65%)",
        "raster": "rgba(11,11,18,.07)",
        # Auf Weiss braucht Gruen mehr Tiefe, sonst leuchtet es und sagt nichts.
        "gut": "#0E9F6E", "grenze": "#D92D4B",
    },
    "tim": {
        "canvas": "#14201C", "surface": "#1B2A25", "raised": "#24352E",
        "text": "#F5F2EA", "muted": "#9AAAA2", "linie": "rgba(245,242,234,.16)",
        "glow": "0 24px 60px rgba(0,0,0,.45)",
        "grund": "linear-gradient(170deg,#1B2A25 0%,#111B18 70%)",
        "raster": "rgba(245,242,234,.06)",
        # Petrol verschwindet auf dunklem Petrol — der Akzent muss aufhellen.
        "akzent": "#3FB89B", "gut": "#4FBE86", "grenze": "#E0736A",
    },
}


def kit_fuer(client_id: str, wende: bool = False) -> dict:
    k = KITS.get((client_id or "justus").lower(), KITS["justus"])
    if not wende:
        return k
    return {**k, **WENDE.get((client_id or "justus").lower(), WENDE["justus"])}


def _css(k: dict, breit: int, hoch: int) -> str:
    """Grundlage jeder Komponente. Safe Area, Raster, Typo — einmal, nicht
    jedes Mal neu erfunden."""
    return f"""
    <style>
      .wrap {{ position:relative; width:{breit}px; height:{hoch}px;
               font-family:{k['font']}; color:{k['text']};
               display:flex; flex-direction:column; justify-content:center;
               padding:{max(28, int(breit * .07))}px; box-sizing:border-box; }}
      .flaeche {{ position:absolute; inset:0; background:{k['grund']};
                  border-radius:{k['radius']}px;
                  border:1px solid {k['linie']}; box-shadow:{k['glow']}; }}
      .raster {{ position:absolute; inset:0; border-radius:{k['radius']}px;
                 background-image:radial-gradient({k['raster']} 1px,transparent 1px);
                 background-size:14px 14px; opacity:.6; }}
      .inhalt {{ position:relative; }}
      .kicker {{ font-size:{max(16, int(breit * .038))}px; letter-spacing:.14em;
                 text-transform:uppercase; color:{k['muted']};
                 margin:0 0 {int(breit * .03)}px; font-family:{k['font']}; }}
      .wert {{ font-size:{max(52, int(breit * .20))}px; line-height:.94;
               font-weight:{500 if k['serif'] else 800};
               letter-spacing:{'.005em' if k['serif'] else '-.03em'};
               margin:0; font-variant-numeric:tabular-nums; }}
      .einheit {{ font-size:{max(22, int(breit * .075))}px; color:{k['muted']};
                  margin:{int(breit * .02)}px 0 0; }}
      .stuetze {{ font-size:{max(18, int(breit * .05))}px; color:{k['muted']};
                  margin:{int(breit * .035)}px 0 0; line-height:1.35; }}
      .regel {{ position:absolute; left:0; top:0; bottom:0; width:4px;
                background:{k['akzent']}; border-radius:4px; }}
      .balken {{ position:relative; height:8px; border-radius:8px;
                 background:{k['raised']}; margin-top:{int(breit * .05)}px;
                 overflow:hidden; }}
      .balken i {{ position:absolute; inset:0 auto 0 0; width:0;
                   background:linear-gradient(90deg,{k['akzent']},{k['akzent_soft']});
                   border-radius:8px; display:block;
                   animation:fuellen {k['rein_ms'] * 2}ms cubic-bezier(.22,1,.36,1) forwards;
                   box-shadow:{k['glow']}; }}
      @keyframes fuellen {{ from {{ width:0 }} to {{ width:var(--fuell,72%) }} }}
      .wrap::after {{ content:''; position:absolute; left:0; top:14%; bottom:14%;
                      width:5px; border-radius:5px; background:{k['akzent']};
                      box-shadow:{k['glow']}; }}
      .wert em {{ font-style:normal; color:{k['akzent']}; }}
      .unterstrich {{ position:relative; display:inline-block; }}
      .unterstrich::after {{ content:''; position:absolute; left:0; right:0;
                             bottom:-.14em; height:5px; border-radius:5px;
                             background:{k['akzent']}; transform-origin:left;
                             animation:wischen {k['rein_ms']}ms
                                       cubic-bezier(.22,1,.36,1) 120ms both; }}
      @keyframes wischen {{ from {{ transform:scaleX(0) }} to {{ transform:scaleX(1) }} }}
      .chip {{ display:inline-block; padding:.35em .9em; border-radius:999px; font-weight:600;
               background:{k['raised']}; border:1px solid {k['akzent']};
               color:{k['akzent']}; font-size:.22em; vertical-align:middle;
               margin-left:.5em; }}
      .zeile {{ display:flex; align-items:center; gap:{int(breit * .035)}px;
                padding:{int(breit * .035)}px {int(breit * .04)}px;
                border:1px solid {k['linie']}; border-radius:{k['radius'] - 6 if k['radius'] > 8 else 3}px;
                background:{k['surface']}; margin-bottom:{int(breit * .025)}px; }}
      .zeile.hell {{ border-color:{k['akzent']}; }}
      .nr {{ font-size:{max(22, int(breit * .07))}px; color:{k['muted']};
             font-variant-numeric:tabular-nums; min-width:1.4em; }}
      .zt {{ font-size:{max(20, int(breit * .055))}px; }}
      .zn {{ font-size:{max(15, int(breit * .036))}px; color:{k['muted']}; }}
      .spalten {{ display:flex; align-items:stretch; gap:0; }}
      .sp {{ flex:1; padding:{int(breit * .04)}px; }}
      .sp + .sp {{ border-left:1px solid {k['linie']}; }}
      .sp h4 {{ margin:0 0 {int(breit * .03)}px; font-size:{max(20, int(breit * .06))}px;
                color:{k['muted']}; font-weight:600; }}
      .sp.stark h4 {{ color:{k['akzent']}; }}
      .sp li {{ list-style:none; font-size:{max(16, int(breit * .042))}px;
                padding:{int(breit * .02)}px 0; color:{k['text']};
                display:flex; align-items:flex-start; gap:.55em; }}
      .sp ul {{ margin:0; padding:0; }}
      .zitat {{ font-size:{max(30, int(breit * .085))}px; line-height:1.25;
                margin:0; font-weight:{500 if k['serif'] else 600}; }}
      .marke {{ font-size:{max(48, int(breit * .16))}px; color:{k['akzent']};
                line-height:.7; margin:0 0 {int(breit * .02)}px; }}
      .quelle {{ font-size:{max(14, int(breit * .033))}px; color:{k['muted']};
                 margin-top:{int(breit * .03)}px; }}
      .lt {{ background:{k['surface']}; border-left:4px solid {k['akzent']};
             padding:{int(breit * .035)}px {int(breit * .045)}px;
             border-radius:0 {k['radius']}px {k['radius']}px 0; }}
      .lt b {{ display:block; font-size:{max(24, int(breit * .07))}px; }}
      .lt span {{ font-size:{max(16, int(breit * .042))}px; color:{k['muted']}; }}
      .leiste {{ display:flex; align-items:center; justify-content:space-between;
                 gap:{int(breit * .03)}px; }}
      .leiste div {{ text-align:center; flex:1; }}
      .leiste b {{ display:block; font-size:{max(22, int(breit * .06))}px;
                   font-variant-numeric:tabular-nums; }}
      .leiste span {{ font-size:{max(13, int(breit * .03))}px; color:{k['muted']}; }}
      .cta {{ font-size:{max(46, int(breit * .17))}px; font-weight:800;
              letter-spacing:-.02em; text-align:center; margin:0; }}

      /* ── DIE APP-ICON-KACHEL ────────────────────────────────────────────
         Der wiederkehrende Held aus der Referenz: abgerundetes Quadrat,
         Schatten, ein Ring der einmal nach aussen laeuft. Das Logo sitzt
         darin, nicht daneben — eine Kachel liest sich als App, ein
         freistehendes Logo als Aufkleber. */
      .kachel {{ position:relative; display:inline-flex; align-items:center;
                 justify-content:center;
                 width:var(--kachel,{max(88, int(breit * .30))}px);
                 height:var(--kachel,{max(88, int(breit * .30))}px);
                 border-radius:{max(16, int(breit * .066))}px;
                 background:linear-gradient(160deg,{k['raised']},{k['surface']});
                 border:1px solid {k['linie']};
                 box-shadow:0 {int(breit * .022)}px {int(breit * .05)}px
                            rgba(0,0,0,{'.55' if not k['serif'] else '.16'}),
                            inset 0 1px 0 rgba(255,255,255,{'.10' if not k['serif'] else '.6'});
                 color:{k['akzent']};
                 animation:kachel_rein {k['rein_ms']}ms cubic-bezier(.34,1.56,.64,1) both; }}
      .kachel svg, .kachel .lg {{ width:56%; height:56%; fill:currentColor; }}
      /* Markenfarbe: ein Logo in der falschen Farbe ist falsch, auch wenn die
         Palette stimmt. Deshalb darf .kachel[data-marke] die Farbe setzen. */
      .kachel[style*="--marke"] {{ color:var(--marke); }}
      .kachel::after {{ content:''; position:absolute; inset:0;
                        border-radius:inherit; border:2px solid {k['akzent']};
                        animation:ring_puls {int(k['rein_ms'] * 3)}ms
                                  cubic-bezier(.22,1,.36,1) {k['versatz_ms']}ms both; }}
      @keyframes kachel_rein {{ from {{ opacity:0; transform:scale(.86) translateY(8px) }}
                                to {{ opacity:1; transform:none }} }}
      @keyframes ring_puls {{ 0% {{ opacity:.85; transform:scale(1) }}
                              100% {{ opacity:0; transform:scale(1.5) }} }}
      .kachel_reihe {{ display:flex; align-items:center; gap:{int(breit * .045)}px; }}
      .kachel_name {{ font-size:{max(24, int(breit * .085))}px; font-weight:700;
                      letter-spacing:-.01em; margin:0; }}

      /* ── STATUSMARKEN ───────────────────────────────────────────────────
         Semantisch gebunden, nicht nach Laune: gut = gruen mit Haken,
         grenze = rot mit X. Die Farbe kommt aus dem Kit, nicht aus dem Satz. */
      .mark {{ display:inline-flex; align-items:center; justify-content:center;
               width:1.15em; height:1.15em; border-radius:999px; flex:none;
               font-size:inherit; vertical-align:-.15em; }}
      .mark svg {{ width:.72em; height:.72em; }}
      .mark.gut {{ color:{k['gut']}; background:{k['gut']}22;
                   box-shadow:inset 0 0 0 1.5px {k['gut']}66; }}
      .mark.grenze {{ color:{k['grenze']}; background:{k['grenze']}22;
                      box-shadow:inset 0 0 0 1.5px {k['grenze']}66; }}
      .mark.warnung {{ color:{k['warnung']}; background:{k['warnung']}22;
                       box-shadow:inset 0 0 0 1.5px {k['warnung']}66; }}
      .mark path {{ stroke:currentColor; stroke-width:3.2; fill:none;
                    stroke-linecap:round; stroke-linejoin:round;
                    stroke-dasharray:var(--len,28); stroke-dashoffset:var(--len,28);
                    animation:zeichnen {k['rein_ms']}ms cubic-bezier(.22,1,.36,1)
                              var(--verzug,0ms) forwards; }}
      @keyframes zeichnen {{ to {{ stroke-dashoffset:0 }} }}
      .zeile.gut {{ border-color:{k['gut']}55; }}
      .zeile.grenze {{ border-color:{k['grenze']}55; }}
      .befund {{ display:inline-flex; align-items:center; gap:.45em;
                 padding:.3em .8em .3em .5em; border-radius:999px;
                 font-size:{max(15, int(breit * .038))}px; font-weight:600;
                 margin:0 {int(breit * .02)}px {int(breit * .02)}px 0; }}
      .befund.gut {{ color:{k['gut']}; background:{k['gut']}1A;
                     border:1px solid {k['gut']}55; }}
      .befund.grenze {{ color:{k['grenze']}; background:{k['grenze']}1A;
                        border:1px solid {k['grenze']}55; }}
      .befund.warnung {{ color:{k['warnung']}; background:{k['warnung']}1A;
                         border:1px solid {k['warnung']}55; }}
      .geprueft {{ display:inline-flex; align-items:center; gap:.4em;
                   padding:.28em .7em; border-radius:6px;
                   font-size:{max(13, int(breit * .032))}px; font-weight:700;
                   letter-spacing:.1em; text-transform:uppercase;
                   color:{k['gut']}; border:1px solid {k['gut']}66;
                   background:{k['gut']}14; }}
    </style>"""


def _e(t) -> str:
    return (str(t or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _zahl_teilen(wert: str) -> tuple:
    """'70 Shops' → ('70', 'Shops'). Damit die Zahl zaehlen kann und die
    Einheit ruhig danebensteht."""
    import re
    w = str(wert or "").strip()
    m = re.match(r"^\s*([0-9]+(?:[.,][0-9]+)?(?:\s*[-–]\s*[0-9]+)?)\s*(.*)$", w)
    if not m:
        return "", w
    return m.group(1), m.group(2).strip()


# Der Plan schreibt den Zustand VOR die Zeile. Ein Zeichen, kein Feld — der
# Art Director schreibt Textzeilen, keine Objekte, und ein zweites Schema haette
# er genauso zuverlaessig ignoriert wie art_element wochenlang.
ZUSTAND_ZEICHEN = {"+": "gut", "-": "grenze", "!": "warnung"}
# Pfad und Konturlaenge je Zeichen. Die Laenge steuert die Zeichen-Animation;
# geraten waere sie zu kurz und der Haken bliebe halb.
_MARK_PFAD = {
    "gut": ("M5 12.5 L10 17.5 L19 6.5", 26),
    "grenze": ("M6.5 6.5 L17.5 17.5 M17.5 6.5 L6.5 17.5", 32),
    "warnung": ("M12 5.5 L12 13.5 M12 17.4 L12 17.5", 12),
}


def _zustand(zeile: str) -> tuple:
    """'+ laeuft lokal' -> ('gut', 'laeuft lokal'). Ohne Zeichen: (None, Zeile)."""
    z = str(zeile or "").strip()
    if len(z) >= 2 and z[0] in ZUSTAND_ZEICHEN and z[1] in " \t":
        return ZUSTAND_ZEICHEN[z[0]], z[1:].strip()
    return None, z


def _mark(zustand: str, verzug_ms: int = 0) -> str:
    """Haken, X oder Ausrufezeichen — gezeichnet, nicht gesetzt."""
    if zustand not in _MARK_PFAD:
        return ""
    d, laenge = _MARK_PFAD[zustand]
    return (f"<span class='mark {zustand}'><svg viewBox='0 0 24 24'>"
            f"<path d='{d}' style='--len:{laenge};--verzug:{verzug_ms}ms'/>"
            f"</svg></span>")


def _logo(slug: str, farbe: str = "") -> str:
    """Ein Symbol aus dem simple-icons-Sprite. Leerer Slug = nichts, nie ein
    Platzhalterkasten."""
    s = str(slug or "").strip().lower()
    if not s:
        return ""
    if s.startswith("lg-"):
        s = s[3:]
    stil = f" style='--marke:{farbe}'" if farbe else ""
    return (f"<span class='kachel'{stil}><svg class='lg' viewBox='0 0 24 24'>"
            f"<use href='#lg-{_e(s)}'/></svg></span>")


def baue(art: str, felder: dict, client_id: str, breit: int, hoch: int,
         sekunden: float = 3.0, wende: bool = False) -> Optional[str]:
    """Fertiges Markup fuer eine Komponente. Gibt None, wenn das Kit die Art
    nicht kennt — dann uebernimmt der Gestalter.

    wende=True baut dieselbe Komponente in der anderen Helligkeit."""
    k = kit_fuer(client_id, wende)
    roh_zeilen = [str(z).strip() for z in (felder.get("zeilen") or []) if str(z).strip()]
    # Zustand einmal abtrennen. Danach ist `zeilen` reiner Anzeigetext — sonst
    # stuende das '+' im Bild, und genau so etwas hat schon einmal die interne
    # Begruendung des Art Directors ins fertige Video getragen.
    getrennt = [_zustand(z) for z in roh_zeilen]
    zustaende = [z for z, _ in getrennt]
    zeilen = [t for _, t in getrennt]
    logo_slug = str(felder.get("logo") or "").strip()
    logo_farbe = str(felder.get("logo_farbe") or "").strip()
    haupt = zeilen[0] if zeilen else ""
    zwei = zeilen[1] if len(zeilen) > 1 else ""
    rest = zeilen[2:]
    kicker = _e(felder.get("kicker") or zwei if art == "stat" else felder.get("kicker") or "")
    kopf = _css(k, breit, hoch)
    rein = k["rein_ms"] / 1000.0

    def rahmen(inhalt: str, klasse: str = "") -> str:
        return (f"{kopf}<div class='wrap {klasse}'><div class='flaeche'></div>"
                f"<div class='raster'></div><div class='inhalt'>{inhalt}</div></div>")

    if art == "stat":
        zahl, einheit = _zahl_teilen(haupt)
        wert = (f"<span data-count='{_e(zahl)}' data-count-duration='600'>0</span>"
                if zahl else _e(haupt))
        return rahmen(
            (f"<p class='kicker'>{_e(rest[0]) if rest else _e(zwei)}</p>" if (rest or zwei) else "")
            + f"<p class='wert' data-blur-text><em>{wert}</em>"
            + (f"<span class='chip'>{_e(einheit)}</span>" if einheit else "") + "</p>"
            + (f"<p class='stuetze'>{_e(zwei)}</p>" if zwei and rest else "")
            + "<div class='balken'><i style='--fuell:78%'></i></div>")

    if art == "vergleich":
        # Die linke Seite ist die schwaechere, die rechte die staerkere — steht
        # kein Zustand dran, traegt die Spalte ihn: links Grenze, rechts gut.
        def spalte(titel: str, punkte: list, zust: list, stark: bool) -> str:
            vor = "gut" if stark else "grenze"
            lis = "".join(
                f"<li>{_mark(z or vor, 120 + i * 90)}{_e(p)}</li>"
                for i, (p, z) in enumerate(zip(punkte, zust + [None] * len(punkte))))
            return (f"<div class='sp{' stark' if stark else ''}'><h4>{_e(titel)}</h4>"
                    f"<ul>{lis}</ul></div>")
        return rahmen(
            "<div class='spalten'>"
            + spalte(haupt, rest[:3], zustaende[2:5], False)
            + spalte(zwei, rest[3:6] or rest[:3], zustaende[5:8], True)
            + "</div>")

    if art == "ablauf":
        zeilen_html = ""
        for i, z in enumerate(zeilen[:4], 1):
            teile = str(z).split("—", 1)
            titel, note = teile[0].strip(), (teile[1].strip() if len(teile) > 1 else "")
            zust = zustaende[i - 1] if i - 1 < len(zustaende) else None
            klasse = (" " + zust) if zust else (" hell" if i == 1 else "")
            # Steht ein Zustand dran, ersetzt die Marke die Nummer: eine Liste
            # aus Haken und Kreuzen ist ein Befund, keine Reihenfolge.
            kopf_zelle = (f"<span class='nr'>{_mark(zust, 100 + i * 90)}</span>"
                          if zust else f"<span class='nr'>{i}</span>")
            zeilen_html += (f"<div class='zeile{klasse}'>{kopf_zelle}"
                            f"<span><span class='zt'>{_e(titel)}</span>"
                            + (f"<br><span class='zn'>{_e(note)}</span>" if note else "")
                            + "</span></div>")
        return rahmen((f"<p class='kicker'>{kicker}</p>" if kicker else "") + zeilen_html)

    if art == "befund":
        # Reine Zustandsliste: was geht, was nicht. Ohne Zeichen davor waere es
        # ein Ablauf — deshalb bekommt jede Zeile eine Marke, notfalls 'gut'.
        chips = "".join(
            f"<span class='befund {zustaende[i] or 'gut'}'>"
            f"{_mark(zustaende[i] or 'gut', 120 + i * 100)}{_e(z)}</span>"
            for i, z in enumerate(zeilen[:5]))
        return rahmen((f"<p class='kicker'>{kicker}</p>" if kicker else "")
                      + f"<div>{chips}</div>"
                      + ("<p class='quelle'><span class='geprueft'>"
                         + _mark("gut", 400) + "geprueft</span></p>"
                         if felder.get("geprueft") else ""))

    if art == "marke":
        # Die App-Icon-Kachel als Held. Kein Kasten mit Firmennamen, sondern
        # das Zeichen selbst — daran haengt der Zuschauer sein Wiedererkennen.
        return rahmen(
            (f"<p class='kicker'>{kicker}</p>" if kicker else "")
            + "<div class='kachel_reihe'>"
            + (_logo(logo_slug, logo_farbe) or "")
            + f"<div><p class='kachel_name' data-blur-text>{_e(haupt)}</p>"
            + (f"<p class='zn'>{_e(zwei)}</p>" if zwei else "") + "</div></div>"
            + (f"<p class='stuetze'>{_e(rest[0])}</p>" if rest else ""))

    if art == "zitat":
        return rahmen(
            "<p class='marke'>&bdquo;</p>"
            f"<p class='zitat' data-blur-text>{_e(haupt)}</p>"
            + (f"<p class='quelle'>{_e(zwei)}</p>" if zwei else ""))

    if art == "titel":
        return rahmen(
            (f"<p class='kicker'>{_e(zwei)}</p>" if zwei else "")
            + f"<p class='wert' data-blur-text style='font-size:{max(40, int(breit * .13))}px'>"
            f"<span class='unterstrich'>{_e(haupt)}</span></p>"
            + (f"<p class='stuetze'>{_e(rest[0])}</p>" if rest else ""))

    if art == "lower":
        return (f"{kopf}<div class='wrap' style='justify-content:flex-end;padding:0'>"
                f"<div class='lt'><b>{_e(haupt)}</b>"
                + (f"<span>{_e(zwei)}</span>" if zwei else "") + "</div></div>")

    if art == "leiste":
        felder_html = "".join(
            f"<div><b>{_e(_zahl_teilen(z)[0] or z)}</b><span>{_e(_zahl_teilen(z)[1])}</span></div>"
            for z in zeilen[:3])
        return (f"{kopf}<div class='wrap' style='justify-content:center;padding:"
                f"{int(breit * .03)}px'><div class='flaeche'></div>"
                f"<div class='inhalt leiste'>{felder_html}</div></div>")

    if art == "cta":
        return rahmen(f"<p class='cta' data-blur-text><span class='unterstrich'>"
                      f"{_e(haupt)}</span></p>")

    return None


# Welche Plan-Arten das Kit bedient. Alles andere geht weiter an den Gestalter.
KANN = ("stat", "vergleich", "ablauf", "zitat", "titel", "lower", "leiste", "cta",
        "befund", "marke")

# Die Zustandsfarben je Kunde. Der Farbwaechter im Gestalter kennt nur die
# Markentokens aus der Datenbank — ohne diese Liste wuerde er den gruenen Haken
# als "fremde Farbe" zurueckweisen, den das Kit selbst gesetzt hat.
def status_farben(client_id: str) -> dict:
    # Beide Helligkeiten. Der Waechter laeuft einmal je Element und weiss nicht,
    # ob es auf hellem oder dunklem Grund steht.
    aus = {}
    for w in (False, True):
        k = kit_fuer(client_id, w)
        for n in ("gut", "grenze", "warnung", "akzent", "text", "canvas",
                  "surface", "raised"):
            if k.get(n, "").startswith("#"):
                aus["%s%s" % (n, "_hell" if w else "")] = k[n]
    return aus
