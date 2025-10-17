// Minimal observable store for the UI shell

const subscribers = new Set();
let state = {
  context: { workspace: 'default', apiKey: '' },
  route: { name: 'home', params: {} },
  workspaces: { list: [], loading: false, error: null },
  stats: { docs: 0, steps: 0, tau: null, paths: {}, loading: false },
  settings: { data: {}, loading: false, error: null },
  rag: {
    ingested: { items: [], loading: false, lastUpdated: 0 },
    status: { items: [], loading: false, filter: 'all', q: '' },
    fileDetail: { byPath: {}, loading: false },
    env: { parsers: {}, loading: false },
  },
  obs: {
    metrics: { data: {}, loading: false },
    steps: { items: [], loading: false, domain: '', action: '', limit: 50 },
  },
  wsDash: {
    last_step_ts: null,
    doc_latest: null,
    trend: { docs: [], steps: [], days: 7, loading: false },
  },
  cp: { domain: 'default', result: null, loading: false, error: null },
  evals: {
    suites: { items: [], loading: false, error: null },
    runs: { items: [], loading: false, error: null },
    report: { data: null, loading: false, error: null },
    proposal: { id: null, patch: null, canary: [], loading: false, error: null },
    adhoc: { data: null, loading: false, error: null },
  },
  wsAdmin: {
    keys: { ws: '', items: [], loading: false, error: null },
    packs: { items: [], loading: false, error: null },
    preview: { data: null, loading: false, error: null },
    overlay: { applying: false, error: null, ok: false },
  },
};

export function getState() { return state; }

export function setState(next) {
  state = next;
  notify();
}

export function patchState(partial) {
  state = deepMerge(state, partial);
  notify();
}

export function subscribe(fn) {
  subscribers.add(fn);
  try { fn(state); } catch (_) {}
  return () => { subscribers.delete(fn); };
}

export function select(selector, onChange) {
  let prev;
  function handle(s) {
    const next = selector(s);
    if (!shallowEqual(prev, next)) {
      prev = next;
      try { onChange(next); } catch (_) {}
    }
  }
  return subscribe(handle);
}

function notify() {
  subscribers.forEach((fn) => { try { fn(state); } catch (_) {} });
}

function shallowEqual(a, b) {
  if (a === b) return true;
  if (!a || !b) return false;
  const ka = Object.keys(a), kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  for (let k of ka) { if (a[k] !== b[k]) return false; }
  return true;
}

function deepMerge(dst, src) {
  if (!src || typeof src !== 'object') return dst;
  const out = Array.isArray(dst) ? dst.slice() : { ...dst };
  for (const [k, v] of Object.entries(src)) {
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      out[k] = deepMerge(dst ? dst[k] : undefined, v);
    } else {
      out[k] = v;
    }
  }
  return out;
}

export default { getState, setState, patchState, subscribe, select };
