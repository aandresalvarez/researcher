// Entry module for the Web Components refactor (vanilla JS only)
// Phase 0: no UI changes — set up core helpers and bootstrap context/debug.

import { initContext } from './core/context.mjs';
import { installEventLog, log, isDebugEnabled } from './core/debug.mjs';
import { EV } from './core/events.mjs';
import { startRouter } from './core/router.mjs';
import store, { patchState, select } from './core/store.mjs';
import { selectRoute } from './core/selectors.mjs';
import { refreshStats } from './core/actions.mjs';
import './components/uamm-sidebar.mjs';
import './components/uamm-context-panel.mjs';
import './components/uamm-playground.mjs';
import './components/uamm-wizard.mjs';
import './components/uamm-obs-page.mjs';
import './components/uamm-rag-upload.mjs';
import './components/uamm-rag-page.mjs';
import './components/uamm-cp-page.mjs';
import './components/uamm-evals-page.mjs';
import './components/uamm-home-page.mjs';
import './components/uamm-workspaces-page.mjs';
import './components/uamm-toast.mjs';
import './components/uamm-modal-host.mjs';
import './components/uamm-router-outlet.mjs';

// Initialize context from localStorage / ?ws= and emit initial event
initContext();

// Install console/event logging if flags enabled
installEventLog(document);

document.addEventListener('DOMContentLoaded', () => {
  if (isDebugEnabled()) log('app', 'app.mjs loaded; context initialized');
  try { startRouter(); } catch (_) {}
  // Update navbar active state on route changes (hash)
  try { window.addEventListener('hashchange', () => { if (window.initActiveNavHighlight) window.initActiveNavHighlight(); }); } catch (_) {}
  // Hide server-rendered content when SPA route is active
  try {
    const SPA_PAGES = new Set(['home','rag','obs','cp','evals','workspaces']);
    select(selectRoute, (route) => {
      const sc = document.getElementById('server-content');
      if (!sc) return;
      const show = !(route && SPA_PAGES.has(route.name));
      sc.style.display = show ? '' : 'none';
    });
  } catch (_) {}
  // Keep navbar badge in sync
  document.addEventListener(EV.CONTEXT_CHANGE, (ev) => {
    try {
      const ws = (ev.detail && ev.detail.workspace) || 'default';
      const currentWs = store.getState().context.workspace;
      // Only sync if workspace actually changed
      if (ws !== currentWs) {
        patchState({ context: { ...store.getState().context, workspace: ws } });
        refreshStats();
      }
    } catch (_) {}
  });
});

// Note: Components will be registered in later milestones.
