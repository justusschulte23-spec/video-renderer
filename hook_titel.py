"""Hook-Titel (03.10.): die Hook-Einblendung ist die wichtigste Grafik im Video, der Skip
faellt in den ersten drei Sekunden. Bisher stand dort ein Transkript-Bruchstueck im weissen
Streifen ("Kunden und das ohne Werbebudget", Skript 100). Jetzt: der Bildtext aus dem
Skriptbau (drei bis fuenf Woerter, vom Autor gesetzt) als freie Typografie, ohne Kasten,
ohne Freisteller (Justus: "sieht nach billigem Greenscreen aus"). Paper-Cut-Buchstaben mit
Korn und hartem Schlagschatten, ein Schluesselwort auf einem gerissenen Papier-Tag in der
Akzentfarbe, Woerter stempeln einzeln, alles steht nach 0,6 s.
"""
import html
import random
import re


def _e(t):
    return html.escape(str(t), quote=False)


def schluesselwort(text):
    """Das Wort, das den Tag bekommt: die erste Zahl, sonst das laengste Nomen, sonst das
    laengste Wort. Zahlen tragen den Hook (die-formel: Zahl, Name, Verlust im ersten Satz)."""
    worte = [w.strip(" ,.;:!?\"'„“()=") for w in str(text).split()]
    worte = [w for w in worte if w]
    zahl = [w for w in worte if re.search(r"\d", w)]
    if zahl:
        return zahl[0]
    nomen = [w for w in worte if w[:1].isupper() and len(w) > 3]
    kand = nomen or [w for w in worte if len(w) > 3] or worte
    return max(kand, key=len) if kand else ""


def _groesse(worte, breit, hoch, max_zeilen=2):
    """Groesste Schrift, bei der der Text in hoechstens zwei Zeilen in Breite und Hoehe
    passt. Zeichenbreite 0,62 em (Inter 900, Versalien), mit Luft."""
    fs = 48
    for px in range(int(breit * 0.20), 47, -2):
        je_zeile = max(1, int((breit * 0.92) / (px * 0.62)))
        if max(len(w) for w in worte) > je_zeile:
            continue
        zeilen, laenge = 1, 0
        for w in worte:
            neu = laenge + (1 if laenge else 0) + len(w)
            if neu > je_zeile:
                zeilen, laenge = zeilen + 1, len(w)
            else:
                laenge = neu
        if zeilen <= max_zeilen and zeilen * px * 1.02 + px * 0.5 <= hoch * 0.92:
            fs = px
            break
    return fs


def markup(text, k, breit, hoch, sekunden=3.0, seed=0):
    rnd = random.Random(seed)
    text = " ".join(str(text).split())
    worte = text.split()
    if not worte:
        return ""
    key = schluesselwort(text)
    fs = _groesse(worte, breit, hoch)
    papier = k.get("surface") or "#FFFFFF"
    tinte = k.get("text") or "#111111"
    akzent = k.get("akzent") or "#6D28D9"
    # Satzzeichen trennen Gedanken: "Eine Linie. Ein Kunde." bricht nach dem Punkt.
    spans, delay = [], 0.0
    for j, w in enumerate(worte):
        roh = w.strip(" ,.;:!?\"'„“()=")
        rot = rnd.uniform(-2.2, 2.2)
        if key and roh == key:
            zo = " ".join("%d%% %.1f%%" % (x, rnd.uniform(0, 10)) for x in range(0, 101, 5))
            zu = " ".join("%d%% %.1f%%" % (x, 100 - rnd.uniform(0, 10)) for x in range(100, -1, -5))
            inner = ("<span class='tag' style='--rot:%.1fdeg'><span class='tagpapier' style='clip-path:polygon(%s, %s)'></span>"
                     "<span class='tagtext'>%s</span></span>" % (rnd.uniform(-3, 3), zo, zu, _e(w)))
        else:
            inner = "<span class='buchst'>%s</span>" % _e(w)
        spans.append("<span class='w' style='animation-delay:%.2fs;--rot:%.1fdeg'>%s</span>" % (delay, rot, inner))
        delay += 0.11
        if w.endswith((".", "!", "?", ":")) and j < len(worte) - 1:
            spans.append("<br>")
    raus_ab = max(0.4, sekunden - 0.35)
    css = f"""
    .wrap{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
           animation:raus .35s {raus_ab:.2f}s ease-in both;}}
    .satz{{font-size:{fs}px;line-height:1.02;font-weight:900;letter-spacing:-0.025em;text-transform:uppercase;
           text-align:center;max-width:{int(breit*0.94)}px;color:{papier};
           hyphens:none;word-break:normal;overflow-wrap:normal;}}
    .w{{display:inline-block;margin:0 .14em;opacity:0;transform-origin:50% 70%;
        animation:stempel .34s cubic-bezier(.2,1.5,.4,1) both;}}
    .buchst{{display:inline-block;
        text-shadow:0 1px 0 rgba(0,0,0,.18), 0 3px 0 {tinte}, 0 5px 0 {tinte}, 0 7px 0 {tinte},
                    0 12px 24px rgba(0,0,0,.35);
        filter:url(#korn);}}
    .tag{{position:relative;display:inline-block;padding:.04em .22em .08em;transform:rotate(var(--rot));
          filter:drop-shadow(0 10px 18px rgba(0,0,0,.35));}}
    .tagpapier{{position:absolute;inset:-.06em -.04em;background:{akzent};}}
    .tagpapier::after{{content:'';position:absolute;inset:0;opacity:.28;mix-blend-mode:multiply;
      background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 .3  0 0 0 0 .3  0 0 0 0 .3  0 0 0 .6 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>");}}
    .tagtext{{position:relative;color:{papier};text-shadow:0 2px 0 rgba(0,0,0,.25);}}
    @keyframes stempel{{from{{opacity:0;transform:scale(1.7) rotate(var(--rot)) translateY(-.08em)}}
                        60%{{opacity:1}}
                        to{{opacity:1;transform:scale(1) rotate(var(--rot))}}}}
    @keyframes raus{{to{{opacity:0;transform:translateY(-14px) scale(.98)}}}}"""
    body = ("<svg width='0' height='0' style='position:absolute'><filter id='korn'>"
            "<feTurbulence type='fractalNoise' baseFrequency='.8' numOctaves='2' result='n'/>"
            "<feDisplacementMap in='SourceGraphic' in2='n' scale='1.6'/></filter></svg>"
            "<div class='wrap'><div class='satz'>%s</div></div>" % "".join(spans))
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
    html,body{{margin:0;padding:0;width:{breit}px;height:{hoch}px;overflow:hidden;background:transparent;}}
    *{{box-sizing:border-box;}}
    body{{font-family:{k['font']};-webkit-font-smoothing:antialiased;}}
    .buehne{{position:absolute;left:0;top:0;width:{breit}px;height:{hoch}px;}}
    {css}
    </style></head><body><div class="buehne">{body}</div></body></html>"""
