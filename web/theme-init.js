/* Synchronous pre-CSS theme bootstrap to avoid initial theme flash. */
(() => {
  "use strict";
  const prefix = "h3-monitor-display:";
  const choices = {
    palette: ["sage", "ash-marble"],
    mode: ["auto", "light", "dark"],
    tone: ["pale", "gray"],
    material: ["marble", "flat"]
  };
  const defaults = {
    palette: "sage",
    mode: "auto",
    tone: "pale",
    material: "marble"
  };

  const state = {};
  let storageAvailable = true;

  function loadStoredState() {
    for (const dim of Object.keys(choices)) {
      let saved = null;
      try {
        saved = window.localStorage.getItem(prefix + dim);
      } catch {
        storageAvailable = false;
      }
      state[dim] = choices[dim].includes(saved) ? saved : defaults[dim];
    }
  }

  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  const resolveMode = () => (state.mode === "auto" ? (systemDark.matches ? "dark" : "light") : state.mode);

  function applyRootAttributes() {
    const root = document.documentElement;
    const resolved = resolveMode();
    root.dataset.palette = state.palette;
    root.dataset.themeMode = state.mode;
    root.dataset.resolvedTheme = resolved;
    root.dataset.ashTone = state.tone;
    root.dataset.ashMaterial = state.material;
    const meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) {
      meta.content = state.mode === "auto" ? "light dark" : state.mode;
    }
  }

  loadStoredState();
  applyRootAttributes();

  window.__H3_DISPLAY__ = {
    prefix,
    choices,
    defaults,
    state,
    get storageAvailable() {
      return storageAvailable;
    },
    setStorageUnavailable() {
      storageAvailable = false;
    },
    loadStoredState,
    resolveMode,
    applyRootAttributes
  };
})();
