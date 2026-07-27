"""Render-Engine: regie_hints aus used_topics mitlesen und an den Renderer geben.

Die Regie-Daten (punch_words, lower_thirds, cta_keyword, Blockgrenzen) gehen
direkt aus der Queue in den Render — nie ueber den Kunden-Chat. Der Renderer
remappt sie danach gegen das echte WhisperX-Transkript.

Aendert GENAU zwei Nodes.
"""
from build_kalle import api

RENDER = "qw3X2odQ8FpUeLJP"
LOOKUP = "Supabase Lookup Latest Topic1"
PAYLOAD = "Final-Payload-Railway-Render1"

ZUSATZ = """
// Regie-Hinweise mitgeben: sie steuern Punch-In, Einblendung und CTA-Wort.
// Der Renderer prueft jeden Hinweis gegen das echte Transkript und wirft weg,
// was nicht gesprochen wurde — hier wird nichts vorentschieden.
if (nutzbar && t.regie_hints) {
  payload.regie_hints = t.regie_hints;
  payload.briefing.mitnahme = s.mitnahme || '';
  payload.briefing.segments = ((t.production_briefing || {}).segments) || [];
}
"""


def main():
    st, wf = api("GET", f"/workflows/{RENDER}")
    if st >= 400:
        raise SystemExit(wf)

    lookup = next(n for n in wf["nodes"] if n["name"] == LOOKUP)
    url = lookup["parameters"]["url"]
    if "regie_hints" not in url:
        lookup["parameters"]["url"] = url.replace(
            "production_briefing", "production_briefing,regie_hints", 1)
        print("lookup: regie_hints ergaenzt")
    else:
        print("lookup: schon drin")

    pay = next(n for n in wf["nodes"] if n["name"] == PAYLOAD)
    js = pay["parameters"]["jsCode"]
    if "payload.regie_hints" in js:
        print("payload: schon gepatcht")
    else:
        marker = "return [{ json: Object.assign({}, payload, {"
        if marker not in js:
            raise SystemExit("Rueckgabezeile nicht gefunden — Node hat sich geaendert")
        pay["parameters"]["jsCode"] = js.replace(marker, ZUSATZ + "\n" + marker, 1)
        print("payload: regie_hints ergaenzt")

    ALLOWED = {"saveExecutionProgress", "saveManualExecutions", "saveDataErrorExecution",
               "saveDataSuccessExecution", "executionTimeout", "errorWorkflow",
               "timezone", "executionOrder"}
    settings = {k: v for k, v in (wf.get("settings") or {}).items() if k in ALLOWED}
    settings.setdefault("executionOrder", "v1")
    st, res = api("PUT", f"/workflows/{RENDER}",
                  {"name": wf["name"], "nodes": wf["nodes"],
                   "connections": wf["connections"], "settings": settings})
    print("PUT", st)
    if st >= 400:
        raise SystemExit(res)
    print("activate", api("POST", f"/workflows/{RENDER}/activate")[0])
    st, d = api("GET", f"/workflows/{RENDER}")
    live = {n["name"]: n for n in d["nodes"]}
    print("published:", d.get("versionId") == d.get("activeVersionId"),
          "| lookup ok:", "regie_hints" in live[LOOKUP]["parameters"]["url"],
          "| payload ok:", "payload.regie_hints" in live[PAYLOAD]["parameters"]["jsCode"])


if __name__ == "__main__":
    main()
