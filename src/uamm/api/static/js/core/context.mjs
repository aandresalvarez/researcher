// Minimal context store: workspace + apiKey, with localStorage persistence
// and event-based change notifications. No globals; consumers import APIs.

import { EV, emit } from './events.mjs';

const LS_WS = 'uamm.ws';
const LS_KEY = 'uamm.key';

const state = {
  workspace: readLS(LS_WS, 'default'),
  apiKey: readLS(LS_KEY, ''),
};

function readLS(name, fallback) {
  try {
    const v = localStorage.getItem(name);
    return v === null || v === undefined ? fallback : v;
  } catch (_) {
    return fallback;
  }
}

function writeLS(name, value) {
  try {
    if (value === null || value === undefined || value === '') {
      localStorage.removeItem(name);
    } else {
      localStorage.setItem(name, value);
    }
  } catch (_) {}
}

export function getContext() {
  return { workspace: state.workspace || 'default', apiKey: state.apiKey || '' };
}

export function setContext(partial) {
  const next = {
    workspace: partial.workspace ?? state.workspace,
    apiKey: partial.apiKey ?? state.apiKey,
  };
  const changed = next.workspace !== state.workspace || next.apiKey !== state.apiKey;
  if (!changed) return getContext();
  state.workspace = next.workspace || 'default';
  state.apiKey = next.apiKey || '';
  writeLS(LS_WS, state.workspace);
  writeLS(LS_KEY, state.apiKey);
  emit(document, EV.CONTEXT_CHANGE, getContext());
  return getContext();
}

export function onContextChange(handler) {
  return document.addEventListener(EV.CONTEXT_CHANGE, (ev) => handler(ev.detail));
}

function qs(name) {
  try {
    return new URLSearchParams(window.location.search).get(name);
  } catch (_) {
    return null;
  }
}

export function initContext() {
  // Support ?ws= override (mirrors legacy behavior)
  const wsParam = qs('ws');
  if (wsParam && wsParam !== state.workspace) {
    setContext({ workspace: wsParam });
  } else {
    // Emit initial context so listeners can bootstrap
    emit(document, EV.CONTEXT_CHANGE, getContext());
  }
}

