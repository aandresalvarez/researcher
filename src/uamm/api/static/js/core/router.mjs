// Minimal hash-based router that writes into the central store
import { patchState, getState } from './store.mjs';

function parseRoute() {
  const raw = (window.location.hash || '').replace(/^#/, '');
  let path = raw;
  let search = '';
  if (raw && raw.includes('?')) {
    const i = raw.indexOf('?');
    path = raw.slice(0, i);
    search = raw.slice(i + 1);
  }
  if (!path) {
    // Fallback to server pathname mapping when no hash is present
    const p = (window.location.pathname || '').replace(/\/$/, '');
    const map = {
      // '/ui' is the Playground (server-rendered), not an SPA route
      '/ui': 'playground',
      '/ui/home': 'home',
      '/ui/rag': 'rag',
      '/ui/obs': 'obs',
      '/ui/cp': 'cp',
      '/ui/evals': 'evals',
      '/ui/workspaces': 'workspaces',
      '/ui/docs': 'docs',
    };
    const name = map[p] || 'home';
    return { name, params: {} };
  }
  const seg = path.replace(/^\//, '').split('/');
  const name = seg[0] || 'home';
  const qs = new URLSearchParams(search || '');
  const params = {};
  qs.forEach((v, k) => { params[k] = v; });
  return { name, params };
}

function onHashChange() {
  const route = parseRoute();
  patchState({ route });
}

export function startRouter() {
  // Initialize
  onHashChange();
  // Listen to changes
  window.addEventListener('hashchange', onHashChange);
}

export function navigate(name, params = {}) {
  const qs = new URLSearchParams(params).toString();
  const hash = `#/${encodeURIComponent(name)}` + (qs ? `?${qs}` : '');
  if (window.location.hash !== hash) window.location.hash = hash;
  else onHashChange();
}

export default { startRouter, navigate };
