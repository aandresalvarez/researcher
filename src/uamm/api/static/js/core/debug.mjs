// Lightweight debug utilities and an optional event log overlay.

import { on } from './events.mjs';

export function isDebugEnabled() {
  return getFlag('uamm.debug') || getFlag('debug');
}

export function enableDebug(flag) {
  try { localStorage.setItem('uamm.debug', flag ? '1' : '0'); } catch (_) {}
}

export function log(scope, ...args) {
  if (!isDebugEnabled()) return;
  try { console.debug(`[${scope}]`, ...args); } catch (_) {}
}

export function installEventLog(root = document) {
  // Console-only by default; overlay behind `uamm.devtools` flag.
  const unsub = on(root, '*', () => {});
  // Listen to all custom events by monkey-patching addEventListener? Not needed.
  // Instead, capture bubbling CustomEvent at document level.
  const handler = (ev) => {
    if (!(ev instanceof CustomEvent)) return;
    if (!String(ev.type).startsWith('uamm:')) return;
    if (isDebugEnabled()) {
      try { console.info(`(event) ${ev.type}`, ev.detail); } catch (_) {}
    }
    if (getFlag('uamm.devtools')) appendOverlay(ev.type, ev.detail);
  };
  document.addEventListener('*', handler, true);
  document.addEventListener('uamm:toast', handler, true);
  document.addEventListener('uamm:context-change', handler, true);
  document.addEventListener('uamm:workspace-change', handler, true);
  document.addEventListener('uamm:stream-ready', handler, true);
  document.addEventListener('uamm:stream-token', handler, true);
  document.addEventListener('uamm:stream-final', handler, true);
  document.addEventListener('uamm:stream-error', handler, true);
  return () => {
    document.removeEventListener('uamm:toast', handler, true);
    document.removeEventListener('uamm:context-change', handler, true);
    document.removeEventListener('uamm:workspace-change', handler, true);
    document.removeEventListener('uamm:stream-ready', handler, true);
    document.removeEventListener('uamm:stream-token', handler, true);
    document.removeEventListener('uamm:stream-final', handler, true);
    document.removeEventListener('uamm:stream-error', handler, true);
    try { unsub(); } catch (_) {}
  };
}

function appendOverlay(type, detail) {
  const rootId = 'uamm-devtools-overlay';
  let root = document.getElementById(rootId);
  if (!root) {
    root = document.createElement('div');
    root.id = rootId;
    root.style.cssText = 'position:fixed;right:8px;bottom:8px;z-index:2147483647;max-width:40vw;max-height:40vh;overflow:auto;background:rgba(0,0,0,.75);color:#e6e6e6;font:12px/1.4 system-ui, sans-serif;border-radius:6px;padding:6px 8px;box-shadow:0 2px 8px rgba(0,0,0,.4)';
    document.body.appendChild(root);
  }
  const row = document.createElement('div');
  row.style.margin = '2px 0';
  const ts = new Date().toLocaleTimeString();
  const pre = document.createElement('pre');
  pre.style.whiteSpace = 'pre-wrap';
  pre.style.margin = '0';
  pre.textContent = `[${ts}] ${type}  ${safeJSON(detail)}`;
  row.appendChild(pre);
  root.appendChild(row);
  // Trim
  while (root.childNodes.length > 200) root.removeChild(root.firstChild);
}

function getFlag(name) {
  try {
    // Priority: localStorage flag, then URL ?name=1
    const v = localStorage.getItem(name);
    if (v === '1' || v === 'true') return true;
  } catch (_) {}
  try {
    const u = new URLSearchParams(window.location.search).get(name);
    if (u === '1' || u === 'true') return true;
  } catch (_) {}
  return false;
}

function safeJSON(v) {
  try { return JSON.stringify(v); } catch (_) { return String(v); }
}
