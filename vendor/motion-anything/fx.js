/* _fx/shaderbg */
/* shaderbg.js — motion-anything · a tiny dependency-free full-screen fragment-shader runner.
 * Replaces ogl/three for react-bits-style background shaders. Handles both WebGL1
 * (attribute/varying/gl_FragColor) and WebGL2 (#version 300 es / in / out) fragment shaders.
 *
 * Usage:  ShaderBG(container, FRAG, { uniforms:{ uColor:{t:'3f',v:[1,1,1]}, uSpeed:{t:'1f',v:1} } })
 *   - Auto uniforms (set each frame if present): uTime/iTime (seconds), uResolution/iResolution
 *     (vec2 or vec3, auto-detected), uMouse (vec2, 0..1, follows the pointer).
 *   - Renders a static frame under prefers-reduced-motion. Falls back to data-fallback bg if no WebGL. */
(function (g) {
  'use strict';
  function reduced(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function compile(gl, type, src){ var s=gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
    if(!gl.getShaderParameter(s, gl.COMPILE_STATUS)) console.warn('[shaderbg]', gl.getShaderInfoLog(s)); return s; }

  g.ShaderBG = function (el, FRAG, opts) {
    if(!el || el.__sbg) return; el.__sbg = 1; opts = opts || {};
    var isGL2 = /#version\s+300/.test(FRAG);
    var canvas=document.createElement('canvas'); canvas.style.cssText='width:100%;height:100%;display:block'; el.appendChild(canvas);
    var gl = isGL2 ? canvas.getContext('webgl2', {alpha:true, premultipliedAlpha:true, antialias:true}) : null;
    if(!gl) gl = canvas.getContext('webgl', {alpha:true, premultipliedAlpha:true, antialias:true});
    if(!gl){ el.style.background = el.getAttribute('data-fallback') || '#0b0b12'; return; }
    var gl2 = isGL2 && (gl instanceof (g.WebGL2RenderingContext||function(){}));
    var VERT = gl2
      ? '#version 300 es\nin vec2 position;\nin vec2 uv;\nout vec2 vUv;\nvoid main(){ vUv=uv; gl_Position=vec4(position,0.0,1.0); }\n'
      : 'attribute vec2 position;\nattribute vec2 uv;\nvarying vec2 vUv;\nvoid main(){ vUv=uv; gl_Position=vec4(position,0.0,1.0); }\n';
    gl.clearColor(0,0,0,0); gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    var p=gl.createProgram(); gl.attachShader(p, compile(gl,gl.VERTEX_SHADER,VERT)); gl.attachShader(p, compile(gl,gl.FRAGMENT_SHADER,FRAG)); gl.linkProgram(p);
    if(!gl.getProgramParameter(p, gl.LINK_STATUS)){ console.warn('[shaderbg] link', gl.getProgramInfoLog(p)); el.style.background = el.getAttribute('data-fallback') || '#0b0b12'; return; }
    gl.useProgram(p);
    // full-screen triangle: position + matching uv
    var pos=gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER,pos); gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1, 3,-1, -1,3]),gl.STATIC_DRAW);
    var lp=gl.getAttribLocation(p,'position'); gl.enableVertexAttribArray(lp); gl.vertexAttribPointer(lp,2,gl.FLOAT,false,0,0);
    var lu=gl.getAttribLocation(p,'uv'); if(lu>=0){ var uvb=gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER,uvb); gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([0,0, 2,0, 0,2]),gl.STATIC_DRAW); gl.enableVertexAttribArray(lu); gl.vertexAttribPointer(lu,2,gl.FLOAT,false,0,0); }
    // uniform locations
    var U={}; ['uTime','iTime','uResolution','iResolution','uMouse'].forEach(function(n){ U[n]=gl.getUniformLocation(p,n); });
    var resDim3 = /vec3\s+(uResolution|iResolution)/.test(FRAG);
    var custom=[]; var uni=opts.uniforms||{}; Object.keys(uni).forEach(function(n){ var loc=gl.getUniformLocation(p,n); if(loc!=null) custom.push({loc:loc, s:uni[n]}); });
    function setU(c){ var t=c.s.t, v=c.s.v; if(t==='1f') gl.uniform1f(c.loc,v); else if(t==='2f') gl.uniform2f(c.loc,v[0],v[1]); else if(t==='3f') gl.uniform3f(c.loc,v[0],v[1],v[2]); else if(t==='1i') gl.uniform1i(c.loc,v); else if(t==='3fv') gl.uniform3fv(c.loc,new Float32Array(v)); else if(t==='2fv') gl.uniform2fv(c.loc,new Float32Array(v)); else if(t==='1fv') gl.uniform1fv(c.loc,new Float32Array(v)); }
    custom.forEach(setU);
    var mouse=[0.5,0.5]; el.addEventListener('pointermove', function(e){ var r=el.getBoundingClientRect(); mouse=[(e.clientX-r.left)/r.width, 1.0-(e.clientY-r.top)/r.height]; });
    var W=1,H=1; function resize(){ W=Math.max(1,el.offsetWidth||600); H=Math.max(1,el.offsetHeight||360); canvas.width=W; canvas.height=H; gl.viewport(0,0,W,H); }
    g.addEventListener('resize', resize); resize();
    var red=reduced();
    var ts=opts.timeScale||1, lastTime=red?2.0:0;
    function frame(t){ var time = red ? 2.0 : t*0.001*ts; lastTime=time;
      if(U.uTime) gl.uniform1f(U.uTime, time); if(U.iTime) gl.uniform1f(U.iTime, time);
      if(U.uResolution){ resDim3 ? gl.uniform3f(U.uResolution,W,H,1) : gl.uniform2f(U.uResolution,W,H); }
      if(U.iResolution){ resDim3 ? gl.uniform3f(U.iResolution,W,H,1) : gl.uniform2f(U.iResolution,W,H); }
      if(U.uMouse) gl.uniform2f(U.uMouse, mouse[0], mouse[1]);
      gl.clear(gl.COLOR_BUFFER_BIT); gl.drawArrays(gl.TRIANGLES,0,3); if(!red) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
    // Handle for interactive recipes (e.g. click-ripple uniforms): set a custom uniform at runtime.
    return { gl:gl, program:p, canvas:canvas, el:el,
      time:function(){ return lastTime; },
      set:function(name, spec){ var loc=gl.getUniformLocation(p,name); if(loc==null) return; setU({loc:loc, s:spec}); } };
  };
})(window);


/* aurora */
/* aurora.js — motion-anything recipe · category: ambient
 * A living aurora gradient mesh — a GPU fragment shader run in dependency-free raw WebGL2.
 * (Faithful port of the Aurora shader; ogl replaced with plain WebGL2.) Static frame under reduced-motion. */
(function (g) {
  'use strict';
  var VERT = '#version 300 es\nin vec2 position;\nvoid main(){ gl_Position = vec4(position, 0.0, 1.0); }\n';
  var FRAG = '#version 300 es\n\
precision highp float;\n\
uniform float uTime; uniform float uAmplitude; uniform vec3 uColorStops[3]; uniform vec2 uResolution; uniform float uBlend;\n\
out vec4 fragColor;\n\
vec3 permute(vec3 x){ return mod(((x*34.0)+1.0)*x, 289.0); }\n\
float snoise(vec2 v){ const vec4 C = vec4(0.211324865405187,0.366025403784439,-0.577350269189626,0.024390243902439);\n\
  vec2 i=floor(v+dot(v,C.yy)); vec2 x0=v-i+dot(i,C.xx); vec2 i1=(x0.x>x0.y)?vec2(1.0,0.0):vec2(0.0,1.0);\n\
  vec4 x12=x0.xyxy+C.xxzz; x12.xy-=i1; i=mod(i,289.0);\n\
  vec3 p=permute(permute(i.y+vec3(0.0,i1.y,1.0))+i.x+vec3(0.0,i1.x,1.0));\n\
  vec3 m=max(0.5-vec3(dot(x0,x0),dot(x12.xy,x12.xy),dot(x12.zw,x12.zw)),0.0); m=m*m; m=m*m;\n\
  vec3 x=2.0*fract(p*C.www)-1.0; vec3 h=abs(x)-0.5; vec3 ox=floor(x+0.5); vec3 a0=x-ox;\n\
  m*=1.79284291400159-0.85373472095314*(a0*a0+h*h);\n\
  vec3 gg; gg.x=a0.x*x0.x+h.x*x0.y; gg.yz=a0.yz*x12.xz+h.yz*x12.yw; return 130.0*dot(m,gg); }\n\
struct ColorStop { vec3 color; float position; };\n\
#define COLOR_RAMP(colors,factor,finalColor){ int index=0; for(int i=0;i<2;i++){ ColorStop cc=colors[i]; bool ib=cc.position<=factor; index=int(mix(float(index),float(i),float(ib))); } ColorStop cc=colors[index]; ColorStop nc=colors[index+1]; float range=nc.position-cc.position; float lf=(factor-cc.position)/range; finalColor=mix(cc.color,nc.color,lf); }\n\
void main(){ vec2 uv=gl_FragCoord.xy/uResolution;\n\
  ColorStop colors[3]; colors[0]=ColorStop(uColorStops[0],0.0); colors[1]=ColorStop(uColorStops[1],0.5); colors[2]=ColorStop(uColorStops[2],1.0);\n\
  vec3 rampColor; COLOR_RAMP(colors, uv.x, rampColor);\n\
  float height=snoise(vec2(uv.x*2.0+uTime*0.1, uTime*0.25))*0.5*uAmplitude; height=exp(height); height=(uv.y*2.0-height+0.2);\n\
  float intensity=0.6*height; float midPoint=0.20; float aa=smoothstep(midPoint-uBlend*0.5, midPoint+uBlend*0.5, intensity);\n\
  vec3 auroraColor=intensity*rampColor; fragColor=vec4(auroraColor*aa, aa); }\n';

  function hex(h){ h=String(h).replace('#',''); return [parseInt(h.substr(0,2),16)/255, parseInt(h.substr(2,2),16)/255, parseInt(h.substr(4,2),16)/255]; }
  function sh(gl, t, src){ var s=gl.createShader(t); gl.shaderSource(s, src); gl.compileShader(s); if(!gl.getShaderParameter(s, gl.COMPILE_STATUS)) console.warn(gl.getShaderInfoLog(s)); return s; }
  function reduced(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }

  function run(ctn){
    var canvas=document.createElement('canvas'); canvas.style.cssText='width:100%;height:100%;display:block'; ctn.appendChild(canvas);
    var gl=canvas.getContext('webgl2',{alpha:true, premultipliedAlpha:true, antialias:true});
    if(!gl){ ctn.style.background='linear-gradient(135deg,#5227FF,#7cff67)'; return; }
    gl.clearColor(0,0,0,0); gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    var p=gl.createProgram(); gl.attachShader(p, sh(gl,gl.VERTEX_SHADER,VERT)); gl.attachShader(p, sh(gl,gl.FRAGMENT_SHADER,FRAG)); gl.linkProgram(p); gl.useProgram(p);
    var buf=gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 3,-1, -1,3]), gl.STATIC_DRAW);
    var loc=gl.getAttribLocation(p,'position'); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc,2,gl.FLOAT,false,0,0);
    var uTime=gl.getUniformLocation(p,'uTime'), uRes=gl.getUniformLocation(p,'uResolution'), uAmp=gl.getUniformLocation(p,'uAmplitude'), uBlend=gl.getUniformLocation(p,'uBlend'), uStops=gl.getUniformLocation(p,'uColorStops');
    var stops=(ctn.getAttribute('data-colors')||'#5227FF,#7cff67,#5227FF').split(',');
    var flat=[]; stops.slice(0,3).forEach(function(c){ flat=flat.concat(hex(c)); });
    function resize(){ var w=ctn.offsetWidth||600, h=ctn.offsetHeight||360; canvas.width=w; canvas.height=h; gl.viewport(0,0,w,h); gl.uniform2f(uRes,w,h); }
    g.addEventListener('resize', resize); resize();
    gl.uniform1f(uAmp, parseFloat(ctn.getAttribute('data-amp'))||1.0);
    gl.uniform1f(uBlend, parseFloat(ctn.getAttribute('data-blend'))||0.5);
    gl.uniform3fv(uStops, new Float32Array(flat));
    var red=reduced();
    function frame(t){ gl.uniform1f(uTime, (red?2000:t)*0.001); gl.clear(gl.COLOR_BUFFER_BIT); gl.drawArrays(gl.TRIANGLES,0,3); if(!red) requestAnimationFrame(frame); }
    requestAnimationFrame(frame);
  }
  function init(){ var els=document.querySelectorAll('.aurora'); for(var i=0;i<els.length;i++) if(!els[i].__a){ els[i].__a=1; run(els[i]); } }
  g.attachAurora=run; if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* balatro */
/* balatro.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='\nprecision highp float;\n\n#define PI 3.14159265359\n\nuniform float iTime;\nuniform vec3 iResolution;\nuniform float uSpinRotation;\nuniform float uSpinSpeed;\nuniform vec2 uOffset;\nuniform vec4 uColor1;\nuniform vec4 uColor2;\nuniform vec4 uColor3;\nuniform float uContrast;\nuniform float uLighting;\nuniform float uSpinAmount;\nuniform float uPixelFilter;\nuniform float uSpinEase;\nuniform bool uIsRotate;\nuniform vec2 uMouse;\n\nvarying vec2 vUv;\n\nvec4 effect(vec2 screenSize, vec2 screen_coords) {\n    float pixel_size = length(screenSize.xy) / uPixelFilter;\n    vec2 uv = (floor(screen_coords.xy * (1.0 / pixel_size)) * pixel_size - 0.5 * screenSize.xy) / length(screenSize.xy) - uOffset;\n    float uv_len = length(uv);\n    \n    float speed = (uSpinRotation * uSpinEase * 0.2);\n    if(uIsRotate){\n       speed = iTime * speed;\n    }\n    speed += 302.2;\n    \n    float mouseInfluence = (uMouse.x * 2.0 - 1.0);\n    speed += mouseInfluence * 0.1;\n    \n    float new_pixel_angle = atan(uv.y, uv.x) + speed - uSpinEase * 20.0 * (uSpinAmount * uv_len + (1.0 - uSpinAmount));\n    vec2 mid = (screenSize.xy / length(screenSize.xy)) / 2.0;\n    uv = (vec2(uv_len * cos(new_pixel_angle) + mid.x, uv_len * sin(new_pixel_angle) + mid.y) - mid);\n    \n    uv *= 30.0;\n    float baseSpeed = iTime * uSpinSpeed;\n    speed = baseSpeed + mouseInfluence * 2.0;\n    \n    vec2 uv2 = vec2(uv.x + uv.y);\n    \n    for(int i = 0; i < 5; i++) {\n        uv2 += sin(max(uv.x, uv.y)) + uv;\n        uv += 0.5 * vec2(\n            cos(5.1123314 + 0.353 * uv2.y + speed * 0.131121),\n            sin(uv2.x - 0.113 * speed)\n        );\n        uv -= cos(uv.x + uv.y) - sin(uv.x * 0.711 - uv.y);\n    }\n    \n    float contrast_mod = (0.25 * uContrast + 0.5 * uSpinAmount + 1.2);\n    float paint_res = min(2.0, max(0.0, length(uv) * 0.035 * contrast_mod));\n    float c1p = max(0.0, 1.0 - contrast_mod * abs(1.0 - paint_res));\n    float c2p = max(0.0, 1.0 - contrast_mod * abs(paint_res));\n    float c3p = 1.0 - min(1.0, c1p + c2p);\n    float light = (uLighting - 0.2) * max(c1p * 5.0 - 4.0, 0.0) + uLighting * max(c2p * 5.0 - 4.0, 0.0);\n    \n    return (0.3 / uContrast) * uColor1 + (1.0 - 0.3 / uContrast) * (uColor1 * c1p + uColor2 * c2p + vec4(c3p * uColor3.rgb, c3p * uColor1.a)) + light;\n}\n\nvoid main() {\n    vec2 uv = vUv * iResolution.xy;\n    gl_FragColor = effect(iResolution.xy, uv);\n}\n';
  function init(){ var els=document.querySelectorAll('.balatro'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"uSpinRotation":{"t":"1f","v":1},"uSpinSpeed":{"t":"1f","v":1},"uOffset":{"t":"2f","v":[0.5,0.5]},"uColor1":{"t":"3f","v":[1,1,1]},"uColor2":{"t":"3f","v":[1,1,1]},"uColor3":{"t":"3f","v":[1,1,1]},"uContrast":{"t":"1f","v":1},"uLighting":{"t":"1f","v":1},"uSpinAmount":{"t":"1f","v":1},"uPixelFilter":{"t":"1f","v":1},"uSpinEase":{"t":"1f","v":1}} }); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* blob-cursor */
/* blob-cursor.js — ambient · a springy blob follows the pointer. transform only. Hidden on touch / reduced-motion. */
(function(g){ 'use strict';
  function off(){ return g.matchMedia && (g.matchMedia('(prefers-reduced-motion: reduce)').matches || g.matchMedia('(hover: none)').matches); }
  function init(){ if(off()) return; var b=document.createElement('div');
    b.style.cssText='position:fixed;left:0;top:0;width:34px;height:34px;border-radius:50%;pointer-events:none;z-index:9998;background:radial-gradient(circle at 35% 35%,#a99bff,#6d54e6);filter:blur(2px);mix-blend-mode:screen;transform:translate(-50%,-50%)';
    document.body.appendChild(b); var tx=innerWidth/2, ty=innerHeight/2, x=tx, y=ty;
    addEventListener('pointermove', function(e){ tx=e.clientX; ty=e.clientY; });
    (function loop(){ x+=(tx-x)*.18; y+=(ty-y)*.18; b.style.left=x+'px'; b.style.top=y+'px'; requestAnimationFrame(loop); })(); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* blur-text */
/* blur-text.js — text-kinetic · split words + stagger a blur→focus. Reduced-motion → instant. */
(function(g){ 'use strict';
  function red(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function split(el){ var parts=el.textContent.split(/(\s+)/); el.textContent=''; var i=0;
    parts.forEach(function(p){ if(p===''){return;} if(/^\s+$/.test(p)){ var sp=document.createElement('span'); sp.className='bt-sp'; el.appendChild(sp); return; }
      var w=document.createElement('span'); w.className='bt-w'; w.textContent=p; w.style.setProperty('--bt-d',(i*90)+'ms'); i++; el.appendChild(w); }); }
  function init(){ var els=document.querySelectorAll('[data-blur-text]'); if(!els.length) return;
    els.forEach(split); if(red()){ els.forEach(function(e){ e.classList.add('in'); }); return; }
    requestAnimationFrame(function(){ requestAnimationFrame(function(){ els.forEach(function(e){ e.classList.add('in'); }); }); }); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* bounce-cards */
/* bounce-cards.js — entrance · trigger the fan-out on view; stagger via --bc-d. Reduced-motion → shown. */
(function(g){ 'use strict';
  function red(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function init(){ var els=document.querySelectorAll('.bcards'); els.forEach(function(el){ [].slice.call(el.querySelectorAll('.bc')).forEach(function(c,i){ c.style.setProperty('--bc-d',(i*90)+'ms'); });
    if(red()){ el.classList.add('in'); return; } if(!('IntersectionObserver' in g)){ el.classList.add('in'); return; }
    var io=new IntersectionObserver(function(es){ es.forEach(function(e){ if(e.isIntersecting){ e.target.classList.add('in'); io.unobserve(e.target); } }); }, {threshold:.3}); io.observe(el); }); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* circular-text */
/* circular-text.js — ambient · lay characters of data-text around the ring. */
(function(g){ 'use strict';
  function attach(el){ if(el.__c) return; el.__c=1; var t=(el.getAttribute('data-text')||el.textContent).trim(); el.textContent='';
    for(var i=0;i<t.length;i++){ var s=document.createElement('span'); s.className='circ-c'; s.textContent=t[i];
      s.style.transform='rotate('+(i*(360/t.length))+'deg)'; el.appendChild(s); } }
  function init(){ var els=document.querySelectorAll('.circ'); for(var i=0;i<els.length;i++) attach(els[i]); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* click-spark */
/* click-spark.js — feedback-delight · sparks fly from the click point. transform/opacity only. Off under reduced-motion. */
(function(g){ 'use strict';
  function red(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function burst(el, x, y){ var n=8, host=document.createElement('div'); host.style.cssText='position:fixed;left:'+x+'px;top:'+y+'px;pointer-events:none;z-index:9999';
    for(var i=0;i<n;i++){ var s=document.createElement('span'); var a=(i/n)*Math.PI*2, d=18+Math.random()*10;
      s.style.cssText='position:absolute;width:4px;height:4px;border-radius:2px;background:'+(el.getAttribute('data-spark')||'#8b7cf6')+';transform:translate(-50%,-50%)';
      host.appendChild(s); (function(sp,dx,dy){ sp.animate([{transform:'translate(-50%,-50%) translate(0,0)',opacity:1},{transform:'translate(-50%,-50%) translate('+dx+'px,'+dy+'px)',opacity:0}],{duration:480,easing:'cubic-bezier(.2,.7,.3,1)'}); })(s, Math.cos(a)*d, Math.sin(a)*d);
    } document.body.appendChild(host); setTimeout(function(){ host.remove(); }, 520); }
  function attach(el){ if(el.__spark) return; el.__spark=1; el.addEventListener('click', function(e){ if(!red()) burst(el, e.clientX, e.clientY); }); }
  function init(){ var els=document.querySelectorAll('.click-spark'); for(var i=0;i<els.length;i++) attach(els[i]); }
  g.attachClickSpark=attach; if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* count-up */
/* count-up.js — motion-anything recipe · category: emphasis
 *
 * Animates [data-count] elements up to their target when they enter the viewport, once.
 * - data-count="10000"  (target)   data-count-suffix="+"   data-count-prefix="$"
 * - Honors prefers-reduced-motion (sets the final value immediately).
 * - Uses an ease-out curve; updates textContent (no layout thrash with tabular figures).
 *
 * Usage:  <span data-count="10000" data-count-suffix="+">0</span>
 */
(function () {
  'use strict';

  function format(n, prefix, suffix) {
    return (prefix || '') + Math.round(n).toLocaleString() + (suffix || '');
  }

  function run(el) {
    var target = parseFloat(el.getAttribute('data-count')) || 0;
    var prefix = el.getAttribute('data-count-prefix') || '';
    var suffix = el.getAttribute('data-count-suffix') || '';
    var dur = parseInt(el.getAttribute('data-count-duration'), 10) || 900;
    var start = null;

    function step(ts) {
      if (start === null) start = ts;
      var p = Math.min((ts - start) / dur, 1);
      var eased = 1 - Math.pow(1 - p, 3); // ease-out cubic
      el.textContent = format(target * eased, prefix, suffix);
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  function init() {
    var els = document.querySelectorAll('[data-count]');
    if (!els.length) return;

    var reduce = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce || !('IntersectionObserver' in window)) {
      els.forEach(function (el) {
        el.textContent = format(parseFloat(el.getAttribute('data-count')) || 0,
          el.getAttribute('data-count-prefix') || '', el.getAttribute('data-count-suffix') || '');
      });
      return;
    }

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) { run(entry.target); io.unobserve(entry.target); }
      });
    }, { threshold: 0.5 });
    els.forEach(function (el) { io.observe(el); });
  }

  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})();


/* counter */
/* counter.js — emphasis · roll digit strips to the number on view. Reduced-motion → set instantly. */
(function(g){ 'use strict';
  function red(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function build(el){ var n=String(el.getAttribute('data-to')||el.textContent||'0').replace(/[^0-9]/g,''); el.textContent='';
    var strips=[]; for(var i=0;i<n.length;i++){ var d=document.createElement('span'); d.className='dig'; var strip=document.createElement('span'); strip.className='strip';
      for(var k=0;k<=9;k++){ var s=document.createElement('span'); s.textContent=k; strip.appendChild(s); } d.appendChild(strip); el.appendChild(d); strips.push({strip:strip, target:+n[i]}); }
    return strips; }
  function run(strips){ strips.forEach(function(o){ o.strip.style.transform= red()?('translateY(-'+o.target+'em)'):'translateY(0)'; });
    if(red()) return; requestAnimationFrame(function(){ requestAnimationFrame(function(){ strips.forEach(function(o){ o.strip.style.transform='translateY(-'+o.target+'em)'; }); }); }); }
  function init(){ var els=document.querySelectorAll('.counter'); els.forEach(function(el){ var strips=build(el);
    if(!('IntersectionObserver' in g)){ run(strips); return; } var io=new IntersectionObserver(function(es){ es.forEach(function(e){ if(e.isIntersecting){ run(strips); io.unobserve(e.target); } }); }, {threshold:.4}); io.observe(el); }); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* crosshair */
/* crosshair.js — ambient · full-viewport crosshair tracks the pointer. transform only. Off on touch / reduced-motion. */
(function(g){ 'use strict';
  function off(){ return g.matchMedia && (g.matchMedia('(prefers-reduced-motion: reduce)').matches || g.matchMedia('(hover: none)').matches); }
  function line(v){ var d=document.createElement('div'); d.style.cssText='position:fixed;pointer-events:none;z-index:9998;background:'+ (g.__chColor||'rgba(139,124,246,.6)') + (v?';top:0;bottom:0;width:1px':';left:0;right:0;height:1px'); document.body.appendChild(d); return d; }
  function init(){ if(off()) return; var vx=line(true), hz=line(false);
    addEventListener('pointermove', function(e){ vx.style.left=e.clientX+'px'; hz.style.top=e.clientY+'px'; }); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* dark-veil */
/* dark-veil.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='\n#ifdef GL_ES\nprecision lowp float;\n#endif\nuniform vec2 uResolution;\nuniform float uTime;\nuniform float uHueShift;\nuniform float uNoise;\nuniform float uScan;\nuniform float uScanFreq;\nuniform float uWarp;\n#define iTime uTime\n#define iResolution uResolution\n\nvec4 buf[8];\nfloat rand(vec2 c){return fract(sin(dot(c,vec2(12.9898,78.233)))*43758.5453);}\n\nmat3 rgb2yiq=mat3(0.299,0.587,0.114,0.596,-0.274,-0.322,0.211,-0.523,0.312);\nmat3 yiq2rgb=mat3(1.0,0.956,0.621,1.0,-0.272,-0.647,1.0,-1.106,1.703);\n\nvec3 hueShiftRGB(vec3 col,float deg){\n    vec3 yiq=rgb2yiq*col;\n    float rad=radians(deg);\n    float cosh=cos(rad),sinh=sin(rad);\n    vec3 yiqShift=vec3(yiq.x,yiq.y*cosh-yiq.z*sinh,yiq.y*sinh+yiq.z*cosh);\n    return clamp(yiq2rgb*yiqShift,0.0,1.0);\n}\n\nvec4 sigmoid(vec4 x){return 1./(1.+exp(-x));}\n\nvec4 cppn_fn(vec2 coordinate,float in0,float in1,float in2){\n    buf[6]=vec4(coordinate.x,coordinate.y,0.3948333106474662+in0,0.36+in1);\n    buf[7]=vec4(0.14+in2,sqrt(coordinate.x*coordinate.x+coordinate.y*coordinate.y),0.,0.);\n    buf[0]=mat4(vec4(6.5404263,-3.6126034,0.7590882,-1.13613),vec4(2.4582713,3.1660357,1.2219609,0.06276096),vec4(-5.478085,-6.159632,1.8701609,-4.7742867),vec4(6.039214,-5.542865,-0.90925294,3.251348))*buf[6]+mat4(vec4(0.8473259,-5.722911,3.975766,1.6522468),vec4(-0.24321538,0.5839259,-1.7661959,-5.350116),vec4(0.,0.,0.,0.),vec4(0.,0.,0.,0.))*buf[7]+vec4(0.21808943,1.1243913,-1.7969975,5.0294676);\n    buf[1]=mat4(vec4(-3.3522482,-6.0612736,0.55641043,-4.4719114),vec4(0.8631464,1.7432913,5.643898,1.6106541),vec4(2.4941394,-3.5012043,1.7184316,6.357333),vec4(3.310376,8.209261,1.1355612,-1.165539))*buf[6]+mat4(vec4(5.24046,-13.034365,0.009859298,15.870829),vec4(2.987511,3.129433,-0.89023495,-1.6822904),vec4(0.,0.,0.,0.),vec4(0.,0.,0.,0.))*buf[7]+vec4(-5.9457836,-6.573602,-0.8812491,1.5436668);\n    buf[0]=sigmoid(buf[0]);buf[1]=sigmoid(buf[1]);\n    buf[2]=mat4(vec4(-15.219568,8.095543,-2.429353,-1.9381982),vec4(-5.951362,4.3115187,2.6393783,1.274315),vec4(-7.3145227,6.7297835,5.2473326,5.9411426),vec4(5.0796127,8.979051,-1.7278991,-1.158976))*buf[6]+mat4(vec4(-11.967154,-11.608155,6.1486754,11.237008),vec4(2.124141,-6.263192,-1.7050359,-0.7021966),vec4(0.,0.,0.,0.),vec4(0.,0.,0.,0.))*buf[7]+vec4(-4.17164,-3.2281182,-4.576417,-3.6401186);\n    buf[3]=mat4(vec4(3.1832156,-13.738922,1.879223,3.233465),vec4(0.64300746,12.768129,1.9141049,0.50990224),vec4(-0.049295485,4.4807224,1.4733979,1.801449),vec4(5.0039253,13.000481,3.3991797,-4.5561905))*buf[6]+mat4(vec4(-0.1285731,7.720628,-3.1425676,4.742367),vec4(0.6393625,3.714393,-0.8108378,-0.39174938),vec4(0.,0.,0.,0.),vec4(0.,0.,0.,0.))*buf[7]+vec4(-1.1811101,-21.621881,0.7851888,1.2329718);\n    buf[2]=sigmoid(buf[2]);buf[3]=sigmoid(buf[3]);\n    buf[4]=mat4(vec4(5.214916,-7.183024,2.7228765,2.6592617),vec4(-5.601878,-25.3591,4.067988,0.4602802),vec4(-10.57759,24.286327,21.102104,37.546658),vec4(4.3024497,-1.9625226,2.3458803,-1.372816))*buf[0]+mat4(vec4(-17.6526,-10.507558,2.2587414,12.462782),vec4(6.265566,-502.75443,-12.642513,0.9112289),vec4(-10.983244,20.741234,-9.701768,-0.7635988),vec4(5.383626,1.4819539,-4.1911616,-4.8444734))*buf[1]+mat4(vec4(12.785233,-16.345072,-0.39901125,1.7955981),vec4(-30.48365,-1.8345358,1.4542528,-1.1118771),vec4(19.872723,-7.337935,-42.941723,-98.52709),vec4(8.337645,-2.7312303,-2.2927687,-36.142323))*buf[2]+mat4(vec4(-16.298317,3.5471997,-0.44300047,-9.444417),vec4(57.5077,-35.609753,16.163465,-4.1534753),vec4(-0.07470326,-3.8656476,-7.0901804,3.1523974),vec4(-12.559385,-7.077619,1.490437,-0.8211543))*buf[3]+vec4(-7.67914,15.927437,1.3207729,-1.6686112);\n    buf[5]=mat4(vec4(-1.4109162,-0.372762,-3.770383,-21.367174),vec4(-6.2103205,-9.35908,0.92529047,8.82561),vec4(11.460242,-22.348068,13.625772,-18.693201),vec4(-0.3429052,-3.9905605,-2.4626114,-0.45033523))*buf[0]+mat4(vec4(7.3481627,-4.3661838,-6.3037653,-3.868115),vec4(1.5462853,6.5488915,1.9701879,-0.58291394),vec4(6.5858274,-2.2180402,3.7127688,-1.3730392),vec4(-5.7973905,10.134961,-2.3395722,-5.965605))*buf[1]+mat4(vec4(-2.5132585,-6.6685553,-1.4029363,-0.16285264),vec4(-0.37908727,0.53738135,4.389061,-1.3024765),vec4(-0.70647055,2.0111287,-5.1659346,-3.728635),vec4(-13.562562,10.487719,-0.9173751,-2.6487076))*buf[2]+mat4(vec4(-8.645013,6.5546675,-6.3944063,-5.5933375),vec4(-0.57783127,-1.077275,36.91025,5.736769),vec4(14.283112,3.7146652,7.1452246,-4.5958776),vec4(2.7192075,3.6021907,-4.366337,-2.3653464))*buf[3]+vec4(-5.9000807,-4.329569,1.2427121,8.59503);\n    buf[4]=sigmoid(buf[4]);buf[5]=sigmoid(buf[5]);\n    buf[6]=mat4(vec4(-1.61102,0.7970257,1.4675229,0.20917463),vec4(-28.793737,-7.1390953,1.5025433,4.656581),vec4(-10.94861,39.66238,0.74318546,-10.095605),vec4(-0.7229728,-1.5483948,0.7301322,2.1687684))*buf[0]+mat4(vec4(3.2547753,21.489103,-1.0194173,-3.3100595),vec4(-3.7316632,-3.3792162,-7.223193,-0.23685838),vec4(13.1804495,0.7916005,5.338587,5.687114),vec4(-4.167605,-17.798311,-6.815736,-1.6451967))*buf[1]+mat4(vec4(0.604885,-7.800309,-7.213122,-2.741014),vec4(-3.522382,-0.12359311,-0.5258442,0.43852118),vec4(9.6752825,-22.853785,2.062431,0.099892326),vec4(-4.3196306,-17.730087,2.5184598,5.30267))*buf[2]+mat4(vec4(-6.545563,-15.790176,-6.0438633,-5.415399),vec4(-43.591583,28.551912,-16.00161,18.84728),vec4(4.212382,8.394307,3.0958717,8.657522),vec4(-5.0237565,-4.450633,-4.4768,-5.5010443))*buf[3]+mat4(vec4(1.6985557,-67.05806,6.897715,1.9004834),vec4(1.8680354,2.3915145,2.5231109,4.081538),vec4(11.158006,1.7294737,2.0738268,7.386411),vec4(-4.256034,-306.24686,8.258898,-17.132736))*buf[4]+mat4(vec4(1.6889864,-4.5852966,3.8534803,-6.3482175),vec4(1.3543309,-1.2640043,9.932754,2.9079645),vec4(-5.2770967,0.07150358,-0.13962056,3.3269649),vec4(28.34703,-4.918278,6.1044083,4.085355))*buf[5]+vec4(6.6818056,12.522166,-3.7075126,-4.104386);\n    buf[7]=mat4(vec4(-8.265602,-4.7027016,5.098234,0.7509808),vec4(8.6507845,-17.15949,16.51939,-8.884479),vec4(-4.036479,-2.3946867,-2.6055532,-1.9866527),vec4(-2.2167742,-1.8135649,-5.9759874,4.8846445))*buf[0]+mat4(vec4(6.7790847,3.5076547,-2.8191125,-2.7028968),vec4(-5.743024,-0.27844876,1.4958696,-5.0517144),vec4(13.122226,15.735168,-2.9397483,-4.101023),vec4(-14.375265,-5.030483,-6.2599335,2.9848232))*buf[1]+mat4(vec4(4.0950394,-0.94011575,-5.674733,4.755022),vec4(4.3809423,4.8310084,1.7425908,-3.437416),vec4(2.117492,0.16342592,-104.56341,16.949184),vec4(-5.22543,-2.994248,3.8350096,-1.9364246))*buf[2]+mat4(vec4(-5.900337,1.7946124,-13.604192,-3.8060522),vec4(6.6583457,31.911177,25.164474,91.81147),vec4(11.840538,4.1503043,-0.7314397,6.768467),vec4(-6.3967767,4.034772,6.1714606,-0.32874924))*buf[3]+mat4(vec4(3.4992442,-196.91893,-8.923708,2.8142626),vec4(3.4806502,-3.1846354,5.1725626,5.1804223),vec4(-2.4009497,15.585794,1.2863957,2.0252278),vec4(-71.25271,-62.441242,-8.138444,0.50670296))*buf[4]+mat4(vec4(-12.291733,-11.176166,-7.3474145,4.390294),vec4(10.805477,5.6337385,-0.9385842,-4.7348723),vec4(-12.869276,-7.039391,5.3029537,7.5436664),vec4(1.4593618,8.91898,3.5101583,5.840625))*buf[5]+vec4(2.2415268,-6.705987,-0.98861027,-2.117676);\n    buf[6]=sigmoid(buf[6]);buf[7]=sigmoid(buf[7]);\n    buf[0]=mat4(vec4(1.6794263,1.3817469,2.9625452,0.),vec4(-1.8834411,-1.4806935,-3.5924516,0.),vec4(-1.3279216,-1.0918057,-2.3124623,0.),vec4(0.2662234,0.23235129,0.44178495,0.))*buf[0]+mat4(vec4(-0.6299101,-0.5945583,-0.9125601,0.),vec4(0.17828953,0.18300213,0.18182953,0.),vec4(-2.96544,-2.5819945,-4.9001055,0.),vec4(1.4195864,1.1868085,2.5176322,0.))*buf[1]+mat4(vec4(-1.2584374,-1.0552157,-2.1688404,0.),vec4(-0.7200217,-0.52666044,-1.438251,0.),vec4(0.15345335,0.15196142,0.272854,0.),vec4(0.945728,0.8861938,1.2766753,0.))*buf[2]+mat4(vec4(-2.4218085,-1.968602,-4.35166,0.),vec4(-22.683098,-18.0544,-41.954372,0.),vec4(0.63792,0.5470648,1.1078634,0.),vec4(-1.5489894,-1.3075932,-2.6444845,0.))*buf[3]+mat4(vec4(-0.49252132,-0.39877754,-0.91366625,0.),vec4(0.95609266,0.7923952,1.640221,0.),vec4(0.30616966,0.15693925,0.8639857,0.),vec4(1.1825981,0.94504964,2.176963,0.))*buf[4]+mat4(vec4(0.35446745,0.3293795,0.59547555,0.),vec4(-0.58784515,-0.48177817,-1.0614829,0.),vec4(2.5271258,1.9991658,4.6846647,0.),vec4(0.13042648,0.08864098,0.30187556,0.))*buf[5]+mat4(vec4(-1.7718065,-1.4033192,-3.3355875,0.),vec4(3.1664357,2.638297,5.378702,0.),vec4(-3.1724713,-2.6107926,-5.549295,0.),vec4(-2.851368,-2.249092,-5.3013067,0.))*buf[6]+mat4(vec4(1.5203838,1.2212278,2.8404984,0.),vec4(1.5210563,1.2651345,2.683903,0.),vec4(2.9789467,2.4364579,5.2347264,0.),vec4(2.2270417,1.8825914,3.8028636,0.))*buf[7]+vec4(-1.5468478,-3.6171484,0.24762098,0.);\n    buf[0]=sigmoid(buf[0]);\n    return vec4(buf[0].x,buf[0].y,buf[0].z,1.);\n}\n\nvoid mainImage(out vec4 fragColor,in vec2 fragCoord){\n    vec2 uv=fragCoord/uResolution.xy*2.-1.;\n    uv.y*=-1.;\n    uv+=uWarp*vec2(sin(uv.y*6.283+uTime*0.5),cos(uv.x*6.283+uTime*0.5))*0.05;\n    fragColor=cppn_fn(uv,0.1*sin(0.3*uTime),0.1*sin(0.69*uTime),0.1*sin(0.44*uTime));\n}\n\nvoid main(){\n    vec4 col;mainImage(col,gl_FragCoord.xy);\n    col.rgb=hueShiftRGB(col.rgb,uHueShift);\n    float scanline_val=sin(gl_FragCoord.y*uScanFreq)*0.5+0.5;\n    col.rgb*=1.-(scanline_val*scanline_val)*uScan;\n    col.rgb+=(rand(gl_FragCoord.xy+uTime)-0.5)*uNoise;\n    gl_FragColor=vec4(clamp(col.rgb,0.0,1.0),1.0);\n}\n';
  function init(){ var els=document.querySelectorAll('.dark-veil'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"uHueShift":{"t":"1f","v":0},"uNoise":{"t":"1f","v":0.12},"uScan":{"t":"1f","v":0.2},"uScanFreq":{"t":"1f","v":2},"uWarp":{"t":"1f","v":0.3}} }); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* decrypted-text */
/* decrypted-text.js — text-kinetic · resolve scrambled glyphs into the real text on hover/view. Reduced-motion → final text. */
(function(g){ 'use strict';
  var CH='!<>-_\\/[]{}=+*^?#'; function red(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function run(el){ var target=el.getAttribute('data-text')||el.textContent; if(red()){ el.textContent=target; return; }
    var frame=0, id; clearInterval(el.__d); el.__d=setInterval(function(){ var out='';
      for(var i=0;i<target.length;i++){ if(i < frame/2){ out+=target[i]; } else if(target[i]===' '){ out+=' '; } else { out+=CH[Math.floor(Math.random()*CH.length)]; } }
      el.textContent=out; frame++; if(frame/2>=target.length){ clearInterval(el.__d); el.textContent=target; } }, 30); }
  function attach(el){ if(el.__dec) return; el.__dec=1; el.setAttribute('data-text', el.getAttribute('data-text')||el.textContent); el.addEventListener('mouseenter', function(){ run(el); }); run(el); }
  function init(){ var els=document.querySelectorAll('.decrypt'); for(var i=0;i<els.length;i++) attach(els[i]); }
  g.decryptEl=run; if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* dither */
/* dither.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js).
 * Hand-merged from the react-bits two-pass original (wave shader → retro Bayer-dither postprocess):
 * the wave is procedural, so sampling pass-1 at the pixelated uv == computing the wave AT that uv —
 * one #version 300 es frag replicates both passes with zero framebuffers. Real defaults from source. */
(function(g){ 'use strict';
  var FRAG = '#version 300 es\n'
  + 'precision highp float;\n'
  + 'out vec4 fragColor;\n'
  + 'uniform vec2 uResolution;\n'
  + 'uniform float uTime;\n'
  + 'uniform vec2 uMouse;\n'
  + 'uniform float waveSpeed;\n'
  + 'uniform float waveFrequency;\n'
  + 'uniform float waveAmplitude;\n'
  + 'uniform vec3 waveColor;\n'
  + 'uniform int enableMouseInteraction;\n'
  + 'uniform float mouseRadius;\n'
  + 'uniform float colorNum;\n'
  + 'uniform float pixelSize;\n'
  + 'vec4 mod289(vec4 x){ return x - floor(x * (1.0/289.0)) * 289.0; }\n'
  + 'vec4 permute(vec4 x){ return mod289(((x * 34.0) + 1.0) * x); }\n'
  + 'vec4 taylorInvSqrt(vec4 r){ return 1.79284291400159 - 0.85373472095314 * r; }\n'
  + 'vec2 fade(vec2 t){ return t*t*t*(t*(t*6.0-15.0)+10.0); }\n'
  + 'float cnoise(vec2 P){\n'
  + '  vec4 Pi = floor(P.xyxy) + vec4(0.0,0.0,1.0,1.0);\n'
  + '  vec4 Pf = fract(P.xyxy) - vec4(0.0,0.0,1.0,1.0);\n'
  + '  Pi = mod289(Pi);\n'
  + '  vec4 ix = Pi.xzxz; vec4 iy = Pi.yyww; vec4 fx = Pf.xzxz; vec4 fy = Pf.yyww;\n'
  + '  vec4 i = permute(permute(ix) + iy);\n'
  + '  vec4 gx = fract(i * (1.0/41.0)) * 2.0 - 1.0;\n'
  + '  vec4 gy = abs(gx) - 0.5; vec4 tx = floor(gx + 0.5); gx = gx - tx;\n'
  + '  vec2 g00 = vec2(gx.x, gy.x); vec2 g10 = vec2(gx.y, gy.y);\n'
  + '  vec2 g01 = vec2(gx.z, gy.z); vec2 g11 = vec2(gx.w, gy.w);\n'
  + '  vec4 norm = taylorInvSqrt(vec4(dot(g00,g00), dot(g01,g01), dot(g10,g10), dot(g11,g11)));\n'
  + '  g00 *= norm.x; g01 *= norm.y; g10 *= norm.z; g11 *= norm.w;\n'
  + '  float n00 = dot(g00, vec2(fx.x, fy.x)); float n10 = dot(g10, vec2(fx.y, fy.y));\n'
  + '  float n01 = dot(g01, vec2(fx.z, fy.z)); float n11 = dot(g11, vec2(fx.w, fy.w));\n'
  + '  vec2 fade_xy = fade(Pf.xy);\n'
  + '  vec2 n_x = mix(vec2(n00, n01), vec2(n10, n11), fade_xy.x);\n'
  + '  return 2.3 * mix(n_x.x, n_x.y, fade_xy.y);\n'
  + '}\n'
  + 'const int OCTAVES = 4;\n'
  + 'float fbm(vec2 p){\n'
  + '  float value = 0.0; float amp = 1.0; float freq = waveFrequency;\n'
  + '  for (int i = 0; i < OCTAVES; i++){ value += amp * abs(cnoise(p)); p *= freq; amp *= waveAmplitude; }\n'
  + '  return value;\n'
  + '}\n'
  + 'float pattern(vec2 p){ vec2 p2 = p - uTime * waveSpeed; return fbm(p + fbm(p2)); }\n'
  + 'const float bayerMatrix8x8[64] = float[64](\n'
  + '  0.0/64.0, 48.0/64.0, 12.0/64.0, 60.0/64.0,  3.0/64.0, 51.0/64.0, 15.0/64.0, 63.0/64.0,\n'
  + '  32.0/64.0,16.0/64.0, 44.0/64.0, 28.0/64.0, 35.0/64.0,19.0/64.0, 47.0/64.0, 31.0/64.0,\n'
  + '  8.0/64.0, 56.0/64.0,  4.0/64.0, 52.0/64.0, 11.0/64.0,59.0/64.0,  7.0/64.0, 55.0/64.0,\n'
  + '  40.0/64.0,24.0/64.0, 36.0/64.0, 20.0/64.0, 43.0/64.0,27.0/64.0, 39.0/64.0, 23.0/64.0,\n'
  + '  2.0/64.0, 50.0/64.0, 14.0/64.0, 62.0/64.0,  1.0/64.0,49.0/64.0, 13.0/64.0, 61.0/64.0,\n'
  + '  34.0/64.0,18.0/64.0, 46.0/64.0, 30.0/64.0, 33.0/64.0,17.0/64.0, 45.0/64.0, 29.0/64.0,\n'
  + '  10.0/64.0,58.0/64.0,  6.0/64.0, 54.0/64.0,  9.0/64.0,57.0/64.0,  5.0/64.0, 53.0/64.0,\n'
  + '  42.0/64.0,26.0/64.0, 38.0/64.0, 22.0/64.0, 41.0/64.0,25.0/64.0, 37.0/64.0, 21.0/64.0\n'
  + ');\n'
  + 'vec3 dither(vec2 uv, vec3 color){\n'
  + '  vec2 scaledCoord = floor(uv * uResolution / pixelSize);\n'
  + '  int x = int(mod(scaledCoord.x, 8.0)); int y = int(mod(scaledCoord.y, 8.0));\n'
  + '  float threshold = bayerMatrix8x8[y * 8 + x] - 0.25;\n'
  + '  float step = 1.0 / (colorNum - 1.0);\n'
  + '  color += threshold * step;\n'
  + '  float bias = 0.2;\n'
  + '  color = clamp(color - bias, 0.0, 1.0);\n'
  + '  return floor(color * (colorNum - 1.0) + 0.5) / (colorNum - 1.0);\n'
  + '}\n'
  + 'void main(){\n'
  + '  vec2 uvScreen = gl_FragCoord.xy / uResolution;\n'
  + '  vec2 normalizedPixelSize = pixelSize / uResolution;\n'
  + '  vec2 uvPixel = normalizedPixelSize * floor(uvScreen / normalizedPixelSize);\n'
  + '  vec2 fragPix = uvPixel * uResolution;\n'
  + '  vec2 uv = fragPix / uResolution - 0.5;\n'
  + '  uv.x *= uResolution.x / uResolution.y;\n'
  + '  float f = pattern(uv);\n'
  + '  if (enableMouseInteraction == 1) {\n'
  + '    vec2 mouseNDC = uMouse - 0.5;\n'      /* shaderbg uMouse is 0..1, bottom-origin — already "up" */
  + '    mouseNDC.x *= uResolution.x / uResolution.y;\n'
  + '    float dist = length(uv - mouseNDC);\n'
  + '    float effect = 1.0 - smoothstep(0.0, mouseRadius, dist);\n'
  + '    f -= 0.5 * effect;\n'
  + '  }\n'
  + '  vec3 col = mix(vec3(0.0), waveColor, f);\n'
  + '  col = dither(uvScreen, col);\n'
  + '  fragColor = vec4(col, 1.0);\n'
  + '}\n';
  function init(){ var els=document.querySelectorAll('.dither'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{
      waveSpeed:{t:'1f',v:0.05}, waveFrequency:{t:'1f',v:3}, waveAmplitude:{t:'1f',v:0.3},
      waveColor:{t:'3f',v:[0.5,0.5,0.5]}, enableMouseInteraction:{t:'1i',v:1}, mouseRadius:{t:'1f',v:1},
      colorNum:{t:'1f',v:4}, pixelSize:{t:'1f',v:2} } }); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* dock */
/* dock.js — hover-press · scale each icon by proximity to the pointer. */
(function(g){ 'use strict';
  function red(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function attach(el){ if(el.__dk) return; el.__dk=1; var items=[].slice.call(el.querySelectorAll('.dk')); if(red()) return;
    el.addEventListener('pointermove', function(e){ items.forEach(function(d){ var r=d.getBoundingClientRect(); var dist=Math.abs(e.clientX-(r.left+r.width/2)); var s=Math.max(1, 1.6 - dist/140); d.style.transform='scale('+s+')'; }); });
    el.addEventListener('pointerleave', function(){ items.forEach(function(d){ d.style.transform='scale(1)'; }); }); }
  function init(){ var els=document.querySelectorAll('.dock'); for(var i=0;i<els.length;i++) attach(els[i]); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* dot-field */
/* dot-field.js — motion-anything recipe · interaction · faithful canvas 2D port (dependency-free).
 * A gradient dot lattice that bulges away from the pointer (speed-gated engagement) with a soft
 * glow following the cursor. The original renders the glow in an SVG overlay; this port draws it
 * on the same canvas (same look, one element). Real defaults from source. */
(function(g){ 'use strict';
  var DEF = { dotRadius:1.5, dotSpacing:14, cursorRadius:500, cursorForce:0.1, bulgeOnly:true,
    bulgeStrength:67, glowRadius:160, sparkle:false, waveAmplitude:0,
    gradientFrom:'rgba(168, 85, 247, 0.35)', gradientTo:'rgba(180, 151, 207, 0.25)', glowColor:'#120F17' };
  var TWO_PI = Math.PI*2;
  function reduced(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function start(el){
    if(el.__ma) return; el.__ma = 1;
    var canvas = document.createElement('canvas');
    canvas.style.cssText = 'width:100%;height:100%;display:block';
    el.appendChild(canvas);
    var ctx = canvas.getContext('2d', { alpha:true });
    var dpr = Math.min(g.devicePixelRatio||1, 2);
    var dots = [], W=0, H=0;
    function buildDots(){
      var step = DEF.dotRadius + DEF.dotSpacing;
      var cols = Math.floor(W/step), rows = Math.floor(H/step);
      var padX = (W % step)/2, padY = (H % step)/2;
      dots = [];
      for(var row=0;row<rows;row++) for(var col=0;col<cols;col++){
        var ax = padX + col*step + step/2, ay = padY + row*step + step/2;
        dots.push({ ax:ax, ay:ay, sx:ax, sy:ay, vx:0, vy:0, x:ax, y:ay });
      }
    }
    function doResize(){
      var r = el.getBoundingClientRect(); W = r.width; H = r.height;
      canvas.width = W*dpr; canvas.height = H*dpr; ctx.setTransform(dpr,0,0,dpr,0,0);
      buildDots();
    }
    doResize();
    var resizeTimer; g.addEventListener('resize', function(){ clearTimeout(resizeTimer); resizeTimer = setTimeout(doResize, 100); });
    var mouse = { x:-9999, y:-9999, prevX:-9999, prevY:-9999, speed:0 };
    el.addEventListener('pointermove', function(e){ var r = el.getBoundingClientRect();
      mouse.x = e.clientX - r.left; mouse.y = e.clientY - r.top; }, { passive:true });
    setInterval(function(){
      var dx = mouse.prevX - mouse.x, dy = mouse.prevY - mouse.y;
      var dist = Math.sqrt(dx*dx + dy*dy);
      mouse.speed += (dist - mouse.speed)*0.5; if(mouse.speed < 0.001) mouse.speed = 0;
      mouse.prevX = mouse.x; mouse.prevY = mouse.y;
    }, 20);
    var engagement = 0, glowOpacity = 0, frameCount = 0, red = reduced();
    function tick(){
      frameCount++;
      var t = frameCount*0.02;
      var targetEngagement = Math.min(mouse.speed/5, 1);
      engagement += (targetEngagement - engagement)*0.06; if(engagement < 0.001) engagement = 0;
      glowOpacity += (engagement - glowOpacity)*0.08;
      ctx.clearRect(0, 0, W, H);
      var grad = ctx.createLinearGradient(0, 0, W, H);
      grad.addColorStop(0, DEF.gradientFrom); grad.addColorStop(1, DEF.gradientTo);
      ctx.fillStyle = grad;
      var cr = DEF.cursorRadius, crSq = cr*cr, rad = DEF.dotRadius/2;
      ctx.beginPath();
      for(var i=0;i<dots.length;i++){ var d = dots[i];
        var dx = mouse.x - d.ax, dy = mouse.y - d.ay, distSq = dx*dx + dy*dy;
        if(distSq < crSq && engagement > 0.01){
          var dist = Math.sqrt(distSq);
          if(DEF.bulgeOnly){
            var k = 1 - dist/cr;
            var push = k*k*DEF.bulgeStrength*engagement;
            var angle = Math.atan2(dy, dx);
            d.sx += (d.ax - Math.cos(angle)*push - d.sx)*0.15;
            d.sy += (d.ay - Math.sin(angle)*push - d.sy)*0.15;
          } else {
            var ang = Math.atan2(dy, dx);
            var mv = (500/dist)*(mouse.speed*DEF.cursorForce);
            d.vx += Math.cos(ang)*-mv; d.vy += Math.sin(ang)*-mv;
          }
        } else if(DEF.bulgeOnly){
          d.sx += (d.ax - d.sx)*0.1; d.sy += (d.ay - d.sy)*0.1;
        }
        if(!DEF.bulgeOnly){
          d.vx *= 0.9; d.vy *= 0.9;
          d.x = d.ax + d.vx; d.y = d.ay + d.vy;
          d.sx += (d.x - d.sx)*0.1; d.sy += (d.y - d.sy)*0.1;
        }
        var drawX = d.sx, drawY = d.sy;
        if(DEF.waveAmplitude > 0){
          drawY += Math.sin(d.ax*0.03 + t)*DEF.waveAmplitude;
          drawX += Math.cos(d.ay*0.03 + t*0.7)*DEF.waveAmplitude*0.5;
        }
        if(DEF.sparkle){
          var hash = ((i*2654435761) ^ (frameCount>>3)) >>> 0;
          var rr = (hash % 100) < 3 ? rad*1.8 : rad;
          ctx.moveTo(drawX + rr, drawY); ctx.arc(drawX, drawY, rr, 0, TWO_PI);
        } else {
          ctx.moveTo(drawX + rad, drawY); ctx.arc(drawX, drawY, rad, 0, TWO_PI);
        }
      }
      ctx.fill();
      if(glowOpacity > 0.01){ // cursor glow — SVG overlay in the original, same canvas here
        var gl = ctx.createRadialGradient(mouse.x, mouse.y, 0, mouse.x, mouse.y, DEF.glowRadius);
        gl.addColorStop(0, DEF.glowColor); gl.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.globalAlpha = glowOpacity; ctx.fillStyle = gl;
        ctx.fillRect(mouse.x - DEF.glowRadius, mouse.y - DEF.glowRadius, DEF.glowRadius*2, DEF.glowRadius*2);
        ctx.globalAlpha = 1;
      }
      if(!red) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }
  function init(){ var els=document.querySelectorAll('.dot-field'); for(var i=0;i<els.length;i++) start(els[i]); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* dot-grid */
/* dot-grid.js — motion-anything recipe · interaction · faithful canvas 2D port (dependency-free).
 * A grid of dots that lights up near the pointer, gets shoved by fast pointer moves, and blasts
 * outward on click — each dot springs back elastically. The original uses GSAP InertiaPlugin
 * (paid); this port replaces it with an underdamped spring integrator (impulse + elastic return,
 * one integrator). Real defaults from source. */
(function(g){ 'use strict';
  var DEF = { dotSize:16, gap:32, baseColor:'#5227FF', activeColor:'#5227FF', proximity:150,
    speedTrigger:100, shockRadius:250, shockStrength:5, maxSpeed:5000 };
  var SPRING_K = 90, SPRING_C = 11, IMPULSE = 9; // underdamped ≈ elastic.out feel
  function reduced(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function hexToRgb(hex){ var m = hex.match(/^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i);
    return m ? { r:parseInt(m[1],16), g:parseInt(m[2],16), b:parseInt(m[3],16) } : { r:0,g:0,b:0 }; }
  function throttle(fn, limit){ var last=0; return function(){ var now=performance.now();
    if(now-last>=limit){ last=now; fn.apply(this, arguments); } }; }
  function start(el){
    if(el.__ma) return; el.__ma = 1;
    var canvas = document.createElement('canvas');
    canvas.style.cssText = 'width:100%;height:100%;display:block';
    el.appendChild(canvas);
    var ctx = canvas.getContext('2d');
    var baseRgb = hexToRgb(DEF.baseColor), activeRgb = hexToRgb(DEF.activeColor);
    var dots = [], W=0, H=0, dpr = Math.min(g.devicePixelRatio||1, 2);
    function buildGrid(){
      var r = el.getBoundingClientRect(); W=r.width; H=r.height;
      canvas.width = W*dpr; canvas.height = H*dpr; ctx.setTransform(dpr,0,0,dpr,0,0);
      var cell = DEF.dotSize + DEF.gap;
      var cols = Math.floor((W + DEF.gap) / cell), rows = Math.floor((H + DEF.gap) / cell);
      var startX = (W - (cell*cols - DEF.gap))/2 + DEF.dotSize/2;
      var startY = (H - (cell*rows - DEF.gap))/2 + DEF.dotSize/2;
      dots = [];
      for(var y=0;y<rows;y++) for(var x=0;x<cols;x++)
        dots.push({ cx:startX + x*cell, cy:startY + y*cell, ox:0, oy:0, vx:0, vy:0 });
    }
    buildGrid();
    g.addEventListener('resize', buildGrid);
    var mouse = { x:-9999, y:-9999, lx:0, ly:0, lt:0 };
    var proxSq = DEF.proximity*DEF.proximity;
    function impulse(dot, pushX, pushY){ dot.vx += pushX*IMPULSE; dot.vy += pushY*IMPULSE; }
    el.addEventListener('pointermove', throttle(function(e){
      var r = el.getBoundingClientRect();
      var now = performance.now(), dt = mouse.lt ? now - mouse.lt : 16;
      var mvx = (e.clientX - mouse.lx)/dt*1000, mvy = (e.clientY - mouse.ly)/dt*1000;
      var speed = Math.hypot(mvx, mvy);
      if(speed > DEF.maxSpeed){ var s = DEF.maxSpeed/speed; mvx*=s; mvy*=s; speed = DEF.maxSpeed; }
      mouse.lt = now; mouse.lx = e.clientX; mouse.ly = e.clientY;
      mouse.x = e.clientX - r.left; mouse.y = e.clientY - r.top;
      if(speed > DEF.speedTrigger){
        for(var i=0;i<dots.length;i++){ var d = dots[i];
          var dist = Math.hypot(d.cx - mouse.x, d.cy - mouse.y);
          if(dist < DEF.proximity && Math.hypot(d.vx, d.vy) < 40)
            impulse(d, (d.cx - mouse.x)*0.02 + mvx*0.005, (d.cy - mouse.y)*0.02 + mvy*0.005);
        }
      }
    }, 50), { passive:true });
    el.addEventListener('click', function(e){
      var r = el.getBoundingClientRect(), cx = e.clientX - r.left, cy = e.clientY - r.top;
      for(var i=0;i<dots.length;i++){ var d = dots[i];
        var dist = Math.hypot(d.cx - cx, d.cy - cy);
        if(dist < DEF.shockRadius){
          var falloff = Math.max(0, 1 - dist/DEF.shockRadius);
          impulse(d, (d.cx - cx)*DEF.shockStrength*falloff*0.06, (d.cy - cy)*DEF.shockStrength*falloff*0.06);
        }
      }
    });
    var red = reduced(), lastT = 0;
    function draw(t){
      var dt = Math.min(0.033, lastT ? (t - lastT)/1000 : 0.016); lastT = t;
      ctx.clearRect(0, 0, W, H);
      for(var i=0;i<dots.length;i++){ var d = dots[i];
        // underdamped spring toward rest — shove + elastic return in one integrator
        d.vx += (-SPRING_K*d.ox - SPRING_C*d.vx)*dt*10; d.vy += (-SPRING_K*d.oy - SPRING_C*d.vy)*dt*10;
        d.ox += d.vx*dt; d.oy += d.vy*dt;
        var dx = d.cx - mouse.x, dy = d.cy - mouse.y, dsq = dx*dx + dy*dy;
        var fill = DEF.baseColor;
        if(dsq <= proxSq){
          var k = 1 - Math.sqrt(dsq)/DEF.proximity;
          fill = 'rgb(' + Math.round(baseRgb.r + (activeRgb.r-baseRgb.r)*k) + ',' +
            Math.round(baseRgb.g + (activeRgb.g-baseRgb.g)*k) + ',' +
            Math.round(baseRgb.b + (activeRgb.b-baseRgb.b)*k) + ')';
        }
        ctx.beginPath();
        ctx.arc(d.cx + d.ox, d.cy + d.oy, DEF.dotSize/2, 0, Math.PI*2);
        ctx.fillStyle = fill; ctx.fill();
      }
      if(!red) requestAnimationFrame(draw);
    }
    requestAnimationFrame(draw);
  }
  function init(){ var els=document.querySelectorAll('.dot-grid'); for(var i=0;i<els.length;i++) start(els[i]); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* elastic-slider */
/* elastic-slider.js — feedback-delight · drag the track; springy fill + knob. */
(function(g){ 'use strict';
  function attach(el){ if(el.__es) return; el.__es=1; var track=el.querySelector('.es-track'), fill=el.querySelector('.es-fill'), knob=el.querySelector('.es-knob');
    function set(clientX){ var r=track.getBoundingClientRect(); var p=Math.max(0,Math.min(1,(clientX-r.left)/r.width)); fill.style.width=(p*100)+'%'; knob.style.left=(p*100)+'%'; }
    var drag=false; track.addEventListener('pointerdown', function(e){ drag=true; set(e.clientX); track.setPointerCapture(e.pointerId); });
    track.addEventListener('pointermove', function(e){ if(drag) set(e.clientX); }); track.addEventListener('pointerup', function(){ drag=false; });
    knob.style.left='40%'; }
  function init(){ var els=document.querySelectorAll('.es'); for(var i=0;i<els.length;i++) attach(els[i]); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* fade-content */
/* fade-content.js — scroll-reveal · IntersectionObserver adds .in once. Reduced-motion → visible instantly. */
(function(g){ 'use strict';
  function red(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function init(){ var els=document.querySelectorAll('.fade-content'); if(red()||!('IntersectionObserver' in g)){ for(var i=0;i<els.length;i++) els[i].classList.add('in'); return; }
    var io=new IntersectionObserver(function(es){ es.forEach(function(e){ if(e.isIntersecting){ e.target.classList.add('in'); io.unobserve(e.target); } }); }, {threshold:.15});
    for(var j=0;j<els.length;j++) io.observe(els[j]); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* fade-in-up */
/* fade-in-up.js — motion-anything recipe · category: entrance
 *
 * Rises + fades [data-fade] elements in on load. Optional per-element stagger via
 * data-fade-delay="80" (ms), or auto-staggered by document order when omitted.
 * Honors prefers-reduced-motion (shows everything immediately).
 *
 * Usage:  <h1 data-fade>…</h1>  <p data-fade data-fade-delay="80">…</p>
 */
(function () {
  'use strict';
  function init() {
    var els = document.querySelectorAll('[data-fade]');
    if (!els.length) return;

    var reduce = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce) {
      els.forEach(function (el) { el.classList.add('is-in'); });
      return;
    }
    els.forEach(function (el, i) {
      var d = el.getAttribute('data-fade-delay');
      el.style.setProperty('--fade-delay', (d != null ? parseInt(d, 10) : i * 70) + 'ms');
    });
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        els.forEach(function (el) { el.classList.add('is-in'); });
      });
    });
  }
  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})();


/* falling-text */
/* falling-text.js — text-kinetic · split letters + stagger a drop-in. Reduced-motion → instant. */
(function(g){ 'use strict';
  function red(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function split(el){ var t=el.textContent; el.textContent=''; var k=0;
    t.split('').forEach(function(c){ if(c===' '){ el.appendChild(document.createTextNode(' ')); return; } var s=document.createElement('span'); s.className='fl-c'; s.textContent=c; s.style.setProperty('--fl-d',(k*55)+'ms'); k++; el.appendChild(s); }); }
  function init(){ var els=document.querySelectorAll('[data-falling]'); if(!els.length) return; els.forEach(split);
    if(red()){ els.forEach(function(e){ e.classList.add('in'); }); return; }
    requestAnimationFrame(function(){ requestAnimationFrame(function(){ els.forEach(function(e){ e.classList.add('in'); }); }); }); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* faulty-terminal */
/* faulty-terminal.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='\nprecision mediump float;\n\nvarying vec2 vUv;\n\nuniform float iTime;\nuniform vec3  iResolution;\nuniform float uScale;\n\nuniform vec2  uGridMul;\nuniform float uDigitSize;\nuniform float uScanlineIntensity;\nuniform float uGlitchAmount;\nuniform float uFlickerAmount;\nuniform float uNoiseAmp;\nuniform float uChromaticAberration;\nuniform float uDither;\nuniform float uCurvature;\nuniform vec3  uTint;\nuniform vec2  uMouse;\nuniform float uMouseStrength;\nuniform float uUseMouse;\nuniform float uPageLoadProgress;\nuniform float uUsePageLoadAnimation;\nuniform float uBrightness;\n\nfloat time;\n\nfloat hash21(vec2 p){\n  p = fract(p * 234.56);\n  p += dot(p, p + 34.56);\n  return fract(p.x * p.y);\n}\n\nfloat noise(vec2 p)\n{\n  return sin(p.x * 10.0) * sin(p.y * (3.0 + sin(time * 0.090909))) + 0.2; \n}\n\nmat2 rotate(float angle)\n{\n  float c = cos(angle);\n  float s = sin(angle);\n  return mat2(c, -s, s, c);\n}\n\nfloat fbm(vec2 p)\n{\n  p *= 1.1;\n  float f = 0.0;\n  float amp = 0.5 * uNoiseAmp;\n  \n  mat2 modify0 = rotate(time * 0.02);\n  f += amp * noise(p);\n  p = modify0 * p * 2.0;\n  amp *= 0.454545;\n  \n  mat2 modify1 = rotate(time * 0.02);\n  f += amp * noise(p);\n  p = modify1 * p * 2.0;\n  amp *= 0.454545;\n  \n  mat2 modify2 = rotate(time * 0.08);\n  f += amp * noise(p);\n  \n  return f;\n}\n\nfloat pattern(vec2 p, out vec2 q, out vec2 r) {\n  vec2 offset1 = vec2(1.0);\n  vec2 offset0 = vec2(0.0);\n  mat2 rot01 = rotate(0.1 * time);\n  mat2 rot1 = rotate(0.1);\n  \n  q = vec2(fbm(p + offset1), fbm(rot01 * p + offset1));\n  r = vec2(fbm(rot1 * q + offset0), fbm(q + offset0));\n  return fbm(p + r);\n}\n\nfloat digit(vec2 p){\n    vec2 grid = uGridMul * 15.0;\n    vec2 s = floor(p * grid) / grid;\n    p = p * grid;\n    vec2 q, r;\n    float intensity = pattern(s * 0.1, q, r) * 1.3 - 0.03;\n    \n    if(uUseMouse > 0.5){\n        vec2 mouseWorld = uMouse * uScale;\n        float distToMouse = distance(s, mouseWorld);\n        float mouseInfluence = exp(-distToMouse * 8.0) * uMouseStrength * 10.0;\n        intensity += mouseInfluence;\n        \n        float ripple = sin(distToMouse * 20.0 - iTime * 5.0) * 0.1 * mouseInfluence;\n        intensity += ripple;\n    }\n    \n    if(uUsePageLoadAnimation > 0.5){\n        float cellRandom = fract(sin(dot(s, vec2(12.9898, 78.233))) * 43758.5453);\n        float cellDelay = cellRandom * 0.8;\n        float cellProgress = clamp((uPageLoadProgress - cellDelay) / 0.2, 0.0, 1.0);\n        \n        float fadeAlpha = smoothstep(0.0, 1.0, cellProgress);\n        intensity *= fadeAlpha;\n    }\n    \n    p = fract(p);\n    p *= uDigitSize;\n    \n    float px5 = p.x * 5.0;\n    float py5 = (1.0 - p.y) * 5.0;\n    float x = fract(px5);\n    float y = fract(py5);\n    \n    float i = floor(py5) - 2.0;\n    float j = floor(px5) - 2.0;\n    float n = i * i + j * j;\n    float f = n * 0.0625;\n    \n    float isOn = step(0.1, intensity - f);\n    float brightness = isOn * (0.2 + y * 0.8) * (0.75 + x * 0.25);\n    \n    return step(0.0, p.x) * step(p.x, 1.0) * step(0.0, p.y) * step(p.y, 1.0) * brightness;\n}\n\nfloat onOff(float a, float b, float c)\n{\n  return step(c, sin(iTime + a * cos(iTime * b))) * uFlickerAmount;\n}\n\nfloat displace(vec2 look)\n{\n    float y = look.y - mod(iTime * 0.25, 1.0);\n    float window = 1.0 / (1.0 + 50.0 * y * y);\n    return sin(look.y * 20.0 + iTime) * 0.0125 * onOff(4.0, 2.0, 0.8) * (1.0 + cos(iTime * 60.0)) * window;\n}\n\nvec3 getColor(vec2 p){\n    \n    float bar = step(mod(p.y + time * 20.0, 1.0), 0.2) * 0.4 + 1.0;\n    bar *= uScanlineIntensity;\n    \n    float displacement = displace(p);\n    p.x += displacement;\n\n    if (uGlitchAmount != 1.0) {\n      float extra = displacement * (uGlitchAmount - 1.0);\n      p.x += extra;\n    }\n\n    float middle = digit(p);\n    \n    const float off = 0.002;\n    float sum = digit(p + vec2(-off, -off)) + digit(p + vec2(0.0, -off)) + digit(p + vec2(off, -off)) +\n                digit(p + vec2(-off, 0.0)) + digit(p + vec2(0.0, 0.0)) + digit(p + vec2(off, 0.0)) +\n                digit(p + vec2(-off, off)) + digit(p + vec2(0.0, off)) + digit(p + vec2(off, off));\n    \n    vec3 baseColor = vec3(0.9) * middle + sum * 0.1 * vec3(1.0) * bar;\n    return baseColor;\n}\n\nvec2 barrel(vec2 uv){\n  vec2 c = uv * 2.0 - 1.0;\n  float r2 = dot(c, c);\n  c *= 1.0 + uCurvature * r2;\n  return c * 0.5 + 0.5;\n}\n\nvoid main() {\n    time = iTime * 0.333333;\n    vec2 uv = vUv;\n\n    if(uCurvature != 0.0){\n      uv = barrel(uv);\n    }\n    \n    vec2 p = uv * uScale;\n    vec3 col = getColor(p);\n\n    if(uChromaticAberration != 0.0){\n      vec2 ca = vec2(uChromaticAberration) / iResolution.xy;\n      col.r = getColor(p + ca).r;\n      col.b = getColor(p - ca).b;\n    }\n\n    col *= uTint;\n    col *= uBrightness;\n\n    if(uDither > 0.0){\n      float rnd = hash21(gl_FragCoord.xy);\n      col += (rnd - 0.5) * (uDither * 0.003922);\n    }\n\n    gl_FragColor = vec4(col, 1.0);\n}\n';
  function init(){ var els=document.querySelectorAll('.faulty-terminal'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"uScale":{"t":"1f","v":1},"uGridMul":{"t":"2f","v":[0.5,0.5]},"uDigitSize":{"t":"1f","v":1},"uScanlineIntensity":{"t":"1f","v":0.2},"uGlitchAmount":{"t":"1f","v":1},"uFlickerAmount":{"t":"1f","v":1},"uNoiseAmp":{"t":"1f","v":0.3},"uChromaticAberration":{"t":"1f","v":1},"uDither":{"t":"1f","v":1},"uCurvature":{"t":"1f","v":1},"uTint":{"t":"3f","v":[1,1,1]},"uMouseStrength":{"t":"1f","v":1},"uUseMouse":{"t":"1f","v":1},"uPageLoadProgress":{"t":"1f","v":1},"uUsePageLoadAnimation":{"t":"1f","v":1},"uBrightness":{"t":"1f","v":1}} }); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* ferrofluid */
/* ferrofluid.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='\nprecision highp float;\n\nuniform vec3  iResolution;\nuniform vec2  iMouse;\nuniform float iTime;\n\nuniform vec3  uColor0;\nuniform vec3  uColor1;\nuniform vec3  uColor2;\nuniform vec3  uColor3;\nuniform vec3  uColor4;\nuniform vec3  uColor5;\nuniform vec3  uColor6;\nuniform vec3  uColor7;\nuniform int   uColorCount;\n\nuniform vec3  uMouseColor;\nuniform vec2  uFlow;\nuniform float uSpeed;\nuniform float uScale;\nuniform float uTurbulence;\nuniform float uFluidity;\nuniform float uRimWidth;\nuniform float uSharpness;\nuniform float uShimmer;\nuniform float uGlow;\nuniform float uOpacity;\nuniform float uMouseEnabled;\nuniform float uMouseStrength;\nuniform float uMouseRadius;\n\nvarying vec2 vUv;\n\n#define PI 3.14159265\n\nvec3 palette(float h) {\n  int count = uColorCount;\n  if (count < 1) count = 1;\n  int idx = int(floor(clamp(h, 0.0, 0.999999) * float(count)));\n  if (idx <= 0) return uColor0;\n  if (idx == 1) return uColor1;\n  if (idx == 2) return uColor2;\n  if (idx == 3) return uColor3;\n  if (idx == 4) return uColor4;\n  if (idx == 5) return uColor5;\n  if (idx == 6) return uColor6;\n  return uColor7;\n}\n\nfloat hash(vec3 p3) {\n  p3 = fract(p3 * 0.1031);\n  p3 += dot(p3, p3.zyx + 33.33);\n  return fract((p3.x + p3.y) * p3.z);\n}\n\nfloat smin(float a, float b, float k) {\n  float r = exp2(-a / k) + exp2(-b / k);\n  return -k * log2(r);\n}\n\nfloat sinlerp(float a, float b, float w) {\n  return mix(a, b, (sin(w * PI - PI / 2.0) + 1.0) / 2.0);\n}\n\nfloat vn(vec2 p, float s, float seed) {\n  vec2 cellp = floor(p / s);\n  vec2 relp = mod(p, s);\n  float g1 = hash(vec3(cellp, seed));\n  float g2 = hash(vec3(cellp.x + 1.0, cellp.y, seed));\n  float g3 = hash(vec3(cellp.x + 1.0, cellp.y + 1.0, seed));\n  float g4 = hash(vec3(cellp.x, cellp.y + 1.0, seed));\n  float bx = sinlerp(g1, g2, relp.x / s);\n  float tx = sinlerp(g4, g3, relp.x / s);\n  return sinlerp(bx, tx, relp.y / s);\n}\n\nfloat dbn(vec2 p, float s, float seed) {\n  float o = s / 2.0;\n  float n0 = vn(p, s, seed);\n  float n1 = vn(p + vec2(o, o), s, seed + 0.1);\n  float n2 = vn(p + vec2(-o, o), s, seed + 0.2);\n  float n3 = vn(p + vec2(o, -o), s, seed + 0.3);\n  float n4 = vn(p + vec2(-o, -o), s, seed + 0.4);\n  return (2.0 * n0 + 1.5 * n1 + 1.25 * n2 + 1.125 * n3 + n4) / 7.0;\n}\n\nvoid mainImage(out vec4 fragColor, in vec2 fragCoord) {\n  float ref = 700.0 / max(uScale, 0.05);\n  vec2 p = fragCoord / iResolution.y * ref;\n\n  float spd = 200.0 * uSpeed;\n  float t = iTime;\n\n  vec2 dir = uFlow;\n  vec2 perp = vec2(-dir.y, dir.x);\n\n  float distort1 = vn(p + perp * (t * spd), 60.0, 10.0) * 50.0 * uTurbulence;\n  float distort2 = vn(p - perp * (t * spd), 120.0, 15.0) * 100.0 * uTurbulence;\n\n  float peaks = dbn(p + distort1 + dir * (t * spd * 0.5), 40.0, 1.0);\n  float peaks2 = dbn(p + distort2 - dir * (t * spd * 0.5), 40.0, 0.0);\n\n  float mapeaks = smin(peaks, peaks2, max(uFluidity, 0.001));\n\n  float mGlow = 0.0;\n  if (uMouseEnabled > 0.5) {\n    vec2 mp = iMouse / iResolution.y * ref;\n    float md = length(p - mp) / ref;\n    float rr = max(uMouseRadius, 0.02);\n    mGlow = exp(-md * md / (rr * rr)) * uMouseStrength;\n  }\n\n  float band = (uRimWidth - abs((mapeaks - 0.4) * 2.0)) * 5.0;\n  float ltn = clamp(band - vn(p + dir * (t * spd * 0.5), 60.0, 12.0) * uShimmer, 0.0, 1.0);\n  ltn = pow(ltn, uSharpness) * uGlow;\n  ltn *= clamp(1.0 - mGlow, 0.0, 1.0);\n\n  float h = clamp(0.5 + (peaks - peaks2) * 0.8, 0.0, 1.0);\n  vec3 col = palette(h);\n\n  vec3 outc = col * ltn;\n  float a = clamp(max(outc.r, max(outc.g, outc.b)), 0.0, 1.0);\n  fragColor = vec4(outc, a * uOpacity);\n}\n\nvoid main() {\n  vec4 color;\n  mainImage(color, vUv * iResolution.xy);\n  gl_FragColor = color;\n}\n';
  function init(){ var els=document.querySelectorAll('.ferrofluid'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"uColor0":{"t":"3f","v":[1,1,1]},"uColor1":{"t":"3f","v":[1,1,1]},"uColor2":{"t":"3f","v":[1,1,1]},"uColor3":{"t":"3f","v":[1,1,1]},"uColor4":{"t":"3f","v":[1,1,1]},"uColor5":{"t":"3f","v":[1,1,1]},"uColor6":{"t":"3f","v":[1,1,1]},"uColor7":{"t":"3f","v":[1,1,1]},"uColorCount":{"t":"1i","v":3},"uMouseColor":{"t":"3f","v":[1,1,1]},"uFlow":{"t":"2f","v":[0,-1]},"uSpeed":{"t":"1f","v":0.5},"uScale":{"t":"1f","v":1.6},"uTurbulence":{"t":"1f","v":1},"uFluidity":{"t":"1f","v":0.1},"uRimWidth":{"t":"1f","v":0.2},"uSharpness":{"t":"1f","v":2.5},"uShimmer":{"t":"1f","v":1.5},"uGlow":{"t":"1f","v":2},"uOpacity":{"t":"1f","v":1},"uMouseEnabled":{"t":"1f","v":0},"uMouseStrength":{"t":"1f","v":1},"uMouseRadius":{"t":"1f","v":0.35}} });  } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* galaxy */
/* galaxy.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='\nprecision highp float;\n\nuniform float uTime;\nuniform vec3 uResolution;\nuniform vec2 uFocal;\nuniform vec2 uRotation;\nuniform float uStarSpeed;\nuniform float uDensity;\nuniform float uHueShift;\nuniform float uSpeed;\nuniform vec2 uMouse;\nuniform float uGlowIntensity;\nuniform float uSaturation;\nuniform bool uMouseRepulsion;\nuniform float uTwinkleIntensity;\nuniform float uRotationSpeed;\nuniform float uRepulsionStrength;\nuniform float uMouseActiveFactor;\nuniform float uAutoCenterRepulsion;\nuniform bool uTransparent;\n\nvarying vec2 vUv;\n\n#define NUM_LAYER 4.0\n#define STAR_COLOR_CUTOFF 0.2\n#define MAT45 mat2(0.7071, -0.7071, 0.7071, 0.7071)\n#define PERIOD 3.0\n\nfloat Hash21(vec2 p) {\n  p = fract(p * vec2(123.34, 456.21));\n  p += dot(p, p + 45.32);\n  return fract(p.x * p.y);\n}\n\nfloat tri(float x) {\n  return abs(fract(x) * 2.0 - 1.0);\n}\n\nfloat tris(float x) {\n  float t = fract(x);\n  return 1.0 - smoothstep(0.0, 1.0, abs(2.0 * t - 1.0));\n}\n\nfloat trisn(float x) {\n  float t = fract(x);\n  return 2.0 * (1.0 - smoothstep(0.0, 1.0, abs(2.0 * t - 1.0))) - 1.0;\n}\n\nvec3 hsv2rgb(vec3 c) {\n  vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);\n  vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);\n  return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);\n}\n\nfloat Star(vec2 uv, float flare) {\n  float d = length(uv);\n  float m = (0.05 * uGlowIntensity) / d;\n  float rays = smoothstep(0.0, 1.0, 1.0 - abs(uv.x * uv.y * 1000.0));\n  m += rays * flare * uGlowIntensity;\n  uv *= MAT45;\n  rays = smoothstep(0.0, 1.0, 1.0 - abs(uv.x * uv.y * 1000.0));\n  m += rays * 0.3 * flare * uGlowIntensity;\n  m *= smoothstep(1.0, 0.2, d);\n  return m;\n}\n\nvec3 StarLayer(vec2 uv) {\n  vec3 col = vec3(0.0);\n\n  vec2 gv = fract(uv) - 0.5; \n  vec2 id = floor(uv);\n\n  for (int y = -1; y <= 1; y++) {\n    for (int x = -1; x <= 1; x++) {\n      vec2 offset = vec2(float(x), float(y));\n      vec2 si = id + vec2(float(x), float(y));\n      float seed = Hash21(si);\n      float size = fract(seed * 345.32);\n      float glossLocal = tri(uStarSpeed / (PERIOD * seed + 1.0));\n      float flareSize = smoothstep(0.9, 1.0, size) * glossLocal;\n\n      float red = smoothstep(STAR_COLOR_CUTOFF, 1.0, Hash21(si + 1.0)) + STAR_COLOR_CUTOFF;\n      float blu = smoothstep(STAR_COLOR_CUTOFF, 1.0, Hash21(si + 3.0)) + STAR_COLOR_CUTOFF;\n      float grn = min(red, blu) * seed;\n      vec3 base = vec3(red, grn, blu);\n      \n      float hue = atan(base.g - base.r, base.b - base.r) / (2.0 * 3.14159) + 0.5;\n      hue = fract(hue + uHueShift / 360.0);\n      float sat = length(base - vec3(dot(base, vec3(0.299, 0.587, 0.114)))) * uSaturation;\n      float val = max(max(base.r, base.g), base.b);\n      base = hsv2rgb(vec3(hue, sat, val));\n\n      vec2 pad = vec2(tris(seed * 34.0 + uTime * uSpeed / 10.0), tris(seed * 38.0 + uTime * uSpeed / 30.0)) - 0.5;\n\n      float star = Star(gv - offset - pad, flareSize);\n      vec3 color = base;\n\n      float twinkle = trisn(uTime * uSpeed + seed * 6.2831) * 0.5 + 1.0;\n      twinkle = mix(1.0, twinkle, uTwinkleIntensity);\n      star *= twinkle;\n      \n      col += star * size * color;\n    }\n  }\n\n  return col;\n}\n\nvoid main() {\n  vec2 focalPx = uFocal * uResolution.xy;\n  vec2 uv = (vUv * uResolution.xy - focalPx) / uResolution.y;\n\n  vec2 mouseNorm = uMouse - vec2(0.5);\n  \n  if (uAutoCenterRepulsion > 0.0) {\n    vec2 centerUV = vec2(0.0, 0.0);\n    float centerDist = length(uv - centerUV);\n    vec2 repulsion = normalize(uv - centerUV) * (uAutoCenterRepulsion / (centerDist + 0.1));\n    uv += repulsion * 0.05;\n  } else if (uMouseRepulsion) {\n    vec2 mousePosUV = (uMouse * uResolution.xy - focalPx) / uResolution.y;\n    float mouseDist = length(uv - mousePosUV);\n    vec2 repulsion = normalize(uv - mousePosUV) * (uRepulsionStrength / (mouseDist + 0.1));\n    uv += repulsion * 0.05 * uMouseActiveFactor;\n  } else {\n    vec2 mouseOffset = mouseNorm * 0.1 * uMouseActiveFactor;\n    uv += mouseOffset;\n  }\n\n  float autoRotAngle = uTime * uRotationSpeed;\n  mat2 autoRot = mat2(cos(autoRotAngle), -sin(autoRotAngle), sin(autoRotAngle), cos(autoRotAngle));\n  uv = autoRot * uv;\n\n  uv = mat2(uRotation.x, -uRotation.y, uRotation.y, uRotation.x) * uv;\n\n  vec3 col = vec3(0.0);\n\n  for (float i = 0.0; i < 1.0; i += 1.0 / NUM_LAYER) {\n    float depth = fract(i + uStarSpeed * uSpeed);\n    float scale = mix(20.0 * uDensity, 0.5 * uDensity, depth);\n    float fade = depth * smoothstep(1.0, 0.9, depth);\n    col += StarLayer(uv * scale + i * 453.32) * fade;\n  }\n\n  if (uTransparent) {\n    float alpha = length(col);\n    alpha = smoothstep(0.0, 0.3, alpha);\n    alpha = min(alpha, 1.0);\n    gl_FragColor = vec4(col, alpha);\n  } else {\n    gl_FragColor = vec4(col, 1.0);\n  }\n}\n';
  function init(){ var els=document.querySelectorAll('.galaxy'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"uFocal":{"t":"2f","v":[0.5,0.5]},"uRotation":{"t":"2f","v":[0.5,0.5]},"uStarSpeed":{"t":"1f","v":1},"uDensity":{"t":"1f","v":1},"uHueShift":{"t":"1f","v":0},"uSpeed":{"t":"1f","v":1},"uGlowIntensity":{"t":"1f","v":1},"uSaturation":{"t":"1f","v":1},"uTwinkleIntensity":{"t":"1f","v":1},"uRotationSpeed":{"t":"1f","v":1},"uRepulsionStrength":{"t":"1f","v":1},"uMouseActiveFactor":{"t":"1f","v":1},"uAutoCenterRepulsion":{"t":"1f","v":1}} }); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* glare-hover */
/* glare-hover.js — category: hover-press · writes --gx/--gy from the pointer; CSS paints the glare. Off on touch/reduced-motion. */
(function (g) {
  'use strict';
  function off(){ return g.matchMedia && (g.matchMedia('(prefers-reduced-motion: reduce)').matches || g.matchMedia('(hover: none)').matches); }
  function attach(el){ if(el.__glare) return; el.__glare=1; el.addEventListener('pointermove', function(e){ var r=el.getBoundingClientRect(); el.style.setProperty('--gx',(e.clientX-r.left)+'px'); el.style.setProperty('--gy',(e.clientY-r.top)+'px'); }); }
  function init(){ if(off()) return; var els=document.querySelectorAll('.glare'); for(var i=0;i<els.length;i++) attach(els[i]); }
  g.attachGlare=attach; if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* gooey-nav */
/* gooey-nav.js — hover-press · move the pill under the clicked item. */
(function(g){ 'use strict';
  function attach(el){ if(el.__gn) return; el.__gn=1; var pill=el.querySelector('.gn-pill'), btns=[].slice.call(el.querySelectorAll('button'));
    function move(b){ btns.forEach(function(x){ x.classList.toggle('on', x===b); }); pill.style.width=b.offsetWidth+'px'; pill.style.transform='translateX('+(b.offsetLeft-5)+'px)'; }
    btns.forEach(function(b){ b.addEventListener('click', function(){ move(b); }); }); var init=el.querySelector('button.on')||btns[0]; if(init) move(init); }
  function init(){ var els=document.querySelectorAll('.gnav'); for(var i=0;i<els.length;i++) attach(els[i]); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* gradient-blinds */
/* gradient-blinds.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='\n#ifdef GL_ES\nprecision mediump float;\n#endif\n\nuniform vec3  iResolution;\nuniform vec2  iMouse;\nuniform float iTime;\n\nuniform float uAngle;\nuniform float uNoise;\nuniform float uBlindCount;\nuniform float uSpotlightRadius;\nuniform float uSpotlightSoftness;\nuniform float uSpotlightOpacity;\nuniform float uMirror;\nuniform float uDistort;\nuniform float uShineFlip;\nuniform vec3  uColor0;\nuniform vec3  uColor1;\nuniform vec3  uColor2;\nuniform vec3  uColor3;\nuniform vec3  uColor4;\nuniform vec3  uColor5;\nuniform vec3  uColor6;\nuniform vec3  uColor7;\nuniform int   uColorCount;\n\nvarying vec2 vUv;\n\nfloat rand(vec2 co){\n  return fract(sin(dot(co, vec2(12.9898,78.233))) * 43758.5453);\n}\n\nvec2 rotate2D(vec2 p, float a){\n  float c = cos(a);\n  float s = sin(a);\n  return mat2(c, -s, s, c) * p;\n}\n\nvec3 getGradientColor(float t){\n  float tt = clamp(t, 0.0, 1.0);\n  int count = uColorCount;\n  if (count < 2) count = 2;\n  float scaled = tt * float(count - 1);\n  float seg = floor(scaled);\n  float f = fract(scaled);\n\n  if (seg < 1.0) return mix(uColor0, uColor1, f);\n  if (seg < 2.0 && count > 2) return mix(uColor1, uColor2, f);\n  if (seg < 3.0 && count > 3) return mix(uColor2, uColor3, f);\n  if (seg < 4.0 && count > 4) return mix(uColor3, uColor4, f);\n  if (seg < 5.0 && count > 5) return mix(uColor4, uColor5, f);\n  if (seg < 6.0 && count > 6) return mix(uColor5, uColor6, f);\n  if (seg < 7.0 && count > 7) return mix(uColor6, uColor7, f);\n  if (count > 7) return uColor7;\n  if (count > 6) return uColor6;\n  if (count > 5) return uColor5;\n  if (count > 4) return uColor4;\n  if (count > 3) return uColor3;\n  if (count > 2) return uColor2;\n  return uColor1;\n}\n\nvoid mainImage( out vec4 fragColor, in vec2 fragCoord )\n{\n    vec2 uv0 = fragCoord.xy / iResolution.xy;\n\n    float aspect = iResolution.x / iResolution.y;\n    vec2 p = uv0 * 2.0 - 1.0;\n    p.x *= aspect;\n    vec2 pr = rotate2D(p, uAngle);\n    pr.x /= aspect;\n    vec2 uv = pr * 0.5 + 0.5;\n\n    vec2 uvMod = uv;\n    if (uDistort > 0.0) {\n      float a = uvMod.y * 6.0;\n      float b = uvMod.x * 6.0;\n      float w = 0.01 * uDistort;\n      uvMod.x += sin(a) * w;\n      uvMod.y += cos(b) * w;\n    }\n    float t = uvMod.x;\n    if (uMirror > 0.5) {\n      t = 1.0 - abs(1.0 - 2.0 * fract(t));\n    }\n    vec3 base = getGradientColor(t);\n\n    vec2 offset = vec2(iMouse.x/iResolution.x, iMouse.y/iResolution.y);\n  float d = length(uv0 - offset);\n  float r = max(uSpotlightRadius, 1e-4);\n  float dn = d / r;\n  float spot = (1.0 - 2.0 * pow(dn, uSpotlightSoftness)) * uSpotlightOpacity;\n  vec3 cir = vec3(spot);\n  float stripe = fract(uvMod.x * max(uBlindCount, 1.0));\n  if (uShineFlip > 0.5) stripe = 1.0 - stripe;\n    vec3 ran = vec3(stripe);\n\n    vec3 col = cir + base - ran;\n    col += (rand(gl_FragCoord.xy + iTime) - 0.5) * uNoise;\n\n    fragColor = vec4(col, 1.0);\n}\n\nvoid main() {\n    vec4 color;\n    mainImage(color, vUv * iResolution.xy);\n    gl_FragColor = color;\n}\n';
  function init(){ var els=document.querySelectorAll('.gradient-blinds'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"iMouse":{"t":"2f","v":[0,0]},"uAngle":{"t":"1f","v":0},"uNoise":{"t":"1f","v":0.3},"uBlindCount":{"t":"1f","v":16},"uSpotlightRadius":{"t":"1f","v":0.5},"uSpotlightSoftness":{"t":"1f","v":1},"uSpotlightOpacity":{"t":"1f","v":1},"uMirror":{"t":"1f","v":0},"uDistort":{"t":"1f","v":0},"uShineFlip":{"t":"1f","v":0},"uColor0":{"t":"3f","v":[1,0.6235294117647059,0.9882352941176471]},"uColor1":{"t":"3f","v":[0.3215686274509804,0.15294117647058825,1]},"uColor2":{"t":"3f","v":[0.3215686274509804,0.15294117647058825,1]},"uColor3":{"t":"3f","v":[0.3215686274509804,0.15294117647058825,1]},"uColor4":{"t":"3f","v":[0.3215686274509804,0.15294117647058825,1]},"uColor5":{"t":"3f","v":[0.3215686274509804,0.15294117647058825,1]},"uColor6":{"t":"3f","v":[0.3215686274509804,0.15294117647058825,1]},"uColor7":{"t":"3f","v":[0.3215686274509804,0.15294117647058825,1]},"uColorCount":{"t":"1i","v":2}} });  } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* grainient */
/* grainient.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='#version 300 es\nprecision highp float;\nuniform vec2 iResolution;\nuniform float iTime;\nuniform float uTimeSpeed;\nuniform float uColorBalance;\nuniform float uWarpStrength;\nuniform float uWarpFrequency;\nuniform float uWarpSpeed;\nuniform float uWarpAmplitude;\nuniform float uBlendAngle;\nuniform float uBlendSoftness;\nuniform float uRotationAmount;\nuniform float uNoiseScale;\nuniform float uGrainAmount;\nuniform float uGrainScale;\nuniform float uGrainAnimated;\nuniform float uContrast;\nuniform float uGamma;\nuniform float uSaturation;\nuniform vec2 uCenterOffset;\nuniform float uZoom;\nuniform vec3 uColor1;\nuniform vec3 uColor2;\nuniform vec3 uColor3;\nout vec4 fragColor;\n#define S(a,b,t) smoothstep(a,b,t)\nmat2 Rot(float a){float s=sin(a),c=cos(a);return mat2(c,-s,s,c);} \nvec2 hash(vec2 p){p=vec2(dot(p,vec2(2127.1,81.17)),dot(p,vec2(1269.5,283.37)));return fract(sin(p)*43758.5453);} \nfloat noise(vec2 p){vec2 i=floor(p),f=fract(p),u=f*f*(3.0-2.0*f);float n=mix(mix(dot(-1.0+2.0*hash(i+vec2(0.0,0.0)),f-vec2(0.0,0.0)),dot(-1.0+2.0*hash(i+vec2(1.0,0.0)),f-vec2(1.0,0.0)),u.x),mix(dot(-1.0+2.0*hash(i+vec2(0.0,1.0)),f-vec2(0.0,1.0)),dot(-1.0+2.0*hash(i+vec2(1.0,1.0)),f-vec2(1.0,1.0)),u.x),u.y);return 0.5+0.5*n;}\nvoid mainImage(out vec4 o, vec2 C){\n  float t=iTime*uTimeSpeed;\n  vec2 uv=C/iResolution.xy;\n  float ratio=iResolution.x/iResolution.y;\n  vec2 tuv=uv-0.5+uCenterOffset;\n  tuv/=max(uZoom,0.001);\n\n  float degree=noise(vec2(t*0.1,tuv.x*tuv.y)*uNoiseScale);\n  tuv.y*=1.0/ratio;\n  tuv*=Rot(radians((degree-0.5)*uRotationAmount+180.0));\n  tuv.y*=ratio;\n\n  float frequency=uWarpFrequency;\n  float ws=max(uWarpStrength,0.001);\n  float amplitude=uWarpAmplitude/ws;\n  float warpTime=t*uWarpSpeed;\n  tuv.x+=sin(tuv.y*frequency+warpTime)/amplitude;\n  tuv.y+=sin(tuv.x*(frequency*1.5)+warpTime)/(amplitude*0.5);\n\n  vec3 colLav=uColor1;\n  vec3 colOrg=uColor2;\n  vec3 colDark=uColor3;\n  float b=uColorBalance;\n  float s=max(uBlendSoftness,0.0);\n  mat2 blendRot=Rot(radians(uBlendAngle));\n  float blendX=(tuv*blendRot).x;\n  float edge0=-0.3-b-s;\n  float edge1=0.2-b+s;\n  float v0=0.5-b+s;\n  float v1=-0.3-b-s;\n  vec3 layer1=mix(colDark,colOrg,S(edge0,edge1,blendX));\n  vec3 layer2=mix(colOrg,colLav,S(edge0,edge1,blendX));\n  vec3 col=mix(layer1,layer2,S(v0,v1,tuv.y));\n\n  vec2 grainUv=uv*max(uGrainScale,0.001);\n  if(uGrainAnimated>0.5){grainUv+=vec2(iTime*0.05);} \n  float grain=fract(sin(dot(grainUv,vec2(12.9898,78.233)))*43758.5453);\n  col+=(grain-0.5)*uGrainAmount;\n\n  col=(col-0.5)*uContrast+0.5;\n  float luma=dot(col,vec3(0.2126,0.7152,0.0722));\n  col=mix(vec3(luma),col,uSaturation);\n  col=pow(max(col,0.0),vec3(1.0/max(uGamma,0.001)));\n  col=clamp(col,0.0,1.0);\n\n  o=vec4(col,1.0);\n}\nvoid main(){\n  vec4 o=vec4(0.0);\n  mainImage(o,gl_FragCoord.xy);\n  fragColor=o;\n}\n';
  function init(){ var els=document.querySelectorAll('.grainient'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"uTimeSpeed":{"t":"1f","v":1},"uColorBalance":{"t":"1f","v":1},"uWarpStrength":{"t":"1f","v":0.3},"uWarpFrequency":{"t":"1f","v":2},"uWarpSpeed":{"t":"1f","v":1},"uWarpAmplitude":{"t":"1f","v":0.3},"uBlendAngle":{"t":"1f","v":0.5},"uBlendSoftness":{"t":"1f","v":0.5},"uRotationAmount":{"t":"1f","v":1},"uNoiseScale":{"t":"1f","v":1},"uGrainAmount":{"t":"1f","v":1},"uGrainScale":{"t":"1f","v":1},"uGrainAnimated":{"t":"1f","v":1},"uContrast":{"t":"1f","v":1},"uGamma":{"t":"1f","v":1},"uSaturation":{"t":"1f","v":1},"uCenterOffset":{"t":"2f","v":[0.5,0.5]},"uZoom":{"t":"1f","v":1},"uColor1":{"t":"3f","v":[0.55,0.5,1]},"uColor2":{"t":"3f","v":[0.55,0.5,1]},"uColor3":{"t":"3f","v":[0.55,0.5,1]}} }); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* image-trail */
/* image-trail.js — ambient · spawn fading thumbnails along the pointer path over a .trail area. transform/opacity only. */
(function(g){ 'use strict';
  function off(){ return g.matchMedia && (g.matchMedia('(prefers-reduced-motion: reduce)').matches || g.matchMedia('(hover: none)').matches); }
  function attach(el){ if(el.__it) return; el.__it=1; var imgs=(el.getAttribute('data-images')||'').split(',').filter(Boolean); var k=0, last=0;
    el.style.position=el.style.position||'relative'; el.style.overflow='hidden';
    if(off()) return;
    el.addEventListener('pointermove', function(e){ var now=Date.now(); if(now-last<80) return; last=now; var r=el.getBoundingClientRect();
      var n=document.createElement(imgs.length?'img':'div'); if(imgs.length){ n.src=imgs[k%imgs.length]; } else { n.style.background='linear-gradient(135deg,#8b7cf6,#39d98a)'; }
      n.style.cssText+=';position:absolute;left:'+(e.clientX-r.left)+'px;top:'+(e.clientY-r.top)+'px;width:80px;height:56px;border-radius:8px;object-fit:cover;pointer-events:none;transform:translate(-50%,-50%)';
      el.appendChild(n); k++; n.animate([{opacity:.9,transform:'translate(-50%,-50%) scale(1)'},{opacity:0,transform:'translate(-50%,-50%) scale(.8)'}],{duration:700,easing:'ease-out'}); setTimeout(function(){ n.remove(); },700); }); }
  function init(){ var els=document.querySelectorAll('.trail'); for(var i=0;i<els.length;i++) attach(els[i]); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* iridescence */
/* iridescence.js — motion-anything recipe · ambient · a flowing iridescent field (faithful shader, dependency-free WebGL). */
(function (g) {
  'use strict';
  var FRAG = 'precision highp float;\n\
uniform float uTime; uniform vec3 uColor; uniform vec3 uResolution; uniform vec2 uMouse; uniform float uAmplitude; uniform float uSpeed;\n\
varying vec2 vUv;\n\
void main(){\n\
  float mr = min(uResolution.x, uResolution.y);\n\
  vec2 uv = (vUv.xy * 2.0 - 1.0) * uResolution.xy / mr;\n\
  uv += (uMouse - vec2(0.5)) * uAmplitude;\n\
  float d = -uTime * 0.5 * uSpeed; float a = 0.0;\n\
  for (float i = 0.0; i < 8.0; ++i){ a += cos(i - d - a * uv.x); d += sin(uv.y * i + a); }\n\
  d += uTime * 0.5 * uSpeed;\n\
  vec3 col = vec3(cos(uv * vec2(d, a)) * 0.6 + 0.4, cos(a + d) * 0.5 + 0.5);\n\
  col = cos(col * cos(vec3(d, a, 2.5)) * 0.5 + 0.5) * uColor;\n\
  gl_FragColor = vec4(col, 1.0);\n\
}\n';
  function init(){ var els=document.querySelectorAll('.iridescence'); for(var i=0;i<els.length;i++){ var el=els[i];
    var color=(el.getAttribute('data-color')||'1,1,1').split(',').map(Number);
    g.ShaderBG(el, FRAG, { uniforms:{ uColor:{t:'3f',v:color}, uSpeed:{t:'1f',v:parseFloat(el.getAttribute('data-speed'))||1.0}, uAmplitude:{t:'1f',v:parseFloat(el.getAttribute('data-amp'))||0.1} } }); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* kinetic-headline */
/* kinetic-headline.js — motion-anything recipe · category: text-kinetic
 *
 * Splits [data-kinetic] text into words (default) or letters (data-kinetic="letters") and staggers
 * them in on load. The animation STYLE is chosen with data-kinetic-anim (mirrors the video engine's
 * kinetic presets): rise (default) · fade · drop · pop · blur · flip · spin · slide · typewriter ·
 * wave (continuous, ambient). Honors prefers-reduced-motion (shows everything at once).
 *
 * Usage:
 *   <h1 data-kinetic>Words that move</h1>
 *   <h1 data-kinetic="letters" data-kinetic-anim="pop">Short line</h1>
 *   <h1 data-kinetic="letters" data-kinetic-anim="wave">ambient wave</h1>
 */
(function () {
  'use strict';

  function split(el) {
    var mode = el.getAttribute('data-kinetic') || 'words';
    var text = el.textContent;
    el.textContent = '';
    var step = (mode === 'letters') ? 40 : 70;
    var units = (mode === 'letters') ? text.split('') : text.split(/(\s+)/);
    var i = 0;
    units.forEach(function (u) {
      if (u === '') return;
      if (/^\s+$/.test(u)) {
        var sp = document.createElement('span');
        sp.className = 'k-space';
        el.appendChild(sp);
        return;
      }
      var s = document.createElement('span');
      s.className = 'k-unit';
      s.textContent = u;
      s.style.setProperty('--k-delay', (i * step) + 'ms');
      i++;
      el.appendChild(s);
    });
  }

  function init() {
    var els = document.querySelectorAll('[data-kinetic]');
    if (!els.length) return;
    var reduce = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    els.forEach(function (el) {
      el.classList.add('k-anim-' + (el.getAttribute('data-kinetic-anim') || 'rise'));
      split(el);
    });
    if (reduce) {
      els.forEach(function (el) { el.classList.add('is-in'); });
      return;
    }
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        els.forEach(function (el) { el.classList.add('is-in'); });
      });
    });
  }

  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})();


/* light-rays */
/* light-rays.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='precision highp float;\n\nuniform float iTime;\nuniform vec2  iResolution;\n\nuniform vec3  raysColor;\nuniform float raysSpeed;\nuniform float lightSpread;\nuniform float rayLength;\nuniform float pulsating;\nuniform float fadeDistance;\nuniform float saturation;\nuniform vec2  mousePos;\nuniform float mouseInfluence;\nuniform float noiseAmount;\nuniform float distortion;\n\nvarying vec2 vUv;\n\nfloat noise(vec2 st) {\n  return fract(sin(dot(st.xy, vec2(12.9898,78.233))) * 43758.5453123);\n}\n\nfloat rayStrength(vec2 raySource, vec2 rayRefDirection, vec2 coord,\n                  float seedA, float seedB, float speed) {\n  vec2 sourceToCoord = coord - raySource;\n  vec2 dirNorm = normalize(sourceToCoord);\n  float cosAngle = dot(dirNorm, rayRefDirection);\n\n  float distortedAngle = cosAngle + distortion * sin(iTime * 2.0 + length(sourceToCoord) * 0.01) * 0.2;\n  \n  float spreadFactor = pow(max(distortedAngle, 0.0), 1.0 / max(lightSpread, 0.001));\n\n  float distance = length(sourceToCoord);\n  float maxDistance = iResolution.x * rayLength;\n  float lengthFalloff = clamp((maxDistance - distance) / maxDistance, 0.0, 1.0);\n  \n  float fadeFalloff = clamp((iResolution.x * fadeDistance - distance) / (iResolution.x * fadeDistance), 0.5, 1.0);\n  float pulse = pulsating > 0.5 ? (0.8 + 0.2 * sin(iTime * speed * 3.0)) : 1.0;\n\n  float baseStrength = clamp(\n    (0.45 + 0.15 * sin(distortedAngle * seedA + iTime * speed)) +\n    (0.3 + 0.2 * cos(-distortedAngle * seedB + iTime * speed)),\n    0.0, 1.0\n  );\n\n  return baseStrength * lengthFalloff * fadeFalloff * spreadFactor * pulse;\n}\n\nvoid mainImage(out vec4 fragColor, in vec2 fragCoord) {\n  vec2 coord = vec2(fragCoord.x, iResolution.y - fragCoord.y);\n  vec2 rayPos = vec2(iResolution.x * 0.5, -0.2 * iResolution.y);\n  vec2 rayDir = vec2(0.0, 1.0);\n  \n  vec2 finalRayDir = rayDir;\n  if (mouseInfluence > 0.0) {\n    vec2 mouseScreenPos = mousePos * iResolution.xy;\n    vec2 mouseDirection = normalize(mouseScreenPos - rayPos);\n    finalRayDir = normalize(mix(rayDir, mouseDirection, mouseInfluence));\n  }\n\n  vec4 rays1 = vec4(1.0) *\n               rayStrength(rayPos, finalRayDir, coord, 36.2214, 21.11349,\n                           1.5 * raysSpeed);\n  vec4 rays2 = vec4(1.0) *\n               rayStrength(rayPos, finalRayDir, coord, 22.3991, 18.0234,\n                           1.1 * raysSpeed);\n\n  fragColor = rays1 * 0.5 + rays2 * 0.4;\n\n  if (noiseAmount > 0.0) {\n    float n = noise(coord * 0.01 + iTime * 0.1);\n    fragColor.rgb *= (1.0 - noiseAmount + noiseAmount * n);\n  }\n\n  float brightness = 1.0 - (coord.y / iResolution.y);\n  fragColor.x *= 0.1 + brightness * 0.8;\n  fragColor.y *= 0.3 + brightness * 0.6;\n  fragColor.z *= 0.5 + brightness * 0.5;\n\n  if (saturation != 1.0) {\n    float gray = dot(fragColor.rgb, vec3(0.299, 0.587, 0.114));\n    fragColor.rgb = mix(vec3(gray), fragColor.rgb, saturation);\n  }\n\n  fragColor.rgb *= raysColor;\n}\n\nvoid main() {\n  vec4 color;\n  mainImage(color, gl_FragCoord.xy);\n  gl_FragColor  = color;\n}';
  function init(){ var els=document.querySelectorAll('.light-rays'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"raysColor":{"t":"3f","v":[1,1,1]},"raysSpeed":{"t":"1f","v":1},"lightSpread":{"t":"1f","v":1},"rayLength":{"t":"1f","v":2},"pulsating":{"t":"1f","v":0},"fadeDistance":{"t":"1f","v":1},"saturation":{"t":"1f","v":1},"mousePos":{"t":"2f","v":[0.5,0.5]},"mouseInfluence":{"t":"1f","v":0},"noiseAmount":{"t":"1f","v":0},"distortion":{"t":"1f","v":0}} });  } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* lightfall */
/* lightfall.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='\nprecision highp float;\n\nuniform vec3  iResolution;\nuniform vec2  iMouse;\nuniform float iTime;\n\nuniform vec3  uColor0;\nuniform vec3  uColor1;\nuniform vec3  uColor2;\nuniform vec3  uColor3;\nuniform vec3  uColor4;\nuniform vec3  uColor5;\nuniform vec3  uColor6;\nuniform vec3  uColor7;\nuniform int   uColorCount;\n\nuniform vec3  uBgColor;\nuniform vec3  uMouseColor;\nuniform float uSpeed;\nuniform int   uStreakCount;\nuniform float uStreakWidth;\nuniform float uStreakLength;\nuniform float uGlow;\nuniform float uDensity;\nuniform float uTwinkle;\nuniform float uZoom;\nuniform float uBgGlow;\nuniform float uOpacity;\nuniform float uMouseEnabled;\nuniform float uMouseStrength;\nuniform float uMouseRadius;\n\nvarying vec2 vUv;\n\nvec3 palette(float h) {\n  int count = uColorCount;\n  if (count < 1) count = 1;\n  int idx = int(floor(clamp(h, 0.0, 0.999999) * float(count)));\n  if (idx <= 0) return uColor0;\n  if (idx == 1) return uColor1;\n  if (idx == 2) return uColor2;\n  if (idx == 3) return uColor3;\n  if (idx == 4) return uColor4;\n  if (idx == 5) return uColor5;\n  if (idx == 6) return uColor6;\n  return uColor7;\n}\n\nvec3 tanhv(vec3 x) {\n  vec3 e = exp(-2.0 * x);\n  return (1.0 - e) / (1.0 + e);\n}\n\nvec2 sceneC(vec2 frag, vec2 r) {\n  vec2 P = (frag + frag - r) / r.x;\n  float z = 0.0;\n  float d = 1e3;\n  vec4 O = vec4(0.0);\n  for (int k = 0; k < 39; k++) {\n    if (d <= 1e-4) break;\n    O = z * normalize(vec4(P, uZoom, 0.0)) - vec4(0.0, 4.0, 1.0, 0.0) / 4.5;\n    d = 1.0 - sqrt(length(O * O));\n    z += d;\n  }\n  return vec2(O.x, atan(O.z, O.y));\n}\n\nvoid mainImage(out vec4 o, vec2 C) {\n  vec2 r = iResolution.xy;\n  vec2 uv0 = (C + C - r) / r.x;\n  float T = 0.1 * iTime * uSpeed + 9.0;\n  float angRings = max(1.0, floor(6.28318530718 * max(uDensity, 0.05) + 0.5));\n  vec2 Y = vec2(5e-3, 6.28318530718 / angRings);\n\n  vec2 c0 = sceneC(C, r);\n  vec2 cdx = sceneC(C + vec2(1.0, 0.0), r);\n  vec2 cdy = sceneC(C + vec2(0.0, 1.0), r);\n  vec2 dCx = cdx - c0;\n  vec2 dCy = cdy - c0;\n  dCx.y -= 6.28318530718 * floor(dCx.y / 6.28318530718 + 0.5);\n  dCy.y -= 6.28318530718 * floor(dCy.y / 6.28318530718 + 0.5);\n  vec2 fw = abs(dCx) + abs(dCy);\n  C = c0;\n\n  vec2 P = vec2(2.0, 1.0) * uv0 - (r / r.x) * vec2(0.0, 1.0);\n  vec4 O = vec4(uBgColor * 90.0 * uBgGlow / (1e3 * dot(P, P) + 6.0), 0.0);\n\n  float mGlow = 0.0;\n  if (uMouseEnabled > 0.5) {\n    vec2 mN = (iMouse + iMouse - r) / r.x;\n    float md = length(uv0 - mN);\n    mGlow = exp(-md * md / max(uMouseRadius * uMouseRadius, 1e-4)) * uMouseStrength;\n    O.rgb += uMouseColor * mGlow * 0.25;\n  }\n\n  float zr = 5e-4 * uStreakWidth;\n  vec2 rr = vec2(max(length(fw), 1e-5));\n  float tail = 19.0 / max(uStreakLength, 0.05);\n\n  for (int m = 0; m < 16; m++) {\n    if (m >= uStreakCount) break;\n    float jf = float(m) + 1.0;\n    float ic = fract(sin(dot(vec2(jf, floor(C.x / Y.x + 0.5)), vec2(7.0, 11.0)) * 73.0));\n    vec2 Pp = C - (T + T * ic) * vec2(0.0, 1.0);\n    Pp -= floor(Pp / Y + 0.5) * Y;\n    float h = fract(8663.0 * ic);\n    vec3 col = palette(h);\n    float weight = mix(1.5, 1.0 + sin(T + 7.0 * h + 4.0), uTwinkle);\n    weight *= (1.0 + mGlow * 2.0);\n    vec2 inner = vec2(length(max(Pp, vec2(-1.0, 0.0))), length(Pp) - zr) - zr;\n    vec2 sm = vec2(1.0) - smoothstep(-rr, rr, inner);\n    O.rgb += dot(sm, vec2(exp(tail * Pp.y), 3.0)) * col * weight;\n    C.x += Y.x / 8.0;\n  }\n\n  vec3 colr = sqrt(tanhv(max(O.rgb * uGlow - vec3(0.04, 0.08, 0.02), 0.0)));\n  o = vec4(colr, uOpacity);\n}\n\nvoid main() {\n  vec4 color;\n  mainImage(color, vUv * iResolution.xy);\n  gl_FragColor = color;\n}\n';
  function init(){ var els=document.querySelectorAll('.lightfall'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"iMouse":{"t":"2f","v":[0.5,0.5]},"uColor0":{"t":"3f","v":[0.55,0.5,1]},"uColor1":{"t":"3f","v":[0.55,0.5,1]},"uColor2":{"t":"3f","v":[0.55,0.5,1]},"uColor3":{"t":"3f","v":[0.55,0.5,1]},"uColor4":{"t":"3f","v":[0.55,0.5,1]},"uColor5":{"t":"3f","v":[0.55,0.5,1]},"uColor6":{"t":"3f","v":[0.55,0.5,1]},"uColor7":{"t":"3f","v":[0.55,0.5,1]},"uColorCount":{"t":"1i","v":1},"uBgColor":{"t":"3f","v":[0.55,0.5,1]},"uMouseColor":{"t":"3f","v":[0.55,0.5,1]},"uSpeed":{"t":"1f","v":1},"uStreakCount":{"t":"1i","v":1},"uStreakWidth":{"t":"1f","v":1},"uStreakLength":{"t":"1f","v":1},"uGlow":{"t":"1f","v":1},"uDensity":{"t":"1f","v":1},"uTwinkle":{"t":"1f","v":1},"uZoom":{"t":"1f","v":1},"uBgGlow":{"t":"1f","v":1},"uOpacity":{"t":"1f","v":1},"uMouseEnabled":{"t":"1f","v":1},"uMouseStrength":{"t":"1f","v":1},"uMouseRadius":{"t":"1f","v":1}} }); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* lightning */
/* lightning.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='\n      precision mediump float;\n      uniform vec2 iResolution;\n      uniform float iTime;\n      uniform float uHue;\n      uniform float uXOffset;\n      uniform float uSpeed;\n      uniform float uIntensity;\n      uniform float uSize;\n      \n      #define OCTAVE_COUNT 10\n\n      vec3 hsv2rgb(vec3 c) {\n          vec3 rgb = clamp(abs(mod(c.x * 6.0 + vec3(0.0,4.0,2.0), 6.0) - 3.0) - 1.0, 0.0, 1.0);\n          return c.z * mix(vec3(1.0), rgb, c.y);\n      }\n\n      float hash11(float p) {\n          p = fract(p * .1031);\n          p *= p + 33.33;\n          p *= p + p;\n          return fract(p);\n      }\n\n      float hash12(vec2 p) {\n          vec3 p3 = fract(vec3(p.xyx) * .1031);\n          p3 += dot(p3, p3.yzx + 33.33);\n          return fract((p3.x + p3.y) * p3.z);\n      }\n\n      mat2 rotate2d(float theta) {\n          float c = cos(theta);\n          float s = sin(theta);\n          return mat2(c, -s, s, c);\n      }\n\n      float noise(vec2 p) {\n          vec2 ip = floor(p);\n          vec2 fp = fract(p);\n          float a = hash12(ip);\n          float b = hash12(ip + vec2(1.0, 0.0));\n          float c = hash12(ip + vec2(0.0, 1.0));\n          float d = hash12(ip + vec2(1.0, 1.0));\n          \n          vec2 t = smoothstep(0.0, 1.0, fp);\n          return mix(mix(a, b, t.x), mix(c, d, t.x), t.y);\n      }\n\n      float fbm(vec2 p) {\n          float value = 0.0;\n          float amplitude = 0.5;\n          for (int i = 0; i < OCTAVE_COUNT; ++i) {\n              value += amplitude * noise(p);\n              p *= rotate2d(0.45);\n              p *= 2.0;\n              amplitude *= 0.5;\n          }\n          return value;\n      }\n\n      void mainImage( out vec4 fragColor, in vec2 fragCoord ) {\n          vec2 uv = fragCoord / iResolution.xy;\n          uv = 2.0 * uv - 1.0;\n          uv.x *= iResolution.x / iResolution.y;\n          uv.x += uXOffset;\n          \n          uv += 2.0 * fbm(uv * uSize + 0.8 * iTime * uSpeed) - 1.0;\n          \n          float dist = abs(uv.x);\n          vec3 baseColor = hsv2rgb(vec3(uHue / 360.0, 0.7, 0.8));\n          vec3 col = baseColor * pow(mix(0.0, 0.07, hash11(iTime * uSpeed)) / dist, 1.0) * uIntensity;\n          col = pow(col, vec3(1.0));\n          float a = clamp(max(col.r, max(col.g, col.b)), 0.0, 1.0);\n          fragColor = vec4(col, a);\n      }\n\n      void main() {\n          mainImage(gl_FragColor, gl_FragCoord.xy);\n      }\n    ';
  function init(){ var els=document.querySelectorAll('.lightning'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    var h=g.ShaderBG(els[i], FRAG, {"uniforms":{"uHue":{"t":"1f","v":230},"uXOffset":{"t":"1f","v":0},"uSpeed":{"t":"1f","v":1},"uIntensity":{"t":"1f","v":1},"uSize":{"t":"1f","v":1}}}); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* like-burst */
/* like-burst.js — motion-anything recipe · category: feedback-delight
 *
 * A celebratory particle burst for like / favorite / reaction buttons.
 * - Fires on user tap only (never auto-plays).
 * - Bursts on the "like" transition only, not on un-like.
 * - Respects prefers-reduced-motion (scale-only fallback).
 * - Cleans up its DOM nodes after the animation (per MOTION-SPEC §5).
 *
 * Usage:
 *   <button class="like-btn" data-like-burst aria-pressed="false">♥</button>
 *   // auto-attaches to [data-like-burst], or:  attachLikeBurst(el)
 */
(function (global) {
  'use strict';

  var PARTICLE_COUNT = 14;
  var COLORS = ['#ff4d6d', '#ffd166', '#06d6a0', '#4d96ff', '#c77dff'];

  function prefersReducedMotion() {
    return (
      global.matchMedia &&
      global.matchMedia('(prefers-reduced-motion: reduce)').matches
    );
  }

  function spawnParticles(btn) {
    var rect = btn.getBoundingClientRect();
    var cx = rect.left + rect.width / 2 + global.scrollX;
    var cy = rect.top + rect.height / 2 + global.scrollY;

    var layer = document.createElement('div');
    layer.className = 'lb-particles';
    layer.style.left = cx + 'px';
    layer.style.top = cy + 'px';
    document.body.appendChild(layer);

    for (var i = 0; i < PARTICLE_COUNT; i++) {
      var p = document.createElement('span');
      p.className = 'lb-particle';
      var angle = (Math.PI * 2 * i) / PARTICLE_COUNT + Math.random() * 0.4;
      var dist = 26 + Math.random() * 26;
      p.style.setProperty('--dx', Math.cos(angle) * dist + 'px');
      p.style.setProperty('--dy', Math.sin(angle) * dist + 'px');
      p.style.background = COLORS[i % COLORS.length];
      p.style.animationDelay = Math.random() * 40 + 'ms';
      layer.appendChild(p);
    }
    // Clean up after the burst finishes (600ms anim + max 40ms delay + margin).
    setTimeout(function () {
      layer.remove();
    }, 700);
  }

  function attachLikeBurst(btn) {
    if (!btn || btn.__likeBurstBound) return;
    btn.__likeBurstBound = true;

    btn.addEventListener('click', function () {
      var liked = btn.classList.toggle('is-liked');
      btn.setAttribute('aria-pressed', String(liked));

      // Restart the pop animation from the top.
      btn.classList.remove('lb-pop');
      void btn.offsetWidth; // force reflow so the animation re-triggers
      btn.classList.add('lb-pop');

      if (liked && !prefersReducedMotion()) {
        spawnParticles(btn);
      }
    });
  }

  function autoAttach() {
    var els = document.querySelectorAll('[data-like-burst]');
    for (var i = 0; i < els.length; i++) attachLikeBurst(els[i]);
  }

  global.attachLikeBurst = attachLikeBurst;

  if (document.readyState !== 'loading') autoAttach();
  else document.addEventListener('DOMContentLoaded', autoAttach);
})(window);


/* line-waves */
/* line-waves.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='\nprecision highp float;\n\nuniform float uTime;\nuniform vec3 uResolution;\nuniform float uSpeed;\nuniform float uInnerLines;\nuniform float uOuterLines;\nuniform float uWarpIntensity;\nuniform float uRotation;\nuniform float uEdgeFadeWidth;\nuniform float uColorCycleSpeed;\nuniform float uBrightness;\nuniform vec3 uColor1;\nuniform vec3 uColor2;\nuniform vec3 uColor3;\nuniform vec2 uMouse;\nuniform float uMouseInfluence;\nuniform bool uEnableMouse;\n\n#define HALF_PI 1.5707963\n\nfloat hashF(float n) {\n  return fract(sin(n * 127.1) * 43758.5453123);\n}\n\nfloat smoothNoise(float x) {\n  float i = floor(x);\n  float f = fract(x);\n  float u = f * f * (3.0 - 2.0 * f);\n  return mix(hashF(i), hashF(i + 1.0), u);\n}\n\nfloat displaceA(float coord, float t) {\n  float result = sin(coord * 2.123) * 0.2;\n  result += sin(coord * 3.234 + t * 4.345) * 0.1;\n  result += sin(coord * 0.589 + t * 0.934) * 0.5;\n  return result;\n}\n\nfloat displaceB(float coord, float t) {\n  float result = sin(coord * 1.345) * 0.3;\n  result += sin(coord * 2.734 + t * 3.345) * 0.2;\n  result += sin(coord * 0.189 + t * 0.934) * 0.3;\n  return result;\n}\n\nvec2 rotate2D(vec2 p, float angle) {\n  float c = cos(angle);\n  float s = sin(angle);\n  return vec2(p.x * c - p.y * s, p.x * s + p.y * c);\n}\n\nvoid main() {\n  vec2 coords = gl_FragCoord.xy / uResolution.xy;\n  coords = coords * 2.0 - 1.0;\n  coords = rotate2D(coords, uRotation);\n\n  float halfT = uTime * uSpeed * 0.5;\n  float fullT = uTime * uSpeed;\n\n  float mouseWarp = 0.0;\n  if (uEnableMouse) {\n    vec2 mPos = rotate2D(uMouse * 2.0 - 1.0, uRotation);\n    float mDist = length(coords - mPos);\n    mouseWarp = uMouseInfluence * exp(-mDist * mDist * 4.0);\n  }\n\n  float warpAx = coords.x + displaceA(coords.y, halfT) * uWarpIntensity + mouseWarp;\n  float warpAy = coords.y - displaceA(coords.x * cos(fullT) * 1.235, halfT) * uWarpIntensity;\n  float warpBx = coords.x + displaceB(coords.y, halfT) * uWarpIntensity + mouseWarp;\n  float warpBy = coords.y - displaceB(coords.x * sin(fullT) * 1.235, halfT) * uWarpIntensity;\n\n  vec2 fieldA = vec2(warpAx, warpAy);\n  vec2 fieldB = vec2(warpBx, warpBy);\n  vec2 blended = mix(fieldA, fieldB, mix(fieldA, fieldB, 0.5));\n\n  float fadeTop = smoothstep(uEdgeFadeWidth, uEdgeFadeWidth + 0.4, blended.y);\n  float fadeBottom = smoothstep(-uEdgeFadeWidth, -(uEdgeFadeWidth + 0.4), blended.y);\n  float vMask = 1.0 - max(fadeTop, fadeBottom);\n\n  float tileCount = mix(uOuterLines, uInnerLines, vMask);\n  float scaledY = blended.y * tileCount;\n  float nY = smoothNoise(abs(scaledY));\n\n  float ridge = pow(\n    step(abs(nY - blended.x) * 2.0, HALF_PI) * cos(2.0 * (nY - blended.x)),\n    5.0\n  );\n\n  float lines = 0.0;\n  for (float i = 1.0; i < 3.0; i += 1.0) {\n    lines += pow(max(fract(scaledY), fract(-scaledY)), i * 2.0);\n  }\n\n  float pattern = vMask * lines;\n\n  float cycleT = fullT * uColorCycleSpeed;\n  float rChannel = (pattern + lines * ridge) * (cos(blended.y + cycleT * 0.234) * 0.5 + 1.0);\n  float gChannel = (pattern + vMask * ridge) * (sin(blended.x + cycleT * 1.745) * 0.5 + 1.0);\n  float bChannel = (pattern + lines * ridge) * (cos(blended.x + cycleT * 0.534) * 0.5 + 1.0);\n\n  vec3 col = (rChannel * uColor1 + gChannel * uColor2 + bChannel * uColor3) * uBrightness;\n  float alpha = clamp(length(col), 0.0, 1.0);\n\n  gl_FragColor = vec4(col, alpha);\n}\n';
  function init(){ var els=document.querySelectorAll('.line-waves'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"uSpeed":{"t":"1f","v":0.3},"uInnerLines":{"t":"1f","v":32},"uOuterLines":{"t":"1f","v":36},"uWarpIntensity":{"t":"1f","v":1},"uRotation":{"t":"1f","v":0},"uEdgeFadeWidth":{"t":"1f","v":0},"uColorCycleSpeed":{"t":"1f","v":1},"uBrightness":{"t":"1f","v":0.2},"uColor1":{"t":"3f","v":[1,1,1]},"uColor2":{"t":"3f","v":[1,1,1]},"uColor3":{"t":"3f","v":[1,1,1]},"uMouseInfluence":{"t":"1f","v":0},"uEnableMouse":{"t":"1i","v":0}} });  } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* liquid-chrome */
/* liquid-chrome.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='\n      precision highp float;\n      uniform float uTime;\n      uniform vec3 uResolution;\n      uniform vec3 uBaseColor;\n      uniform float uAmplitude;\n      uniform float uFrequencyX;\n      uniform float uFrequencyY;\n      uniform vec2 uMouse;\n      varying vec2 vUv;\n\n      vec4 renderImage(vec2 uvCoord) {\n          vec2 fragCoord = uvCoord * uResolution.xy;\n          vec2 uv = (2.0 * fragCoord - uResolution.xy) / min(uResolution.x, uResolution.y);\n\n          for (float i = 1.0; i < 10.0; i++){\n              uv.x += uAmplitude / i * cos(i * uFrequencyX * uv.y + uTime + uMouse.x * 3.14159);\n              uv.y += uAmplitude / i * cos(i * uFrequencyY * uv.x + uTime + uMouse.y * 3.14159);\n          }\n\n          vec2 diff = (uvCoord - uMouse);\n          float dist = length(diff);\n          float falloff = exp(-dist * 20.0);\n          float ripple = sin(10.0 * dist - uTime * 2.0) * 0.03;\n          uv += (diff / (dist + 0.0001)) * ripple * falloff;\n\n          vec3 color = uBaseColor / abs(sin(uTime - uv.y - uv.x));\n          return vec4(color, 1.0);\n      }\n\n      void main() {\n          vec4 col = vec4(0.0);\n          int samples = 0;\n          for (int i = -1; i <= 1; i++){\n              for (int j = -1; j <= 1; j++){\n                  vec2 offset = vec2(float(i), float(j)) * (1.0 / min(uResolution.x, uResolution.y));\n                  col += renderImage(vUv + offset);\n                  samples++;\n              }\n          }\n          gl_FragColor = col / float(samples);\n      }\n    ';
  function init(){ var els=document.querySelectorAll('.liquid-chrome'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"uBaseColor":{"t":"3f","v":[0.55,0.5,1]},"uAmplitude":{"t":"1f","v":0.3},"uFrequencyX":{"t":"1f","v":2},"uFrequencyY":{"t":"1f","v":2}} }); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* magnet-lines */
/* magnet-lines.js — ambient · build a grid; each line rotates to face the pointer. Off under reduced-motion. */
(function(g){ 'use strict';
  function red(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function attach(el){ if(el.__ml) return; el.__ml=1; var rows=+el.getAttribute('data-rows')||9, cols=+el.getAttribute('data-cols')||9;
    el.style.gridTemplateColumns='repeat('+cols+',1fr)'; var items=[]; for(var i=0;i<rows*cols;i++){ var d=document.createElement('div'); d.className='ml'; el.appendChild(d); items.push(d); }
    if(red()) return;
    el.addEventListener('pointermove', function(e){ items.forEach(function(d){ var r=d.getBoundingClientRect(); var a=Math.atan2(e.clientY-(r.top+r.height/2), e.clientX-(r.left+r.width/2)); d.style.transform='rotate('+(a+Math.PI/2)+'rad)'; }); }); }
  function init(){ var els=document.querySelectorAll('.mag-lines'); for(var i=0;i<els.length;i++) attach(els[i]); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* magnetic-button */
/* magnetic-button.js — motion-anything recipe · category: hover-press
 *
 * Pulls a button toward the pointer as it nears, then springs back on leave.
 * - Subtle by design (a few px). Strength via data-magnet-strength (default 0.3).
 * - Disabled under prefers-reduced-motion and on touch (no cursor to attract).
 * - Uses transform only; resets cleanly on pointerleave.
 *
 * Usage:
 *   <button class="magnetic" data-magnet-strength="0.3">Get started</button>
 */
(function (global) {
  'use strict';

  function reduced() {
    return global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }
  function isTouch() {
    return global.matchMedia && global.matchMedia('(hover: none)').matches;
  }

  function attach(btn) {
    if (btn.__magnetBound) return;
    btn.__magnetBound = true;
    var strength = parseFloat(btn.getAttribute('data-magnet-strength')) || 0.3;

    btn.addEventListener('pointermove', function (e) {
      var r = btn.getBoundingClientRect();
      var dx = e.clientX - (r.left + r.width / 2);
      var dy = e.clientY - (r.top + r.height / 2);
      btn.style.transform = 'translate(' + dx * strength + 'px,' + dy * strength + 'px)';
    });
    btn.addEventListener('pointerleave', function () {
      btn.style.transform = '';
    });
  }

  function init() {
    if (reduced() || isTouch()) return; // leave buttons static
    var els = document.querySelectorAll('.magnetic');
    for (var i = 0; i < els.length; i++) attach(els[i]);
  }

  global.attachMagnetic = attach;
  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})(window);


/* noise */
/* noise.js — motion-anything recipe · ambient · faithful canvas 2D port (dependency-free).
 * Film-grain noise overlay: a 1024² random-luminance pattern refreshed every N frames,
 * stretched over the container with pixelated rendering. Real defaults from source. */
(function(g){ 'use strict';
  var DEF = { patternRefreshInterval: 2, patternAlpha: 15 };
  function reduced(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function start(el){
    if(el.__ma) return; el.__ma = 1;
    var canvas = document.createElement('canvas');
    canvas.style.cssText = 'width:100%;height:100%;display:block;image-rendering:pixelated';
    el.appendChild(canvas);
    var ctx = canvas.getContext('2d', { alpha:true });
    var SIZE = 1024; canvas.width = SIZE; canvas.height = SIZE;
    var frame = 0;
    function drawGrain(){
      var imageData = ctx.createImageData(SIZE, SIZE), data = imageData.data;
      for(var i=0;i<data.length;i+=4){ var v = Math.random()*255; data[i]=v; data[i+1]=v; data[i+2]=v; data[i+3]=DEF.patternAlpha; }
      ctx.putImageData(imageData, 0, 0);
    }
    if(reduced()){ drawGrain(); return; }
    (function loop(){ if(frame % DEF.patternRefreshInterval === 0) drawGrain(); frame++; requestAnimationFrame(loop); })();
  }
  function init(){ var els=document.querySelectorAll('.noise'); for(var i=0;i<els.length;i++) start(els[i]); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* orb */
/* orb.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='\n    precision highp float;\n\n    uniform float iTime;\n    uniform vec3 iResolution;\n    uniform float hue;\n    uniform float hover;\n    uniform float rot;\n    uniform float hoverIntensity;\n    uniform vec3 backgroundColor;\n    varying vec2 vUv;\n\n    vec3 rgb2yiq(vec3 c) {\n      float y = dot(c, vec3(0.299, 0.587, 0.114));\n      float i = dot(c, vec3(0.596, -0.274, -0.322));\n      float q = dot(c, vec3(0.211, -0.523, 0.312));\n      return vec3(y, i, q);\n    }\n    \n    vec3 yiq2rgb(vec3 c) {\n      float r = c.x + 0.956 * c.y + 0.621 * c.z;\n      float g = c.x - 0.272 * c.y - 0.647 * c.z;\n      float b = c.x - 1.106 * c.y + 1.703 * c.z;\n      return vec3(r, g, b);\n    }\n    \n    vec3 adjustHue(vec3 color, float hueDeg) {\n      float hueRad = hueDeg * 3.14159265 / 180.0;\n      vec3 yiq = rgb2yiq(color);\n      float cosA = cos(hueRad);\n      float sinA = sin(hueRad);\n      float i = yiq.y * cosA - yiq.z * sinA;\n      float q = yiq.y * sinA + yiq.z * cosA;\n      yiq.y = i;\n      yiq.z = q;\n      return yiq2rgb(yiq);\n    }\n\n    vec3 hash33(vec3 p3) {\n      p3 = fract(p3 * vec3(0.1031, 0.11369, 0.13787));\n      p3 += dot(p3, p3.yxz + 19.19);\n      return -1.0 + 2.0 * fract(vec3(\n        p3.x + p3.y,\n        p3.x + p3.z,\n        p3.y + p3.z\n      ) * p3.zyx);\n    }\n\n    float snoise3(vec3 p) {\n      const float K1 = 0.333333333;\n      const float K2 = 0.166666667;\n      vec3 i = floor(p + (p.x + p.y + p.z) * K1);\n      vec3 d0 = p - (i - (i.x + i.y + i.z) * K2);\n      vec3 e = step(vec3(0.0), d0 - d0.yzx);\n      vec3 i1 = e * (1.0 - e.zxy);\n      vec3 i2 = 1.0 - e.zxy * (1.0 - e);\n      vec3 d1 = d0 - (i1 - K2);\n      vec3 d2 = d0 - (i2 - K1);\n      vec3 d3 = d0 - 0.5;\n      vec4 h = max(0.6 - vec4(\n        dot(d0, d0),\n        dot(d1, d1),\n        dot(d2, d2),\n        dot(d3, d3)\n      ), 0.0);\n      vec4 n = h * h * h * h * vec4(\n        dot(d0, hash33(i)),\n        dot(d1, hash33(i + i1)),\n        dot(d2, hash33(i + i2)),\n        dot(d3, hash33(i + 1.0))\n      );\n      return dot(vec4(31.316), n);\n    }\n\n    vec4 extractAlpha(vec3 colorIn) {\n      float a = max(max(colorIn.r, colorIn.g), colorIn.b);\n      return vec4(colorIn.rgb / (a + 1e-5), a);\n    }\n\n    const vec3 baseColor1 = vec3(0.611765, 0.262745, 0.996078);\n    const vec3 baseColor2 = vec3(0.298039, 0.760784, 0.913725);\n    const vec3 baseColor3 = vec3(0.062745, 0.078431, 0.600000);\n    const float innerRadius = 0.6;\n    const float noiseScale = 0.65;\n\n    float light1(float intensity, float attenuation, float dist) {\n      return intensity / (1.0 + dist * attenuation);\n    }\n    float light2(float intensity, float attenuation, float dist) {\n      return intensity / (1.0 + dist * dist * attenuation);\n    }\n\n    vec4 draw(vec2 uv) {\n      vec3 color1 = adjustHue(baseColor1, hue);\n      vec3 color2 = adjustHue(baseColor2, hue);\n      vec3 color3 = adjustHue(baseColor3, hue);\n      \n      float ang = atan(uv.y, uv.x);\n      float len = length(uv);\n      float invLen = len > 0.0 ? 1.0 / len : 0.0;\n\n      float bgLuminance = dot(backgroundColor, vec3(0.299, 0.587, 0.114));\n      \n      float n0 = snoise3(vec3(uv * noiseScale, iTime * 0.5)) * 0.5 + 0.5;\n      float r0 = mix(mix(innerRadius, 1.0, 0.4), mix(innerRadius, 1.0, 0.6), n0);\n      float d0 = distance(uv, (r0 * invLen) * uv);\n      float v0 = light1(1.0, 10.0, d0);\n\n      v0 *= smoothstep(r0 * 1.05, r0, len);\n      float innerFade = smoothstep(r0 * 0.8, r0 * 0.95, len);\n      v0 *= mix(innerFade, 1.0, bgLuminance * 0.7);\n      float cl = cos(ang + iTime * 2.0) * 0.5 + 0.5;\n      \n      float a = iTime * -1.0;\n      vec2 pos = vec2(cos(a), sin(a)) * r0;\n      float d = distance(uv, pos);\n      float v1 = light2(1.5, 5.0, d);\n      v1 *= light1(1.0, 50.0, d0);\n      \n      float v2 = smoothstep(1.0, mix(innerRadius, 1.0, n0 * 0.5), len);\n      float v3 = smoothstep(innerRadius, mix(innerRadius, 1.0, 0.5), len);\n      \n      vec3 colBase = mix(color1, color2, cl);\n      float fadeAmount = mix(1.0, 0.1, bgLuminance);\n      \n      vec3 darkCol = mix(color3, colBase, v0);\n      darkCol = (darkCol + v1) * v2 * v3;\n      darkCol = clamp(darkCol, 0.0, 1.0);\n      \n      vec3 lightCol = (colBase + v1) * mix(1.0, v2 * v3, fadeAmount);\n      lightCol = mix(backgroundColor, lightCol, v0);\n      lightCol = clamp(lightCol, 0.0, 1.0);\n      \n      vec3 finalCol = mix(darkCol, lightCol, bgLuminance);\n      \n      return extractAlpha(finalCol);\n    }\n\n    vec4 mainImage(vec2 fragCoord) {\n      vec2 center = iResolution.xy * 0.5;\n      float size = min(iResolution.x, iResolution.y);\n      vec2 uv = (fragCoord - center) / size * 2.0;\n      \n      float angle = rot;\n      float s = sin(angle);\n      float c = cos(angle);\n      uv = vec2(c * uv.x - s * uv.y, s * uv.x + c * uv.y);\n      \n      uv.x += hover * hoverIntensity * 0.1 * sin(uv.y * 10.0 + iTime);\n      uv.y += hover * hoverIntensity * 0.1 * sin(uv.x * 10.0 + iTime);\n      \n      return draw(uv);\n    }\n\n    void main() {\n      vec2 fragCoord = vUv * iResolution.xy;\n      vec4 col = mainImage(fragCoord);\n      gl_FragColor = vec4(col.rgb * col.a, col.a);\n    }\n  ';
  function init(){ var els=document.querySelectorAll('.orb'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"hue":{"t":"1f","v":0},"hover":{"t":"1f","v":1},"rot":{"t":"1f","v":1},"hoverIntensity":{"t":"1f","v":1},"backgroundColor":{"t":"3f","v":[0,0,0]}} }); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* particles */
/* particles.js — motion-anything recipe · ambient · faithful raw-WebGL port (dependency-free).
 * Floating particle cloud (react-bits "Particles", originally ogl POINTS): 200 points in a sphere,
 * per-point drift in the vertex shader, slow scene wobble/rotation, soft round sprites.
 * ogl is replaced by ~40 lines of matrix math + a POINTS draw. Real defaults from source. */
(function(g){ 'use strict';
  var DEF = { particleCount:200, particleSpread:10, speed:0.1, particleBaseSize:100,
    sizeRandomness:1, cameraDistance:20, alphaParticles:0 };
  function reduced(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  var VERT = ''
  + 'attribute vec3 position;\n'
  + 'attribute vec4 random;\n'
  + 'attribute vec3 color;\n'
  + 'uniform mat4 modelMatrix;\n'
  + 'uniform mat4 viewMatrix;\n'
  + 'uniform mat4 projectionMatrix;\n'
  + 'uniform float uTime;\n'
  + 'uniform float uSpread;\n'
  + 'uniform float uBaseSize;\n'
  + 'uniform float uSizeRandomness;\n'
  + 'varying vec4 vRandom;\n'
  + 'varying vec3 vColor;\n'
  + 'void main() {\n'
  + '  vRandom = random;\n'
  + '  vColor = color;\n'
  + '  vec3 pos = position * uSpread;\n'
  + '  pos.z *= 10.0;\n'
  + '  vec4 mPos = modelMatrix * vec4(pos, 1.0);\n'
  + '  float t = uTime;\n'
  + '  mPos.x += sin(t * random.z + 6.28 * random.w) * mix(0.1, 1.5, random.x);\n'
  + '  mPos.y += sin(t * random.y + 6.28 * random.x) * mix(0.1, 1.5, random.w);\n'
  + '  mPos.z += sin(t * random.w + 6.28 * random.y) * mix(0.1, 1.5, random.z);\n'
  + '  vec4 mvPos = viewMatrix * mPos;\n'
  + '  if (uSizeRandomness == 0.0) { gl_PointSize = uBaseSize; }\n'
  + '  else { gl_PointSize = (uBaseSize * (1.0 + uSizeRandomness * (random.x - 0.5))) / length(mvPos.xyz); }\n'
  + '  gl_Position = projectionMatrix * mvPos;\n'
  + '}\n';
  var FRAG = ''
  + 'precision highp float;\n'
  + 'uniform float uTime;\n'
  + 'uniform float uAlphaParticles;\n'
  + 'varying vec4 vRandom;\n'
  + 'varying vec3 vColor;\n'
  + 'void main() {\n'
  + '  vec2 uv = gl_PointCoord.xy;\n'
  + '  float d = length(uv - vec2(0.5));\n'
  + '  if(uAlphaParticles < 0.5) {\n'
  + '    if(d > 0.5) { discard; }\n'
  + '    gl_FragColor = vec4(vColor + 0.2 * sin(uv.yxx + uTime + vRandom.y * 6.28), 1.0);\n'
  + '  } else {\n'
  + '    float circle = smoothstep(0.5, 0.4, d) * 0.8;\n'
  + '    gl_FragColor = vec4(vColor + 0.2 * sin(uv.yxx + uTime + vRandom.y * 6.28), circle);\n'
  + '  }\n'
  + '}\n';
  // --- minimal mat4 (column-major, WebGL layout) ---
  function persp(fovDeg, aspect, near, far){
    var f = 1/Math.tan(fovDeg*Math.PI/360), nf = 1/(near-far);
    return [f/aspect,0,0,0, 0,f,0,0, 0,0,(far+near)*nf,-1, 0,0,2*far*near*nf,0];
  }
  function euler(rx, ry, rz){ // R = Rz·Ry·Rx, small-angle wobble
    var cx=Math.cos(rx),sx=Math.sin(rx),cy=Math.cos(ry),sy=Math.sin(ry),cz=Math.cos(rz),sz=Math.sin(rz);
    return [ cy*cz, cy*sz, -sy, 0,
      sx*sy*cz-cx*sz, sx*sy*sz+cx*cz, sx*cy, 0,
      cx*sy*cz+sx*sz, cx*sy*sz-sx*cz, cx*cy, 0,
      0, 0, 0, 1 ];
  }
  function compile(gl, type, src){ var s=gl.createShader(type); gl.shaderSource(s,src); gl.compileShader(s);
    if(!gl.getShaderParameter(s, gl.COMPILE_STATUS)) console.warn('[particles]', gl.getShaderInfoLog(s)); return s; }
  function hexToRgb(hex){ hex = hex.replace(/^#/, '');
    var n = parseInt(hex, 16); return [((n>>16)&255)/255, ((n>>8)&255)/255, (n&255)/255]; }
  function start(el){
    if(el.__ma) return; el.__ma = 1;
    var canvas = document.createElement('canvas');
    canvas.style.cssText = 'width:100%;height:100%;display:block';
    el.appendChild(canvas);
    var gl = canvas.getContext('webgl', { alpha:true, premultipliedAlpha:false, depth:false });
    if(!gl){ el.style.background = el.getAttribute('data-fallback') || '#0b0b12'; return; }
    gl.clearColor(0,0,0,0); gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    var p = gl.createProgram();
    gl.attachShader(p, compile(gl, gl.VERTEX_SHADER, VERT));
    gl.attachShader(p, compile(gl, gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(p);
    if(!gl.getProgramParameter(p, gl.LINK_STATUS)){ console.warn('[particles] link', gl.getProgramInfoLog(p)); return; }
    gl.useProgram(p);
    // geometry — uniform sphere via rejection sampling + cbrt radius (as source)
    var count = DEF.particleCount;
    var positions = new Float32Array(count*3), randoms = new Float32Array(count*4), colors = new Float32Array(count*3);
    var palette = ['#ffffff', '#ffffff', '#ffffff'];
    for(var i=0;i<count;i++){
      var x, y, z, len;
      do { x = Math.random()*2-1; y = Math.random()*2-1; z = Math.random()*2-1; len = x*x+y*y+z*z; }
      while(len > 1 || len === 0);
      var r = Math.cbrt(Math.random());
      positions[i*3] = x*r; positions[i*3+1] = y*r; positions[i*3+2] = z*r;
      randoms[i*4] = Math.random(); randoms[i*4+1] = Math.random(); randoms[i*4+2] = Math.random(); randoms[i*4+3] = Math.random();
      var col = hexToRgb(palette[Math.floor(Math.random()*palette.length)]);
      colors[i*3] = col[0]; colors[i*3+1] = col[1]; colors[i*3+2] = col[2];
    }
    function attrib(name, data, size){ var b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b);
      gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
      var loc = gl.getAttribLocation(p, name); gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0); }
    attrib('position', positions, 3); attrib('random', randoms, 4); attrib('color', colors, 3);
    var U = {}; ['modelMatrix','viewMatrix','projectionMatrix','uTime','uSpread','uBaseSize','uSizeRandomness','uAlphaParticles']
      .forEach(function(n){ U[n] = gl.getUniformLocation(p, n); });
    gl.uniform1f(U.uSpread, DEF.particleSpread);
    gl.uniform1f(U.uBaseSize, DEF.particleBaseSize);
    gl.uniform1f(U.uSizeRandomness, DEF.sizeRandomness);
    gl.uniform1f(U.uAlphaParticles, DEF.alphaParticles);
    var view = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,-DEF.cameraDistance,1];
    gl.uniformMatrix4fv(U.viewMatrix, false, new Float32Array(view));
    var W=1, H=1;
    function resize(){ W = Math.max(1, el.offsetWidth||600); H = Math.max(1, el.offsetHeight||360);
      canvas.width = W; canvas.height = H; gl.viewport(0, 0, W, H);
      gl.uniformMatrix4fv(U.projectionMatrix, false, new Float32Array(persp(15, W/H, 0.1, 100))); }
    g.addEventListener('resize', resize); resize();
    var red = reduced(), lastTime = performance.now(), elapsed = red ? 2000 : 0, rotZ = 0;
    function frame(t){
      var delta = t - lastTime; lastTime = t;
      if(!red){ elapsed += delta*DEF.speed; rotZ += 0.01*DEF.speed; }
      gl.uniform1f(U.uTime, elapsed*0.001);
      gl.uniformMatrix4fv(U.modelMatrix, false, new Float32Array(
        euler(Math.sin(elapsed*0.0002)*0.1, Math.cos(elapsed*0.0005)*0.15, rotZ)));
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArrays(gl.POINTS, 0, count);
      if(!red) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }
  function init(){ var els=document.querySelectorAll('.particles'); for(var i=0;i<els.length;i++) start(els[i]); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* pixel-blast */
/* pixel-blast.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='#version 300 es\nprecision highp float;\nprecision highp float;\n\nuniform vec3  uColor;\nuniform vec2  uResolution;\nuniform float uTime;\nuniform float uPixelSize;\nuniform float uScale;\nuniform float uDensity;\nuniform float uPixelJitter;\nuniform int   uEnableRipples;\nuniform float uRippleSpeed;\nuniform float uRippleThickness;\nuniform float uRippleIntensity;\nuniform float uEdgeFade;\n\nuniform int   uShapeType;\nconst int SHAPE_SQUARE   = 0;\nconst int SHAPE_CIRCLE   = 1;\nconst int SHAPE_TRIANGLE = 2;\nconst int SHAPE_DIAMOND  = 3;\n\nconst int   MAX_CLICKS = 10;\n\nuniform vec2  uClickPos  [MAX_CLICKS];\nuniform float uClickTimes[MAX_CLICKS];\n\nout vec4 fragColor;\n\nfloat Bayer2(vec2 a) {\n  a = floor(a);\n  return fract(a.x / 2. + a.y * a.y * .75);\n}\n#define Bayer4(a) (Bayer2(.5*(a))*0.25 + Bayer2(a))\n#define Bayer8(a) (Bayer4(.5*(a))*0.25 + Bayer2(a))\n\n#define FBM_OCTAVES     5\n#define FBM_LACUNARITY  1.25\n#define FBM_GAIN        1.0\n\nfloat hash11(float n){ return fract(sin(n)*43758.5453); }\n\nfloat vnoise(vec3 p){\n  vec3 ip = floor(p);\n  vec3 fp = fract(p);\n  float n000 = hash11(dot(ip + vec3(0.0,0.0,0.0), vec3(1.0,57.0,113.0)));\n  float n100 = hash11(dot(ip + vec3(1.0,0.0,0.0), vec3(1.0,57.0,113.0)));\n  float n010 = hash11(dot(ip + vec3(0.0,1.0,0.0), vec3(1.0,57.0,113.0)));\n  float n110 = hash11(dot(ip + vec3(1.0,1.0,0.0), vec3(1.0,57.0,113.0)));\n  float n001 = hash11(dot(ip + vec3(0.0,0.0,1.0), vec3(1.0,57.0,113.0)));\n  float n101 = hash11(dot(ip + vec3(1.0,0.0,1.0), vec3(1.0,57.0,113.0)));\n  float n011 = hash11(dot(ip + vec3(0.0,1.0,1.0), vec3(1.0,57.0,113.0)));\n  float n111 = hash11(dot(ip + vec3(1.0,1.0,1.0), vec3(1.0,57.0,113.0)));\n  vec3 w = fp*fp*fp*(fp*(fp*6.0-15.0)+10.0);\n  float x00 = mix(n000, n100, w.x);\n  float x10 = mix(n010, n110, w.x);\n  float x01 = mix(n001, n101, w.x);\n  float x11 = mix(n011, n111, w.x);\n  float y0  = mix(x00, x10, w.y);\n  float y1  = mix(x01, x11, w.y);\n  return mix(y0, y1, w.z) * 2.0 - 1.0;\n}\n\nfloat fbm2(vec2 uv, float t){\n  vec3 p = vec3(uv * uScale, t);\n  float amp = 1.0;\n  float freq = 1.0;\n  float sum = 1.0;\n  for (int i = 0; i < FBM_OCTAVES; ++i){\n    sum  += amp * vnoise(p * freq);\n    freq *= FBM_LACUNARITY;\n    amp  *= FBM_GAIN;\n  }\n  return sum * 0.5 + 0.5;\n}\n\nfloat maskCircle(vec2 p, float cov){\n  float r = sqrt(cov) * .25;\n  float d = length(p - 0.5) - r;\n  float aa = 0.5 * fwidth(d);\n  return cov * (1.0 - smoothstep(-aa, aa, d * 2.0));\n}\n\nfloat maskTriangle(vec2 p, vec2 id, float cov){\n  bool flip = mod(id.x + id.y, 2.0) > 0.5;\n  if (flip) p.x = 1.0 - p.x;\n  float r = sqrt(cov);\n  float d  = p.y - r*(1.0 - p.x);\n  float aa = fwidth(d);\n  return cov * clamp(0.5 - d/aa, 0.0, 1.0);\n}\n\nfloat maskDiamond(vec2 p, float cov){\n  float r = sqrt(cov) * 0.564;\n  return step(abs(p.x - 0.49) + abs(p.y - 0.49), r);\n}\n\nvoid main(){\n  float pixelSize = uPixelSize;\n  vec2 fragCoord = gl_FragCoord.xy - uResolution * .5;\n  float aspectRatio = uResolution.x / uResolution.y;\n\n  vec2 pixelId = floor(fragCoord / pixelSize);\n  vec2 pixelUV = fract(fragCoord / pixelSize);\n\n  float cellPixelSize = 8.0 * pixelSize;\n  vec2 cellId = floor(fragCoord / cellPixelSize);\n  vec2 cellCoord = cellId * cellPixelSize;\n  vec2 uv = cellCoord / uResolution * vec2(aspectRatio, 1.0);\n\n  float base = fbm2(uv, uTime * 0.05);\n  base = base * 0.5 - 0.65;\n\n  float feed = base + (uDensity - 0.5) * 0.3;\n\n  float speed     = uRippleSpeed;\n  float thickness = uRippleThickness;\n  const float dampT     = 1.0;\n  const float dampR     = 10.0;\n\n  if (uEnableRipples == 1) {\n    for (int i = 0; i < MAX_CLICKS; ++i){\n      vec2 pos = uClickPos[i];\n      if (pos.x < 0.0) continue;\n      float cellPixelSize = 8.0 * pixelSize;\n      vec2 cuv = (((pos - uResolution * .5 - cellPixelSize * .5) / (uResolution))) * vec2(aspectRatio, 1.0);\n      float t = max(uTime - uClickTimes[i], 0.0);\n      float r = distance(uv, cuv);\n      float waveR = speed * t;\n      float ring  = exp(-pow((r - waveR) / thickness, 2.0));\n      float atten = exp(-dampT * t) * exp(-dampR * r);\n      feed = max(feed, ring * atten * uRippleIntensity);\n    }\n  }\n\n  float bayer = Bayer8(fragCoord / uPixelSize) - 0.5;\n  float bw = step(0.5, feed + bayer);\n\n  float h = fract(sin(dot(floor(fragCoord / uPixelSize), vec2(127.1, 311.7))) * 43758.5453);\n  float jitterScale = 1.0 + (h - 0.5) * uPixelJitter;\n  float coverage = bw * jitterScale;\n  float M;\n  if      (uShapeType == SHAPE_CIRCLE)   M = maskCircle (pixelUV, coverage);\n  else if (uShapeType == SHAPE_TRIANGLE) M = maskTriangle(pixelUV, pixelId, coverage);\n  else if (uShapeType == SHAPE_DIAMOND)  M = maskDiamond(pixelUV, coverage);\n  else                                   M = coverage;\n\n  if (uEdgeFade > 0.0) {\n    vec2 norm = gl_FragCoord.xy / uResolution;\n    float edge = min(min(norm.x, norm.y), min(1.0 - norm.x, 1.0 - norm.y));\n    float fade = smoothstep(0.0, uEdgeFade, edge);\n    M *= fade;\n  }\n\n  vec3 color = uColor;\n\n  // sRGB gamma correction - convert linear to sRGB for accurate color output\n  vec3 srgbColor = mix(\n    color * 12.92,\n    1.055 * pow(color, vec3(1.0 / 2.4)) - 0.055,\n    step(0.0031308, color)\n  );\n\n  fragColor = vec4(srgbColor * M, M);\n}\n';
  function init(){ var els=document.querySelectorAll('.pixel-blast'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    var h=g.ShaderBG(els[i], FRAG, {"timeScale":0.5,"uniforms":{"uColor":{"t":"3f","v":[0.7058823529411765,0.592156862745098,0.8117647058823529]},"uShapeType":{"t":"1i","v":0},"uPixelSize":{"t":"1f","v":3},"uScale":{"t":"1f","v":2},"uDensity":{"t":"1f","v":1},"uPixelJitter":{"t":"1f","v":0},"uEnableRipples":{"t":"1i","v":1},"uRippleSpeed":{"t":"1f","v":0.3},"uRippleThickness":{"t":"1f","v":0.1},"uRippleIntensity":{"t":"1f","v":1},"uEdgeFade":{"t":"1f","v":0.5},"uClickPos":{"t":"2fv","v":[-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1]},"uClickTimes":{"t":"1fv","v":[0,0,0,0,0,0,0,0,0,0]}}});
    if(h){ (function(){ var N=10, pos=new Array(2*N), times=new Array(N), ix=0, k;
      for(k=0;k<N;k++){ pos[2*k]=-1; pos[2*k+1]=-1; times[k]=0; }
      h.el.addEventListener('pointerdown', function(e){ var r=h.el.getBoundingClientRect();
        var sx=h.canvas.width/r.width, sy=h.canvas.height/r.height;
        pos[2*ix]=(e.clientX-r.left)*sx; pos[2*ix+1]=(r.height-(e.clientY-r.top))*sy;
        times[ix]=h.time(); ix=(ix+1)%N;
        h.set('uClickPos',{t:'2fv',v:pos}); h.set('uClickTimes',{t:'1fv',v:times}); }); })(); } } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* pixel-card */
/* pixel-card.js — hover-press · build a light pixel overlay with staggered delays. */
(function(g){ 'use strict';
  function attach(el){ if(el.__pc) return; el.__pc=1; var grid=el.querySelector('.pxc-grid'); if(!grid) return; var n=+el.getAttribute('data-grid')||8;
    grid.style.gridTemplateColumns='repeat('+n+',1fr)'; grid.style.gridTemplateRows='repeat('+n+',1fr)';
    for(var i=0;i<n*n;i++){ var d=document.createElement('i'); d.style.setProperty('--d',(Math.random()*300|0)+'ms'); grid.appendChild(d); } }
  function init(){ var els=document.querySelectorAll('.pxc'); for(var i=0;i<els.length;i++) attach(els[i]); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* pixel-snow */
/* pixel-snow.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='#version 300 es\nprecision highp float;\nout vec4 fragColor;\nprecision mediump float;\n\nuniform float uTime;\nuniform vec2 uResolution;\nuniform float uFlakeSize;\nuniform float uMinFlakeSize;\nuniform float uPixelResolution;\nuniform float uSpeed;\nuniform float uDepthFade;\nuniform float uFarPlane;\nuniform vec3 uColor;\nuniform float uBrightness;\nuniform float uGamma;\nuniform float uDensity;\nuniform float uVariant;\nuniform float uDirection;\n\n// Precomputed constants\n#define PI 3.14159265\n#define PI_OVER_6 0.5235988\n#define PI_OVER_3 1.0471976\n#define INV_SQRT3 0.57735027\n#define M1 1597334677U\n#define M2 3812015801U\n#define M3 3299493293U\n#define F0 2.3283064e-10\n\n// Optimized hash - inline multiplication\n#define hash(n) (n * (n ^ (n >> 15)))\n#define coord3(p) (uvec3(p).x * M1 ^ uvec3(p).y * M2 ^ uvec3(p).z * M3)\n\n// Precomputed camera basis vectors (normalized vec3(1,1,1), vec3(1,0,-1))\nconst vec3 camK = vec3(0.57735027, 0.57735027, 0.57735027);\nconst vec3 camI = vec3(0.70710678, 0.0, -0.70710678);\nconst vec3 camJ = vec3(-0.40824829, 0.81649658, -0.40824829);\n\n// Precomputed branch direction\nconst vec2 b1d = vec2(0.574, 0.819);\n\nvec3 hash3(uint n) {\n  uvec3 hashed = hash(n) * uvec3(1U, 511U, 262143U);\n  return vec3(hashed) * F0;\n}\n\nfloat snowflakeDist(vec2 p) {\n  float r = length(p);\n  float a = atan(p.y, p.x);\n  a = abs(mod(a + PI_OVER_6, PI_OVER_3) - PI_OVER_6);\n  vec2 q = r * vec2(cos(a), sin(a));\n  float dMain = max(abs(q.y), max(-q.x, q.x - 1.0));\n  float b1t = clamp(dot(q - vec2(0.4, 0.0), b1d), 0.0, 0.4);\n  float dB1 = length(q - vec2(0.4, 0.0) - b1t * b1d);\n  float b2t = clamp(dot(q - vec2(0.7, 0.0), b1d), 0.0, 0.25);\n  float dB2 = length(q - vec2(0.7, 0.0) - b2t * b1d);\n  return min(dMain, min(dB1, dB2)) * 10.0;\n}\n\nvoid main() {\n  // Precompute reciprocals to avoid division\n  float invPixelRes = 1.0 / uPixelResolution;\n  float pixelSize = max(1.0, floor(0.5 + uResolution.x * invPixelRes));\n  float invPixelSize = 1.0 / pixelSize;\n  \n  vec2 fragCoord = floor(gl_FragCoord.xy * invPixelSize);\n  vec2 res = uResolution * invPixelSize;\n  float invResX = 1.0 / res.x;\n\n  vec3 ray = normalize(vec3((fragCoord - res * 0.5) * invResX, 1.0));\n  ray = ray.x * camI + ray.y * camJ + ray.z * camK;\n\n  // Precompute time-based values\n  float timeSpeed = uTime * uSpeed;\n  float windX = cos(uDirection) * 0.4;\n  float windY = sin(uDirection) * 0.4;\n  vec3 camPos = (windX * camI + windY * camJ + 0.1 * camK) * timeSpeed;\n  vec3 pos = camPos;\n\n  // Precompute ray reciprocal for strides\n  vec3 absRay = max(abs(ray), vec3(0.001));\n  vec3 strides = 1.0 / absRay;\n  vec3 raySign = step(ray, vec3(0.0));\n  vec3 phase = fract(pos) * strides;\n  phase = mix(strides - phase, phase, raySign);\n\n  // Precompute for intersection test\n  float rayDotCamK = dot(ray, camK);\n  float invRayDotCamK = 1.0 / rayDotCamK;\n  float invDepthFade = 1.0 / uDepthFade;\n  float halfInvResX = 0.5 * invResX;\n  vec3 timeAnim = timeSpeed * 0.1 * vec3(7.0, 8.0, 5.0);\n\n  float t = 0.0;\n  for (int i = 0; i < 128; i++) {\n    if (t >= uFarPlane) break;\n    \n    vec3 fpos = floor(pos);\n    uint cellCoord = coord3(fpos);\n    float cellHash = hash3(cellCoord).x;\n\n    if (cellHash < uDensity) {\n      vec3 h = hash3(cellCoord);\n      \n      // Optimized flake position calculation\n      vec3 sinArg1 = fpos.yzx * 0.073;\n      vec3 sinArg2 = fpos.zxy * 0.27;\n      vec3 flakePos = 0.5 - 0.5 * cos(4.0 * sin(sinArg1) + 4.0 * sin(sinArg2) + 2.0 * h + timeAnim);\n      flakePos = flakePos * 0.8 + 0.1 + fpos;\n\n      float toIntersection = dot(flakePos - pos, camK) * invRayDotCamK;\n      \n      if (toIntersection > 0.0) {\n        vec3 testPos = pos + ray * toIntersection - flakePos;\n        float testX = dot(testPos, camI);\n        float testY = dot(testPos, camJ);\n        vec2 testUV = abs(vec2(testX, testY));\n        \n        float depth = dot(flakePos - camPos, camK);\n        float flakeSize = max(uFlakeSize, uMinFlakeSize * depth * halfInvResX);\n        \n        // Avoid branching with step functions where possible\n        float dist;\n        if (uVariant < 0.5) {\n          dist = max(testUV.x, testUV.y);\n        } else if (uVariant < 1.5) {\n          dist = length(testUV);\n        } else {\n          float invFlakeSize = 1.0 / flakeSize;\n          dist = snowflakeDist(vec2(testX, testY) * invFlakeSize) * flakeSize;\n        }\n\n        if (dist < flakeSize) {\n          float flakeSizeRatio = uFlakeSize / flakeSize;\n          float intensity = exp2(-(t + toIntersection) * invDepthFade) *\n                           min(1.0, flakeSizeRatio * flakeSizeRatio) * uBrightness;\n          fragColor = vec4(uColor * pow(vec3(intensity), vec3(uGamma)), 1.0);\n          return;\n        }\n      }\n    }\n\n    float nextStep = min(min(phase.x, phase.y), phase.z);\n    vec3 sel = step(phase, vec3(nextStep));\n    phase = phase - nextStep + strides * sel;\n    t += nextStep;\n    pos = mix(pos + ray * nextStep, floor(pos + ray * nextStep + 0.5), sel);\n  }\n\n  fragColor = vec4(0.0);\n}\n';
  function init(){ var els=document.querySelectorAll('.pixel-snow'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    var h=g.ShaderBG(els[i], FRAG, {"uniforms":{"uFlakeSize":{"t":"1f","v":0.01},"uMinFlakeSize":{"t":"1f","v":1.25},"uPixelResolution":{"t":"1f","v":200},"uSpeed":{"t":"1f","v":1.25},"uDepthFade":{"t":"1f","v":8},"uFarPlane":{"t":"1f","v":20},"uColor":{"t":"3f","v":[1,1,1]},"uBrightness":{"t":"1f","v":1},"uGamma":{"t":"1f","v":0.4545},"uDensity":{"t":"1f","v":0.3},"uVariant":{"t":"1f","v":0},"uDirection":{"t":"1f","v":2.1816615649929116}}}); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* pixel-transition */
/* pixel-transition.js — hover-press · build a pixel grid over the card; randomized reveal delays. */
(function(g){ 'use strict';
  function attach(el){ if(el.__px) return; el.__px=1; var back=el.querySelector('.pixt-back'); if(!back) return;
    var n=+el.getAttribute('data-grid')||10; back.style.gridTemplateColumns='repeat('+n+',1fr)'; back.style.gridTemplateRows='repeat('+n+',1fr)';
    for(var i=0;i<n*n;i++){ var d=document.createElement('div'); d.className='px'; d.style.setProperty('--d',(Math.random()*260|0)+'ms'); back.appendChild(d); } }
  function init(){ var els=document.querySelectorAll('.pixt'); for(var i=0;i<els.length;i++) attach(els[i]); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* plasma */
/* plasma.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='#version 300 es\nprecision highp float;\nuniform vec2 iResolution;\nuniform float iTime;\nuniform vec3 uCustomColor;\nuniform float uUseCustomColor;\nuniform float uSpeed;\nuniform float uDirection;\nuniform float uScale;\nuniform float uOpacity;\nuniform vec2 uMouse;\nuniform float uMouseInteractive;\nout vec4 fragColor;\n\nvoid mainImage(out vec4 o, vec2 C) {\n  vec2 center = iResolution.xy * 0.5;\n  C = (C - center) / uScale + center;\n  \n  vec2 mouseOffset = (uMouse - center) * 0.0002;\n  C += mouseOffset * length(C - center) * step(0.5, uMouseInteractive);\n  \n  float i, d, z, T = iTime * uSpeed * uDirection;\n  vec3 O, p, S;\n\n  for (vec2 r = iResolution.xy, Q; ++i < 60.; O += o.w/d*o.xyz) {\n    p = z*normalize(vec3(C-.5*r,r.y)); \n    p.z -= 4.; \n    S = p;\n    d = p.y-T;\n    \n    p.x += .4*(1.+p.y)*sin(d + p.x*0.1)*cos(.34*d + p.x*0.05); \n    Q = p.xz *= mat2(cos(p.y+vec4(0,11,33,0)-T)); \n    z+= d = abs(sqrt(length(Q*Q)) - .25*(5.+S.y))/3.+8e-4; \n    o = 1.+sin(S.y+p.z*.5+S.z-length(S-p)+vec4(2,1,0,8));\n  }\n  \n  o.xyz = tanh(O/1e4);\n}\n\nbool finite1(float x){ return !(isnan(x) || isinf(x)); }\nvec3 sanitize(vec3 c){\n  return vec3(\n    finite1(c.r) ? c.r : 0.0,\n    finite1(c.g) ? c.g : 0.0,\n    finite1(c.b) ? c.b : 0.0\n  );\n}\n\nvoid main() {\n  vec4 o = vec4(0.0);\n  mainImage(o, gl_FragCoord.xy);\n  vec3 rgb = sanitize(o.rgb);\n  \n  float intensity = (rgb.r + rgb.g + rgb.b) / 3.0;\n  vec3 customColor = intensity * uCustomColor;\n  vec3 finalColor = mix(rgb, customColor, step(0.5, uUseCustomColor));\n  \n  float alpha = length(rgb) * uOpacity;\n  fragColor = vec4(finalColor, alpha);\n}';
  function init(){ var els=document.querySelectorAll('.plasma'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"uCustomColor":{"t":"3f","v":[0.55,0.5,1]},"uUseCustomColor":{"t":"1f","v":1},"uSpeed":{"t":"1f","v":1},"uDirection":{"t":"1f","v":1},"uScale":{"t":"1f","v":1},"uOpacity":{"t":"1f","v":1},"uMouseInteractive":{"t":"1f","v":1}} }); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* plasma-wave */
/* plasma-wave.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='\nprecision mediump float;\nuniform float iTime;\nuniform vec2  iResolution;\nuniform vec2  uOffset;\nuniform float uRotation;\nuniform float uFocalLength;\nuniform float uSpeed1;\nuniform float uSpeed2;\nuniform float uDir2;\nuniform float uBend1;\nuniform float uBend2;\nuniform vec3  uColor1;\nuniform vec3  uColor2;\n\nconst float lt   = 0.3;\nconst float pi   = 3.14159;\nconst float pi2  = 6.28318;\nconst float pi_2 = 1.5708;\n#define MAX_STEPS 14\n\nvoid mainImage(out vec4 C, in vec2 U) {\n  float t = iTime * pi;\n  float s = 1.0;\n  float d = 0.0;\n  vec2  R = iResolution;\n\n  vec3 o = vec3(0.0, 0.0, -7.0);\n  vec3 u = normalize(vec3((U - 0.5 * R) / R.y, uFocalLength));\n  vec2 k = vec2(0.0);\n  vec3 p;\n\n  float t1 = t * 0.7;\n  float t2 = t * 0.9;\n  float tSpeed1 = t * uSpeed1;\n  float tSpeed2 = t * uSpeed2 * uDir2;\n\n  for (int i = 0; i < MAX_STEPS; ++i) {\n    p = o + u * d;\n    p.x -= 15.0;\n\n    float px = p.x;\n    float wob1 = uBend1 + sin(t1 + px * 0.8) * 0.1;\n    float wob2 = uBend2 + cos(t2 + px * 1.1) * 0.1;\n\n    float px2 = px + pi_2;\n    vec2 sinOffset = sin(vec2(px, px2) + tSpeed1) * wob1;\n    vec2 cosOffset = cos(vec2(px, px2) + tSpeed2) * wob2;\n\n    vec2 yz = p.yz;\n    float pxLt = px + lt;\n    k.x = max(pxLt, length(yz - sinOffset) - lt);\n    k.y = max(pxLt, length(yz - cosOffset) - lt);\n\n    float current = min(k.x, k.y);\n    s = min(s, current);\n    if (s < 0.001 || d > 300.0) break;\n    d += s * 0.7;\n  }\n\n  float sqrtD = sqrt(d);\n  vec3 raw = max(cos(d * pi2) - s * sqrtD - vec3(k, 0.0), 0.0);\n  raw.gb += 0.1;\n  float maxC = max(raw.r, max(raw.g, raw.b));\n  if (maxC < 0.15) discard;\n  raw = raw * 0.4 + raw.brg * 0.6 + raw * raw;\n  float lum = dot(raw, vec3(0.299, 0.587, 0.114));\n  float w1 = max(0.0, 1.0 - k.x * 2.0);\n  float w2 = max(0.0, 1.0 - k.y * 2.0);\n  float wt = w1 + w2 + 0.001;\n  vec3 c = (uColor1 * w1 + uColor2 * w2) / wt * lum * 3.5;\n  C = vec4(c, 1.0);\n}\n\nvoid main() {\n  vec2 coord = gl_FragCoord.xy + uOffset;\n  coord -= 0.5 * iResolution;\n  float c = cos(uRotation), s = sin(uRotation);\n  coord = mat2(c, -s, s, c) * coord;\n  coord += 0.5 * iResolution;\n\n  vec4 color;\n  mainImage(color, coord);\n  gl_FragColor = color;\n}\n';
  function init(){ var els=document.querySelectorAll('.plasma-wave'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"uOffset":{"t":"2f","v":[0.5,0.5]},"uRotation":{"t":"1f","v":1},"uFocalLength":{"t":"1f","v":1},"uSpeed1":{"t":"1f","v":1},"uSpeed2":{"t":"1f","v":1},"uDir2":{"t":"1f","v":1},"uBend1":{"t":"1f","v":1},"uBend2":{"t":"1f","v":1},"uColor1":{"t":"3f","v":[0.55,0.5,1]},"uColor2":{"t":"3f","v":[0.55,0.5,1]}} }); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* prism */
/* prism.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='\n      precision highp float;\n\n      uniform vec2  iResolution;\n      uniform float iTime;\n\n      uniform float uHeight;\n      uniform float uBaseHalf;\n      uniform mat3  uRot;\n      uniform int   uUseBaseWobble;\n      uniform float uGlow;\n      uniform vec2  uOffsetPx;\n      uniform float uNoise;\n      uniform float uSaturation;\n      uniform float uScale;\n      uniform float uHueShift;\n      uniform float uColorFreq;\n      uniform float uBloom;\n      uniform float uCenterShift;\n      uniform float uInvBaseHalf;\n      uniform float uInvHeight;\n      uniform float uMinAxis;\n      uniform float uPxScale;\n      uniform float uTimeScale;\n\n      vec4 tanh4(vec4 x){\n        vec4 e2x = exp(2.0*x);\n        return (e2x - 1.0) / (e2x + 1.0);\n      }\n\n      float rand(vec2 co){\n        return fract(sin(dot(co, vec2(12.9898, 78.233))) * 43758.5453123);\n      }\n\n      float sdOctaAnisoInv(vec3 p){\n        vec3 q = vec3(abs(p.x) * uInvBaseHalf, abs(p.y) * uInvHeight, abs(p.z) * uInvBaseHalf);\n        float m = q.x + q.y + q.z - 1.0;\n        return m * uMinAxis * 0.5773502691896258;\n      }\n\n      float sdPyramidUpInv(vec3 p){\n        float oct = sdOctaAnisoInv(p);\n        float halfSpace = -p.y;\n        return max(oct, halfSpace);\n      }\n\n      mat3 hueRotation(float a){\n        float c = cos(a), s = sin(a);\n        mat3 W = mat3(\n          0.299, 0.587, 0.114,\n          0.299, 0.587, 0.114,\n          0.299, 0.587, 0.114\n        );\n        mat3 U = mat3(\n           0.701, -0.587, -0.114,\n          -0.299,  0.413, -0.114,\n          -0.300, -0.588,  0.886\n        );\n        mat3 V = mat3(\n           0.168, -0.331,  0.500,\n           0.328,  0.035, -0.500,\n          -0.497,  0.296,  0.201\n        );\n        return W + U * c + V * s;\n      }\n\n      void main(){\n        vec2 f = (gl_FragCoord.xy - 0.5 * iResolution.xy - uOffsetPx) * uPxScale;\n\n        float z = 5.0;\n        float d = 0.0;\n\n        vec3 p;\n        vec4 o = vec4(0.0);\n\n        float centerShift = uCenterShift;\n        float cf = uColorFreq;\n\n        mat2 wob = mat2(1.0);\n        if (uUseBaseWobble == 1) {\n          float t = iTime * uTimeScale;\n          float c0 = cos(t + 0.0);\n          float c1 = cos(t + 33.0);\n          float c2 = cos(t + 11.0);\n          wob = mat2(c0, c1, c2, c0);\n        }\n\n        const int STEPS = 100;\n        for (int i = 0; i < STEPS; i++) {\n          p = vec3(f, z);\n          p.xz = p.xz * wob;\n          p = uRot * p;\n          vec3 q = p;\n          q.y += centerShift;\n          d = 0.1 + 0.2 * abs(sdPyramidUpInv(q));\n          z -= d;\n          o += (sin((p.y + z) * cf + vec4(0.0, 1.0, 2.0, 3.0)) + 1.0) / d;\n        }\n\n        o = tanh4(o * o * (uGlow * uBloom) / 1e5);\n\n        vec3 col = o.rgb;\n        float n = rand(gl_FragCoord.xy + vec2(iTime));\n        col += (n - 0.5) * uNoise;\n        col = clamp(col, 0.0, 1.0);\n\n        float L = dot(col, vec3(0.2126, 0.7152, 0.0722));\n        col = clamp(mix(vec3(L), col, uSaturation), 0.0, 1.0);\n\n        if(abs(uHueShift) > 0.0001){\n          col = clamp(hueRotation(uHueShift) * col, 0.0, 1.0);\n        }\n\n        gl_FragColor = vec4(col, o.a);\n      }\n    ';
  function init(){ var els=document.querySelectorAll('.prism'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"uHeight":{"t":"1f","v":1},"uBaseHalf":{"t":"1f","v":1},"uUseBaseWobble":{"t":"1i","v":1},"uGlow":{"t":"1f","v":1},"uOffsetPx":{"t":"2f","v":[0.5,0.5]},"uNoise":{"t":"1f","v":0.12},"uSaturation":{"t":"1f","v":1},"uScale":{"t":"1f","v":1},"uHueShift":{"t":"1f","v":0},"uColorFreq":{"t":"1f","v":2},"uBloom":{"t":"1f","v":1},"uCenterShift":{"t":"1f","v":1},"uInvBaseHalf":{"t":"1f","v":1},"uInvHeight":{"t":"1f","v":1},"uMinAxis":{"t":"1f","v":1},"uPxScale":{"t":"1f","v":1},"uTimeScale":{"t":"1f","v":1}} }); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* prismatic-burst */
/* prismatic-burst.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='#version 300 es\nprecision highp float;\nprecision highp int;\n\nout vec4 fragColor;\n\nuniform vec2  uResolution;\nuniform float uTime;\n\nuniform float uIntensity;\nuniform float uSpeed;\nuniform int   uAnimType;\nuniform vec2  uMouse;\nuniform int   uColorCount;\nuniform float uDistort;\nuniform vec2  uOffset;\nuniform sampler2D uGradient;\nuniform float uNoiseAmount;\nuniform int   uRayCount;\n\nfloat hash21(vec2 p){\n    p = floor(p);\n    float f = 52.9829189 * fract(dot(p, vec2(0.065, 0.005)));\n    return fract(f);\n}\n\nmat2 rot30(){ return mat2(0.8, -0.5, 0.5, 0.8); }\n\nfloat layeredNoise(vec2 fragPx){\n    vec2 p = mod(fragPx + vec2(uTime * 30.0, -uTime * 21.0), 1024.0);\n    vec2 q = rot30() * p;\n    float n = 0.0;\n    n += 0.40 * hash21(q);\n    n += 0.25 * hash21(q * 2.0 + 17.0);\n    n += 0.20 * hash21(q * 4.0 + 47.0);\n    n += 0.10 * hash21(q * 8.0 + 113.0);\n    n += 0.05 * hash21(q * 16.0 + 191.0);\n    return n;\n}\n\nvec3 rayDir(vec2 frag, vec2 res, vec2 offset, float dist){\n    float focal = res.y * max(dist, 1e-3);\n    return normalize(vec3(2.0 * (frag - offset) - res, focal));\n}\n\nfloat edgeFade(vec2 frag, vec2 res, vec2 offset){\n    vec2 toC = frag - 0.5 * res - offset;\n    float r = length(toC) / (0.5 * min(res.x, res.y));\n    float x = clamp(r, 0.0, 1.0);\n    float q = x * x * x * (x * (x * 6.0 - 15.0) + 10.0);\n    float s = q * 0.5;\n    s = pow(s, 1.5);\n    float tail = 1.0 - pow(1.0 - s, 2.0);\n    s = mix(s, tail, 0.2);\n    float dn = (layeredNoise(frag * 0.15) - 0.5) * 0.0015 * s;\n    return clamp(s + dn, 0.0, 1.0);\n}\n\nmat3 rotX(float a){ float c = cos(a), s = sin(a); return mat3(1.0,0.0,0.0, 0.0,c,-s, 0.0,s,c); }\nmat3 rotY(float a){ float c = cos(a), s = sin(a); return mat3(c,0.0,s, 0.0,1.0,0.0, -s,0.0,c); }\nmat3 rotZ(float a){ float c = cos(a), s = sin(a); return mat3(c,-s,0.0, s,c,0.0, 0.0,0.0,1.0); }\n\nvec3 sampleGradient(float t){\n    t = clamp(t, 0.0, 1.0);\n    return 0.5 + 0.5 * cos(6.28318 * (t + vec3(0.0, 0.33, 0.67)));\n}\n\nvec2 rot2(vec2 v, float a){\n    float s = sin(a), c = cos(a);\n    return mat2(c, -s, s, c) * v;\n}\n\nfloat bendAngle(vec3 q, float t){\n    float a = 0.8 * sin(q.x * 0.55 + t * 0.6)\n            + 0.7 * sin(q.y * 0.50 - t * 0.5)\n            + 0.6 * sin(q.z * 0.60 + t * 0.7);\n    return a;\n}\n\nvoid main(){\n    vec2 frag = gl_FragCoord.xy;\n    float t = uTime * uSpeed;\n    float jitterAmp = 0.1 * clamp(uNoiseAmount, 0.0, 1.0);\n    vec3 dir = rayDir(frag, uResolution, uOffset, 1.0);\n    float marchT = 0.0;\n    vec3 col = vec3(0.0);\n    float n = layeredNoise(frag);\n    vec4 c = cos(t * 0.2 + vec4(0.0, 33.0, 11.0, 0.0));\n    mat2 M2 = mat2(c.x, c.y, c.z, c.w);\n    float amp = clamp(uDistort, 0.0, 50.0) * 0.15;\n\n    mat3 rot3dMat = mat3(1.0);\n    if(uAnimType == 1){\n      vec3 ang = vec3(t * 0.31, t * 0.21, t * 0.17);\n      rot3dMat = rotZ(ang.z) * rotY(ang.y) * rotX(ang.x);\n    }\n    mat3 hoverMat = mat3(1.0);\n    if(uAnimType == 2){\n      vec2 m = uMouse * 2.0 - 1.0;\n      vec3 ang = vec3(m.y * 0.6, m.x * 0.6, 0.0);\n      hoverMat = rotY(ang.y) * rotX(ang.x);\n    }\n\n    for (int i = 0; i < 44; ++i) {\n        vec3 P = marchT * dir;\n        P.z -= 2.0;\n        float rad = length(P);\n        vec3 Pl = P * (10.0 / max(rad, 1e-6));\n\n        if(uAnimType == 0){\n            Pl.xz *= M2;\n        } else if(uAnimType == 1){\n      Pl = rot3dMat * Pl;\n        } else {\n      Pl = hoverMat * Pl;\n        }\n\n        float stepLen = min(rad - 0.3, n * jitterAmp) + 0.1;\n\n        float grow = smoothstep(0.35, 3.0, marchT);\n        float a1 = amp * grow * bendAngle(Pl * 0.6, t);\n        float a2 = 0.5 * amp * grow * bendAngle(Pl.zyx * 0.5 + 3.1, t * 0.9);\n        vec3 Pb = Pl;\n        Pb.xz = rot2(Pb.xz, a1);\n        Pb.xy = rot2(Pb.xy, a2);\n\n        float rayPattern = smoothstep(\n            0.5, 0.7,\n            sin(Pb.x + cos(Pb.y) * cos(Pb.z)) *\n            sin(Pb.z + sin(Pb.y) * cos(Pb.x + t))\n        );\n\n        if (uRayCount > 0) {\n            float ang = atan(Pb.y, Pb.x);\n            float comb = 0.5 + 0.5 * cos(float(uRayCount) * ang);\n            comb = pow(comb, 3.0);\n            rayPattern *= smoothstep(0.15, 0.95, comb);\n        }\n\n        vec3 spectralDefault = 1.0 + vec3(\n            cos(marchT * 3.0 + 0.0),\n            cos(marchT * 3.0 + 1.0),\n            cos(marchT * 3.0 + 2.0)\n        );\n\n        float saw = fract(marchT * 0.25);\n        float tRay = saw * saw * (3.0 - 2.0 * saw);\n        vec3 userGradient = 2.0 * sampleGradient(tRay);\n        vec3 spectral = (uColorCount > 0) ? userGradient : spectralDefault;\n        vec3 base = (0.05 / (0.4 + stepLen))\n                  * smoothstep(5.0, 0.0, rad)\n                  * spectral;\n\n        col += base * rayPattern;\n        marchT += stepLen;\n    }\n\n    col *= edgeFade(frag, uResolution, uOffset);\n    col *= uIntensity;\n\n    fragColor = vec4(clamp(col, 0.0, 1.0), 1.0);\n}';
  function init(){ var els=document.querySelectorAll('.prismatic-burst'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"uIntensity":{"t":"1f","v":1},"uSpeed":{"t":"1f","v":1},"uAnimType":{"t":"1i","v":1},"uColorCount":{"t":"1i","v":1},"uDistort":{"t":"1f","v":0.5},"uOffset":{"t":"2f","v":[0.5,0.5]},"uNoiseAmount":{"t":"1f","v":0.12},"uRayCount":{"t":"1i","v":1}} }); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* radar */
/* radar.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='\nprecision highp float;\n\nuniform float uTime;\nuniform vec3 uResolution;\nuniform float uSpeed;\nuniform float uScale;\nuniform float uRingCount;\nuniform float uSpokeCount;\nuniform float uRingThickness;\nuniform float uSpokeThickness;\nuniform float uSweepSpeed;\nuniform float uSweepWidth;\nuniform float uSweepLobes;\nuniform vec3 uColor;\nuniform vec3 uBgColor;\nuniform float uFalloff;\nuniform float uBrightness;\nuniform vec2 uMouse;\nuniform float uMouseInfluence;\nuniform bool uEnableMouse;\n\n#define TAU 6.28318530718\n#define PI 3.14159265359\n\nvoid main() {\n  vec2 st = gl_FragCoord.xy / uResolution.xy;\n  st = st * 2.0 - 1.0;\n  st.x *= uResolution.x / uResolution.y;\n\n  if (uEnableMouse) {\n    vec2 mShift = (uMouse * 2.0 - 1.0);\n    mShift.x *= uResolution.x / uResolution.y;\n    st -= mShift * uMouseInfluence;\n  }\n\n  st *= uScale;\n\n  float dist = length(st);\n  float theta = atan(st.y, st.x);\n  float t = uTime * uSpeed;\n\n  float ringPhase = dist * uRingCount - t;\n  float ringDist = abs(fract(ringPhase) - 0.5);\n  float ringGlow = 1.0 - smoothstep(0.0, uRingThickness, ringDist);\n\n  float spokeAngle = abs(fract(theta * uSpokeCount / TAU + 0.5) - 0.5) * TAU / uSpokeCount;\n  float arcDist = spokeAngle * dist;\n  float spokeGlow = (1.0 - smoothstep(0.0, uSpokeThickness, arcDist)) * smoothstep(0.0, 0.1, dist);\n\n  float sweepPhase = t * uSweepSpeed;\n  float sweepBeam = pow(max(0.5 * sin(uSweepLobes * theta + sweepPhase) + 0.5, 0.0), uSweepWidth);\n\n  float fade = smoothstep(1.05, 0.85, dist) * pow(max(1.0 - dist, 0.0), uFalloff);\n\n  float intensity = max((ringGlow + spokeGlow + sweepBeam) * fade * uBrightness, 0.0);\n  vec3 col = uColor * intensity + uBgColor;\n\n  float alpha = clamp(length(col), 0.0, 1.0);\n  gl_FragColor = vec4(col, alpha);\n}\n';
  function init(){ var els=document.querySelectorAll('.radar'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"uSpeed":{"t":"1f","v":1},"uScale":{"t":"1f","v":0.5},"uRingCount":{"t":"1f","v":10},"uSpokeCount":{"t":"1f","v":10},"uRingThickness":{"t":"1f","v":0.05},"uSpokeThickness":{"t":"1f","v":0.01},"uSweepSpeed":{"t":"1f","v":1},"uSweepWidth":{"t":"1f","v":2},"uSweepLobes":{"t":"1f","v":1},"uColor":{"t":"3f","v":[0.6235294117647059,0.1607843137254902,1]},"uBgColor":{"t":"3f","v":[0,0,0]},"uFalloff":{"t":"1f","v":2},"uBrightness":{"t":"1f","v":1},"uMouseInfluence":{"t":"1f","v":0},"uEnableMouse":{"t":"1i","v":0}} });  } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* ripple-grid */
/* ripple-grid.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='precision highp float;\nuniform float iTime;\nuniform vec2 iResolution;\nuniform bool enableRainbow;\nuniform vec3 gridColor;\nuniform float rippleIntensity;\nuniform float gridSize;\nuniform float gridThickness;\nuniform float fadeDistance;\nuniform float vignetteStrength;\nuniform float glowIntensity;\nuniform float opacity;\nuniform float gridRotation;\nuniform bool mouseInteraction;\nuniform vec2 mousePosition;\nuniform float mouseInfluence;\nuniform float mouseInteractionRadius;\nvarying vec2 vUv;\n\nfloat pi = 3.141592;\n\nmat2 rotate(float angle) {\n    float s = sin(angle);\n    float c = cos(angle);\n    return mat2(c, -s, s, c);\n}\n\nvoid main() {\n    vec2 uv = vUv * 2.0 - 1.0;\n    uv.x *= iResolution.x / iResolution.y;\n\n    if (gridRotation != 0.0) {\n        uv = rotate(gridRotation * pi / 180.0) * uv;\n    }\n\n    float dist = length(uv);\n    float func = sin(pi * (iTime - dist));\n    vec2 rippleUv = uv + uv * func * rippleIntensity;\n\n    if (mouseInteraction && mouseInfluence > 0.0) {\n        vec2 mouseUv = (mousePosition * 2.0 - 1.0);\n        mouseUv.x *= iResolution.x / iResolution.y;\n        float mouseDist = length(uv - mouseUv);\n        \n        float influence = mouseInfluence * exp(-mouseDist * mouseDist / (mouseInteractionRadius * mouseInteractionRadius));\n        \n        float mouseWave = sin(pi * (iTime * 2.0 - mouseDist * 3.0)) * influence;\n        rippleUv += normalize(uv - mouseUv) * mouseWave * rippleIntensity * 0.3;\n    }\n\n    vec2 a = sin(gridSize * 0.5 * pi * rippleUv - pi / 2.0);\n    vec2 b = abs(a);\n\n    float aaWidth = 0.5;\n    vec2 smoothB = vec2(\n        smoothstep(0.0, aaWidth, b.x),\n        smoothstep(0.0, aaWidth, b.y)\n    );\n\n    vec3 color = vec3(0.0);\n    color += exp(-gridThickness * smoothB.x * (0.8 + 0.5 * sin(pi * iTime)));\n    color += exp(-gridThickness * smoothB.y);\n    color += 0.5 * exp(-(gridThickness / 4.0) * sin(smoothB.x));\n    color += 0.5 * exp(-(gridThickness / 3.0) * smoothB.y);\n\n    if (glowIntensity > 0.0) {\n        color += glowIntensity * exp(-gridThickness * 0.5 * smoothB.x);\n        color += glowIntensity * exp(-gridThickness * 0.5 * smoothB.y);\n    }\n\n    float ddd = exp(-2.0 * clamp(pow(dist, fadeDistance), 0.0, 1.0));\n    \n    vec2 vignetteCoords = vUv - 0.5;\n    float vignetteDistance = length(vignetteCoords);\n    float vignette = 1.0 - pow(vignetteDistance * 2.0, vignetteStrength);\n    vignette = clamp(vignette, 0.0, 1.0);\n    \n    vec3 t;\n    if (enableRainbow) {\n        t = vec3(\n            uv.x * 0.5 + 0.5 * sin(iTime),\n            uv.y * 0.5 + 0.5 * cos(iTime),\n            pow(cos(iTime), 4.0)\n        ) + 0.5;\n    } else {\n        t = gridColor;\n    }\n\n    float finalFade = ddd * vignette;\n    float alpha = length(color) * finalFade * opacity;\n    gl_FragColor = vec4(color * t * finalFade * opacity, alpha);\n}';
  function init(){ var els=document.querySelectorAll('.ripple-grid'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"gridColor":{"t":"3f","v":[0.55,0.5,1]},"rippleIntensity":{"t":"1f","v":1},"gridSize":{"t":"1f","v":1},"gridThickness":{"t":"1f","v":1},"fadeDistance":{"t":"1f","v":0.5},"vignetteStrength":{"t":"1f","v":1},"glowIntensity":{"t":"1f","v":1},"opacity":{"t":"1f","v":1},"gridRotation":{"t":"1f","v":1},"mousePosition":{"t":"2f","v":[0.5,0.5]},"mouseInfluence":{"t":"1f","v":1},"mouseInteractionRadius":{"t":"1f","v":1}} }); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* ripple-press */
/* ripple.js — motion-anything recipe · category: feedback-delight
 *
 * Spawns a ripple at the exact press point of any .ripple element, then removes it.
 * - Transform/opacity only. No ripple under prefers-reduced-motion (native :active remains).
 *
 * Usage:  <button class="ripple">Save</button>
 */
(function (global) {
  'use strict';

  function reduced() { return global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches; }

  function spawn(el, e) {
    var r = el.getBoundingClientRect();
    var size = Math.max(r.width, r.height) * 2;
    var x = (e.clientX != null ? e.clientX : r.left + r.width / 2) - r.left;
    var y = (e.clientY != null ? e.clientY : r.top + r.height / 2) - r.top;
    var wave = document.createElement('span');
    wave.className = 'ripple-wave';
    wave.style.width = wave.style.height = size + 'px';
    wave.style.left = (x - size / 2) + 'px';
    wave.style.top = (y - size / 2) + 'px';
    el.appendChild(wave);
    wave.addEventListener('animationend', function () { wave.remove(); });
  }

  function attach(el) {
    if (el.__rippleBound) return;
    el.__rippleBound = true;
    el.addEventListener('pointerdown', function (e) { if (!reduced()) spawn(el, e); });
  }

  function init() {
    var els = document.querySelectorAll('.ripple');
    for (var i = 0; i < els.length; i++) attach(els[i]);
  }

  global.attachRipple = attach;
  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})(window);


/* rotating-text */
/* rotating-text.js — text-kinetic · cycles data-words in place. Reduced-motion → swaps text without travel. */
(function(g){ 'use strict';
  function red(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function attach(el){ if(el.__rot) return; el.__rot=1; var words=(el.getAttribute('data-words')||el.textContent).split('|'); var i=0;
    el.textContent=''; var w=document.createElement('span'); w.className='rot-w'; w.textContent=words[0].trim(); el.appendChild(w);
    setInterval(function(){ i=(i+1)%words.length; if(red()){ w.textContent=words[i].trim(); return; } el.classList.add('swap');
      setTimeout(function(){ w.textContent=words[i].trim(); el.classList.remove('swap'); }, 460); }, 2200); }
  function init(){ var els=document.querySelectorAll('.rot'); for(var i=0;i<els.length;i++) attach(els[i]); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* scroll-float */
/* scroll-float.js — text-kinetic · split words, reveal on view once. Reduced-motion → visible. */
(function(g){ 'use strict';
  function red(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function split(el){ var parts=el.textContent.split(/(\s+)/); el.textContent=''; var i=0;
    parts.forEach(function(p){ if(p==='')return; if(/^\s+$/.test(p)){ var s=document.createElement('span'); s.className='sf-sp'; el.appendChild(s); return; }
      var w=document.createElement('span'); w.className='sf-w'; w.textContent=p; w.style.setProperty('--sf-d',(i*70)+'ms'); i++; el.appendChild(w); }); }
  function init(){ var els=document.querySelectorAll('[data-scroll-float]'); if(!els.length) return; els.forEach(split);
    if(red()||!('IntersectionObserver' in g)){ els.forEach(function(e){e.classList.add('in');}); return; }
    var io=new IntersectionObserver(function(es){ es.forEach(function(e){ if(e.isIntersecting){ e.target.classList.add('in'); io.unobserve(e.target); } }); }, {threshold:.2});
    els.forEach(function(e){ io.observe(e); }); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* scroll-reveal */
/* scroll-reveal.js — motion-anything recipe · category: scroll-reveal
 *
 * Reveals [data-reveal] elements as they scroll into view, once.
 * - Respects prefers-reduced-motion (reveals everything immediately).
 * - Optional per-element stagger via data-reveal-delay="80" (milliseconds).
 * - Cleans up: unobserves each element after it reveals.
 *
 * Usage:
 *   <section data-reveal>…</section>
 *   <li data-reveal data-reveal-delay="80">…</li>
 */
(function () {
  'use strict';

  function revealAll() {
    document.querySelectorAll('[data-reveal]').forEach(function (el) {
      el.classList.add('is-in');
    });
  }

  function init() {
    var els = document.querySelectorAll('[data-reveal]');
    if (!els.length) return;

    var reduce =
      window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce || !('IntersectionObserver' in window)) {
      revealAll();
      return;
    }

    els.forEach(function (el) {
      var d = el.getAttribute('data-reveal-delay');
      if (d) el.style.setProperty('--reveal-delay', parseInt(d, 10) + 'ms');
    });

    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-in');
            io.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.15, rootMargin: '0px 0px -10% 0px' }
    );
    els.forEach(function (el) { io.observe(el); });
  }

  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})();


/* shuffle-text */
/* shuffle-text.js — text-kinetic · letters cycle through A-Z then settle into the word. Reduced-motion → final. */
(function(g){ 'use strict';
  var AZ='ABCDEFGHIJKLMNOPQRSTUVWXYZ'; function red(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function run(el){ var target=el.getAttribute('data-text')||el.textContent; if(red()){ el.textContent=target; return; }
    var f=0; clearInterval(el.__s); el.__s=setInterval(function(){ var out='';
      for(var i=0;i<target.length;i++){ if(f > i*3+6){ out+=target[i]; } else if(target[i]===' '){ out+=' '; } else { out+=AZ[Math.floor(Math.random()*26)]; } }
      el.textContent=out; f++; if(f > target.length*3+6){ clearInterval(el.__s); el.textContent=target; } }, 28); }
  function attach(el){ if(el.__sh) return; el.__sh=1; el.setAttribute('data-text', el.getAttribute('data-text')||el.textContent); run(el); el.addEventListener('mouseenter', function(){ run(el); }); }
  function init(){ var els=document.querySelectorAll('.shuffle'); for(var i=0;i<els.length;i++) attach(els[i]); }
  g.shuffleEl=run; if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* side-rays */
/* side-rays.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='precision highp float;\n\nuniform float iTime;\nuniform vec2 iResolution;\nuniform float iSpeed;\nuniform vec3 iRayColor1;\nuniform vec3 iRayColor2;\nuniform float iIntensity;\nuniform float iSpread;\nuniform float iFlipX;\nuniform float iFlipY;\nuniform float iTilt;\nuniform float iSaturation;\nuniform float iBlend;\nuniform float iFalloff;\nuniform float iOpacity;\n\nfloat rayStrength(vec2 raySource, vec2 rayRefDirection, vec2 coord, float seedA, float seedB, float speed) {\n  vec2 sourceToCoord = coord - raySource;\n  float cosAngle = dot(normalize(sourceToCoord), rayRefDirection);\n  return clamp(\n    (0.45 + 0.15 * sin(cosAngle * seedA + iTime * speed)) +\n    (0.3 + 0.2 * cos(-cosAngle * seedB + iTime * speed)),\n    0.0, 1.0) *\n    clamp((iResolution.x - length(sourceToCoord)) / iResolution.x, 0.5, 1.0);\n}\n\nvoid main() {\n  vec2 fragCoord = gl_FragCoord.xy;\n  if (iFlipX > 0.5) fragCoord.x = iResolution.x - fragCoord.x;\n  if (iFlipY > 0.5) fragCoord.y = iResolution.y - fragCoord.y;\n\n  vec2 coord = vec2(fragCoord.x, iResolution.y - fragCoord.y);\n  vec2 rayPos = vec2(iResolution.x * 1.1, -0.5 * iResolution.y);\n\n  float tiltRad = iTilt * 3.14159265 / 180.0;\n  float cs = cos(tiltRad);\n  float sn = sin(tiltRad);\n  vec2 rel = coord - rayPos;\n  vec2 tiltedCoord = vec2(rel.x * cs - rel.y * sn, rel.x * sn + rel.y * cs) + rayPos;\n\n  float halfSpread = iSpread * 0.275;\n  vec2 rayRefDir1 = normalize(vec2(cos(0.785398 + halfSpread), sin(0.785398 + halfSpread)));\n  vec2 rayRefDir2 = normalize(vec2(cos(0.785398 - halfSpread), sin(0.785398 - halfSpread)));\n\n  vec4 rays1 = vec4(iRayColor1, 1.0) * rayStrength(rayPos, rayRefDir1, tiltedCoord, 36.2214, 21.11349, iSpeed);\n  vec4 rays2 = vec4(iRayColor2, 1.0) * rayStrength(rayPos, rayRefDir2, tiltedCoord, 22.3991, 18.0234, iSpeed * 0.2);\n\n  vec4 color = rays1 * (1.0 - iBlend) * 0.9 + rays2 * iBlend * 0.9;\n\n  float distanceToLight = length(fragCoord.xy - vec2(rayPos.x, iResolution.y - rayPos.y)) / iResolution.y;\n  float brightness = iIntensity * 0.4 / pow(max(distanceToLight, 0.001), iFalloff);\n  color.rgb *= brightness;\n\n  float gray = dot(color.rgb, vec3(0.299, 0.587, 0.114));\n  color.rgb = mix(vec3(gray), color.rgb, iSaturation);\n\n  color.a = max(color.r, max(color.g, color.b)) * iOpacity;\n  gl_FragColor = color;\n}';
  function init(){ var els=document.querySelectorAll('.side-rays'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"iSpeed":{"t":"1f","v":2.5},"iRayColor1":{"t":"3f","v":[0.9176470588235294,0.7019607843137254,0.03137254901960784]},"iRayColor2":{"t":"3f","v":[0.5882352941176471,0.7843137254901961,1]},"iIntensity":{"t":"1f","v":2},"iSpread":{"t":"1f","v":2},"iFlipX":{"t":"1f","v":0},"iFlipY":{"t":"1f","v":0},"iTilt":{"t":"1f","v":0},"iSaturation":{"t":"1f","v":1.5},"iBlend":{"t":"1f","v":0.75},"iFalloff":{"t":"1f","v":1.6},"iOpacity":{"t":"1f","v":1}} });  } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* silk */
/* silk.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='precision highp float;\n\nvarying vec2 vUv;\n\nuniform float uTime;\nuniform vec3  uColor;\nuniform float uSpeed;\nuniform float uScale;\nuniform float uRotation;\nuniform float uNoiseIntensity;\n\nconst float e = 2.71828182845904523536;\n\nfloat noise(vec2 texCoord) {\n  float G = e;\n  vec2  r = (G * sin(G * texCoord));\n  return fract(r.x * r.y * (1.0 + texCoord.x));\n}\n\nvec2 rotateUvs(vec2 uv, float angle) {\n  float c = cos(angle);\n  float s = sin(angle);\n  mat2  rot = mat2(c, -s, s, c);\n  return rot * uv;\n}\n\nvoid main() {\n  float rnd        = noise(gl_FragCoord.xy);\n  vec2  uv         = rotateUvs(vUv * uScale, uRotation);\n  vec2  tex        = uv * uScale;\n  float tOffset    = uSpeed * uTime;\n\n  tex.y += 0.03 * sin(8.0 * tex.x - tOffset);\n\n  float pattern = 0.6 +\n                  0.4 * sin(5.0 * (tex.x + tex.y +\n                                   cos(3.0 * tex.x + 5.0 * tex.y) +\n                                   0.02 * tOffset) +\n                           sin(20.0 * (tex.x + tex.y - 0.1 * tOffset)));\n\n  vec4 col = vec4(uColor, 1.0) * vec4(pattern) - rnd / 15.0 * uNoiseIntensity;\n  col.a = 1.0;\n  gl_FragColor = col;\n}\n';
  function init(){ var els=document.querySelectorAll('.silk'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    var h=g.ShaderBG(els[i], FRAG, {"uniforms":{"uColor":{"t":"3f","v":[0.4823529411764706,0.4549019607843137,0.5058823529411764]},"uSpeed":{"t":"1f","v":0.5},"uScale":{"t":"1f","v":1},"uRotation":{"t":"1f","v":0},"uNoiseIntensity":{"t":"1f","v":1.5}}}); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* soft-aurora */
/* soft-aurora.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='\nprecision highp float;\n\nuniform float uTime;\nuniform vec3 uResolution;\nuniform float uSpeed;\nuniform float uScale;\nuniform float uBrightness;\nuniform vec3 uColor1;\nuniform vec3 uColor2;\nuniform float uNoiseFreq;\nuniform float uNoiseAmp;\nuniform float uBandHeight;\nuniform float uBandSpread;\nuniform float uOctaveDecay;\nuniform float uLayerOffset;\nuniform float uColorSpeed;\nuniform vec2 uMouse;\nuniform float uMouseInfluence;\nuniform bool uEnableMouse;\n\n#define TAU 6.28318\n\nvec3 gradientHash(vec3 p) {\n  p = vec3(\n    dot(p, vec3(127.1, 311.7, 234.6)),\n    dot(p, vec3(269.5, 183.3, 198.3)),\n    dot(p, vec3(169.5, 283.3, 156.9))\n  );\n  vec3 h = fract(sin(p) * 43758.5453123);\n  float phi = acos(2.0 * h.x - 1.0);\n  float theta = TAU * h.y;\n  return vec3(cos(theta) * sin(phi), sin(theta) * cos(phi), cos(phi));\n}\n\nfloat quinticSmooth(float t) {\n  float t2 = t * t;\n  float t3 = t * t2;\n  return 6.0 * t3 * t2 - 15.0 * t2 * t2 + 10.0 * t3;\n}\n\nvec3 cosineGradient(float t, vec3 a, vec3 b, vec3 c, vec3 d) {\n  return a + b * cos(TAU * (c * t + d));\n}\n\nfloat perlin3D(float amplitude, float frequency, float px, float py, float pz) {\n  float x = px * frequency;\n  float y = py * frequency;\n\n  float fx = floor(x); float fy = floor(y); float fz = floor(pz);\n  float cx = ceil(x);  float cy = ceil(y);  float cz = ceil(pz);\n\n  vec3 g000 = gradientHash(vec3(fx, fy, fz));\n  vec3 g100 = gradientHash(vec3(cx, fy, fz));\n  vec3 g010 = gradientHash(vec3(fx, cy, fz));\n  vec3 g110 = gradientHash(vec3(cx, cy, fz));\n  vec3 g001 = gradientHash(vec3(fx, fy, cz));\n  vec3 g101 = gradientHash(vec3(cx, fy, cz));\n  vec3 g011 = gradientHash(vec3(fx, cy, cz));\n  vec3 g111 = gradientHash(vec3(cx, cy, cz));\n\n  float d000 = dot(g000, vec3(x - fx, y - fy, pz - fz));\n  float d100 = dot(g100, vec3(x - cx, y - fy, pz - fz));\n  float d010 = dot(g010, vec3(x - fx, y - cy, pz - fz));\n  float d110 = dot(g110, vec3(x - cx, y - cy, pz - fz));\n  float d001 = dot(g001, vec3(x - fx, y - fy, pz - cz));\n  float d101 = dot(g101, vec3(x - cx, y - fy, pz - cz));\n  float d011 = dot(g011, vec3(x - fx, y - cy, pz - cz));\n  float d111 = dot(g111, vec3(x - cx, y - cy, pz - cz));\n\n  float sx = quinticSmooth(x - fx);\n  float sy = quinticSmooth(y - fy);\n  float sz = quinticSmooth(pz - fz);\n\n  float lx00 = mix(d000, d100, sx);\n  float lx10 = mix(d010, d110, sx);\n  float lx01 = mix(d001, d101, sx);\n  float lx11 = mix(d011, d111, sx);\n\n  float ly0 = mix(lx00, lx10, sy);\n  float ly1 = mix(lx01, lx11, sy);\n\n  return amplitude * mix(ly0, ly1, sz);\n}\n\nfloat auroraGlow(float t, vec2 shift) {\n  vec2 uv = gl_FragCoord.xy / uResolution.y;\n  uv += shift;\n\n  float noiseVal = 0.0;\n  float freq = uNoiseFreq;\n  float amp = uNoiseAmp;\n  vec2 samplePos = uv * uScale;\n\n  for (float i = 0.0; i < 3.0; i += 1.0) {\n    noiseVal += perlin3D(amp, freq, samplePos.x, samplePos.y, t);\n    amp *= uOctaveDecay;\n    freq *= 2.0;\n  }\n\n  float yBand = uv.y * 10.0 - uBandHeight * 10.0;\n  return 0.3 * max(exp(uBandSpread * (1.0 - 1.1 * abs(noiseVal + yBand))), 0.0);\n}\n\nvoid main() {\n  vec2 uv = gl_FragCoord.xy / uResolution.xy;\n  float t = uSpeed * 0.4 * uTime;\n\n  vec2 shift = vec2(0.0);\n  if (uEnableMouse) {\n    shift = (uMouse - 0.5) * uMouseInfluence;\n  }\n\n  vec3 col = vec3(0.0);\n  col += 0.99 * auroraGlow(t, shift) * cosineGradient(uv.x + uTime * uSpeed * 0.2 * uColorSpeed, vec3(0.5), vec3(0.5), vec3(1.0), vec3(0.3, 0.20, 0.20)) * uColor1;\n  col += 0.99 * auroraGlow(t + uLayerOffset, shift) * cosineGradient(uv.x + uTime * uSpeed * 0.1 * uColorSpeed, vec3(0.5), vec3(0.5), vec3(2.0, 1.0, 0.0), vec3(0.5, 0.20, 0.25)) * uColor2;\n\n  col *= uBrightness;\n  float alpha = clamp(length(col), 0.0, 1.0);\n  gl_FragColor = vec4(col, alpha);\n}\n';
  function init(){ var els=document.querySelectorAll('.soft-aurora'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"uSpeed":{"t":"1f","v":1},"uScale":{"t":"1f","v":1},"uBrightness":{"t":"1f","v":1},"uColor1":{"t":"3f","v":[0.55,0.5,1]},"uColor2":{"t":"3f","v":[0.55,0.5,1]},"uNoiseFreq":{"t":"1f","v":2},"uNoiseAmp":{"t":"1f","v":0.3},"uBandHeight":{"t":"1f","v":1},"uBandSpread":{"t":"1f","v":1},"uOctaveDecay":{"t":"1f","v":1},"uLayerOffset":{"t":"1f","v":1},"uColorSpeed":{"t":"1f","v":1},"uMouseInfluence":{"t":"1f","v":1}} }); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* splash-cursor */
/* splash-cursor.js — motion-anything recipe · interaction · faithful port (dependency-free).
 * The react-bits fluid-simulation cursor (raw WebGL multi-pass: curl/vorticity/pressure/advection,
 * dye + velocity framebuffer ping-pong). The source is one zero-state useEffect of vanilla WebGL —
 * this port extracts it verbatim; only the React wrapper (refs/JSX/cleanup) is replaced. Pointer
 * splats colorful fluid; pointer-events stay on the page (canvas is a passive overlay).
 * Real defaults from source. Skipped under prefers-reduced-motion. */
(function(g){ 'use strict';
  function reduced(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function start(el){
    if(el.__ma) return; el.__ma = 1;
    if(reduced()) return; // pointer-driven fluid — nothing to show statically
    var canvas = document.createElement('canvas');
    canvas.style.cssText = 'width:100%;height:100%;display:block;pointer-events:none';
    el.appendChild(canvas);
    var animationFrameId = { current: null }; // shim for the extracted ref
    // —— real defaults from the source component props ——
    const SIM_RESOLUTION = 128, DYE_RESOLUTION = 1440, CAPTURE_RESOLUTION = 512,
      DENSITY_DISSIPATION = 3.5, VELOCITY_DISSIPATION = 2, PRESSURE = 0.1,
      PRESSURE_ITERATIONS = 20, CURL = 3, SPLAT_RADIUS = 0.2, SPLAT_FORCE = 6000,
      SHADING = true, COLOR_UPDATE_SPEED = 10, BACK_COLOR = { r: 0.5, g: 0, b: 0 },
      TRANSPARENT = true, RAINBOW_MODE = true, COLOR = '#ff0000';
    // —— verbatim body extracted from the source useEffect ——
    let isActive = true;

    function pointerPrototype() {
      this.id = -1;
      this.texcoordX = 0;
      this.texcoordY = 0;
      this.prevTexcoordX = 0;
      this.prevTexcoordY = 0;
      this.deltaX = 0;
      this.deltaY = 0;
      this.down = false;
      this.moved = false;
      this.color = [0, 0, 0];
    }

    let config = {
      SIM_RESOLUTION,
      DYE_RESOLUTION,
      CAPTURE_RESOLUTION,
      DENSITY_DISSIPATION,
      VELOCITY_DISSIPATION,
      PRESSURE,
      PRESSURE_ITERATIONS,
      CURL,
      SPLAT_RADIUS,
      SPLAT_FORCE,
      SHADING,
      COLOR_UPDATE_SPEED,
      PAUSED: false,
      BACK_COLOR,
      TRANSPARENT,
      RAINBOW_MODE,
      COLOR
    };

    let pointers = [new pointerPrototype()];

    const { gl, ext } = getWebGLContext(canvas);
    if (!ext.supportLinearFiltering) {
      config.DYE_RESOLUTION = 256;
      config.SHADING = false;
    }

    function getWebGLContext(canvas) {
      const params = {
        alpha: true,
        depth: false,
        stencil: false,
        antialias: false,
        preserveDrawingBuffer: false
      };
      let gl = canvas.getContext('webgl2', params);
      const isWebGL2 = !!gl;
      if (!isWebGL2) gl = canvas.getContext('webgl', params) || canvas.getContext('experimental-webgl', params);

      let halfFloat;
      let supportLinearFiltering;
      if (isWebGL2) {
        gl.getExtension('EXT_color_buffer_float');
        supportLinearFiltering = gl.getExtension('OES_texture_float_linear');
      } else {
        halfFloat = gl.getExtension('OES_texture_half_float');
        supportLinearFiltering = gl.getExtension('OES_texture_half_float_linear');
      }
      gl.clearColor(0.0, 0.0, 0.0, 1.0);

      const halfFloatTexType = isWebGL2 ? gl.HALF_FLOAT : halfFloat && halfFloat.HALF_FLOAT_OES;
      let formatRGBA;
      let formatRG;
      let formatR;

      if (isWebGL2) {
        formatRGBA = getSupportedFormat(gl, gl.RGBA16F, gl.RGBA, halfFloatTexType);
        formatRG = getSupportedFormat(gl, gl.RG16F, gl.RG, halfFloatTexType);
        formatR = getSupportedFormat(gl, gl.R16F, gl.RED, halfFloatTexType);
      } else {
        formatRGBA = getSupportedFormat(gl, gl.RGBA, gl.RGBA, halfFloatTexType);
        formatRG = getSupportedFormat(gl, gl.RGBA, gl.RGBA, halfFloatTexType);
        formatR = getSupportedFormat(gl, gl.RGBA, gl.RGBA, halfFloatTexType);
      }

      return {
        gl,
        ext: {
          formatRGBA,
          formatRG,
          formatR,
          halfFloatTexType,
          supportLinearFiltering
        }
      };
    }

    function getSupportedFormat(gl, internalFormat, format, type) {
      if (!supportRenderTextureFormat(gl, internalFormat, format, type)) {
        switch (internalFormat) {
          case gl.R16F:
            return getSupportedFormat(gl, gl.RG16F, gl.RG, type);
          case gl.RG16F:
            return getSupportedFormat(gl, gl.RGBA16F, gl.RGBA, type);
          default:
            return null;
        }
      }
      return { internalFormat, format };
    }

    function supportRenderTextureFormat(gl, internalFormat, format, type) {
      const texture = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texImage2D(gl.TEXTURE_2D, 0, internalFormat, 4, 4, 0, format, type, null);
      const fbo = gl.createFramebuffer();
      gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
      const status = gl.checkFramebufferStatus(gl.FRAMEBUFFER);
      return status === gl.FRAMEBUFFER_COMPLETE;
    }

    class Material {
      constructor(vertexShader, fragmentShaderSource) {
        this.vertexShader = vertexShader;
        this.fragmentShaderSource = fragmentShaderSource;
        this.programs = [];
        this.activeProgram = null;
        this.uniforms = [];
      }
      setKeywords(keywords) {
        let hash = 0;
        for (let i = 0; i < keywords.length; i++) hash += hashCode(keywords[i]);
        let program = this.programs[hash];
        if (program == null) {
          let fragmentShader = compileShader(gl.FRAGMENT_SHADER, this.fragmentShaderSource, keywords);
          program = createProgram(this.vertexShader, fragmentShader);
          this.programs[hash] = program;
        }
        if (program === this.activeProgram) return;
        this.uniforms = getUniforms(program);
        this.activeProgram = program;
      }
      bind() {
        gl.useProgram(this.activeProgram);
      }
    }

    class Program {
      constructor(vertexShader, fragmentShader) {
        this.uniforms = {};
        this.program = createProgram(vertexShader, fragmentShader);
        this.uniforms = getUniforms(this.program);
      }
      bind() {
        gl.useProgram(this.program);
      }
    }

    function createProgram(vertexShader, fragmentShader) {
      let program = gl.createProgram();
      gl.attachShader(program, vertexShader);
      gl.attachShader(program, fragmentShader);
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) console.trace(gl.getProgramInfoLog(program));
      return program;
    }

    function getUniforms(program) {
      let uniforms = [];
      let uniformCount = gl.getProgramParameter(program, gl.ACTIVE_UNIFORMS);
      for (let i = 0; i < uniformCount; i++) {
        let uniformName = gl.getActiveUniform(program, i).name;
        uniforms[uniformName] = gl.getUniformLocation(program, uniformName);
      }
      return uniforms;
    }

    function compileShader(type, source, keywords) {
      source = addKeywords(source, keywords);
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) console.trace(gl.getShaderInfoLog(shader));
      return shader;
    }

    function addKeywords(source, keywords) {
      if (!keywords) return source;
      let keywordsString = '';
      keywords.forEach(keyword => {
        keywordsString += '#define ' + keyword + '\n';
      });
      return keywordsString + source;
    }

    const baseVertexShader = compileShader(
      gl.VERTEX_SHADER,
      `
        precision highp float;
        attribute vec2 aPosition;
        varying vec2 vUv;
        varying vec2 vL;
        varying vec2 vR;
        varying vec2 vT;
        varying vec2 vB;
        uniform vec2 texelSize;

        void main () {
            vUv = aPosition * 0.5 + 0.5;
            vL = vUv - vec2(texelSize.x, 0.0);
            vR = vUv + vec2(texelSize.x, 0.0);
            vT = vUv + vec2(0.0, texelSize.y);
            vB = vUv - vec2(0.0, texelSize.y);
            gl_Position = vec4(aPosition, 0.0, 1.0);
        }
      `
    );

    const copyShader = compileShader(
      gl.FRAGMENT_SHADER,
      `
        precision mediump float;
        precision mediump sampler2D;
        varying highp vec2 vUv;
        uniform sampler2D uTexture;

        void main () {
            gl_FragColor = texture2D(uTexture, vUv);
        }
      `
    );

    const clearShader = compileShader(
      gl.FRAGMENT_SHADER,
      `
        precision mediump float;
        precision mediump sampler2D;
        varying highp vec2 vUv;
        uniform sampler2D uTexture;
        uniform float value;

        void main () {
            gl_FragColor = value * texture2D(uTexture, vUv);
        }
      `
    );

    const displayShaderSource = `
      precision highp float;
      precision highp sampler2D;
      varying vec2 vUv;
      varying vec2 vL;
      varying vec2 vR;
      varying vec2 vT;
      varying vec2 vB;
      uniform sampler2D uTexture;
      uniform sampler2D uDithering;
      uniform vec2 ditherScale;
      uniform vec2 texelSize;

      vec3 linearToGamma (vec3 color) {
          color = max(color, vec3(0));
          return max(1.055 * pow(color, vec3(0.416666667)) - 0.055, vec3(0));
      }

      void main () {
          vec3 c = texture2D(uTexture, vUv).rgb;
          #ifdef SHADING
              vec3 lc = texture2D(uTexture, vL).rgb;
              vec3 rc = texture2D(uTexture, vR).rgb;
              vec3 tc = texture2D(uTexture, vT).rgb;
              vec3 bc = texture2D(uTexture, vB).rgb;

              float dx = length(rc) - length(lc);
              float dy = length(tc) - length(bc);

              vec3 n = normalize(vec3(dx, dy, length(texelSize)));
              vec3 l = vec3(0.0, 0.0, 1.0);

              float diffuse = clamp(dot(n, l) + 0.7, 0.7, 1.0);
              c *= diffuse;
          #endif

          float a = max(c.r, max(c.g, c.b));
          gl_FragColor = vec4(c, a);
      }
    `;

    const splatShader = compileShader(
      gl.FRAGMENT_SHADER,
      `
        precision highp float;
        precision highp sampler2D;
        varying vec2 vUv;
        uniform sampler2D uTarget;
        uniform float aspectRatio;
        uniform vec3 color;
        uniform vec2 point;
        uniform float radius;

        void main () {
            vec2 p = vUv - point.xy;
            p.x *= aspectRatio;
            vec3 splat = exp(-dot(p, p) / radius) * color;
            vec3 base = texture2D(uTarget, vUv).xyz;
            gl_FragColor = vec4(base + splat, 1.0);
        }
      `
    );

    const advectionShader = compileShader(
      gl.FRAGMENT_SHADER,
      `
        precision highp float;
        precision highp sampler2D;
        varying vec2 vUv;
        uniform sampler2D uVelocity;
        uniform sampler2D uSource;
        uniform vec2 texelSize;
        uniform vec2 dyeTexelSize;
        uniform float dt;
        uniform float dissipation;

        vec4 bilerp (sampler2D sam, vec2 uv, vec2 tsize) {
            vec2 st = uv / tsize - 0.5;
            vec2 iuv = floor(st);
            vec2 fuv = fract(st);

            vec4 a = texture2D(sam, (iuv + vec2(0.5, 0.5)) * tsize);
            vec4 b = texture2D(sam, (iuv + vec2(1.5, 0.5)) * tsize);
            vec4 c = texture2D(sam, (iuv + vec2(0.5, 1.5)) * tsize);
            vec4 d = texture2D(sam, (iuv + vec2(1.5, 1.5)) * tsize);

            return mix(mix(a, b, fuv.x), mix(c, d, fuv.x), fuv.y);
        }

        void main () {
            #ifdef MANUAL_FILTERING
                vec2 coord = vUv - dt * bilerp(uVelocity, vUv, texelSize).xy * texelSize;
                vec4 result = bilerp(uSource, coord, dyeTexelSize);
            #else
                vec2 coord = vUv - dt * texture2D(uVelocity, vUv).xy * texelSize;
                vec4 result = texture2D(uSource, coord);
            #endif
            float decay = 1.0 + dissipation * dt;
            gl_FragColor = result / decay;
        }
      `,
      ext.supportLinearFiltering ? null : ['MANUAL_FILTERING']
    );

    const divergenceShader = compileShader(
      gl.FRAGMENT_SHADER,
      `
        precision mediump float;
        precision mediump sampler2D;
        varying highp vec2 vUv;
        varying highp vec2 vL;
        varying highp vec2 vR;
        varying highp vec2 vT;
        varying highp vec2 vB;
        uniform sampler2D uVelocity;

        void main () {
            float L = texture2D(uVelocity, vL).x;
            float R = texture2D(uVelocity, vR).x;
            float T = texture2D(uVelocity, vT).y;
            float B = texture2D(uVelocity, vB).y;

            vec2 C = texture2D(uVelocity, vUv).xy;
            if (vL.x < 0.0) { L = -C.x; }
            if (vR.x > 1.0) { R = -C.x; }
            if (vT.y > 1.0) { T = -C.y; }
            if (vB.y < 0.0) { B = -C.y; }

            float div = 0.5 * (R - L + T - B);
            gl_FragColor = vec4(div, 0.0, 0.0, 1.0);
        }
      `
    );

    const curlShader = compileShader(
      gl.FRAGMENT_SHADER,
      `
        precision mediump float;
        precision mediump sampler2D;
        varying highp vec2 vUv;
        varying highp vec2 vL;
        varying highp vec2 vR;
        varying highp vec2 vT;
        varying highp vec2 vB;
        uniform sampler2D uVelocity;

        void main () {
            float L = texture2D(uVelocity, vL).y;
            float R = texture2D(uVelocity, vR).y;
            float T = texture2D(uVelocity, vT).x;
            float B = texture2D(uVelocity, vB).x;
            float vorticity = R - L - T + B;
            gl_FragColor = vec4(0.5 * vorticity, 0.0, 0.0, 1.0);
        }
      `
    );

    const vorticityShader = compileShader(
      gl.FRAGMENT_SHADER,
      `
        precision highp float;
        precision highp sampler2D;
        varying vec2 vUv;
        varying vec2 vL;
        varying vec2 vR;
        varying vec2 vT;
        varying vec2 vB;
        uniform sampler2D uVelocity;
        uniform sampler2D uCurl;
        uniform float curl;
        uniform float dt;

        void main () {
            float L = texture2D(uCurl, vL).x;
            float R = texture2D(uCurl, vR).x;
            float T = texture2D(uCurl, vT).x;
            float B = texture2D(uCurl, vB).x;
            float C = texture2D(uCurl, vUv).x;

            vec2 force = 0.5 * vec2(abs(T) - abs(B), abs(R) - abs(L));
            force /= length(force) + 0.0001;
            force *= curl * C;
            force.y *= -1.0;

            vec2 velocity = texture2D(uVelocity, vUv).xy;
            velocity += force * dt;
            velocity = min(max(velocity, -1000.0), 1000.0);
            gl_FragColor = vec4(velocity, 0.0, 1.0);
        }
      `
    );

    const pressureShader = compileShader(
      gl.FRAGMENT_SHADER,
      `
        precision mediump float;
        precision mediump sampler2D;
        varying highp vec2 vUv;
        varying highp vec2 vL;
        varying highp vec2 vR;
        varying highp vec2 vT;
        varying highp vec2 vB;
        uniform sampler2D uPressure;
        uniform sampler2D uDivergence;

        void main () {
            float L = texture2D(uPressure, vL).x;
            float R = texture2D(uPressure, vR).x;
            float T = texture2D(uPressure, vT).x;
            float B = texture2D(uPressure, vB).x;
            float C = texture2D(uPressure, vUv).x;
            float divergence = texture2D(uDivergence, vUv).x;
            float pressure = (L + R + B + T - divergence) * 0.25;
            gl_FragColor = vec4(pressure, 0.0, 0.0, 1.0);
        }
      `
    );

    const gradientSubtractShader = compileShader(
      gl.FRAGMENT_SHADER,
      `
        precision mediump float;
        precision mediump sampler2D;
        varying highp vec2 vUv;
        varying highp vec2 vL;
        varying highp vec2 vR;
        varying highp vec2 vT;
        varying highp vec2 vB;
        uniform sampler2D uPressure;
        uniform sampler2D uVelocity;

        void main () {
            float L = texture2D(uPressure, vL).x;
            float R = texture2D(uPressure, vR).x;
            float T = texture2D(uPressure, vT).x;
            float B = texture2D(uPressure, vB).x;
            vec2 velocity = texture2D(uVelocity, vUv).xy;
            velocity.xy -= vec2(R - L, T - B);
            gl_FragColor = vec4(velocity, 0.0, 1.0);
        }
      `
    );

    const blit = (() => {
      gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, -1, 1, 1, 1, 1, -1]), gl.STATIC_DRAW);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array([0, 1, 2, 0, 2, 3]), gl.STATIC_DRAW);
      gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
      gl.enableVertexAttribArray(0);
      return (target, clear = false) => {
        if (target == null) {
          gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight);
          gl.bindFramebuffer(gl.FRAMEBUFFER, null);
        } else {
          gl.viewport(0, 0, target.width, target.height);
          gl.bindFramebuffer(gl.FRAMEBUFFER, target.fbo);
        }
        if (clear) {
          gl.clearColor(0.0, 0.0, 0.0, 1.0);
          gl.clear(gl.COLOR_BUFFER_BIT);
        }
        gl.drawElements(gl.TRIANGLES, 6, gl.UNSIGNED_SHORT, 0);
      };
    })();

    let dye, velocity, divergence, curl, pressure;

    const copyProgram = new Program(baseVertexShader, copyShader);
    const clearProgram = new Program(baseVertexShader, clearShader);
    const splatProgram = new Program(baseVertexShader, splatShader);
    const advectionProgram = new Program(baseVertexShader, advectionShader);
    const divergenceProgram = new Program(baseVertexShader, divergenceShader);
    const curlProgram = new Program(baseVertexShader, curlShader);
    const vorticityProgram = new Program(baseVertexShader, vorticityShader);
    const pressureProgram = new Program(baseVertexShader, pressureShader);
    const gradienSubtractProgram = new Program(baseVertexShader, gradientSubtractShader);
    const displayMaterial = new Material(baseVertexShader, displayShaderSource);

    function initFramebuffers() {
      let simRes = getResolution(config.SIM_RESOLUTION);
      let dyeRes = getResolution(config.DYE_RESOLUTION);
      const texType = ext.halfFloatTexType;
      const rgba = ext.formatRGBA;
      const rg = ext.formatRG;
      const r = ext.formatR;
      const filtering = ext.supportLinearFiltering ? gl.LINEAR : gl.NEAREST;
      gl.disable(gl.BLEND);

      if (!dye)
        dye = createDoubleFBO(dyeRes.width, dyeRes.height, rgba.internalFormat, rgba.format, texType, filtering);
      else
        dye = resizeDoubleFBO(dye, dyeRes.width, dyeRes.height, rgba.internalFormat, rgba.format, texType, filtering);

      if (!velocity)
        velocity = createDoubleFBO(simRes.width, simRes.height, rg.internalFormat, rg.format, texType, filtering);
      else
        velocity = resizeDoubleFBO(
          velocity,
          simRes.width,
          simRes.height,
          rg.internalFormat,
          rg.format,
          texType,
          filtering
        );

      divergence = createFBO(simRes.width, simRes.height, r.internalFormat, r.format, texType, gl.NEAREST);
      curl = createFBO(simRes.width, simRes.height, r.internalFormat, r.format, texType, gl.NEAREST);
      pressure = createDoubleFBO(simRes.width, simRes.height, r.internalFormat, r.format, texType, gl.NEAREST);
    }

    function createFBO(w, h, internalFormat, format, type, param) {
      gl.activeTexture(gl.TEXTURE0);
      let texture = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, param);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, param);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texImage2D(gl.TEXTURE_2D, 0, internalFormat, w, h, 0, format, type, null);

      let fbo = gl.createFramebuffer();
      gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
      gl.viewport(0, 0, w, h);
      gl.clear(gl.COLOR_BUFFER_BIT);

      let texelSizeX = 1.0 / w;
      let texelSizeY = 1.0 / h;
      return {
        texture,
        fbo,
        width: w,
        height: h,
        texelSizeX,
        texelSizeY,
        attach(id) {
          gl.activeTexture(gl.TEXTURE0 + id);
          gl.bindTexture(gl.TEXTURE_2D, texture);
          return id;
        }
      };
    }

    function createDoubleFBO(w, h, internalFormat, format, type, param) {
      let fbo1 = createFBO(w, h, internalFormat, format, type, param);
      let fbo2 = createFBO(w, h, internalFormat, format, type, param);
      return {
        width: w,
        height: h,
        texelSizeX: fbo1.texelSizeX,
        texelSizeY: fbo1.texelSizeY,
        get read() {
          return fbo1;
        },
        set read(value) {
          fbo1 = value;
        },
        get write() {
          return fbo2;
        },
        set write(value) {
          fbo2 = value;
        },
        swap() {
          let temp = fbo1;
          fbo1 = fbo2;
          fbo2 = temp;
        }
      };
    }

    function resizeFBO(target, w, h, internalFormat, format, type, param) {
      let newFBO = createFBO(w, h, internalFormat, format, type, param);
      copyProgram.bind();
      gl.uniform1i(copyProgram.uniforms.uTexture, target.attach(0));
      blit(newFBO);
      return newFBO;
    }

    function resizeDoubleFBO(target, w, h, internalFormat, format, type, param) {
      if (target.width === w && target.height === h) return target;
      target.read = resizeFBO(target.read, w, h, internalFormat, format, type, param);
      target.write = createFBO(w, h, internalFormat, format, type, param);
      target.width = w;
      target.height = h;
      target.texelSizeX = 1.0 / w;
      target.texelSizeY = 1.0 / h;
      return target;
    }

    function updateKeywords() {
      let displayKeywords = [];
      if (config.SHADING) displayKeywords.push('SHADING');
      displayMaterial.setKeywords(displayKeywords);
    }

    updateKeywords();
    initFramebuffers();
    let lastUpdateTime = Date.now();
    let colorUpdateTimer = 0.0;

    function updateFrame() {
      if (!isActive) return;
      const dt = calcDeltaTime();
      if (resizeCanvas()) initFramebuffers();
      updateColors(dt);
      applyInputs();
      step(dt);
      render(null);
      animationFrameId.current = requestAnimationFrame(updateFrame);
    }

    function calcDeltaTime() {
      let now = Date.now();
      let dt = (now - lastUpdateTime) / 1000;
      dt = Math.min(dt, 0.016666);
      lastUpdateTime = now;
      return dt;
    }

    function resizeCanvas() {
      let width = scaleByPixelRatio(canvas.clientWidth);
      let height = scaleByPixelRatio(canvas.clientHeight);
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
        return true;
      }
      return false;
    }

    function updateColors(dt) {
      colorUpdateTimer += dt * config.COLOR_UPDATE_SPEED;
      if (colorUpdateTimer >= 1) {
        colorUpdateTimer = wrap(colorUpdateTimer, 0, 1);
        pointers.forEach(p => {
          p.color = generateColor();
        });
      }
    }

    function applyInputs() {
      pointers.forEach(p => {
        if (p.moved) {
          p.moved = false;
          splatPointer(p);
        }
      });
    }

    function step(dt) {
      gl.disable(gl.BLEND);
      curlProgram.bind();
      gl.uniform2f(curlProgram.uniforms.texelSize, velocity.texelSizeX, velocity.texelSizeY);
      gl.uniform1i(curlProgram.uniforms.uVelocity, velocity.read.attach(0));
      blit(curl);

      vorticityProgram.bind();
      gl.uniform2f(vorticityProgram.uniforms.texelSize, velocity.texelSizeX, velocity.texelSizeY);
      gl.uniform1i(vorticityProgram.uniforms.uVelocity, velocity.read.attach(0));
      gl.uniform1i(vorticityProgram.uniforms.uCurl, curl.attach(1));
      gl.uniform1f(vorticityProgram.uniforms.curl, config.CURL);
      gl.uniform1f(vorticityProgram.uniforms.dt, dt);
      blit(velocity.write);
      velocity.swap();

      divergenceProgram.bind();
      gl.uniform2f(divergenceProgram.uniforms.texelSize, velocity.texelSizeX, velocity.texelSizeY);
      gl.uniform1i(divergenceProgram.uniforms.uVelocity, velocity.read.attach(0));
      blit(divergence);

      clearProgram.bind();
      gl.uniform1i(clearProgram.uniforms.uTexture, pressure.read.attach(0));
      gl.uniform1f(clearProgram.uniforms.value, config.PRESSURE);
      blit(pressure.write);
      pressure.swap();

      pressureProgram.bind();
      gl.uniform2f(pressureProgram.uniforms.texelSize, velocity.texelSizeX, velocity.texelSizeY);
      gl.uniform1i(pressureProgram.uniforms.uDivergence, divergence.attach(0));
      for (let i = 0; i < config.PRESSURE_ITERATIONS; i++) {
        gl.uniform1i(pressureProgram.uniforms.uPressure, pressure.read.attach(1));
        blit(pressure.write);
        pressure.swap();
      }

      gradienSubtractProgram.bind();
      gl.uniform2f(gradienSubtractProgram.uniforms.texelSize, velocity.texelSizeX, velocity.texelSizeY);
      gl.uniform1i(gradienSubtractProgram.uniforms.uPressure, pressure.read.attach(0));
      gl.uniform1i(gradienSubtractProgram.uniforms.uVelocity, velocity.read.attach(1));
      blit(velocity.write);
      velocity.swap();

      advectionProgram.bind();
      gl.uniform2f(advectionProgram.uniforms.texelSize, velocity.texelSizeX, velocity.texelSizeY);
      if (!ext.supportLinearFiltering)
        gl.uniform2f(advectionProgram.uniforms.dyeTexelSize, velocity.texelSizeX, velocity.texelSizeY);
      let velocityId = velocity.read.attach(0);
      gl.uniform1i(advectionProgram.uniforms.uVelocity, velocityId);
      gl.uniform1i(advectionProgram.uniforms.uSource, velocityId);
      gl.uniform1f(advectionProgram.uniforms.dt, dt);
      gl.uniform1f(advectionProgram.uniforms.dissipation, config.VELOCITY_DISSIPATION);
      blit(velocity.write);
      velocity.swap();

      if (!ext.supportLinearFiltering)
        gl.uniform2f(advectionProgram.uniforms.dyeTexelSize, dye.texelSizeX, dye.texelSizeY);
      gl.uniform1i(advectionProgram.uniforms.uVelocity, velocity.read.attach(0));
      gl.uniform1i(advectionProgram.uniforms.uSource, dye.read.attach(1));
      gl.uniform1f(advectionProgram.uniforms.dissipation, config.DENSITY_DISSIPATION);
      blit(dye.write);
      dye.swap();
    }

    function render(target) {
      gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      gl.enable(gl.BLEND);
      drawDisplay(target);
    }

    function drawDisplay(target) {
      let width = target == null ? gl.drawingBufferWidth : target.width;
      let height = target == null ? gl.drawingBufferHeight : target.height;
      displayMaterial.bind();
      if (config.SHADING) gl.uniform2f(displayMaterial.uniforms.texelSize, 1.0 / width, 1.0 / height);
      gl.uniform1i(displayMaterial.uniforms.uTexture, dye.read.attach(0));
      blit(target);
    }

    function splatPointer(pointer) {
      let dx = pointer.deltaX * config.SPLAT_FORCE;
      let dy = pointer.deltaY * config.SPLAT_FORCE;
      splat(pointer.texcoordX, pointer.texcoordY, dx, dy, pointer.color);
    }

    function clickSplat(pointer) {
      const color = generateColor();
      color.r *= 10.0;
      color.g *= 10.0;
      color.b *= 10.0;
      let dx = 10 * (Math.random() - 0.5);
      let dy = 30 * (Math.random() - 0.5);
      splat(pointer.texcoordX, pointer.texcoordY, dx, dy, color);
    }

    function splat(x, y, dx, dy, color) {
      splatProgram.bind();
      gl.uniform1i(splatProgram.uniforms.uTarget, velocity.read.attach(0));
      gl.uniform1f(splatProgram.uniforms.aspectRatio, canvas.width / canvas.height);
      gl.uniform2f(splatProgram.uniforms.point, x, y);
      gl.uniform3f(splatProgram.uniforms.color, dx, dy, 0.0);
      gl.uniform1f(splatProgram.uniforms.radius, correctRadius(config.SPLAT_RADIUS / 100.0));
      blit(velocity.write);
      velocity.swap();

      gl.uniform1i(splatProgram.uniforms.uTarget, dye.read.attach(0));
      gl.uniform3f(splatProgram.uniforms.color, color.r, color.g, color.b);
      blit(dye.write);
      dye.swap();
    }

    function correctRadius(radius) {
      let aspectRatio = canvas.width / canvas.height;
      if (aspectRatio > 1) radius *= aspectRatio;
      return radius;
    }

    function updatePointerDownData(pointer, id, posX, posY) {
      pointer.id = id;
      pointer.down = true;
      pointer.moved = false;
      pointer.texcoordX = posX / canvas.width;
      pointer.texcoordY = 1.0 - posY / canvas.height;
      pointer.prevTexcoordX = pointer.texcoordX;
      pointer.prevTexcoordY = pointer.texcoordY;
      pointer.deltaX = 0;
      pointer.deltaY = 0;
      pointer.color = generateColor();
    }

    function updatePointerMoveData(pointer, posX, posY, color) {
      pointer.prevTexcoordX = pointer.texcoordX;
      pointer.prevTexcoordY = pointer.texcoordY;
      pointer.texcoordX = posX / canvas.width;
      pointer.texcoordY = 1.0 - posY / canvas.height;
      pointer.deltaX = correctDeltaX(pointer.texcoordX - pointer.prevTexcoordX);
      pointer.deltaY = correctDeltaY(pointer.texcoordY - pointer.prevTexcoordY);
      pointer.moved = Math.abs(pointer.deltaX) > 0 || Math.abs(pointer.deltaY) > 0;
      pointer.color = color;
    }

    function updatePointerUpData(pointer) {
      pointer.down = false;
    }

    function correctDeltaX(delta) {
      let aspectRatio = canvas.width / canvas.height;
      if (aspectRatio < 1) delta *= aspectRatio;
      return delta;
    }

    function correctDeltaY(delta) {
      let aspectRatio = canvas.width / canvas.height;
      if (aspectRatio > 1) delta /= aspectRatio;
      return delta;
    }

    function hexToRGB(hex) {
      let val = hex.replace('#', '');
      if (val.length === 3) val = val[0] + val[0] + val[1] + val[1] + val[2] + val[2];
      const r = parseInt(val.slice(0, 2), 16) / 255;
      const g = parseInt(val.slice(2, 4), 16) / 255;
      const b = parseInt(val.slice(4, 6), 16) / 255;
      return { r: r * 0.15, g: g * 0.15, b: b * 0.15 };
    }

    function generateColor() {
      if (!config.RAINBOW_MODE) {
        return hexToRGB(config.COLOR);
      }
      let c = HSVtoRGB(Math.random(), 1.0, 1.0);
      c.r *= 0.15;
      c.g *= 0.15;
      c.b *= 0.15;
      return c;
    }

    function HSVtoRGB(h, s, v) {
      let r, g, b, i, f, p, q, t;
      i = Math.floor(h * 6);
      f = h * 6 - i;
      p = v * (1 - s);
      q = v * (1 - f * s);
      t = v * (1 - (1 - f) * s);
      switch (i % 6) {
        case 0:
          r = v;
          g = t;
          b = p;
          break;
        case 1:
          r = q;
          g = v;
          b = p;
          break;
        case 2:
          r = p;
          g = v;
          b = t;
          break;
        case 3:
          r = p;
          g = q;
          b = v;
          break;
        case 4:
          r = t;
          g = p;
          b = v;
          break;
        case 5:
          r = v;
          g = p;
          b = q;
          break;
        default:
          break;
      }
      return { r, g, b };
    }

    function wrap(value, min, max) {
      const range = max - min;
      if (range === 0) return min;
      return ((value - min) % range) + min;
    }

    function getResolution(resolution) {
      let aspectRatio = gl.drawingBufferWidth / gl.drawingBufferHeight;
      if (aspectRatio < 1) aspectRatio = 1.0 / aspectRatio;
      const min = Math.round(resolution);
      const max = Math.round(resolution * aspectRatio);
      if (gl.drawingBufferWidth > gl.drawingBufferHeight) return { width: max, height: min };
      else return { width: min, height: max };
    }

    function scaleByPixelRatio(input) {
      const pixelRatio = window.devicePixelRatio || 1;
      return Math.floor(input * pixelRatio);
    }

    function hashCode(s) {
      if (s.length === 0) return 0;
      let hash = 0;
      for (let i = 0; i < s.length; i++) {
        hash = (hash << 5) - hash + s.charCodeAt(i);
        hash |= 0;
      }
      return hash;
    }

    // Named event handlers for proper cleanup
    function handleMouseDown(e) {
      let pointer = pointers[0];
      let posX = scaleByPixelRatio(e.clientX);
      let posY = scaleByPixelRatio(e.clientY);
      updatePointerDownData(pointer, -1, posX, posY);
      clickSplat(pointer);
    }

    let firstMouseMoveHandled = false;
    function handleMouseMove(e) {
      let pointer = pointers[0];
      let posX = scaleByPixelRatio(e.clientX);
      let posY = scaleByPixelRatio(e.clientY);
      if (!firstMouseMoveHandled) {
        let color = generateColor();
        updatePointerMoveData(pointer, posX, posY, color);
        firstMouseMoveHandled = true;
      } else {
        updatePointerMoveData(pointer, posX, posY, pointer.color);
      }
    }

    function handleTouchStart(e) {
      const touches = e.targetTouches;
      let pointer = pointers[0];
      for (let i = 0; i < touches.length; i++) {
        let posX = scaleByPixelRatio(touches[i].clientX);
        let posY = scaleByPixelRatio(touches[i].clientY);
        updatePointerDownData(pointer, touches[i].identifier, posX, posY);
      }
    }

    function handleTouchMove(e) {
      const touches = e.targetTouches;
      let pointer = pointers[0];
      for (let i = 0; i < touches.length; i++) {
        let posX = scaleByPixelRatio(touches[i].clientX);
        let posY = scaleByPixelRatio(touches[i].clientY);
        updatePointerMoveData(pointer, posX, posY, pointer.color);
      }
    }

    function handleTouchEnd(e) {
      const touches = e.changedTouches;
      let pointer = pointers[0];
      for (let i = 0; i < touches.length; i++) {
        updatePointerUpData(pointer);
      }
    }

    // Add event listeners
    window.addEventListener('mousedown', handleMouseDown);
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('touchstart', handleTouchStart);
    window.addEventListener('touchmove', handleTouchMove, false);
    window.addEventListener('touchend', handleTouchEnd);

    updateFrame();
  }
  function init(){ var els=document.querySelectorAll('.splash-cursor'); for(var i=0;i<els.length;i++) start(els[i]); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* spotlight-card */
/* spotlight-card.js — motion-anything recipe · category: hover-press
 *
 * Tracks the pointer over a .spotlight card and writes its position into CSS vars
 * (--mx/--my), which the CSS uses to paint a radial spotlight. Paint only; no layout.
 * - Static (plain border) under prefers-reduced-motion and on touch.
 *
 * Usage:  <div class="spotlight"> … </div>
 */
(function (global) {
  'use strict';

  function reduced() { return global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function isTouch() { return global.matchMedia && global.matchMedia('(hover: none)').matches; }

  function attach(card) {
    if (card.__spotBound) return;
    card.__spotBound = true;
    card.addEventListener('pointermove', function (e) {
      var r = card.getBoundingClientRect();
      card.style.setProperty('--mx', (e.clientX - r.left) + 'px');
      card.style.setProperty('--my', (e.clientY - r.top) + 'px');
    });
  }

  function init() {
    if (reduced() || isTouch()) return;      // leave cards static
    var els = document.querySelectorAll('.spotlight');
    for (var i = 0; i < els.length; i++) attach(els[i]);
  }

  global.attachSpotlight = attach;
  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})(window);


/* stagger-list */
/* stagger-list.js — motion-anything recipe · category: entrance · target: list
 *
 * Staggers the direct children of every [data-stagger] container in on load.
 * Optional step in ms via data-stagger-step="70". Honors prefers-reduced-motion.
 *
 * Usage:  <ul data-stagger> <li>…</li> <li>…</li> </ul>
 */
(function () {
  'use strict';
  function init() {
    var lists = document.querySelectorAll('[data-stagger]');
    if (!lists.length) return;
    var reduce = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    lists.forEach(function (list) {
      var step = parseInt(list.getAttribute('data-stagger-step') || '70', 10);
      [].slice.call(list.children).forEach(function (child, i) {
        child.style.setProperty('--st-delay', (i * step) + 'ms');
      });
    });

    if (reduce) {
      lists.forEach(function (l) { l.classList.add('is-in'); });
      return;
    }
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        lists.forEach(function (l) { l.classList.add('is-in'); });
      });
    });
  }
  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})();


/* stepper */
/* stepper.js — feedback-delight · setStep(el, n) fills up to step n. */
(function(g){ 'use strict';
  function set(el, n){ var sts=el.querySelectorAll('.st'), bars=el.querySelectorAll('.bar');
    sts.forEach(function(s,i){ s.classList.toggle('done', i<n); }); bars.forEach(function(b,i){ b.classList.toggle('fill', i<n-1); }); el.__n=n; }
  function attach(el){ if(el.__st) return; el.__st=1; set(el, +el.getAttribute('data-step')||1);
    el.addEventListener('click', function(){ var total=el.querySelectorAll('.st').length; set(el, (el.__n%total)+1); }); }
  function init(){ var els=document.querySelectorAll('.stepper'); for(var i=0;i<els.length;i++) attach(els[i]); }
  g.setStep=set; if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* strands */
/* strands.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='#version 300 es\nprecision highp float;\n\nuniform float uTime;\nuniform vec2 uResolution;\nuniform vec3 uColors[8];\nuniform int uColorCount;\nuniform int uStrandCount;\nuniform float uSpeed;\nuniform float uAmplitude;\nuniform float uWaviness;\nuniform float uThickness;\nuniform float uGlow;\nuniform float uTaper;\nuniform float uSpread;\nuniform float uHueShift;\nuniform float uIntensity;\nuniform float uOpacity;\nuniform float uScale;\nuniform float uSaturation;\n\nout vec4 fragColor;\n\nconst float PI = 3.14159265;\n\nvec3 spectrum(float t) {\n  return 0.5 + 0.5 * cos(2.0 * PI * (t + vec3(0.00, 0.33, 0.67)));\n}\n\nvec3 samplePalette(float t) {\n  t = fract(t);\n  float scaled = t * float(uColorCount);\n  int idx = int(floor(scaled));\n  float blend = fract(scaled);\n  int nextIdx = idx + 1;\n  if (nextIdx >= uColorCount) nextIdx = 0;\n  return mix(uColors[idx], uColors[nextIdx], blend);\n}\n\nvec3 strandColor(float t) {\n  if (uColorCount > 0) return samplePalette(t);\n  return spectrum(t);\n}\n\nvoid main() {\n  vec2 uv = (gl_FragCoord.xy - 0.5 * uResolution) / uResolution.y;\n  uv /= max(uScale, 0.0001);\n\n  float e = 0.06 + uIntensity * 0.94;\n  float env = pow(max(cos(uv.x * PI * 1.3), 0.0), uTaper);\n\n  vec3 col = vec3(0.0);\n\n  for (int i = 0; i < 12; i++) {\n    if (i >= uStrandCount) break;\n\n    float fi = float(i);\n    float ph = fi * 1.7 * uSpread;\n    float freq = (2.0 + fi * 0.35) * uWaviness;\n    float spd = 1.4 + fi * 1.2;\n\n    float tt = uTime * uSpeed;\n    float w = sin(uv.x * freq + tt * spd + ph) * 0.60\n            + sin(uv.x * freq * 1.1 - tt * spd * 0.7 + ph * 1.7) * 0.40;\n\n    float amp = (0.1 + 0.02 * e) * env * uAmplitude;\n    float y = w * amp;\n\n    float d = abs(uv.y - y);\n    float thick = (0.001 + 0.05 * e) * (0.35 + env) * uThickness;\n    float g = thick / (d + thick * 0.45);\n    g = g * g;\n\n    float h = fi / float(uStrandCount) + uv.x * 0.30 + uTime * 0.04 + uHueShift;\n    col += strandColor(h) * g * env;\n  }\n\n  col *= 0.45 + 0.7 * e;\n  col = 1.0 - exp(-col * uGlow);\n\n  float gray = dot(col, vec3(0.2126, 0.7152, 0.0722));\n  col = max(mix(vec3(gray), col, uSaturation), 0.0);\n\n  float lum = max(max(col.r, col.g), col.b);\n  float alpha = clamp(lum, 0.0, 1.0) * uOpacity;\n\n  fragColor = vec4(col * uOpacity, alpha);\n}\n';
  function init(){ var els=document.querySelectorAll('.strands'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"uColors":{"t":"3fv","v":[1,0.25882352941176473,0.25882352941176473,0.48627450980392156,0.22745098039215686,0.9294117647058824,0.023529411764705882,0.7137254901960784,0.8313725490196079,0.9176470588235294,0.7019607843137254,0.03137254901960784,0.9176470588235294,0.7019607843137254,0.03137254901960784,0.9176470588235294,0.7019607843137254,0.03137254901960784,0.9176470588235294,0.7019607843137254,0.03137254901960784,0.9176470588235294,0.7019607843137254,0.03137254901960784]},"uColorCount":{"t":"1i","v":4},"uStrandCount":{"t":"1i","v":3},"uSpeed":{"t":"1f","v":0.5},"uAmplitude":{"t":"1f","v":1},"uWaviness":{"t":"1f","v":1},"uThickness":{"t":"1f","v":0.7},"uGlow":{"t":"1f","v":2.6},"uTaper":{"t":"1f","v":3},"uSpread":{"t":"1f","v":1},"uHueShift":{"t":"1f","v":0},"uIntensity":{"t":"1f","v":0.6},"uOpacity":{"t":"1f","v":1},"uScale":{"t":"1f","v":1.5},"uSaturation":{"t":"1f","v":1.5}} });  } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* target-cursor */
/* target-cursor.js — ambient · a ring follows the pointer and grows over .tc-target elements. transform only. */
(function(g){ 'use strict';
  function off(){ return g.matchMedia && (g.matchMedia('(prefers-reduced-motion: reduce)').matches || g.matchMedia('(hover: none)').matches); }
  function init(){ if(off()) return; var r=document.createElement('div');
    r.style.cssText='position:fixed;left:0;top:0;width:26px;height:26px;border:2px solid #8b7cf6;border-radius:50%;pointer-events:none;z-index:9998;transform:translate(-50%,-50%);transition:width .18s,height .18s,border-color .18s';
    document.body.appendChild(r);
    addEventListener('pointermove', function(e){ r.style.left=e.clientX+'px'; r.style.top=e.clientY+'px'; var t=e.target.closest && e.target.closest('.tc-target,a,button');
      if(t){ r.style.width='44px'; r.style.height='44px'; r.style.borderColor='#39d98a'; } else { r.style.width='26px'; r.style.height='26px'; r.style.borderColor='#8b7cf6'; } }); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* text-scramble */
/* text-scramble.js — motion-anything recipe · category: text-kinetic
 *
 * Resolves a line of text from random glyphs — a techy decode reveal. One short line.
 * - Under prefers-reduced-motion the final text appears instantly (no scramble).
 * - The resolved text is the real content; keep it short and readable.
 *
 * Usage:
 *   <span class="scramble" data-text="motion, anything"></span>
 *   // or programmatically: scrambleTo(el, "next phrase")
 */
(function (global) {
  'use strict';

  var CHARS = '!<>-_\\/[]{}—=+*^?#________';
  function reduced() { return global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches; }

  function scrambleTo(el, newText) {
    newText = newText != null ? newText : (el.getAttribute('data-text') || el.textContent || '');
    if (reduced()) { el.textContent = newText; return Promise.resolve(); }
    var oldText = el.textContent || '';
    var len = Math.max(oldText.length, newText.length);
    var queue = [];
    for (var i = 0; i < len; i++) {
      var from = oldText[i] || '';
      var to = newText[i] || '';
      var start = Math.floor(Math.random() * 20);
      var end = start + Math.floor(Math.random() * 20) + 8;
      queue.push({ from: from, to: to, start: start, end: end, char: '' });
    }
    if (el.__scrambleRaf) cancelAnimationFrame(el.__scrambleRaf);
    return new Promise(function (resolve) {
      var frame = 0;
      function update() {
        var out = '', done = 0;
        for (var i = 0; i < queue.length; i++) {
          var q = queue[i];
          if (frame >= q.end) { done++; out += q.to; }
          else if (frame >= q.start) {
            if (!q.char || Math.random() < 0.28) q.char = CHARS[Math.floor(Math.random() * CHARS.length)];
            out += '<span style="opacity:.55">' + q.char + '</span>';
          } else { out += q.from; }
        }
        el.innerHTML = out;
        if (done === queue.length) { el.textContent = newText; resolve(); return; }
        frame++;
        el.__scrambleRaf = requestAnimationFrame(update);
      }
      update();
    });
  }

  function init() {
    var els = document.querySelectorAll('.scramble');
    for (var i = 0; i < els.length; i++) {
      var el = els[i], txt = el.getAttribute('data-text') || el.textContent || '';
      // start blank so it decodes in; but on a hidden tab (rAF paused) keep the real text visible
      el.textContent = document.hidden ? txt : '';
      scrambleTo(el, txt);
    }
  }

  global.scrambleTo = scrambleTo;
  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})(window);


/* threads */
/* threads.js — motion-anything recipe · ambient · faithful GPU shader (dependency-free WebGL via _fx/shaderbg.js). */
(function(g){ 'use strict';
  var FRAG='\nprecision highp float;\n\nuniform float iTime;\nuniform vec3 iResolution;\nuniform vec3 uColor;\nuniform float uAmplitude;\nuniform float uDistance;\nuniform vec2 uMouse;\n\n#define PI 3.1415926538\n\nconst int u_line_count = 40;\nconst float u_line_width = 7.0;\nconst float u_line_blur = 10.0;\n\nfloat Perlin2D(vec2 P) {\n    vec2 Pi = floor(P);\n    vec4 Pf_Pfmin1 = P.xyxy - vec4(Pi, Pi + 1.0);\n    vec4 Pt = vec4(Pi.xy, Pi.xy + 1.0);\n    Pt = Pt - floor(Pt * (1.0 / 71.0)) * 71.0;\n    Pt += vec2(26.0, 161.0).xyxy;\n    Pt *= Pt;\n    Pt = Pt.xzxz * Pt.yyww;\n    vec4 hash_x = fract(Pt * (1.0 / 951.135664));\n    vec4 hash_y = fract(Pt * (1.0 / 642.949883));\n    vec4 grad_x = hash_x - 0.49999;\n    vec4 grad_y = hash_y - 0.49999;\n    vec4 grad_results = inversesqrt(grad_x * grad_x + grad_y * grad_y)\n        * (grad_x * Pf_Pfmin1.xzxz + grad_y * Pf_Pfmin1.yyww);\n    grad_results *= 1.4142135623730950;\n    vec2 blend = Pf_Pfmin1.xy * Pf_Pfmin1.xy * Pf_Pfmin1.xy\n               * (Pf_Pfmin1.xy * (Pf_Pfmin1.xy * 6.0 - 15.0) + 10.0);\n    vec4 blend2 = vec4(blend, vec2(1.0 - blend));\n    return dot(grad_results, blend2.zxzx * blend2.wwyy);\n}\n\nfloat pixel(float count, vec2 resolution) {\n    return (1.0 / max(resolution.x, resolution.y)) * count;\n}\n\nfloat lineFn(vec2 st, float width, float perc, float offset, vec2 mouse, float time, float amplitude, float distance) {\n    float split_offset = (perc * 0.4);\n    float split_point = 0.1 + split_offset;\n\n    float amplitude_normal = smoothstep(split_point, 0.7, st.x);\n    float amplitude_strength = 0.5;\n    float finalAmplitude = amplitude_normal * amplitude_strength\n                           * amplitude * (1.0 + (mouse.y - 0.5) * 0.2);\n\n    float time_scaled = time / 10.0 + (mouse.x - 0.5) * 1.0;\n    float blur = smoothstep(split_point, split_point + 0.05, st.x) * perc;\n\n    float xnoise = mix(\n        Perlin2D(vec2(time_scaled, st.x + perc) * 2.5),\n        Perlin2D(vec2(time_scaled, st.x + time_scaled) * 3.5) / 1.5,\n        st.x * 0.3\n    );\n\n    float y = 0.5 + (perc - 0.5) * distance + xnoise / 2.0 * finalAmplitude;\n\n    float line_start = smoothstep(\n        y + (width / 2.0) + (u_line_blur * pixel(1.0, iResolution.xy) * blur),\n        y,\n        st.y\n    );\n\n    float line_end = smoothstep(\n        y,\n        y - (width / 2.0) - (u_line_blur * pixel(1.0, iResolution.xy) * blur),\n        st.y\n    );\n\n    return clamp(\n        (line_start - line_end) * (1.0 - smoothstep(0.0, 1.0, pow(perc, 0.3))),\n        0.0,\n        1.0\n    );\n}\n\nvoid mainImage(out vec4 fragColor, in vec2 fragCoord) {\n    vec2 uv = fragCoord / iResolution.xy;\n\n    float line_strength = 1.0;\n    for (int i = 0; i < u_line_count; i++) {\n        float p = float(i) / float(u_line_count);\n        line_strength *= (1.0 - lineFn(\n            uv,\n            u_line_width * pixel(1.0, iResolution.xy) * (1.0 - p),\n            p,\n            (PI * 1.0) * p,\n            uMouse,\n            iTime,\n            uAmplitude,\n            uDistance\n        ));\n    }\n\n    float colorVal = 1.0 - line_strength;\n    fragColor = vec4(uColor * colorVal, colorVal);\n}\n\nvoid main() {\n    mainImage(gl_FragColor, gl_FragCoord.xy);\n}\n';
  function init(){ var els=document.querySelectorAll('.threads'); for(var i=0;i<els.length;i++){ if(els[i].__sbg) continue;
    g.ShaderBG(els[i], FRAG, { uniforms:{"uColor":{"t":"3f","v":[0.55,0.5,1]},"uAmplitude":{"t":"1f","v":0.3},"uDistance":{"t":"1f","v":0.5}} }); } }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* tilt-3d */
/* tilt-3d.js — motion-anything recipe · category: hover-press
 *
 * Maps the pointer position over a .tilt card to a small 3D rotation + glare position.
 * - Max angle via data-tilt-max (default 10°). Transform only. Flat under reduced-motion / touch.
 *
 * Usage:  <div class="tilt" data-tilt-max="10"> … </div>
 */
(function (global) {
  'use strict';

  function reduced() { return global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function isTouch() { return global.matchMedia && global.matchMedia('(hover: none)').matches; }

  function attach(card) {
    if (card.__tiltBound) return;
    card.__tiltBound = true;
    var max = parseFloat(card.getAttribute('data-tilt-max')) || 10;

    card.addEventListener('pointermove', function (e) {
      var r = card.getBoundingClientRect();
      var px = (e.clientX - r.left) / r.width;      // 0..1
      var py = (e.clientY - r.top) / r.height;      // 0..1
      card.style.setProperty('--ry', ((px - 0.5) * 2 * max).toFixed(2) + 'deg');
      card.style.setProperty('--rx', ((0.5 - py) * 2 * max).toFixed(2) + 'deg');
      card.style.setProperty('--gx', (px * 100).toFixed(1) + '%');
      card.style.setProperty('--gy', (py * 100).toFixed(1) + '%');
    });
    card.addEventListener('pointerleave', function () {
      card.style.setProperty('--rx', '0deg');
      card.style.setProperty('--ry', '0deg');
    });
  }

  function init() {
    if (reduced() || isTouch()) return;       // leave cards flat
    var els = document.querySelectorAll('.tilt');
    for (var i = 0; i < els.length; i++) attach(els[i]);
  }

  global.attachTilt = attach;
  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})(window);


/* toggle-spring */
/* toggle-spring.js — motion-anything recipe · category: feedback-delight
 *
 * Click any [data-toggle] to flip its .on state; the knob springs across (CSS handles the spring).
 * Reflects state via aria-pressed for accessibility.
 *
 * Usage:  <button class="ms-toggle" data-toggle aria-pressed="false"><span class="knob"></span></button>
 */
(function () {
  'use strict';
  function init() {
    document.querySelectorAll('[data-toggle]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var on = btn.classList.toggle('on');
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
    });
  }
  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})();


/* true-focus */
/* true-focus.js — text-kinetic · cycles focus across words. Reduced-motion → all sharp, no cycling. */
(function(g){ 'use strict';
  function red(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function attach(el){ if(el.__tf) return; el.__tf=1; var words=(el.textContent).trim().split(/\s+/); el.textContent='';
    var spans=words.map(function(w){ var s=document.createElement('span'); s.className='tf-w'; s.textContent=w; el.appendChild(s); return s; });
    if(red()){ spans.forEach(function(s){ s.classList.add('on'); }); return; }
    var i=0; spans[0].classList.add('on'); setInterval(function(){ spans[i].classList.remove('on'); i=(i+1)%spans.length; spans[i].classList.add('on'); }, 1100); }
  function init(){ var els=document.querySelectorAll('.tf'); for(var i=0;i<els.length;i++) attach(els[i]); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* waves */
/* waves.js — motion-anything recipe · ambient · faithful canvas 2D port (dependency-free).
 * A field of vertical lines warped by 2D perlin noise; the pointer drags nearby points with a
 * friction/tension spring wake. Direct port of the react-bits component (its perlin impl is
 * self-contained). Real defaults from source; line color via data-line-color (default black —
 * designed for light backgrounds). */
(function(g){ 'use strict';
  var DEF = { lineColor:'black', waveSpeedX:0.0125, waveSpeedY:0.005, waveAmpX:32, waveAmpY:16,
    xGap:10, yGap:32, friction:0.925, tension:0.005, maxCursorMove:100 };
  function reduced(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function Grad(x,y){ this.x=x; this.y=y; }
  Grad.prototype.dot2 = function(x,y){ return this.x*x + this.y*y; };
  function Noise(seed){
    this.grad3 = [new Grad(1,1),new Grad(-1,1),new Grad(1,-1),new Grad(-1,-1),
      new Grad(1,0),new Grad(-1,0),new Grad(1,0),new Grad(-1,0),
      new Grad(0,1),new Grad(0,-1),new Grad(0,1),new Grad(0,-1)];
    this.p = [151,160,137,91,90,15,131,13,201,95,96,53,194,233,7,225,140,36,103,30,69,142,8,99,37,240,
      21,10,23,190,6,148,247,120,234,75,0,26,197,62,94,252,219,203,117,35,11,32,57,177,33,88,
      237,149,56,87,174,20,125,136,171,168,68,175,74,165,71,134,139,48,27,166,77,146,158,231,83,
      111,229,122,60,211,133,230,220,105,92,41,55,46,245,40,244,102,143,54,65,25,63,161,1,216,
      80,73,209,76,132,187,208,89,18,169,200,196,135,130,116,188,159,86,164,100,109,198,173,186,
      3,64,52,217,226,250,124,123,5,202,38,147,118,126,255,82,85,212,207,206,59,227,47,16,58,
      17,182,189,28,42,223,183,170,213,119,248,152,2,44,154,163,70,221,153,101,155,167,43,172,9,
      129,22,39,253,19,98,108,110,79,113,224,232,178,185,112,104,218,246,97,228,251,34,242,193,
      238,210,144,12,191,179,162,241,81,51,145,235,249,14,239,107,49,192,214,31,181,199,106,157,
      184,84,204,176,115,121,50,45,127,4,150,254,138,236,205,93,222,114,67,29,24,72,243,141,128,
      195,78,66,215,61,156,180];
    this.perm = new Array(512); this.gradP = new Array(512); this.seed(seed||0);
  }
  Noise.prototype.seed = function(seed){
    if(seed > 0 && seed < 1) seed *= 65536;
    seed = Math.floor(seed); if(seed < 256) seed |= seed << 8;
    for(var i=0;i<256;i++){ var v = (i & 1) ? this.p[i] ^ (seed & 255) : this.p[i] ^ ((seed>>8) & 255);
      this.perm[i] = this.perm[i+256] = v; this.gradP[i] = this.gradP[i+256] = this.grad3[v % 12]; }
  };
  Noise.prototype.fade = function(t){ return t*t*t*(t*(t*6-15)+10); };
  Noise.prototype.lerp = function(a,b,t){ return (1-t)*a + t*b; };
  Noise.prototype.perlin2 = function(x,y){
    var X = Math.floor(x), Y = Math.floor(y); x -= X; y -= Y; X &= 255; Y &= 255;
    var n00 = this.gradP[X + this.perm[Y]].dot2(x, y);
    var n01 = this.gradP[X + this.perm[Y+1]].dot2(x, y-1);
    var n10 = this.gradP[X+1 + this.perm[Y]].dot2(x-1, y);
    var n11 = this.gradP[X+1 + this.perm[Y+1]].dot2(x-1, y-1);
    var u = this.fade(x);
    return this.lerp(this.lerp(n00, n10, u), this.lerp(n01, n11, u), this.fade(y));
  };
  function start(el){
    if(el.__ma) return; el.__ma = 1;
    var lineColor = el.getAttribute('data-line-color') || DEF.lineColor;
    var canvas = document.createElement('canvas');
    canvas.style.cssText = 'width:100%;height:100%;display:block';
    el.appendChild(canvas);
    var ctx = canvas.getContext('2d');
    var noise = new Noise(Math.random());
    var lines = [], W=0, H=0;
    var mouse = { x:-10, y:0, lx:0, ly:0, sx:0, sy:0, v:0, vs:0, a:0, set:false };
    function setSize(){ var r = el.getBoundingClientRect(); W = r.width; H = r.height;
      canvas.width = W; canvas.height = H; }
    function setLines(){
      lines = [];
      var oW = W + 200, oH = H + 30;
      var totalLines = Math.ceil(oW / DEF.xGap), totalPoints = Math.ceil(oH / DEF.yGap);
      var xStart = (W - DEF.xGap*totalLines)/2, yStart = (H - DEF.yGap*totalPoints)/2;
      for(var i=0;i<=totalLines;i++){ var pts = [];
        for(var j=0;j<=totalPoints;j++)
          pts.push({ x:xStart + DEF.xGap*i, y:yStart + DEF.yGap*j, wx:0, wy:0, cx:0, cy:0, cvx:0, cvy:0 });
        lines.push(pts);
      }
    }
    function movePoints(time){
      for(var i=0;i<lines.length;i++){ var pts = lines[i];
        for(var j=0;j<pts.length;j++){ var p = pts[j];
          var move = noise.perlin2((p.x + time*DEF.waveSpeedX)*0.002, (p.y + time*DEF.waveSpeedY)*0.0015)*12;
          p.wx = Math.cos(move)*DEF.waveAmpX; p.wy = Math.sin(move)*DEF.waveAmpY;
          var dx = p.x - mouse.sx, dy = p.y - mouse.sy;
          var dist = Math.hypot(dx, dy), l = Math.max(175, mouse.vs);
          if(dist < l){ var s = 1 - dist/l; var f = Math.cos(dist*0.001)*s;
            p.cvx += Math.cos(mouse.a)*f*l*mouse.vs*0.00065;
            p.cvy += Math.sin(mouse.a)*f*l*mouse.vs*0.00065; }
          p.cvx += (0 - p.cx)*DEF.tension; p.cvy += (0 - p.cy)*DEF.tension;
          p.cvx *= DEF.friction; p.cvy *= DEF.friction;
          p.cx += p.cvx*2; p.cy += p.cvy*2;
          p.cx = Math.min(DEF.maxCursorMove, Math.max(-DEF.maxCursorMove, p.cx));
          p.cy = Math.min(DEF.maxCursorMove, Math.max(-DEF.maxCursorMove, p.cy));
        }
      }
    }
    function moved(p, withCursor){
      var x = p.x + p.wx + (withCursor ? p.cx : 0), y = p.y + p.wy + (withCursor ? p.cy : 0);
      return { x: Math.round(x*10)/10, y: Math.round(y*10)/10 };
    }
    function drawLines(){
      ctx.clearRect(0, 0, W, H);
      ctx.beginPath(); ctx.strokeStyle = lineColor;
      for(var i=0;i<lines.length;i++){ var pts = lines[i];
        var p1 = moved(pts[0], false); ctx.moveTo(p1.x, p1.y);
        for(var j=0;j<pts.length;j++){ var isLast = j === pts.length-1;
          p1 = moved(pts[j], !isLast);
          var p2 = moved(pts[j+1] || pts[pts.length-1], !isLast);
          ctx.lineTo(p1.x, p1.y);
          if(isLast) ctx.moveTo(p2.x, p2.y);
        }
      }
      ctx.stroke();
    }
    function updateMouse(x, y){ var r = el.getBoundingClientRect();
      mouse.x = x - r.left; mouse.y = y - r.top;
      if(!mouse.set){ mouse.sx = mouse.x; mouse.sy = mouse.y; mouse.lx = mouse.x; mouse.ly = mouse.y; mouse.set = true; } }
    el.addEventListener('mousemove', function(e){ updateMouse(e.clientX, e.clientY); }, { passive:true });
    el.addEventListener('touchmove', function(e){ var t = e.touches[0]; updateMouse(t.clientX, t.clientY); }, { passive:true });
    g.addEventListener('resize', function(){ setSize(); setLines(); });
    setSize(); setLines();
    var red = reduced();
    function tick(t){
      mouse.sx += (mouse.x - mouse.sx)*0.1; mouse.sy += (mouse.y - mouse.sy)*0.1;
      var d = Math.hypot(mouse.x - mouse.lx, mouse.y - mouse.ly);
      mouse.vs += (d - mouse.vs)*0.1; mouse.vs = Math.min(100, mouse.vs);
      mouse.a = Math.atan2(mouse.y - mouse.ly, mouse.x - mouse.lx);
      mouse.lx = mouse.x; mouse.ly = mouse.y;
      movePoints(t); drawLines();
      if(!red) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }
  function init(){ var els=document.querySelectorAll('.waves'); for(var i=0;i<els.length;i++) start(els[i]); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);
