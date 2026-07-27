"""Render-Engine: die synchronisierte B-Roll nicht mehr in einer offenen
Verbindung abwarten, sondern pollen — wie der Render daneben es laengst tut.

Grund (Execution 566): /generate-broll-synced hielt die Verbindung ueber vier
Minuten ohne ein Antwort-Byte (ein Sonnet-Call ueber 32k Token fuer neun
Szenen). Bei exakt 179,7s kam `Error: aborted`, obwohl der Node-Timeout auf
900s stand. Der Server hat die B-Roll danach fertiggebaut und hochgeladen —
n8n hatte da schon aufgegeben.

Baut dieselbe Schleife wie fuer den Render: Wait -> Status -> Done? -> Fehler?
"""
import uuid
from build_kalle import api

RENDER = "qw3X2odQ8FpUeLJP"
RENDERER = "https://video-renderer-production-5f0a.up.railway.app"
NEU = {"Wait B-Roll", "Check B-Roll Status", "B-Roll Done?", "B-Roll Fehler?"}


def nid():
    return str(uuid.uuid4())


def main():
    st, wf = api("GET", f"/workflows/{RENDER}")
    if st >= 400:
        raise SystemExit(wf)

    # idempotent: einen frueheren Lauf erst rausschneiden
    nodes = [n for n in wf["nodes"] if n["name"] not in NEU]
    conns = {k: v for k, v in wf["connections"].items() if k not in NEU}

    nodes.append({"id": nid(), "name": "Wait B-Roll", "type": "n8n-nodes-base.wait",
                  "typeVersion": 1, "position": [-40, 700],
                  "parameters": {"amount": 20, "unit": "seconds"}})
    nodes.append({"id": nid(), "name": "Check B-Roll Status",
                  "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
                  "position": [180, 700],
                  "parameters": {"url": "=" + RENDERER + "/render-status/"
                                        "{{ $('Generate B-Roll').first().json.job_id }}",
                                 "options": {}}})

    def iff(name, pos, conds, comb="and"):
        return {"id": nid(), "name": name, "type": "n8n-nodes-base.if",
                "typeVersion": 2, "position": pos,
                "parameters": {"conditions": {
                    "options": {"caseSensitive": True, "typeValidation": "loose",
                                "version": 2},
                    "conditions": conds, "combinator": comb}, "options": {}}}

    nodes.append(iff("B-Roll Done?", [400, 700], [
        {"id": "bd1", "leftValue": "={{ $json.status }}", "rightValue": "done",
         "operator": {"type": "string", "operation": "equals"}}]))
    # Abbruch nach ~13 Minuten (40 Runden x 20s) statt endlos zu kreisen
    nodes.append(iff("B-Roll Fehler?", [400, 880], [
        {"id": "be1", "leftValue": "={{ $json.status }}", "rightValue": "error",
         "operator": {"type": "string", "operation": "equals"}},
        {"id": "be2", "leftValue": "={{ $runIndex }}", "rightValue": 40,
         "operator": {"type": "number", "operation": "gte"}}], comb="or"))

    def m(*ziele):
        return {"main": [[{"node": z, "type": "main", "index": 0}] if z else []
                         for z in ziele]}

    conns["Generate B-Roll"] = m("Wait B-Roll")
    conns["Wait B-Roll"] = m("Check B-Roll Status")
    conns["Check B-Roll Status"] = m("B-Roll Done?")
    conns["B-Roll Done?"] = m("Final-Payload-Railway-Render1", "B-Roll Fehler?")
    conns["B-Roll Fehler?"] = m("Notify Fail", "Wait B-Roll")

    ALLOWED = {"saveExecutionProgress", "saveManualExecutions", "saveDataErrorExecution",
               "saveDataSuccessExecution", "executionTimeout", "errorWorkflow",
               "timezone", "executionOrder"}
    settings = {k: v for k, v in (wf.get("settings") or {}).items() if k in ALLOWED}
    settings.setdefault("executionOrder", "v1")
    st, res = api("PUT", f"/workflows/{RENDER}",
                  {"name": wf["name"], "nodes": nodes, "connections": conns,
                   "settings": settings})
    print("PUT", st)
    if st >= 400:
        raise SystemExit(res)
    print("activate", api("POST", f"/workflows/{RENDER}/activate")[0])

    st, d = api("GET", f"/workflows/{RENDER}")
    namen = {n["name"] for n in d["nodes"]}
    c = d["connections"]
    print("published:", d.get("versionId") == d.get("activeVersionId"),
          "| Nodes da:", NEU <= namen,
          "| Kette:", [[e["node"] for e in (l or [])]
                       for l in c.get("B-Roll Done?", {}).get("main", [])])


if __name__ == "__main__":
    main()
