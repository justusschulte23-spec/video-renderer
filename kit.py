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
        "serif": True,
        "font": "'Georgia','Iowan Old Style',serif",
        "radius": 4, "rein_ms": 600, "raus_ms": 400, "versatz_ms": 220,
        "glow": "none",
        "grund": "#FDFCF9",
        "raster": "rgba(31,36,33,.05)",
    },
}


def kit_fuer(client_id: str) -> dict:
    return KITS.get((client_id or "justus").lower(), KITS["justus"])


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
                padding:{int(breit * .02)}px 0; color:{k['text']}; }}
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


def baue(art: str, felder: dict, client_id: str, breit: int, hoch: int,
         sekunden: float = 3.0) -> Optional[str]:
    """Fertiges Markup fuer eine Komponente. Gibt None, wenn das Kit die Art
    nicht kennt — dann uebernimmt der Gestalter."""
    k = kit_fuer(client_id)
    zeilen = [str(z).strip() for z in (felder.get("zeilen") or []) if str(z).strip()]
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
            + f"<p class='wert blur-text'><em>{wert}</em>"
            + (f"<span class='chip'>{_e(einheit)}</span>" if einheit else "") + "</p>"
            + (f"<p class='stuetze'>{_e(zwei)}</p>" if zwei and rest else "")
            + "<div class='balken'><i style='--fuell:78%'></i></div>")

    if art == "vergleich":
        links = rest[:1] or [""]
        return rahmen(
            "<div class='spalten'>"
            f"<div class='sp'><h4>{_e(haupt)}</h4><ul>"
            + "".join(f"<li>{_e(z)}</li>" for z in rest[:3]) + "</ul></div>"
            f"<div class='sp stark'><h4>{_e(zwei)}</h4><ul>"
            + "".join(f"<li>{_e(z)}</li>" for z in rest[3:6] or rest[:3]) + "</ul></div>"
            "</div>")

    if art == "ablauf":
        zeilen_html = ""
        for i, z in enumerate(zeilen[:4], 1):
            teile = str(z).split("—", 1)
            titel, note = teile[0].strip(), (teile[1].strip() if len(teile) > 1 else "")
            hell = " hell" if i == 1 else ""
            zeilen_html += (f"<div class='zeile{hell}'><span class='nr'>{i}</span>"
                            f"<span><span class='zt'>{_e(titel)}</span>"
                            + (f"<br><span class='zn'>{_e(note)}</span>" if note else "")
                            + "</span></div>")
        return rahmen((f"<p class='kicker'>{kicker}</p>" if kicker else "") + zeilen_html)

    if art == "zitat":
        return rahmen(
            "<p class='marke'>&bdquo;</p>"
            f"<p class='zitat blur-text'>{_e(haupt)}</p>"
            + (f"<p class='quelle'>{_e(zwei)}</p>" if zwei else ""))

    if art == "titel":
        return rahmen(
            (f"<p class='kicker'>{_e(zwei)}</p>" if zwei else "")
            + f"<p class='wert blur-text' style='font-size:{max(40, int(breit * .13))}px'>"
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
        return rahmen(f"<p class='cta blur-text'><span class='unterstrich'>"
                      f"{_e(haupt)}</span></p>")

    return None


# Welche Plan-Arten das Kit bedient. Alles andere geht weiter an den Gestalter.
KANN = ("stat", "vergleich", "ablauf", "zitat", "titel", "lower", "leiste", "cta")
