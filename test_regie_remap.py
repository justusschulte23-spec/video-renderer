"""Gegentest fuer das Remapping der Regie-Hinweise gegen das ECHTE Transkript.

Zieht die Funktionen aus main.py heraus (ohne die FastAPI-App zu starten) und
prueft die eine Zusage: was im Skript stand, aber nicht gesprochen wurde,
faellt weg — es wird nicht geraten.
"""
import io
import re
import sys
import logging

SRC = io.open("main.py", encoding="utf-8").read()


def _schnitt(name: str) -> str:
    i = SRC.index(f"def {name}(")
    j = SRC.index("\ndef ", i + 1)
    return SRC[i:j]


_GAP = float(re.search(r"REMOTION_PUNCH_GAP_S\s*=\s*([0-9.]+)", SRC).group(1))
ns = {"re": re, "FPS": 30, "log": logging.getLogger("t"), "Optional": None,
      "REMOTION_PUNCH_GAP_S": _GAP}
ns["Optional"] = __import__("typing").Optional
for fn in ("_norm_token", "_find_word_time", "_block_grenzen", "_briefing_props"):
    exec(_schnitt(fn), ns)

_briefing_props = ns["_briefing_props"]
_block_grenzen = ns["_block_grenzen"]


def worte(satz: str, start: float = 0.0, takt: float = 0.4):
    """Wort-Transkript wie WhisperX es liefert."""
    return [{"word": w, "start": start + i * takt, "end": start + (i + 1) * takt}
            for i, w in enumerate(satz.split())]


# Er hat NICHT gesagt, was im Skript stand: "Credit-Limit" fehlt komplett,
# und den CTA hat er verschluckt.
GESPROCHEN = worte(
    "Ich hab vierzig Euro im Monat fuer Instantly gezahlt und irgendwann "
    "gemerkt dass die Kampagne komplett stillstand "
    "dann hab ich mir das Dashboard angeschaut und alles selber nachgebaut "
    "seitdem geht jede Mail wieder raus"
)
DAUER = GESPROCHEN[-1]["end"] + 0.5

HINTS = {
    "blocks": [
        {"rolle": "hook", "text": "Vierzig Euro im Monat verbrannt", "dauer_sek": 3},
        {"rolle": "szene", "text": "Ich hab Instantly gezahlt und die Kampagne stillstand", "dauer_sek": 8},
        {"rolle": "wendung", "text": "Dashboard angeschaut und selber nachgebaut", "dauer_sek": 6},
        # dieser Block wurde NIE gesprochen
        {"rolle": "mitnahme", "text": "Pruef dein Credit-Limit in den Einstellungen", "dauer_sek": 5},
        {"rolle": "cta", "text": "Schreib mir SETUP", "dauer_sek": 3},
    ],
    "punch_words": [
        {"rolle": "szene", "wort": "Instantly"},        # gesprochen -> bleibt
        {"rolle": "wendung", "wort": "nachgebaut"},     # gesprochen -> bleibt
        {"rolle": "mitnahme", "wort": "Credit-Limit"},  # NICHT gesprochen -> weg
    ],
    "lower_thirds": [
        {"rolle": "szene", "title": "Instantly", "subtitle": "40 EUR/Monat"},
        # Block nicht gesprochen -> keine Einblendung
        {"rolle": "mitnahme", "title": "Credit-Limit", "subtitle": "pruefen"},
    ],
    "cta_keyword": "SETUP",   # nicht gesprochen -> weg
}


def main():
    grenzen = _block_grenzen(HINTS, GESPROCHEN, DAUER)
    punch, lowers = _briefing_props(None, GESPROCHEN, 1.0, DAUER, hints=HINTS)

    fails = 0

    def pruef(bedingung, text):
        nonlocal fails
        if not bedingung:
            fails += 1
        print(("OK  " if bedingung else "FAIL") + "  " + text)

    pruef("mitnahme" not in grenzen,
          "nicht gesprochener Block bekommt keine Zeit (wird nicht geraten)")
    # Er hat den CTA verschluckt — auch der Block bekommt keine Zeit.
    pruef("cta" not in grenzen, "verschluckter CTA-Block bekommt keine Zeit")
    pruef(len(grenzen) == 3,
          f"3 von 5 Bloecken wiedergefunden, 2 verschluckt (ist: {len(grenzen)})")
    pruef(len(punch) == 2, f"2 Punch-Ins statt 3+CTA (ist: {len(punch)})")
    pruef(len(lowers) == 1, f"1 Einblendung statt 2 (ist: {len(lowers)})")
    if lowers:
        pruef(lowers[0]["title"] == "Instantly", "die verbliebene Einblendung ist die gesprochene")
        pruef(lowers[0]["endFrame"] > lowers[0]["startFrame"], "Einblendung hat positive Laenge")
    # Reihenfolge: Bloecke duerfen sich nicht ueberholen
    zeiten = [grenzen[r][0] for r in ("hook", "szene", "wendung", "cta") if r in grenzen]
    pruef(zeiten == sorted(zeiten), "Blockgrenzen laufen vorwaerts")

    print("\ngefundene Grenzen:", {k: round(v[0], 1) for k, v in grenzen.items()})
    print("FEHLER:", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
