// Mesh / wireframe SVG primitives.
// Exports: MeshSphere, MeshGlobe, EthernetCableLogo, MeshWave, MeshScatter

// ---- Sphere generator -----------------------------------------------------
// Deterministic spherical mesh: latitude/longitude grid + diagonals so it
// looks like the triangulated geodesic spheres in the reference.
function buildSphere(latSeg = 14, lonSeg = 18, r = 1) {
  const pts = [];
  // generate grid of (lat,lon) points
  for (let i = 0; i <= latSeg; i++) {
    const theta = i / latSeg * Math.PI; // 0..PI
    const sinT = Math.sin(theta),cosT = Math.cos(theta);
    for (let j = 0; j < lonSeg; j++) {
      const phi = j / lonSeg * Math.PI * 2;
      pts.push({
        x: r * sinT * Math.cos(phi),
        y: r * cosT,
        z: r * sinT * Math.sin(phi),
        i, j
      });
    }
  }
  const idx = (i, j) => i * lonSeg + (j + lonSeg) % lonSeg;
  const lines = [];
  for (let i = 0; i <= latSeg; i++) {
    for (let j = 0; j < lonSeg; j++) {
      const a = idx(i, j);
      // horizontal
      const b = idx(i, j + 1);
      lines.push([a, b]);
      // vertical
      if (i < latSeg) {
        const c = idx(i + 1, j);
        lines.push([a, c]);
        // diagonal (gives triangulated look)
        const d = idx(i + 1, j + 1);
        lines.push([a, d]);
      }
    }
  }
  return { pts, lines };
}

// rotate point around Y then X
function rot(p, ry, rx) {
  let { x, y, z } = p;
  // Y
  const cY = Math.cos(ry),sY = Math.sin(ry);
  let x2 = x * cY + z * sY;
  let z2 = -x * sY + z * cY;
  x = x2;z = z2;
  // X
  const cX = Math.cos(rx),sX = Math.sin(rx);
  let y2 = y * cX - z * sX;
  let z3 = y * sX + z * cX;
  return { x, y: y2, z: z3 };
}

function MeshSphere({
  size = 240,
  rotY = 0.4,
  rotX = -0.15,
  stroke = "currentColor",
  strokeWidth = 0.6,
  opacity = 1,
  latSeg = 14,
  lonSeg = 18,
  dotted = false,
  dots = true,
  autoRotate = false,
  speed = 0.1,
  className = "",
  style = {}
}) {
  const [tick, setTick] = React.useState(0);
  React.useEffect(() => {
    if (!autoRotate) return;
    let raf;
    let start = performance.now();
    const tickFn = (now) => {
      setTick((now - start) / 1000);
      raf = requestAnimationFrame(tickFn);
    };
    raf = requestAnimationFrame(tickFn);
    return () => cancelAnimationFrame(raf);
  }, [autoRotate]);

  const liveRotY = rotY + (autoRotate ? tick * speed : 0);
  const liveRotX = rotX + (autoRotate ? Math.sin(tick * speed * 0.5) * 0.05 : 0);

  const r = size * 0.46;
  const cx = size / 2,cy = size / 2;
  const { pts, lines } = React.useMemo(
    () => buildSphere(latSeg, lonSeg, 1),
    [latSeg, lonSeg]
  );

  // project
  const proj = pts.map((p) => {
    const rp = rot(p, liveRotY, liveRotX);
    // simple orthographic
    return { x: cx + rp.x * r, y: cy - rp.y * r, z: rp.z };
  });

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      className={className}
      style={style}
      aria-hidden="true">
      
      <g
        stroke={stroke}
        strokeWidth={strokeWidth}
        fill="none"
        opacity={opacity}
        strokeLinecap="round"
        strokeDasharray={dotted ? "1 2" : undefined}>
        
        {lines.map(([a, b], i) => {
          const pa = proj[a],pb = proj[b];
          // only draw front-facing edges for clarity
          const visible = (pa.z + pb.z) / 2 > -0.05;
          if (!visible) return null;
          // fade by depth
          const o = Math.max(0.18, ((pa.z + pb.z) / 2 + 1) / 2);
          return (
            <line
              key={i}
              x1={pa.x}
              y1={pa.y}
              x2={pb.x}
              y2={pb.y}
              opacity={o} />);


        })}
        {dots &&
        proj.map((p, i) => {
          if (p.z < -0.05) return null;
          const o = Math.max(0.2, (p.z + 1) / 2);
          // sparse dots: only at every other longitude
          const pt = pts[i];
          if (pt.j % 2 !== 0 || pt.i % 2 !== 0) return null;
          return (
            <circle
              key={`d${i}`}
              cx={p.x}
              cy={p.y}
              r={0.8}
              fill={stroke}
              stroke="none"
              opacity={o} />);


        })}
      </g>
    </svg>);

}

// alias
const MeshGlobe = MeshSphere;

// ---- Ethernet RJ45 cable logo — renders the PNG asset --------------------
function EthernetCableLogo({
  size = 96,
  className = "",
  style = {}
}) {
  return (
    <span
      className={"ethernet-logo " + className}
      style={{
        display: "inline-block",
        width: size + "px",
        height: size * (1035 / 417) + "px",
        ...style
      }}
      aria-hidden="true" />);


}

// ---- Mesh wave (decorative bottom band) ----------------------------------
function MeshWave({
  width = 1400,
  height = 220,
  stroke = "currentColor",
  strokeWidth = 0.5,
  opacity = 0.5,
  className = "",
  style = {},
  rows = 8,
  cols = 40,
  seed = 1
}) {
  // pseudo-random with seed
  const rand = (n) => {
    const x = Math.sin(n * 9301 + seed * 7919) * 43758.5453;
    return x - Math.floor(x);
  };
  const pts = [];
  for (let i = 0; i <= rows; i++) {
    for (let j = 0; j <= cols; j++) {
      const baseY = i / rows * height;
      const x = j / cols * width;
      // wave displacement
      const wave =
      Math.sin(j / cols * Math.PI * 2.3 + i * 0.3) * (height * 0.22) +
      Math.sin(j / cols * Math.PI * 5 + i * 0.7) * (height * 0.06);
      const y = baseY + wave + (rand(i * 100 + j) - 0.5) * 6;
      pts.push({ x, y });
    }
  }
  const idx = (i, j) => i * (cols + 1) + j;
  const lines = [];
  for (let i = 0; i <= rows; i++) {
    for (let j = 0; j <= cols; j++) {
      if (j < cols) lines.push([idx(i, j), idx(i, j + 1)]);
      if (i < rows) lines.push([idx(i, j), idx(i + 1, j)]);
      if (i < rows && j < cols) lines.push([idx(i, j), idx(i + 1, j + 1)]);
    }
  }
  return (
    <svg
      width="100%"
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={className}
      style={style}
      aria-hidden="true">
      
      <g
        stroke={stroke}
        strokeWidth={strokeWidth}
        fill="none"
        opacity={opacity}
        strokeLinecap="round">
        
        {lines.map(([a, b], i) =>
        <line
          key={i}
          x1={pts[a].x}
          y1={pts[a].y}
          x2={pts[b].x}
          y2={pts[b].y} />

        )}
        {pts.map((p, i) =>
        i % 5 === 0 ?
        <circle key={`p${i}`} cx={p.x} cy={p.y} r={0.9} fill={stroke} stroke="none" /> :
        null
        )}
      </g>
    </svg>);

}

// ---- Interactive mesh wave (canvas, cursor-reactive) ----------------------
// Horizontal flowing mesh — rows of triangulated dots undulating continuously,
// reacting to mouse position. Click drops an expanding ripple pulse.
function InteractiveMeshWave({
  height = 240,
  className = "",
  style = {},
  influence = 220,
  amplitude = 38,
  rows = 8
}) {
  const canvasRef = React.useRef(null);
  const stateRef = React.useRef({
    mx: -9999, my: -9999,
    tx: -9999, ty: -9999,
    t: 0,
    hover: false,
    pulses: []
  });

  React.useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    let raf;

    let dpr = window.devicePixelRatio || 1;
    const resize = () => {
      dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      canvas.width = Math.round(rect.width * dpr);
      canvas.height = Math.round(rect.height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    const onMove = (e) => {
      const rect = canvas.getBoundingClientRect();
      stateRef.current.mx = e.clientX - rect.left;
      stateRef.current.my = e.clientY - rect.top;
      stateRef.current.hover = true;
    };
    const onLeave = () => {stateRef.current.hover = false;};
    const onClick = (e) => {
      const rect = canvas.getBoundingClientRect();
      stateRef.current.pulses.push({
        x: e.clientX - rect.left,
        y: e.clientY - rect.top,
        t0: stateRef.current.t
      });
    };
    canvas.addEventListener("mousemove", onMove);
    canvas.addEventListener("mouseleave", onLeave);
    canvas.addEventListener("click", onClick);

    const readColors = () => {
      const cs = getComputedStyle(document.documentElement);
      return {
        ink: cs.getPropertyValue("--ink").trim() || "#0a0a0a",
        accent: cs.getPropertyValue("--accent").trim() || "#1e6cff"
      };
    };

    const draw = () => {
      const w = canvas.clientWidth,h = canvas.clientHeight;
      if (!w || !h) {raf = requestAnimationFrame(draw);return;}
      const st = stateRef.current;
      st.t += 0.014;

      if (!st.hover) {
        st.mx += (-9999 - st.mx) * 0.06;
        st.my += (-9999 - st.my) * 0.06;
      }
      st.tx += (st.mx - st.tx) * 0.22;
      st.ty += (st.my - st.ty) * 0.22;

      const colors = readColors();

      // Horizontal flowing wave field — denser mesh
      const cols = Math.max(28, Math.floor(w / 22));
      const dx = w / cols;
      const bandH = h * 0.95;
      const baseTop = h - bandH;
      const rowGap = bandH / rows;

      const pts = new Array((rows + 1) * (cols + 1));
      for (let i = 0; i <= rows; i++) {
        for (let j = 0; j <= cols; j++) {
          const baseX = j * dx;
          const phase = i * 0.7;
          const baseY = baseTop + i * rowGap;
          const wave =
          Math.sin(j * 0.18 + st.t * 1.3 + phase) * 22 * (1 - i / rows * 0.4) +
          Math.sin(j * 0.06 + st.t * 0.6 + phase * 0.5) * 14 +
          Math.cos(j * 0.31 + st.t * 0.9 + phase) * 6;
          let x = baseX;
          let y = baseY + wave;

          // cursor pull/push
          const ddx = baseX - st.tx;
          const ddy = baseY + wave - st.ty;
          const dist = Math.sqrt(ddx * ddx + ddy * ddy);
          let cursorForce = 0;
          if (dist < influence) {
            cursorForce = 1 - dist / influence;
            cursorForce = cursorForce * cursorForce;
            const ang = Math.atan2(ddy, ddx);
            x += Math.cos(ang) * cursorForce * amplitude;
            y += Math.sin(ang) * cursorForce * amplitude;
          }

          // click pulse ripples
          let pulseForce = 0;
          for (const p of st.pulses) {
            const age = st.t - p.t0;
            if (age > 2.4) continue;
            const ddx2 = baseX - p.x;
            const ddy2 = baseY - p.y;
            const d = Math.sqrt(ddx2 * ddx2 + ddy2 * ddy2);
            const ring = age * 260;
            const band = Math.exp(-((d - ring) ** 2) / (2 * 38 * 38));
            const fade = Math.max(0, 1 - age / 2.4);
            const strength = band * fade * 32;
            if (d > 0) {
              x += ddx2 / d * strength;
              y += ddy2 / d * strength;
            }
            pulseForce += band * fade;
          }
          pts[i * (cols + 1) + j] = { x, y, force: Math.max(cursorForce, pulseForce) };
        }
      }
      st.pulses = st.pulses.filter((p) => st.t - p.t0 < 2.4);

      ctx.clearRect(0, 0, w, h);

      // ink lines
      ctx.lineWidth = 0.7;
      ctx.strokeStyle = colors.ink;
      ctx.globalAlpha = 0.55;
      ctx.beginPath();
      for (let i = 0; i <= rows; i++) {
        for (let j = 0; j <= cols; j++) {
          const p = pts[i * (cols + 1) + j];
          if (j < cols) {
            const n = pts[i * (cols + 1) + j + 1];
            ctx.moveTo(p.x, p.y);ctx.lineTo(n.x, n.y);
          }
          if (i < rows) {
            const n = pts[(i + 1) * (cols + 1) + j];
            ctx.moveTo(p.x, p.y);ctx.lineTo(n.x, n.y);
          }
          if (i < rows && j < cols) {
            const n = pts[(i + 1) * (cols + 1) + j + 1];
            ctx.moveTo(p.x, p.y);ctx.lineTo(n.x, n.y);
          }
        }
      }
      ctx.stroke();

      // accent overlay near cursor / pulses
      ctx.strokeStyle = colors.accent;
      ctx.lineWidth = 1;
      for (let i = 0; i <= rows; i++) {
        for (let j = 0; j <= cols; j++) {
          const p = pts[i * (cols + 1) + j];
          if (p.force < 0.18) continue;
          if (j < cols) {
            const n = pts[i * (cols + 1) + j + 1];
            ctx.globalAlpha = Math.min(1, (p.force + n.force) * 0.7);
            ctx.beginPath();
            ctx.moveTo(p.x, p.y);ctx.lineTo(n.x, n.y);ctx.stroke();
          }
          if (i < rows) {
            const n = pts[(i + 1) * (cols + 1) + j];
            ctx.globalAlpha = Math.min(1, (p.force + n.force) * 0.7);
            ctx.beginPath();
            ctx.moveTo(p.x, p.y);ctx.lineTo(n.x, n.y);ctx.stroke();
          }
          if (i < rows && j < cols) {
            const n = pts[(i + 1) * (cols + 1) + j + 1];
            ctx.globalAlpha = Math.min(1, (p.force + n.force) * 0.6);
            ctx.beginPath();
            ctx.moveTo(p.x, p.y);ctx.lineTo(n.x, n.y);ctx.stroke();
          }
        }
      }

      // dots
      for (let i = 0; i <= rows; i++) {
        for (let j = 0; j <= cols; j++) {
          const p = pts[i * (cols + 1) + j];
          const f = p.force;
          if (f > 0.15) {
            ctx.fillStyle = colors.accent;
            ctx.globalAlpha = Math.min(0.25, f * 0.5);
            ctx.beginPath();
            ctx.arc(p.x, p.y, 6 + f * 6, 0, Math.PI * 2);
            ctx.fill();
            ctx.globalAlpha = Math.min(1, f * 1.4);
            ctx.beginPath();
            ctx.arc(p.x, p.y, 1.3 + f * 2.6, 0, Math.PI * 2);
            ctx.fill();
          } else {
            ctx.fillStyle = colors.ink;
            ctx.globalAlpha = 0.65;
            ctx.beginPath();
            ctx.arc(p.x, p.y, 1.1, 0, Math.PI * 2);
            ctx.fill();
          }
        }
      }

      raf = requestAnimationFrame(draw);
    };
    draw();

    return () => {
      cancelAnimationFrame(raf);
      canvas.removeEventListener("mousemove", onMove);
      canvas.removeEventListener("mouseleave", onLeave);
      canvas.removeEventListener("click", onClick);
      ro.disconnect();
    };
  }, [influence, amplitude, rows]);

  return (
    <canvas
      ref={canvasRef}
      className={"mesh-wave-canvas " + className}
      style={style} />);


}

// ---- Mesh corner decoration (static SVG, for card corners) ----------------
function MeshCorner({
  size = 120,
  stroke = "currentColor",
  strokeWidth = 0.5,
  className = "",
  style = {},
  rotate = 0
}) {
  // a small triangulated quadrant
  const pts = [];
  const N = 5;
  for (let i = 0; i <= N; i++) {
    for (let j = 0; j <= N; j++) {
      const r = i / N;
      const a = j / N * Math.PI * 0.5;
      pts.push({
        x: size - r * size * Math.cos(a),
        y: size - r * size * Math.sin(a),
        ring: i,
        spoke: j
      });
    }
  }
  const idx = (i, j) => i * (N + 1) + j;
  const lines = [];
  for (let i = 0; i <= N; i++) {
    for (let j = 0; j <= N; j++) {
      if (j < N) lines.push([idx(i, j), idx(i, j + 1)]);
      if (i < N) lines.push([idx(i, j), idx(i + 1, j)]);
      if (i < N && j < N) lines.push([idx(i, j), idx(i + 1, j + 1)]);
    }
  }
  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      className={className}
      style={{ transform: `rotate(${rotate}deg)`, ...style }}
      aria-hidden="true">
      
      <g stroke={stroke} strokeWidth={strokeWidth} fill="none" opacity="0.7">
        {lines.map(([a, b], i) =>
        <line
          key={i}
          x1={pts[a].x} y1={pts[a].y}
          x2={pts[b].x} y2={pts[b].y}
          opacity={0.5 + 0.5 * (pts[a].ring / N)} />

        )}
        {pts.map((p, i) =>
        (p.ring + p.spoke) % 2 === 0 ?
        <circle key={`p${i}`} cx={p.x} cy={p.y} r={0.9} fill={stroke} stroke="none" /> :
        null
        )}
      </g>
    </svg>);

}

// ---- Mesh ribbon (thin animated mesh stripe) -----------------------------
function MeshRibbon({
  width = 800,
  height = 80,
  stroke = "currentColor",
  strokeWidth = 0.5,
  opacity = 0.6,
  className = "",
  style = {},
  seed = 3,
  speed = 1
}) {
  const [t, setT] = React.useState(0);
  React.useEffect(() => {
    let raf;
    let start = performance.now();
    const tick = (now) => {
      setT((now - start) / 1000 * speed);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [speed]);

  const cols = 32,rows = 3;
  const pts = [];
  for (let i = 0; i <= rows; i++) {
    for (let j = 0; j <= cols; j++) {
      const x = j / cols * width;
      const y = i / rows * height + Math.sin(j * 0.5 + i * 0.5 + t) * 8;
      pts.push({ x, y });
    }
  }
  const idx = (i, j) => i * (cols + 1) + j;
  const lines = [];
  for (let i = 0; i <= rows; i++) {
    for (let j = 0; j <= cols; j++) {
      if (j < cols) lines.push([idx(i, j), idx(i, j + 1)]);
      if (i < rows) lines.push([idx(i, j), idx(i + 1, j)]);
      if (i < rows && j < cols) lines.push([idx(i, j), idx(i + 1, j + 1)]);
    }
  }
  return (
    <svg
      width="100%"
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={className}
      style={style}
      aria-hidden="true">
      
      <g stroke={stroke} strokeWidth={strokeWidth} fill="none" opacity={opacity}>
        {lines.map(([a, b], i) =>
        <line key={i} x1={pts[a].x} y1={pts[a].y} x2={pts[b].x} y2={pts[b].y} />
        )}
        {pts.map((p, i) =>
        i % 3 === 0 ?
        <circle key={`p${i}`} cx={p.x} cy={p.y} r={0.8} fill={stroke} stroke="none" /> :
        null
        )}
      </g>
    </svg>);

}

// ---- Mesh scatter (background sparkle of points + connecting lines) -------
function MeshScatter({
  width = 600,
  height = 600,
  count = 60,
  stroke = "currentColor",
  strokeWidth = 0.5,
  opacity = 0.35,
  className = "",
  style = {},
  seed = 1
}) {
  const rand = (n) => {
    const x = Math.sin(n * 9301 + seed * 7919) * 43758.5453;
    return x - Math.floor(x);
  };
  const pts = [];
  for (let i = 0; i < count; i++) {
    pts.push({ x: rand(i * 2) * width, y: rand(i * 2 + 1) * height });
  }
  const lines = [];
  for (let i = 0; i < pts.length; i++) {
    for (let j = i + 1; j < pts.length; j++) {
      const d = Math.hypot(pts[i].x - pts[j].x, pts[i].y - pts[j].y);
      if (d < width * 0.18) lines.push([i, j, d]);
    }
  }
  return (
    <svg
      width="100%"
      height="100%"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="xMidYMid slice"
      className={className}
      style={style}
      aria-hidden="true">
      
      <g stroke={stroke} strokeWidth={strokeWidth} fill="none" opacity={opacity}>
        {lines.map(([a, b, d], i) =>
        <line
          key={i}
          x1={pts[a].x}
          y1={pts[a].y}
          x2={pts[b].x}
          y2={pts[b].y}
          opacity={1 - d / (width * 0.18)} />

        )}
        {pts.map((p, i) =>
        <circle key={i} cx={p.x} cy={p.y} r={1.2} fill={stroke} stroke="none" />
        )}
      </g>
    </svg>);

}

// ---- Cable meteors: flying cables that hook to a target ----------------
// Renders a fixed-size container with animated cable sprites that fly in from
// random off-screen positions, hook into the sphere surface, dwell, then fall
// off and fade out.
function CableMeteors({
  size = 200,
  count = 2,
  interval = 4000,
  onHook,
  className = "",
  style = {}
}) {
  const [meteors, setMeteors] = React.useState(() =>
  Array.from({ length: count }).map((_, i) => spawnMeteor(i, size))
  );
  const onHookRef = React.useRef(onHook);
  onHookRef.current = onHook;

  // schedule a hook event for newly-spawned meteors
  const scheduleHook = (m) => {
    const hookAt = (m.delay + m.dur * 0.38) * 1000;
    setTimeout(() => {
      if (onHookRef.current) onHookRef.current();
    }, hookAt);
  };

  React.useEffect(() => {
    // schedule hooks for the initial set too
    meteors.forEach(scheduleHook);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  React.useEffect(() => {
    let id = 0;
    const t = setInterval(() => {
      id++;
      const newMeteor = spawnMeteor(Date.now() + id, size);
      scheduleHook(newMeteor);
      setMeteors((m) => {
        const next = m.slice(1);
        next.push(newMeteor);
        return next;
      });
    }, interval);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interval, size]);

  return (
    <div
      className={"cable-meteors " + className}
      style={{ width: size, height: size, ...style }}
      aria-hidden="true">
      
      {meteors.map((m) =>
      <span
        key={m.id}
        className="meteor"
        style={{
          "--from-x": m.fromX + "px",
          "--from-y": m.fromY + "px",
          "--hook-x": m.hookX + "px",
          "--hook-y": m.hookY + "px",
          "--rot": m.rot + "deg",
          "--swing-a": m.swingA + "deg",
          "--swing-b": m.swingB + "deg",
          "--fall-rot": m.fallRot + "deg",
          "--fall-drop": m.fallDropY + "px",
          "--size": m.size + "px",
          "--dur": m.dur + "s",
          "--delay": m.delay + "s"
        }} />

      )}
    </div>);

}

let meteorId = 0;
function spawnMeteor(seed, size) {
  meteorId++;
  const angle = Math.random() * Math.PI * 2;
  const radius = size * (0.95 + Math.random() * 0.55);
  const fromX = Math.cos(angle) * radius;
  const fromY = Math.sin(angle) * radius;
  // Hook point on sphere surface
  const hookR = size * 0.44;
  const hookX = Math.cos(angle) * hookR;
  const hookY = Math.sin(angle) * hookR;
  // Cable's RJ45 head sits at top of sprite. With pivot at top-center
  // (transform-origin 50% 0%), rotation swings the body around the head.
  // Sprite at rotation 0 has body pointing down. For the head to point
  // inward (toward globe center), sprite-top points toward center, so
  // sprite rotation = angle + 90deg (CSS clockwise).
  const rot = angle * 180 / Math.PI + 90;
  // Hang pendulum swing direction & amount
  const swingSide = Math.random() < 0.5 ? -1 : 1;
  const swingAmount = 6 + Math.random() * 5;
  // Fall rotation: body swings to point straight down (sprite-top up = 0deg
  // mod 360). Reach the nearest equivalent of 0 from current rot.
  const norm = ((rot + 180) % 360 + 360) % 360 - 180;
  const fallRot = rot - norm;
  const sz = size * (0.34 + Math.random() * 0.18);
  return {
    id: "m" + meteorId,
    fromX, fromY,
    hookX, hookY,
    rot,
    swingA: rot + swingSide * swingAmount,
    swingB: rot - swingSide * swingAmount * 0.5,
    fallRot,
    fallDropY: size * 0.95,
    size: sz,
    dur: 4.0 + Math.random() * 0.8,
    delay: Math.random() * 0.8
  };
}

// MeshSphere with cable meteors behind it, deforms on each hook
function AnimatedGlobe({
  size = 160,
  rotY = 0.4,
  rotX = -0.1,
  strokeWidth = 0.55,
  meteors = 2,
  interval = 4000,
  thump = true,
  className = "",
  style = {}
}) {
  const coreRef = React.useRef(null);
  const onHook = React.useCallback(() => {
    if (!thump) return;
    const el = coreRef.current;
    if (!el) return;
    // jitter the impact direction so it doesn't feel scripted
    const angle = Math.random() * Math.PI * 2;
    const dx = Math.cos(angle) * 1.5;
    const dy = Math.sin(angle) * 1.5;
    el.style.setProperty("--thump-x", dx + "px");
    el.style.setProperty("--thump-y", dy + "px");
    el.classList.remove("is-thump");
    // force reflow to restart animation
    void el.offsetWidth;
    el.classList.add("is-thump");
  }, [thump]);

  return (
    <div
      className={"animated-globe " + className}
      style={{ width: size, height: size, ...style }}>
      
      <CableMeteors size={size} count={meteors} interval={interval} onHook={onHook} />
      <div className="globe-core" ref={coreRef}>
        <MeshSphere
          size={size}
          rotY={rotY}
          rotX={rotX}
          strokeWidth={strokeWidth}
          autoRotate
          speed={0.16} />
        
      </div>
    </div>);

}

Object.assign(window, { CableMeteors, AnimatedGlobe });