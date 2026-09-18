# -*- coding: utf-8 -*-
"""Karussell-Seiten fuer LinkedIn (18.09.2026).

Das Kit (kit.py) ist fuer Reels gebaut: eine Aussage je Bild, Bewegung, Leerraum
als Wirkung. Als Dokument-Seite wirkt genau das billig: ein Satz in der Mitte,
drei Viertel Grund, keine Marke, keine Seitenzahl, dazu die Ausweichschrift
des Containers (DejaVu statt Inter). Justus, 18.09.: "die Karussell-Bilder
sehen etwas cheap aus".

Hier ist jede Seite ein gesetztes Blatt: Marke und Seitenzahl oben, Aussage
gross und linksbuendig, Fusszeile mit Weiter-Pfeil. Die Farben kommen aus dem
Kit des Kunden (Akzent, Tinte, Grund), die Schrift liegt als Datei im Repo
(fonts/Inter.ttf, fonts/SourceSerif4.ttf, beide OFL) und wird per @font-face
eingebunden, damit der Container nicht auf DejaVu zurueckfaellt.

Eine Kachel ist {art, zeilen, kicker}. Arten wie im Karussell-Prompt des
Dashboards: titel, zitat, cta, stat, befund (+/- je Zeile), ablauf, vergleich.
"""
from html import escape
from pathlib import Path
import re

import kit

FONTS = Path(__file__).parent / "fonts"

# Ueberlauf wird an der MITTE gemessen, nicht am Dokument: der Akzentfleck
# ragt absichtlich ueber den Rand, das Dokument meldet darum immer Ueberlauf.
UEBERLAUF_JS = """() => { const m = document.querySelector('.mitte');
  if (!m) return 0;
  return Math.max(m.scrollHeight - m.clientHeight, m.scrollWidth - m.clientWidth); }"""
KLEINER_JS = """() => { document.querySelectorAll('.gross,.mittel,.zahl,.label,.zeile,.sp p,.rest,.kicker')
  .forEach(el => { const px = parseFloat(getComputedStyle(el).fontSize);
    if (px > 12) el.style.fontSize = (px * 0.9) + 'px'; }); }"""


def _e(s) -> str:
    return escape(str(s or ""), quote=True)


def _font_face(serif: bool) -> str:
    datei = FONTS / ("SourceSerif4.ttf" if serif else "Inter.ttf")
    if not datei.exists():
        return ""
    name = "KSerif" if serif else "KSans"
    return ("@font-face{font-family:'%s';src:url('file:///%s') format('truetype');"
            "font-weight:100 900;font-style:normal;}"
            % (name, str(datei.resolve()).replace("\\", "/")))


def _familie(k: dict) -> str:
    return ("'KSerif','Source Serif 4',Georgia,serif" if k.get("serif")
            else "'KSans','Inter','Helvetica Neue',Arial,sans-serif")


def _hex_rgba(farbe: str, alpha: float) -> str:
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", (farbe or "").strip())
    if not m:
        return farbe
    h = m.group(1)
    return "rgba(%d,%d,%d,%.2f)" % (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def _css(k: dict, breit: int, hoch: int, dunkel: bool) -> str:
    """Grundsatz: eine Spalte, linksbuendig, grosszuegiger Rand. Masse haengen
    an der Breite, damit 1200x1500 und 1080x1350 gleich wirken."""
    r = breit / 1200.0
    grund = k["text"] if dunkel else k["canvas"]
    tinte = k["canvas"] if dunkel else k["text"]
    muted = _hex_rgba(k["canvas"], .62) if dunkel else k["muted"]
    linie = _hex_rgba(k["canvas"], .18) if dunkel else k["linie"]
    akzent = k["akzent"]
    surface = _hex_rgba(k["canvas"], .08) if dunkel else k["surface"]
    radius = max(6, int(k.get("radius", 18) * r))
    return f"""
{_font_face(bool(k.get('serif')))}
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{breit}px;height:{hoch}px;overflow:hidden}}
body{{font-family:{_familie(k)};color:{tinte};background:{grund};
  -webkit-font-smoothing:antialiased;font-feature-settings:'ss01','cv11';}}
.blatt{{position:relative;overflow:hidden;width:{breit}px;height:{hoch}px;
  padding:{int(88*r)}px {int(96*r)}px {int(84*r)}px;
  display:grid;grid-template-rows:auto 1fr auto;gap:{int(40*r)}px;}}
/* Ein weicher Akzentfleck oben rechts: gibt der Seite Tiefe, ohne zu schreien. */
.blatt::before{{content:'';position:absolute;right:{int(-220*r)}px;top:{int(-260*r)}px;
  width:{int(720*r)}px;height:{int(720*r)}px;border-radius:50%;
  background:radial-gradient(closest-side,{_hex_rgba(akzent, .22 if dunkel else .16)},transparent 72%);
  pointer-events:none;}}
.kopf,.fuss{{display:flex;align-items:center;justify-content:space-between;
  position:relative;z-index:1;}}
.marke{{display:flex;align-items:center;gap:{int(14*r)}px;
  font-size:{int(28*r)}px;font-weight:600;letter-spacing:-.01em;color:{tinte};}}
.marke i{{display:block;width:{int(22*r)}px;height:{int(22*r)}px;border-radius:{int(7*r)}px;
  background:{akzent};}}
.seite{{font-size:{int(26*r)}px;font-weight:500;color:{muted};letter-spacing:.02em;
  font-variant-numeric:tabular-nums;}}
.mitte{{position:relative;z-index:1;display:flex;flex-direction:column;
  justify-content:center;min-height:0;}}
.kicker{{font-size:{int(28*r)}px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
  color:{akzent};margin-bottom:{int(30*r)}px;}}
.gross{{font-size:{int(104*r)}px;line-height:1.04;font-weight:{500 if k.get('serif') else 800};
  letter-spacing:{'0' if k.get('serif') else '-.035em'};text-wrap:balance;}}
.mittel{{font-size:{int(74*r)}px;line-height:1.14;font-weight:{500 if k.get('serif') else 700};
  letter-spacing:{'0' if k.get('serif') else '-.025em'};text-wrap:balance;}}
.strich{{width:{int(160*r)}px;height:{int(12*r)}px;border-radius:{int(6*r)}px;
  background:{akzent};margin-top:{int(44*r)}px;}}
.zitat{{border-left:{int(14*r)}px solid {akzent};padding-left:{int(44*r)}px;}}
.zitat .anf{{display:block;font-size:{int(30*r)}px;font-weight:700;letter-spacing:.14em;
  text-transform:uppercase;color:{akzent};margin-bottom:{int(26*r)}px;}}
.zitat .wer{{margin-top:{int(40*r)}px;font-size:{int(30*r)}px;color:{muted};font-weight:500;}}
.stat .zahl{{font-size:{int(300*r)}px;line-height:.92;font-weight:{600 if k.get('serif') else 800};
  letter-spacing:{'0' if k.get('serif') else '-.05em'};color:{akzent};
  font-variant-numeric:tabular-nums;}}
.stat .label{{font-size:{int(58*r)}px;line-height:1.14;font-weight:600;margin-top:{int(28*r)}px;
  text-wrap:balance;max-width:{int(920*r)}px;}}
.stat .rest{{font-size:{int(32*r)}px;line-height:1.3;color:{muted};margin-top:{int(22*r)}px;}}
.liste{{display:flex;flex-direction:column;gap:{int(18*r)}px;}}
.zeile{{display:flex;align-items:flex-start;gap:{int(26*r)}px;
  padding:{int(26*r)}px {int(30*r)}px;border-radius:{radius}px;
  background:{surface};border:1px solid {linie};
  font-size:{int(46*r)}px;line-height:1.2;font-weight:{500 if k.get('serif') else 600};}}
.zeile .nr{{flex:none;width:{int(64*r)}px;height:{int(64*r)}px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  background:{akzent};color:#fff;font-size:{int(30*r)}px;font-weight:700;margin-top:{int(-4*r)}px;}}
.zeile .mk{{flex:none;width:{int(64*r)}px;height:{int(64*r)}px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;font-size:{int(34*r)}px;font-weight:800;
  margin-top:{int(-4*r)}px;color:#fff;}}
.zeile.plus .mk{{background:{k['gut']};}}
.zeile.minus .mk{{background:{k['grenze']};}}
.zeile.minus{{color:{muted};}}
.zeile.minus span.t{{text-decoration:none;}}
.spalten{{display:grid;grid-template-columns:1fr 1fr;gap:{int(28*r)}px;align-items:stretch;}}
.sp{{border-radius:{radius}px;padding:{int(44*r)}px {int(40*r)}px;
  border:1px solid {linie};background:{surface};min-height:{int(520*r)}px;
  display:flex;flex-direction:column;gap:{int(26*r)}px;}}
.sp.stark{{border-color:{akzent};box-shadow:0 0 0 {int(4*r)}px {_hex_rgba(akzent, .18)};}}
.sp h4{{font-size:{int(26*r)}px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;
  color:{muted};}}
.sp.stark h4{{color:{akzent};}}
.sp p{{font-size:{int(52*r)}px;line-height:1.14;font-weight:{500 if k.get('serif') else 700};
  letter-spacing:{'0' if k.get('serif') else '-.02em'};text-wrap:balance;}}
.sp.schwach p{{color:{muted};}}
.sp .mk{{width:{int(58*r)}px;height:{int(58*r)}px;border-radius:50%;display:flex;
  align-items:center;justify-content:center;color:#fff;font-size:{int(30*r)}px;font-weight:800;}}
.sp.schwach .mk{{background:{k['grenze']};}}
.sp.stark .mk{{background:{k['gut']};}}
.cta .pille{{display:inline-flex;align-items:center;gap:{int(18*r)}px;margin-top:{int(56*r)}px;
  padding:{int(26*r)}px {int(44*r)}px;border-radius:999px;background:{akzent};color:#fff;
  font-size:{int(40*r)}px;font-weight:700;letter-spacing:-.01em;}}
.fuss{{border-top:1px solid {linie};padding-top:{int(28*r)}px;
  font-size:{int(26*r)}px;color:{muted};font-weight:500;}}
.fuss .weiter{{display:flex;align-items:center;gap:{int(12*r)}px;color:{tinte};font-weight:600;}}
.fuss .weiter b{{display:inline-flex;width:{int(44*r)}px;height:{int(44*r)}px;border-radius:50%;
  background:{akzent};color:#fff;align-items:center;justify-content:center;
  font-size:{int(24*r)}px;font-weight:800;}}
"""


def _zahl_label(zeilen: list) -> tuple:
    """stat: erste Zeile Zahl, zweite Label. Steht beides in einer Zeile
    ('12 Kontaktpunkte'), trennt das Kit-Werkzeug."""
    if len(zeilen) >= 2:
        return zeilen[0], zeilen[1], zeilen[2:]
    zahl, einheit = kit._zahl_teilen(zeilen[0] if zeilen else "")
    return (zahl or zeilen[0] if zeilen else ""), (einheit if zahl else ""), []


def _mitte(art: str, zeilen: list, kicker: str, k: dict) -> tuple:
    """(html der Mitte, dunkel?)"""
    kick = f"<div class='kicker'>{_e(kicker)}</div>" if kicker else ""
    if art == "titel":
        return (f"<div class='titel'>{kick}<div class='gross'>{_e(' '.join(zeilen))}</div>"
                f"<div class='strich'></div></div>", False)
    if art == "zitat":
        kopf = kick or "<span class='anf'>Kurz gesagt</span>"
        return (f"<div class='zitat'>{kopf}"
                f"<div class='mittel'>{_e(' '.join(zeilen))}</div></div>", False)
    if art == "cta":
        return (f"<div class='cta'>{kick}<div class='mittel'>{_e(' '.join(zeilen))}</div>"
                f"<div class='pille'>{_e(k.get('_marke') or '')}<span>→</span></div></div>", True)
    if art == "stat":
        zahl, label, rest = _zahl_label(zeilen)
        return (f"<div class='stat'>{kick}<div class='zahl'>{_e(zahl)}</div>"
                + (f"<div class='label'>{_e(label)}</div>" if label else "")
                + (f"<div class='rest'>{_e(' '.join(rest))}</div>" if rest else "")
                + "</div>", False)
    if art == "befund":
        html = ""
        for z in zeilen[:5]:
            m = re.match(r"^\s*([+\-–−✓✗x])\s*(.*)$", z)
            plus = bool(m) and m.group(1) in "+✓"
            text = m.group(2) if m else z
            klasse = "plus" if plus else ("minus" if m else "")
            mk = ("✓" if plus else "✕") if m else ""
            html += (f"<div class='zeile {klasse}'>"
                     + (f"<span class='mk'>{mk}</span>" if mk else "")
                     + f"<span class='t'>{_e(text)}</span></div>")
        return f"<div class='befund'>{kick}<div class='liste'>{html}</div></div>", False
    if art == "ablauf":
        html = "".join(
            f"<div class='zeile'><span class='nr'>{i}</span><span class='t'>{_e(z)}</span></div>"
            for i, z in enumerate(zeilen[:5], 1))
        return f"<div class='ablauf'>{kick}<div class='liste'>{html}</div></div>", False
    if art == "vergleich":
        links = zeilen[0] if zeilen else ""
        rechts = zeilen[1] if len(zeilen) > 1 else ""
        return (f"<div class='vergleich'>{kick}<div class='spalten'>"
                f"<div class='sp schwach'><span class='mk'>✕</span><p>{_e(links)}</p></div>"
                f"<div class='sp stark'><span class='mk'>✓</span><p>{_e(rechts)}</p></div>"
                "</div></div>", False)
    # Unbekannte Art: wie Titel setzen, nichts verschlucken.
    return (f"<div class='titel'>{kick}<div class='gross'>{_e(' '.join(zeilen))}</div>"
            f"<div class='strich'></div></div>", False)


def seite(art: str, felder: dict, client_id: str, marke: str, nr: int, gesamt: int,
          breit: int = 1200, hoch: int = 1500, wende: bool = False) -> str:
    """Eine fertige HTML-Seite. nr ist 1-basiert; gesamt 0 heisst: ohne Zaehler
    (das Einzelbild zum Textpost)."""
    k = dict(kit.kit_fuer(client_id, wende))
    k["_marke"] = marke or (client_id or "").capitalize()
    zeilen = [str(z).strip() for z in (felder.get("zeilen") or []) if str(z).strip()]
    art = (art or "titel").strip().lower()
    mitte, dunkel = _mitte(art, zeilen, str(felder.get("kicker") or ""), k)
    letzte = gesamt and nr >= gesamt
    zaehler = f"<div class='seite'>{nr} / {gesamt}</div>" if gesamt else ""
    fuss_rechts = ("" if (letzte or not gesamt)
                   else "<div class='weiter'>weiter<b>→</b></div>")
    fuss_links = _e(k["_marke"]) if not letzte else "Danke fürs Lesen"
    return ("<!doctype html><html lang='de'><head><meta charset='utf-8'><style>"
            + _css(k, breit, hoch, dunkel) + "</style></head><body>"
            f"<div class='blatt'>"
            f"<div class='kopf'><div class='marke'><i></i>{_e(k['_marke'])}</div>{zaehler}</div>"
            f"<div class='mitte'>{mitte}</div>"
            f"<div class='fuss'><span>{fuss_links}</span>{fuss_rechts}</div>"
            "</div></body></html>")
