// Steam Market Intelligence - visual effects only (the dashboard works without this file).
//   1. WebGL shader background: a wireframe landscape you fly over; scrolling moves you forward
//   2. 3D bar "tower" in the hero (CSS 3D transforms), driven by real data from app.js
//   3. Scroll-reveal for cards, 3D tilt on metric cards, scroll progress bar

(() => {
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const root = document.documentElement;

  // Each tab tints the world: [main colour, peak colour] as RGB 0-1, plus the CSS accent.
  const THEMES = {
    market:    { a: [0.30, 0.60, 1.00], b: [0.94, 0.38, 0.55], css: "#4c9aff" },
    benchmark: { a: [1.00, 0.48, 0.27], b: [0.96, 0.72, 0.24], css: "#ff7a45" },
    chat:      { a: [0.18, 0.75, 0.44], b: [0.30, 0.60, 1.00], css: "#2fbf71" },
    data:      { a: [0.96, 0.72, 0.24], b: [1.00, 0.48, 0.27], css: "#f5b83d" },
  };
  let theme = THEMES.market;
  const colA = [...theme.a];
  const colB = [...theme.b];

  document.addEventListener("tabchange", (event) => {
    theme = THEMES[event.detail] || THEMES.market;
    root.style.setProperty("--accent", theme.css);
  });

  // Mouse and scroll are smoothed so the motion feels weighty rather than twitchy.
  const mouse = { x: 0, y: 0, sx: 0, sy: 0 };
  addEventListener("pointermove", (e) => {
    mouse.x = (e.clientX / innerWidth) * 2 - 1;
    mouse.y = (e.clientY / innerHeight) * 2 - 1;
  }, { passive: true });
  let scrollSmooth = scrollY;

  // ------------------------------------------------------------------
  // 1. Shader background
  // ------------------------------------------------------------------
  const VERTEX = `#version 300 es
    in vec2 pos;
    void main() { gl_Position = vec4(pos, 0.0, 1.0); }`;

  const FRAGMENT = `#version 300 es
    precision highp float;
    uniform vec2 uRes;
    uniform float uTime, uScroll;
    uniform vec2 uMouse;
    uniform vec3 uA, uB;
    out vec4 outColor;

    float hash(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
    float noise(vec2 p) {
      vec2 i = floor(p), f = fract(p), u = f * f * (3.0 - 2.0 * f);
      return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
    }
    // Rolling hills with a flat "runway" down the middle of the view.
    float terrain(vec2 p) {
      float h = noise(p * 0.18) * 1.6 + noise(p * 0.42) * 0.6 + noise(p * 0.9) * 0.2;
      return h * h * smoothstep(1.2, 6.0, abs(p.x)) * 0.8;
    }

    void main() {
      vec2 uv = (gl_FragCoord.xy - 0.5 * uRes) / uRes.y;
      vec3 ro = vec3(uMouse.x * 0.8, 1.15, uTime * 0.9 + uScroll * 0.006);
      vec3 rd = normalize(vec3(uv.x, uv.y - 0.12 + uMouse.y * 0.05, 1.2));

      // Ray-march the height field.
      float d = 0.0;
      bool hit = false;
      vec3 p = ro;
      for (int i = 0; i < 100; i++) {
        p = ro + rd * d;
        float h = p.y - terrain(p.xz);
        if (h < 0.002 * d) { hit = true; break; }
        d += max(h * 0.45, 0.02);
        if (d > 45.0) break;
      }

      // Sky: dark, with a glow on the horizon and a few twinkling stars.
      vec3 sky = mix(vec3(0.045, 0.06, 0.1), vec3(0.018, 0.024, 0.04), clamp(rd.y * 2.5, 0.0, 1.0));
      sky += uA * 0.32 * exp(-abs(rd.y) * 14.0);
      vec2 sg = uv * 90.0;
      float seed = hash(floor(sg));
      float star = step(0.985, seed) * smoothstep(0.35, 0.0, length(fract(sg) - 0.5));
      sky += star * (0.4 + 0.4 * sin(uTime * 2.0 + seed * 40.0)) * step(0.0, rd.y);

      // Grid lines on the ground (derivatives keep them crisp near and soft far away).
      vec2 fw = fwidth(p.xz);
      vec2 edge = 0.5 - abs(fract(p.xz) - 0.5);
      vec2 l = 1.0 - smoothstep(vec2(0.0), fw * 1.5, edge);
      float line = max(l.x, l.y) * smoothstep(1.0, 0.2, max(fw.x, fw.y));

      vec3 col = sky;
      if (hit) {
        vec3 lineCol = mix(uA, uB, smoothstep(0.2, 1.6, terrain(p.xz)));
        vec3 ground = vec3(0.022, 0.03, 0.048) + lineCol * 0.035 + lineCol * line * 0.85;
        col = mix(sky, ground, exp(-d * 0.07));
      }
      col *= 1.0 - 0.35 * dot(uv, uv);  // vignette
      outColor = vec4(col, 1.0);
    }`;

  const canvas = document.getElementById("bg");
  const gl = canvas && canvas.getContext("webgl2", { antialias: false, alpha: false, powerPreference: "low-power" });
  let drawBackground = () => {};

  function compile(type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
    return shader;
  }

  try {
    if (!gl) throw new Error("WebGL2 not available");
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, VERTEX));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FRAGMENT));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    gl.useProgram(program);

    // One triangle that covers the whole screen.
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const posLoc = gl.getAttribLocation(program, "pos");
    gl.enableVertexAttribArray(posLoc);
    gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0);

    const u = {};
    ["uRes", "uTime", "uScroll", "uMouse", "uA", "uB"].forEach((name) => (u[name] = gl.getUniformLocation(program, name)));

    // Render below screen resolution and let CSS scale it up: the background is soft anyway,
    // and it keeps the frame rate smooth on laptops. Drop quality further if frames run slow.
    let quality = 0.6;
    function resize() {
      canvas.width = Math.round(innerWidth * quality);
      canvas.height = Math.round(innerHeight * quality);
      gl.viewport(0, 0, canvas.width, canvas.height);
    }
    addEventListener("resize", resize);
    resize();

    let slowFrames = 0;
    drawBackground = (time, dt) => {
      if (dt > 28 && quality > 0.35 && ++slowFrames > 40) { quality -= 0.1; slowFrames = 0; resize(); }
      gl.uniform2f(u.uRes, canvas.width, canvas.height);
      gl.uniform1f(u.uTime, time / 1000);
      gl.uniform1f(u.uScroll, scrollSmooth);
      gl.uniform2f(u.uMouse, mouse.sx, -mouse.sy);
      gl.uniform3fv(u.uA, colA);
      gl.uniform3fv(u.uB, colB);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    };
    canvas.classList.add("on");
  } catch (err) {
    console.info("Background effect disabled:", err.message);
    if (canvas) canvas.remove();
  }

  // ------------------------------------------------------------------
  // 2. 3D tower chart in the hero
  // ------------------------------------------------------------------
  const scene = document.getElementById("scene");
  const legend = document.getElementById("tower-legend");
  const BAR = 34, GAP = 14, MAX_H = 190, SCENE_W = 300;

  function tower(items) {
    if (!scene) return;
    const max = Math.max(50, ...items.map((item) => item.value));
    const total = items.length * BAR + (items.length - 1) * GAP;
    items.forEach((item, i) => {
      let bar = scene.querySelector(`[data-i="${i}"]`);
      if (!bar) {
        bar = document.createElement("div");
        bar.className = "bar3d";
        bar.dataset.i = i;
        bar.innerHTML = '<i class="face front"><b></b></i><i class="face back"></i><i class="face left"></i><i class="face right"></i><i class="face top"></i>';
        scene.appendChild(bar);
      }
      const height = Math.max(4, (item.value / max) * MAX_H);
      bar.style.left = SCENE_W / 2 - total / 2 + i * (BAR + GAP) + "px";
      bar.style.setProperty("--c", item.color);
      bar.style.height = height + "px";
      bar.classList.toggle("off", !item.value);
      bar.querySelector("b").textContent = height > 26 ? Math.round(item.value) + "%" : "";
      bar.title = `${item.label}: ${item.value.toFixed(1)}% of games got a recommendation`;
    });
    if (legend) {
      legend.innerHTML = items.map((item) =>
        `<span style="--c: ${item.color}"><i></i>${item.label}</span>`).join("");
    }
  }
  window.FX = { tower };

  // ------------------------------------------------------------------
  // 3. Scroll reveal, card tilt, progress bar
  // ------------------------------------------------------------------
  const progress = document.getElementById("progress");

  if (!reduceMotion && "IntersectionObserver" in window) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("in");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12 });
    document.querySelectorAll("main .card, .stat").forEach((el, i) => {
      el.classList.add("reveal");
      el.style.setProperty("--delay", (i % 4) * 70 + "ms");
      observer.observe(el);
    });
  }

  if (!reduceMotion && matchMedia("(hover: hover)").matches) {
    document.querySelectorAll(".metric, .stat, .profile").forEach((el) => {
      el.classList.add("tilt");
      el.addEventListener("pointermove", (e) => {
        const box = el.getBoundingClientRect();
        const x = (e.clientX - box.left) / box.width;
        const y = (e.clientY - box.top) / box.height;
        el.style.setProperty("--rx", ((0.5 - y) * 10).toFixed(2) + "deg");
        el.style.setProperty("--ry", ((x - 0.5) * 12).toFixed(2) + "deg");
        el.style.setProperty("--mx", (x * 100).toFixed(1) + "%");
        el.style.setProperty("--my", (y * 100).toFixed(1) + "%");
      });
      el.addEventListener("pointerleave", () => {
        el.style.removeProperty("--rx");
        el.style.removeProperty("--ry");
      });
    });
  }

  // ------------------------------------------------------------------
  // Animation loop
  // ------------------------------------------------------------------
  let last = performance.now();
  function frame(now) {
    const dt = now - last;
    last = now;

    scrollSmooth += (scrollY - scrollSmooth) * 0.08;
    mouse.sx += (mouse.x - mouse.sx) * 0.05;
    mouse.sy += (mouse.y - mouse.sy) * 0.05;
    for (let i = 0; i < 3; i++) {
      colA[i] += (theme.a[i] - colA[i]) * 0.04;
      colB[i] += (theme.b[i] - colB[i]) * 0.04;
    }

    const maxScroll = Math.max(1, root.scrollHeight - innerHeight);
    if (progress) progress.style.transform = `scaleX(${Math.min(1, scrollY / maxScroll)})`;

    if (scene) {
      const spin = -28 + Math.sin(now / 2500) * 10 + mouse.sx * 14 - Math.min(scrollSmooth, 900) * 0.04;
      const tilt = -18 + mouse.sy * 6 - Math.min(scrollSmooth, 900) * 0.012;
      scene.style.transform = `rotateX(${tilt.toFixed(2)}deg) rotateY(${spin.toFixed(2)}deg)`;
    }

    if (!document.hidden) drawBackground(now, dt);
    requestAnimationFrame(frame);
  }

  if (reduceMotion) {
    drawBackground(0, 16);  // one still frame, no movement
  } else {
    requestAnimationFrame(frame);
  }
})();
