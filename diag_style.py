"""
Test the NEW visual style (brain_chip philosophy) through the REAL pipeline.
Builds a 1080x622-adapted scene (header, hero+brand+flames, pulse rings, glass cards,
big-num with data-count, bottom bar) + CSS keyframes + window.animateCounter, runs it
through the real _build_broll_html + _inject_gsap_inline, renders headless, and checks:
  - #bigNum counts up (low at t=0.5s, high at t=2s)
  - a glass-card has flown in (opacity ~1) and has a CSS animation
  - a flame element has a running CSS animation (flicker)
"""
import os, sys, ast, shutil, asyncio
from pathlib import Path

FONT_DIR = Path("/tmp/fonts"); FONT_DIR.mkdir(parents=True, exist_ok=True)
GSAP_DST = FONT_DIR / "gsap.min.js"
if not GSAP_DST.exists():
    shutil.copy("C:/tmp_diag/gsap.min.js", GSAP_DST)

_src = Path(__file__).with_name("main.py").read_text(encoding="utf-8")
_lines = _src.splitlines(keepends=True)
_tree = ast.parse(_src)
BROLL_H = 622
_ns = {"re": __import__("re"), "Path": Path, "BROLL_H": BROLL_H, "GSAP_LOCAL": GSAP_DST}
_wanted = {"_inject_gsap_inline", "_safe_wrap_scripts", "_build_broll_html", "_broll_anim_bootstrap"}
for _node in ast.walk(_tree):
    if isinstance(_node, ast.FunctionDef) and _node.name in _wanted:
        exec("".join(_lines[_node.lineno - 1:_node.end_lineno]), _ns)
build = _ns["_build_broll_html"]
inject = _ns["_inject_gsap_inline"]

OUT = Path("C:/tmp_diag"); OUT.mkdir(exist_ok=True)

# A scene in the new style, compressed for 1080x622, tagging the counter with data-count.
SCENE = """
<style>
@keyframes heroFloat{0%,100%{transform:translate(-50%,-50%) translateY(0);}50%{transform:translate(-50%,-50%) translateY(-14px);}}
@keyframes ringExpand{0%{transform:translate(-50%,-50%) scale(0.4);opacity:0.8;}100%{transform:translate(-50%,-50%) scale(2.0);opacity:0;}}
@keyframes cardFlyIn{0%{opacity:0;transform:translateY(30px) scale(0.85);}100%{opacity:1;transform:translateY(0) scale(1);}}
@keyframes cardFloat{0%,100%{transform:translateY(0);}50%{transform:translateY(-8px);}}
@keyframes flameFlicker{0%{transform:scaleY(1) scaleX(1);opacity:0.9;}100%{transform:scaleY(1.3) scaleX(0.85);opacity:0.6;}}
@keyframes liveBlink{0%,100%{opacity:1;}50%{opacity:0.2;}}
@keyframes fadeUp{0%{opacity:0;transform:translateY(20px);}100%{opacity:1;transform:translateY(0);}}
.hdr{position:absolute;top:14px;left:40px;right:40px;display:flex;justify-content:space-between;align-items:center;opacity:0;animation:fadeUp 0.6s ease forwards;z-index:20;}
.tag{font-size:11px;letter-spacing:.2em;color:#8B5CF6;border:1px solid rgba(139,92,246,0.3);padding:5px 14px;border-radius:100px;background:rgba(139,92,246,0.06);}
.live{font-size:11px;color:#06B6D4;display:flex;align-items:center;gap:7px;}
.ldot{width:7px;height:7px;border-radius:50%;background:#06B6D4;animation:liveBlink 1s steps(1) infinite;}
.pring{position:absolute;top:50%;left:50%;width:230px;height:230px;border-radius:50%;border:2px solid rgba(139,92,246,0.5);transform:translate(-50%,-50%) scale(0.4);animation:ringExpand 2.4s ease-out infinite;}
.pring.b{animation-delay:1.2s;border-color:rgba(6,182,212,0.4);width:190px;height:190px;}
.hero{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);animation:heroFloat 5s ease-in-out infinite;}
.gc{position:absolute;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:12px 20px;opacity:0;animation:cardFlyIn 0.7s cubic-bezier(0.175,0.885,0.32,1.275) forwards,cardFloat 3s ease-in-out infinite;animation-delay:0s,0.7s;}
.gl{font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:rgba(192,192,192,0.7);}
.gv{font-size:24px;font-weight:900;font-family:'Arial Black',sans-serif;color:#fff;}
.bottom{position:absolute;bottom:12px;left:40px;right:40px;background:rgba(139,92,246,0.06);border:1px solid rgba(139,92,246,0.2);border-radius:12px;padding:10px 20px;display:flex;justify-content:space-between;opacity:0;animation:fadeUp 0.8s ease forwards;animation-delay:0.9s;}
.bi{text-align:center;}.bv{font-size:16px;font-weight:700;font-family:'Arial Black',sans-serif;}.bl{font-size:9px;color:#666;text-transform:uppercase;}
#bn{font-size:96px;font-weight:900;line-height:1;font-family:'Arial Black',sans-serif;background:linear-gradient(135deg,#fff,#C0C0C0 40%,#8B5CF6);-webkit-background-clip:text;-webkit-text-fill-color:transparent;}
</style>
<div class="scene" id="scene0" style="background:#09090B;font-family:'SF Mono','Consolas',monospace;overflow:hidden;">
  <div style="position:absolute;width:600px;height:600px;top:-200px;left:-150px;background:radial-gradient(circle,rgba(139,92,246,0.14) 0%,transparent 65%);filter:blur(30px);"></div>
  <div class="hdr"><div class="tag">SPACEX · CURSOR AI</div><div class="live"><div class="ldot"></div>LIVE DEAL</div></div>
  <div class="pring"></div><div class="pring b"></div>
  <div class="hero">
    <svg width="170" height="170" viewBox="0 0 260 260" fill="none">
      <path d="M130 30 C155 55 165 110 162 160 L98 160 C95 110 105 55 130 30 Z" fill="#1a1726" stroke="#8B5CF6" stroke-width="3"/>
      <circle cx="130" cy="112" r="20" fill="#0a0910" stroke="#06B6D4" stroke-width="2.5"/>
      <ellipse id="flame1" cx="122" cy="192" rx="8" ry="16" fill="#F59E0B" opacity="0.9" style="transform-origin:center top;animation:flameFlicker 0.15s ease-in-out infinite alternate;"/>
      <ellipse id="flame2" cx="138" cy="192" rx="8" ry="16" fill="#F59E0B" opacity="0.9" style="transform-origin:center top;animation:flameFlicker 0.12s ease-in-out infinite alternate;"/>
      <text x="150" y="60" font-size="20" font-family="monospace" fill="#06B6D4">&lt;/&gt;</text>
      <text x="150" y="76" font-size="10" font-family="monospace" fill="rgba(6,182,212,0.6)">CURSOR</text>
    </svg>
  </div>
  <div class="gc" id="card0" style="top:70px;left:50px;animation-delay:0.3s,1.0s;"><div class="gl">Übernahme</div><div class="gv">$60 Mrd.</div></div>
  <div class="gc" style="top:70px;right:50px;animation-delay:0.5s,1.2s;"><div class="gl">Sofort</div><div class="gv">$10 Mrd.</div></div>
  <div style="position:absolute;bottom:80px;left:0;right:0;text-align:center;opacity:0;animation:fadeUp 0.8s ease forwards;animation-delay:0.4s;">
    <div style="font-size:11px;letter-spacing:.25em;color:#8B5CF6;text-transform:uppercase;">DEAL-VOLUMEN</div>
    <div id="bn" class="big-num" data-count="60">60</div>
    <div style="font-size:14px;color:#06B6D4;letter-spacing:.12em;">MRD. DOLLAR · OPTION</div>
  </div>
  <div class="bottom">
    <div class="bi"><div class="bv" style="color:#8B5CF6;">SpaceX</div><div class="bl">Käufer</div></div>
    <div class="bi"><div class="bv" style="color:#06B6D4;">Cursor</div><div class="bl">Tool</div></div>
    <div class="bi"><div class="bv" style="color:#10B981;">Musk</div><div class="bl">Deal</div></div>
    <div class="bi"><div class="bv" style="color:#F59E0B;">Option</div><div class="bl">später</div></div>
  </div>
</div>
<script>
window.animateCounter = function(el, target, dur, suffix) {
  if (!el) return;
  const start = performance.now();
  const durationMs = dur * 1000;
  function step(now) {
    const p = Math.min((now - start) / durationMs, 1);
    const ease = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(ease * target).toLocaleString('de-DE') + (suffix || '');
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
};
</script>
"""


async def main_run():
    from playwright.async_api import async_playwright
    full = build(SCENE, scenes=[{"start": 0.0, "end": 6.0}], accent="#8B5CF6")
    injected = inject(full)
    p = OUT / "style_test.html"; p.write_text(injected, encoding="utf-8")
    console = []
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        pg = await b.new_page(viewport={"width": 1080, "height": BROLL_H})
        pg.on("console", lambda m: console.append(f"[{m.type}] {m.text}"))
        pg.on("pageerror", lambda e: console.append(f"[PAGEERR] {e}"))
        await pg.goto(f"file://{p.absolute()}", wait_until="load")
        await pg.evaluate("()=>{if(window.gsap){gsap.globalTimeline.pause();gsap.globalTimeline.seek(0);gsap.globalTimeline.play();}}")

        async def snap():
            return await pg.evaluate("""()=>{
                var bn=document.getElementById('bn');
                var c=document.getElementById('card0');
                var f=document.getElementById('flame1');
                var cs=c?getComputedStyle(c):null;
                var fs=f?getComputedStyle(f):null;
                return {
                  counter: bn?bn.textContent:'NONE',
                  cardOpacity: cs?cs.opacity:'NONE',
                  cardAnim: cs?cs.animationName:'NONE',
                  flameAnim: fs?fs.animationName:'NONE'
                };
            }""")

        await asyncio.sleep(0.5); s05 = await snap()
        await pg.screenshot(path=str(OUT / "style_t05.png"))
        await asyncio.sleep(1.5); s20 = await snap()
        await pg.screenshot(path=str(OUT / "style_t20.png"))
        await b.close()

    def num(s):
        import re
        m = re.search(r"\d+", (s or "").replace(".", ""))
        return int(m.group()) if m else -1

    c05, c20 = num(s05["counter"]), num(s20["counter"])
    a = c05 >= 0 and c20 > c05 and c20 >= 55
    b_card = (float(s20["cardOpacity"]) > 0.8) and ("cardFlyIn" in s20["cardAnim"] or "cardFloat" in s20["cardAnim"])
    c_flame = "flameFlicker" in s20["flameAnim"]
    d = not any("Error" in l or "PAGEERR" in l for l in console)

    print("="*60)
    print("NEW STYLE TEST (1080x622, real pipeline)")
    print("="*60)
    print(f"  counter   t0.5={s05['counter']!r}  t2.0={s20['counter']!r}")
    print(f"  cardOpacity t2.0={s20['cardOpacity']!r}  cardAnim={s20['cardAnim']!r}")
    print(f"  flameAnim t2.0={s20['flameAnim']!r}")
    print(f"  console ({len(console)}): {console}")
    print(f"\n  [a] counter counts up (low->~60) : {'PASS' if a else 'FAIL'}")
    print(f"  [b] glass-card flew in + floats  : {'PASS' if b_card else 'FAIL'}")
    print(f"  [c] flame flickers (CSS)         : {'PASS' if c_flame else 'FAIL'}")
    print(f"  [d] no console errors            : {'PASS' if d else 'FAIL'}")
    print(f"\n  ==> {'ALL PASS' if (a and b_card and c_flame and d) else 'FAIL'}")
    print(f"  screenshots: {OUT}/style_t05.png , style_t20.png")


if __name__ == "__main__":
    asyncio.run(main_run())
