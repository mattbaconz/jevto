(() => {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  // Tablist helper: click + Arrow/Home/End keys, roving tabindex.
  function tablist(tabs, onSelect) {
    const select = (tab, focus) => {
      tabs.forEach((t) => {
        const on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
      });
      if (focus) tab.focus();
      onSelect(tab);
    };
    tabs.forEach((tab, i) => {
      tab.addEventListener("click", () => select(tab));
      tab.addEventListener("keydown", (e) => {
        const step = { ArrowRight: 1, ArrowLeft: -1, Home: -i, End: tabs.length - 1 - i }[e.key];
        if (step === undefined) return;
        e.preventDefault();
        select(tabs[(i + step + tabs.length) % tabs.length], true);
      });
    });
  }

  // Output inspector: real benchmark views (site/data, copied from benchmarks/results/views).
  const scenarios = {
    "python-unittest-failure": { goal: "Fix the failing parser test", exit: 1, native: [3572, 3, 3], rtk: [3572, 3, 3, "not routed"], jevto: [200, 3, 3] },
    "go-test-failure": { goal: "Fix the failing header parser test", exit: 1, native: [2651, 2, 2], rtk: [64, 2, 2], jevto: [78, 2, 2] },
    "git-diff-lockfile": { goal: "Review the header parsing change", exit: 0, native: [11484, 2, 2], rtk: [766, 2, 2], jevto: [120, 2, 2] },
    "git-log-history": { goal: "When did we change the reconnect backoff?", exit: 0, native: [12618, 1, 1], rtk: [222, 0, 1], jevto: [551, 1, 1] },
  };
  const output = document.getElementById("evidence-output");
  const score = document.getElementById("evidence-score");
  const goal = document.getElementById("evidence-goal");
  const meta = document.getElementById("result-meta");
  const picker = document.getElementById("scenario-select");
  const panel = document.getElementById("panel-evidence");
  const armTabs = [...document.querySelectorAll(".evidence-tab")];
  const cache = new Map();
  let arm = "jevto";

  async function render() {
    const name = picker.value;
    const s = scenarios[name];
    const [tokens, found, total, note] = s[arm];
    const pct = arm === "native" ? "" : ` (${Math.round((tokens / s.native[0] - 1) * 100)}%)`.replace("-", "−");
    goal.textContent = s.goal;
    meta.textContent = `exit ${s.exit}`;
    meta.className = "result-meta" + (s.exit ? " failed" : "");
    score.innerHTML = "";
    const t = document.createElement("span");
    t.textContent = `≈ ${tokens.toLocaleString("en-US")} tokens${pct}${note ? ` · ${note}` : ""}`;
    const f = document.createElement("span");
    f.className = found < total ? "miss" : "ok";
    f.textContent = `${found}/${total} facts visible`;
    score.append(t, f);
    const key = `${name}/${arm}`;
    if (!cache.has(key)) {
      output.textContent = "Loading capture…";
      try {
        const res = await fetch(`./data/${key}.txt`);
        if (!res.ok) throw new Error(res.status);
        cache.set(key, (await res.text()).replace(/'<jevto>'/g, "jevto"));
      } catch {
        output.textContent = "Couldn't load this capture. Serve the site over HTTP (python -m http.server) to view it.";
        return;
      }
    }
    if (picker.value !== name || key !== `${name}/${arm}`) return;
    output.textContent = cache.get(key);
    output.scrollTop = 0;
    if (!reduceMotion.matches && output.animate) {
      output.animate([{ opacity: 0.4 }, { opacity: 1 }], { duration: 220, easing: "ease-out" });
    }
  }

  if (output && picker) {
    tablist(armTabs, (tab) => {
      arm = tab.dataset.view;
      panel.setAttribute("aria-labelledby", tab.id);
      render();
    });
    picker.addEventListener("change", render);
    render();
  }

  // Benchmark tabs.
  const benchTabs = [...document.querySelectorAll(".bench-tab")];
  tablist(benchTabs, (tab) => {
    benchTabs.forEach((t) => {
      document.getElementById(t.getAttribute("aria-controls")).hidden = t !== tab;
    });
  });

  // Copy button.
  document.querySelectorAll(".copy-button").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(document.getElementById(button.dataset.copy).innerText);
        button.textContent = "Copied";
      } catch {
        button.textContent = "Select and copy";
      }
      setTimeout(() => (button.textContent = "Copy"), 1600);
    });
  });

  // Start menu.
  const startButton = document.getElementById("start-button");
  const startMenu = document.getElementById("start-menu");
  if (startButton && startMenu) {
    const closeMenu = () => {
      startMenu.hidden = true;
      startButton.setAttribute("aria-expanded", "false");
    };
    startButton.addEventListener("click", () => {
      const open = startMenu.hidden;
      startMenu.hidden = !open;
      startButton.setAttribute("aria-expanded", String(open));
    });
    startMenu.querySelectorAll("a").forEach((link) => link.addEventListener("click", closeMenu));
    document.addEventListener("click", (event) => {
      if (!startMenu.hidden && !startMenu.contains(event.target) && !startButton.contains(event.target)) closeMenu();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !startMenu.hidden) {
        closeMenu();
        startButton.focus();
      }
    });
  }

  // Wallpaper motion toggle.
  const motionToggle = document.getElementById("motion-toggle");
  if (motionToggle) {
    const motionLabel = document.getElementById("motion-label");
    motionToggle.addEventListener("click", () => {
      const paused = document.body.classList.toggle("motion-paused");
      const label = paused ? "Resume wallpaper animation" : "Pause wallpaper animation";
      motionToggle.setAttribute("aria-pressed", String(paused));
      motionToggle.setAttribute("aria-label", label);
      motionToggle.title = label;
      if (motionLabel) motionLabel.textContent = paused ? "Resume wallpaper" : "Pause wallpaper";
    });
  }
  document.addEventListener("visibilitychange", () => {
    document.body.classList.toggle("page-hidden", document.hidden);
  });
})();
