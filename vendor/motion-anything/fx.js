/* ── _fx/shaderbg ── */
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


/* ── count-up ── */
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


/* ── decrypted-text ── */
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


/* ── blur-text ── */
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


/* ── bounce-cards ── */
/* bounce-cards.js — entrance · trigger the fan-out on view; stagger via --bc-d. Reduced-motion → shown. */
(function(g){ 'use strict';
  function red(){ return g.matchMedia && g.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  function init(){ var els=document.querySelectorAll('.bcards'); els.forEach(function(el){ [].slice.call(el.querySelectorAll('.bc')).forEach(function(c,i){ c.style.setProperty('--bc-d',(i*90)+'ms'); });
    if(red()){ el.classList.add('in'); return; } if(!('IntersectionObserver' in g)){ el.classList.add('in'); return; }
    var io=new IntersectionObserver(function(es){ es.forEach(function(e){ if(e.isIntersecting){ e.target.classList.add('in'); io.unobserve(e.target); } }); }, {threshold:.3}); io.observe(el); }); }
  if(document.readyState!=='loading') init(); else document.addEventListener('DOMContentLoaded', init);
})(window);


/* ── aurora ── */
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


/* ── dot-grid ── */
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
