// Fetch and SSE helpers. Fetch injects context headers; SSE appends workspace
// as a query param because EventSource cannot set custom headers.

import { getContext } from './context.mjs';
import { getState } from './store.mjs';

export async function apiFetch(input, init = {}) {
  // Prefer central store context; fallback to legacy context
  let ctx = { workspace: undefined, apiKey: undefined };
  try { ctx = (getState && getState().context) || ctx; } catch (_) {}
  if (!ctx || (!ctx.workspace && !ctx.apiKey)) {
    try { ctx = getContext(); } catch (_) {}
  }
  const headers = Object.assign({}, init.headers || {});
  if (ctx.workspace) headers['X-Workspace'] = ctx.workspace;
  if (ctx.apiKey) headers['Authorization'] = 'Bearer ' + ctx.apiKey;
  const nextInit = Object.assign({}, init, { headers });
  return fetch(input, nextInit);
}

// apiFetchWith allows overriding workspace/apiKey for the request only.
export async function apiFetchWith(input, init = {}, overrides = {}) {
  const ctx = getContext();
  const headers = Object.assign({}, init.headers || {});
  const ws = overrides.workspace || ctx.workspace;
  const key = overrides.apiKey || ctx.apiKey;
  if (ws) headers['X-Workspace'] = ws;
  if (key) headers['Authorization'] = 'Bearer ' + key;
  const nextInit = Object.assign({}, init, { headers });
  return fetch(input, nextInit);
}

export function withWorkspace(url) {
  const ctx = getContext();
  try {
    const u = new URL(url, window.location.origin);
    if (ctx.workspace) u.searchParams.set('workspace', ctx.workspace);
    return u.pathname + (u.search ? u.search : '');
  } catch (_) {
    // Fallback for relative URLs
    const j = url.includes('?') ? '&' : '?';
    return ctx.workspace ? `${url}${j}workspace=${encodeURIComponent(ctx.workspace)}` : url;
  }
}

export function sse(url, opts = {}) {
  const { on = {}, onOpen, onError, onClose } = opts;
  const full = withWorkspace(url);
  const es = new EventSource(full);
  if (onOpen) es.addEventListener('open', () => onOpen());
  if (onError)
    es.addEventListener('error', (e) => {
      try { onError(e); } catch (_) {}
    });
  // Register custom event handlers
  Object.entries(on).forEach(([name, handler]) => {
    if (!handler) return;
    es.addEventListener(name, (e) => {
      try {
        const data = parseJSON(e.data);
        handler(data, e);
      } catch (_) {
        handler(undefined, e);
      }
    });
  });
  function close() {
    try { es.close(); } catch (_) {}
    if (onClose) try { onClose(); } catch (_) {}
  }
  return { source: es, close };
}

function parseJSON(s) {
  if (!s) return undefined;
  try { return JSON.parse(s); } catch (_) { return undefined; }
}
