// Agent-swarm page intro: colored "agent" cursors fly in, draw a labelled wireframe over each
// [data-swarm] element, then reveal it. Decorative only: skipped under prefers-reduced-motion,
// and the HUD has Replay and Skip buttons. No dependencies; include with <script src="agent-swarm.js" defer>.
(() => {
  const AGENTS = window.SWARM_AGENTS || [
    ["parser", "#ff6b5e"], ["planner", "#ffb84d"], ["builder", "#7ee07e"], ["styler", "#5ec8ff"],
    ["writer", "#c58bff"], ["tester", "#ff7ac6"], ["linker", "#f5f06a"], ["reviewer", "#8affd8"],
  ];
  const STEP = 300; // ms between agents starting on consecutive elements
  const FLOCK = [[.5, .3], [.8, .55], [.3, .65], [.55, .45]]; // viewport spots (x, y fractions) the swarm roams through together
  const HOP = 650; // ms per flock hop

  const css = `
  #swarm-stage { position: absolute; left: 0; top: 0; width: 100%; pointer-events: none; z-index: 9990; overflow: hidden; }
  .swarm-building [data-swarm] { opacity: 0; }
  [data-swarm] { transition: opacity .5s ease; }
  .swarm-wire { position: absolute; border: 1px solid var(--swarm-wire, #3d8bff); background: rgba(61,139,255,.06); width: 0; height: 0;
    transition: width .7s cubic-bezier(.2,.8,.2,1), height .7s cubic-bezier(.2,.8,.2,1), opacity .4s ease; }
  .swarm-wire::before, .swarm-wire::after { content: ""; position: absolute; width: 6px; height: 6px; background: #fff; border: 1px solid var(--swarm-wire, #3d8bff); }
  .swarm-wire::before { left: -4px; top: -4px; } .swarm-wire::after { right: -4px; bottom: -4px; }
  .swarm-wire span { position: absolute; left: 50%; bottom: -22px; transform: translateX(-50%); background: var(--swarm-wire, #3d8bff); color: #fff;
    font: 700 10px ui-monospace, Consolas, monospace; padding: 2px 6px; white-space: nowrap; }
  .swarm-cursor { position: absolute; left: 0; top: 0; transition: transform .6s cubic-bezier(.3,.7,.2,1), opacity .4s ease; }
  .swarm-cursor svg { display: block; filter: drop-shadow(0 1px 2px #000); }
  .swarm-cursor em { position: absolute; left: 14px; top: 14px; font: 700 10px ui-monospace, Consolas, monospace; font-style: normal; color: #000; padding: 1px 5px; white-space: nowrap; }
  .swarm-hud { position: fixed; left: 50%; bottom: 16px; transform: translateX(-50%); z-index: 9991; display: flex; align-items: center; gap: 14px;
    background: rgba(18,18,18,.92); color: #aaa; border: 1px solid #333; padding: 8px 10px 8px 14px; font: 12px ui-monospace, Consolas, monospace; white-space: nowrap; }
  .swarm-hud b { color: #fff; }
  .swarm-hud .dots { display: flex; } .swarm-hud .dots i { width: 10px; height: 10px; border-radius: 50%; margin-left: -3px; border: 1px solid #121212; }
  .swarm-hud button { font: 700 11px ui-monospace, Consolas, monospace; text-transform: uppercase; letter-spacing: .08em; background: none; color: #fff;
    border: 1px solid #444; padding: 6px 10px; cursor: pointer; min-height: 32px; }
  .swarm-hud button:hover { border-color: var(--swarm-accent, #ff3b2f); }
  .swarm-hud button:focus-visible { outline: 2px solid var(--swarm-accent, #ff3b2f); outline-offset: 2px; }
  @media (max-width: 600px) { .swarm-hud .count { display: none; } }`;

  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let timers = [], opsTimer, stage, hud, userScrolled = false;
  const later = (ms, fn) => timers.push(setTimeout(fn, ms));
  const pageRect = (el) => { const r = el.getBoundingClientRect(); return { x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height }; };
  const moveTo = (c, x, y) => (c.style.transform = `translate(${x}px, ${y}px)`);
  const targets = () => [...document.querySelectorAll("[data-swarm]")];
  const reset = () => targets().forEach((el) => (el.style.opacity = ""));
  const stop = () => { timers.forEach(clearTimeout); timers = []; clearInterval(opsTimer); };

  function setup() {
    document.head.append(Object.assign(document.createElement("style"), { textContent: css }));
    stage = Object.assign(document.createElement("div"), { id: "swarm-stage" });
    stage.setAttribute("aria-hidden", "true");
    hud = document.createElement("div");
    hud.className = "swarm-hud";
    hud.setAttribute("role", "group");
    hud.setAttribute("aria-label", "Build animation controls");
    hud.innerHTML = `<span class="dots"></span><span><b class="agents">0</b> agents</span>
      <span class="count"><b class="ops">0</b> ops</span><button type="button">Replay</button><button type="button">Skip</button>`;
    document.body.append(stage, hud);
    const [replay, skip] = hud.querySelectorAll("button");
    replay.onclick = () => { reset(); run(); };
    skip.onclick = () => { reset(); finish(); };
    hud.querySelector(".dots").innerHTML = AGENTS.map(([, c]) => `<i style="background:${c}"></i>`).join("");
    // Stop auto-scrolling as soon as the visitor scrolls themselves.
    ["wheel", "touchstart", "keydown"].forEach((t) => addEventListener(t, () => (userScrolled = true), { passive: true }));
  }

  // Keep the element being built in view (page coords), unless the visitor took over scrolling.
  function follow(r) {
    if (userScrolled || (r.y >= scrollY && r.y + Math.min(r.h, innerHeight / 2) <= scrollY + innerHeight - 60)) return;
    scrollTo({ top: Math.max(0, r.y - innerHeight * 0.25), behavior: "smooth" });
  }

  function finish() {
    stop();
    document.documentElement.classList.remove("swarm-building");
    stage.querySelectorAll(".swarm-cursor, .swarm-wire").forEach((n) => (n.style.opacity = 0));
    later(450, () => stage.replaceChildren());
    hud.querySelector(".agents").textContent = 0;
  }

  function run() {
    stop();
    stage.replaceChildren();
    if (reduceMotion) return;
    document.documentElement.classList.add("swarm-building");
    stage.style.height = document.documentElement.scrollHeight + "px";
    userScrolled = false;
    scrollTo(0, 0); // the build always starts at the top, even after a reload restored a scroll position

    // Each agent keeps a fixed offset inside the pack and trails the leader slightly, so they move as one swarm.
    const pack = AGENTS.map(() => [Math.random() * 90 - 45, Math.random() * 70 - 35]);
    const cursors = AGENTS.map(([name, color], i) => {
      const c = document.createElement("div");
      c.className = "swarm-cursor";
      c.innerHTML = `<svg width="16" height="18" viewBox="0 0 16 18"><path d="M1 1l14 7-6 2-3 6z" fill="${color}"/></svg><em style="background:${color}">${name}</em>`;
      c.style.transition = "none";
      c.style.transitionDelay = i * 40 + "ms";
      moveTo(c, -60 + pack[i][0], scrollY + innerHeight * 0.75 + pack[i][1]); // enter together from the left edge
      stage.append(c);
      return c;
    });
    stage.offsetWidth; // apply the start position before transitions turn back on
    cursors.forEach((c) => (c.style.transition = ""));

    FLOCK.forEach(([fx, fy], k) => later(80 + k * HOP, () =>
      cursors.forEach((c, i) => moveTo(c, fx * innerWidth + pack[i][0], scrollY + fy * innerHeight + pack[i][1]))));
    const t0 = 80 + FLOCK.length * HOP; // swarm splits up and starts building here
    later(t0, () => cursors.forEach((c) => (c.style.transitionDelay = "")));
    hud.querySelector(".agents").textContent = AGENTS.length;
    let ops = 0;
    opsTimer = setInterval(() => (hud.querySelector(".ops").textContent = (ops += 7 + Math.floor(Math.random() * 30))), 80);

    const els = targets();
    els.forEach((el, i) => {
      const cursor = cursors[i % cursors.length];
      later(t0 + i * STEP, () => {
        const r = pageRect(el);
        follow(r);
        moveTo(cursor, r.x, r.y);
        later(600, () => {
          const wire = document.createElement("div");
          wire.className = "swarm-wire";
          Object.assign(wire.style, { left: r.x + "px", top: r.y + "px" });
          const label = document.createElement("span");
          label.textContent = `${el.dataset.swarm || el.tagName.toLowerCase()} · ${Math.round(r.w)}×${Math.round(r.h)}`;
          wire.append(label);
          stage.append(wire);
          requestAnimationFrame(() => Object.assign(wire.style, { width: r.w + "px", height: r.h + "px" }));
          moveTo(cursor, r.x + r.w, r.y + r.h); // drag to the opposite corner
          later(750, () => (el.style.opacity = 1));
          later(1300, () => (wire.style.opacity = 0));
        });
      });
    });
    later(t0 + els.length * STEP + 2000, () => {
      reset();
      finish();
      if (!userScrolled) scrollTo({ top: 0, behavior: "smooth" }); // land back on the hero once built
    });
  }

  if (!reduceMotion) history.scrollRestoration = "manual"; // a reload would otherwise jump back down after the build starts at the top
  const start = () => { setup(); run(); };
  document.readyState === "complete" ? start() : addEventListener("load", start);
})();
