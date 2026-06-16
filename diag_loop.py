"""
Autonomous broll-animation test loop.
Imports the REAL _build_broll_html + _inject_gsap_inline from main.py
(1:1 with production) and renders a representative Sonnet-style scene with:
  - animateCounter (the big number)
  - a self-drawing SVG line graph (stroke-dashoffset)
  - moving artifacts (ambient glow pulse + rotating ring)
Then evaluates 4 pass criteria at t=0.3 / 1.5 / 2.5s.

Usage: python diag_loop.py [--no-script]   (--no-script simulates Sonnet truncation)
"""
import os, sys, ast, shutil, asyncio
from pathlib import Path

FONT_DIR = Path("/tmp/fonts")
FONT_DIR.mkdir(parents=True, exist_ok=True)
GSAP_DST = FONT_DIR / "gsap.min.js"
if not GSAP_DST.exists():
    shutil.copy("C:/tmp_diag/gsap.min.js", GSAP_DST)

# ── extract the REAL pure functions from main.py (exact source, no heavy imports) ──
_src   = Path(__file__).with_name("main.py").read_text(encoding="utf-8")
_lines = _src.splitlines(keepends=True)
_tree  = ast.parse(_src)
BROLL_H = 622

class _M:  # namespace holder so test code reads like main.<fn>
    pass
main = _M()
_ns = {"re": __import__("re"), "Path": Path, "BROLL_H": BROLL_H, "GSAP_LOCAL": GSAP_DST}
_wanted = {"_inject_gsap_inline", "_safe_wrap_scripts", "_build_broll_html"}
for _node in ast.walk(_tree):
    if isinstance(_node, ast.FunctionDef) and _node.name in _wanted:
        exec("".join(_lines[_node.lineno - 1:_node.end_lineno]), _ns)
main._build_broll_html   = _ns["_build_broll_html"]
main._inject_gsap_inline = _ns["_inject_gsap_inline"]
main.BROLL_H = BROLL_H

OUT = Path("C:/tmp_diag")
OUT.mkdir(exist_ok=True)

# ── A representative Sonnet-style scene (counter + self-drawing graph + artifacts) ──
SCENE_DIVS = """
<div class="scene" id="scene0" style="background:#0a0910;font-family:'SF Mono','Consolas',monospace;overflow:hidden;">
  <div style="position:absolute;inset:0;pointer-events:none;background:repeating-linear-gradient(0deg,transparent,transparent 3px,rgba(0,0,0,0.06) 3px,rgba(0,0,0,0.06) 4px);z-index:10;"></div>
  <div class="glow" id="g1" style="position:absolute;width:600px;height:600px;top:-150px;left:-120px;border-radius:50%;background:radial-gradient(circle,rgba(139,92,246,0.18) 0%,transparent 65%);filter:blur(30px);"></div>
  <svg id="heroSvg" viewBox="0 0 180 180" width="180" height="180" style="filter:drop-shadow(0 0 20px #8B5CF6);">
    <circle id="ring1" cx="90" cy="90" r="60" fill="none" stroke="#8B5CF6" stroke-width="3" stroke-dasharray="6 10"/>
    <circle id="ring2" cx="90" cy="90" r="40" fill="none" stroke="#06B6D4" stroke-width="2" stroke-dasharray="3 8"/>
  </svg>
  <div class="dp" id="counter0">0</div>
  <div class="lbl">Milliarden USD Deal-Volumen</div>
  <svg id="graphSvg" viewBox="0 0 400 120" width="400" height="120">
    <path id="gl" d="M 0,110 L 60,90 L 120,82 L 180,60 L 240,48 L 300,30 L 360,18 L 400,8"
          fill="none" stroke="#06B6D4" stroke-width="3" stroke-linecap="round"/>
  </svg>
</div>
"""

SONNET_SCRIPT = """
<script>
// ambient glow pulse (immediate)
gsap.to("#g1",{x:40,y:30,scale:1.2,duration:6,repeat:-1,yoyo:true,ease:"sine.inOut"});
// rotating hero rings (immediate)
gsap.to("#ring1",{rotation:360,duration:14,repeat:-1,ease:"linear",transformOrigin:"90px 90px"});
gsap.to("#ring2",{rotation:-360,duration:18,repeat:-1,ease:"linear",transformOrigin:"90px 90px"});
gsap.delayedCall(0, function(){
  // counter
  animateCounter(document.getElementById("counter0"), 60, 2.2, " Mrd");
  // self-drawing graph line with the mandated null-guard
  var gl = document.getElementById("gl");
  if (gl) {
    var len = gl.getTotalLength();
    gsap.set(gl, {strokeDasharray: len, strokeDashoffset: len});
    gsap.to(gl, {strokeDashoffset: 0, duration: 2.2, ease: "power2.out"});
  }
});
</script>
"""


async def evaluate(name: str, scene_divs: str, with_script: bool, ext_script: str = None):
    from playwright.async_api import async_playwright

    if ext_script is not None:
        appended = ext_script
    elif with_script:
        appended = SONNET_SCRIPT
    else:
        appended = ""
    full_raw = main._build_broll_html(
        scene_divs + appended,
        scenes=[{"start": 0.0, "end": 6.0}],
        accent="#8B5CF6",
    )
    injected = main._inject_gsap_inline(full_raw)
    html_path = OUT / f"loop_{name}.html"
    html_path.write_text(injected, encoding="utf-8")

    console = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
        page = await browser.new_page(viewport={"width": 1080, "height": main.BROLL_H})
        page.on("console", lambda m: console.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: console.append(f"[PAGEERROR] {e}"))
        await page.goto(f"file://{html_path.absolute()}", wait_until="load")

        await page.evaluate("""() => { if(window.gsap){
            gsap.globalTimeline.pause(); gsap.globalTimeline.seek(0); gsap.globalTimeline.play(); } }""")

        tweens = await page.evaluate(
            "() => window.gsap ? gsap.globalTimeline.getChildren(true,true,true).length : -1")

        async def snap(t):
            return await page.evaluate("""() => {
                var c = document.getElementById('counter0');
                var gl = document.getElementById('gl');
                var r1 = document.getElementById('ring1');
                var g1 = document.getElementById('g1');
                return {
                    counter: c ? c.textContent : 'NONE',
                    dashoffset: gl ? getComputedStyle(gl).strokeDashoffset : 'NONE',
                    ringTransform: r1 ? getComputedStyle(r1).transform : 'NONE',
                    glowTransform: g1 ? getComputedStyle(g1).transform : 'NONE'
                };
            }""")

        await asyncio.sleep(0.3); s03 = await snap(0.3)
        await page.screenshot(path=str(OUT / f"loop_{name}_t03.png"))
        await asyncio.sleep(1.2); s15 = await snap(1.5)
        await asyncio.sleep(1.0); s25 = await snap(2.5)
        await page.screenshot(path=str(OUT / f"loop_{name}_t25.png"))
        await browser.close()

    # ── criteria ──
    def num(s):
        import re
        m = re.search(r"\d+", s or "")
        return int(m.group()) if m else -1

    c03, c25 = num(s03["counter"]), num(s25["counter"])
    a = c03 >= 0 and c25 > c03 and c25 > 0             # counter visibly counts up
    do03, do25 = num(s03["dashoffset"]), num(s25["dashoffset"])
    b = do03 > do25 and do25 <= 5                      # graph draws (dashoffset → 0)
    c = (s03["ringTransform"] != s25["ringTransform"]) or (s03["glowTransform"] != s25["glowTransform"])
    d = not any("TypeError" in l or "null" in l or "PAGEERROR" in l for l in console)

    print(f"\n{'='*64}\nSCENARIO {name}  (with_script={with_script})\n{'='*64}")
    print(f"  tweens registered : {tweens}")
    print(f"  counter   t0.3={s03['counter']!r}  t1.5={s15['counter']!r}  t2.5={s25['counter']!r}")
    print(f"  dashoffset t0.3={s03['dashoffset']!r}  t2.5={s25['dashoffset']!r}")
    print(f"  ringXform changed : {s03['ringTransform'] != s25['ringTransform']}")
    print(f"  glowXform changed : {s03['glowTransform'] != s25['glowTransform']}")
    print(f"  console ({len(console)}):")
    for l in console:
        print(f"     {l}")
    print(f"\n  [a] counter counts up      : {'PASS' if a else 'FAIL'}")
    print(f"  [b] graph draws itself     : {'PASS' if b else 'FAIL'}")
    print(f"  [c] artifacts move         : {'PASS' if c else 'FAIL'}")
    print(f"  [d] no TypeError/null      : {'PASS' if d else 'FAIL'}")
    allpass = a and b and c and d
    print(f"\n  ==> {'ALL PASS' if allpass else 'FAIL'}")
    return allpass


async def main_run():
    if "--no-script" in sys.argv:
        await evaluate("truncated", SCENE_DIVS, with_script=False)
    elif "--from-json" in sys.argv:
        import json
        idx = sys.argv.index("--from-json")
        path = sys.argv[idx + 1]
        d = json.load(open(path, encoding="utf-8"))
        script = d["script"]
        print(f"[from-json] using real Sonnet 2nd-call script ({len(script)} chars)")
        await evaluate("real2ndcall", SCENE_DIVS, with_script=False, ext_script=script)
    else:
        await evaluate("full", SCENE_DIVS, with_script=True)


if __name__ == "__main__":
    asyncio.run(main_run())
