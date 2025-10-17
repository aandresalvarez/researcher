import { apiFetch } from '../core/api.mjs';
import { setContext, getContext } from '../core/context.mjs';
import { EV, emit } from '../core/events.mjs';
import { log, isDebugEnabled } from '../core/debug.mjs';
import { setWorkspace, loadWorkspaces } from '../core/actions.mjs';
import { subscribe, select, getState } from '../core/store.mjs';
import { selectWorkspaces, selectContext } from '../core/selectors.mjs';

export class UammSidebar extends HTMLElement {
  constructor() {
    super();
    this._abort = new AbortController();
    this._mounted = false;
  }

  connectedCallback() {
    if (this._mounted) return;
    this._mounted = true;
    // Render structure
    this.innerHTML = this._template();
    // Restore collapsed state from storage (optional)
    try { const s = localStorage.getItem('uamm.sidebarCollapsed'); const host = document.getElementById('sidebar'); if (host && (s === '1')) host.classList.add('collapsed'); } catch(_){}
    // Observe collapse/expand to adjust UI and persist
    this._watchCollapse();
    // Wire handlers (support both expanded and collapsed icon toolbars)
    this.querySelectorAll('[data-action="refresh"]').forEach(el => el.addEventListener('click', () => loadWorkspaces()));
    this.querySelectorAll('[data-action="create-test"]').forEach(el => el.addEventListener('click', () => this.createTest()));
    this.querySelectorAll('[data-action="wizard"]').forEach(el => el.addEventListener('click', (ev) => {
      try{
        ev.preventDefault();
        const m = document.getElementById('wsWizardGlobal');
        if (m && window.bootstrap) window.bootstrap.Modal.getOrCreateInstance(m).show();
      }catch(_){}
    }));
    // Toggle sidebar (collapses or opens offcanvas on small screens)
    this.querySelectorAll('[data-action="toggle-sidebar"]').forEach(el => el.addEventListener('click', (ev) => {
      try{
        ev.preventDefault();
        if (window.toggleSidebarResponsive) window.toggleSidebarResponsive();
        else if (window.toggleSidebar) window.toggleSidebar();
        else {
          // Fallback local toggle
          const aside = document.getElementById('sidebar');
          if (aside) {
            aside.classList.toggle('collapsed');
            const c = aside.classList.contains('collapsed');
            try { localStorage.setItem('uamm.sidebarCollapsed', c ? '1' : '0'); } catch(_){}
            aside.setAttribute('aria-expanded', c ? 'false' : 'true');
          }
        }
      }catch(_){}
    }));
    // Initial load
    // Sync initial aria-expanded for accessibility
    try {
      const host = document.getElementById('sidebar');
      if (host) {
        const collapsed = host.classList.contains('collapsed');
        host.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        this.querySelectorAll('[data-action="toggle-sidebar"]').forEach(el => el.setAttribute('aria-pressed', collapsed ? 'true' : 'false'));
      }
    } catch(_){}
    
    // Subscribe to store for workspaces
    this._workspacesUnsub = select(selectWorkspaces, (data) => {
      if (!data.loading) this._renderWorkspaces(data.list);
    });
    
    // Subscribe to context for active workspace
    this._contextUnsub = select(selectContext, (ctx) => {
      this._updateActiveWorkspace(ctx.workspace || 'default');
    });
    
    // Initial load
    loadWorkspaces();
  }

  disconnectedCallback() {
    try { this._abort.abort(); } catch (_) {}
    this._mounted = false;
    if (this._mo) { try { this._mo.disconnect(); } catch(_){} this._mo = null; }
    // Unsubscribe from store
    if (this._workspacesUnsub) { try { this._workspacesUnsub(); } catch(_){} this._workspacesUnsub = null; }
    if (this._contextUnsub) { try { this._contextUnsub(); } catch(_){} this._contextUnsub = null; }
  }

  _template() {
    // Preserve existing sidebar look; host element should have id="sidebar" in template.
    return `
      <div class="p-2" style="position: sticky; top: 4rem;">
        <div class="d-flex align-items-center mb-2" data-role="header">
          <button id="sidebarToggle" class="btn btn-sm btn-outline-secondary me-2" title="Toggle sidebar" aria-pressed="false" aria-label="Toggle sidebar" data-action="toggle-sidebar">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" class="bi bi-layout-sidebar" viewBox="0 0 16 16">
              <path d="M0 3a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H2a2 2 0 0 1-2-2zm5-1v12h9a1 1 0 0 0 1-1V3a1 1 0 0 0-1-1zM4 2H2a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h2z"/>
            </svg>
          </button>
          <strong class="me-auto sidebar-title">Workspaces</strong>
          <button class="btn btn-sm btn-outline-secondary" data-action="refresh" title="Refresh" aria-label="Refresh" data-bs-toggle="tooltip">
            <span class="spinner-border spinner-border-sm me-1 d-none" data-role="spinner"></span>⟳
          </button>
        </div>
        <div class="collapsed-actions d-flex flex-column align-items-center gap-2" data-role="collapsed-actions">
          <button class="btn btn-sm btn-outline-secondary btn-icon" title="Toggle sidebar" aria-pressed="false" aria-label="Toggle sidebar" data-action="toggle-sidebar" data-bs-toggle="tooltip" data-bs-placement="right">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" class="bi bi-layout-sidebar" viewBox="0 0 16 16">
              <path d="M0 3a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H2a2 2 0 0 1-2-2zm5-1v12h9a1 1 0 0 0 1-1V3a1 1 0 0 0-1-1zM4 2H2a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h2z"/>
            </svg>
          </button>
          <button class="btn btn-sm btn-outline-secondary btn-icon" data-action="refresh" title="Refresh" aria-label="Refresh" data-bs-toggle="tooltip" data-bs-placement="right"><i class="bi bi-arrow-repeat"></i></button>
          <button class="btn btn-sm btn-outline-success btn-icon" data-action="create-test" title="Create workspace" aria-label="Create workspace" data-bs-toggle="tooltip" data-bs-placement="right"><i class="bi bi-plus-circle"></i></button>
          <button class="btn btn-sm btn-outline-primary btn-icon" data-action="wizard" title="Open wizard" aria-label="Open wizard" data-bs-toggle="tooltip" data-bs-placement="right"><i class="bi bi-stars"></i></button>
          <a class="btn btn-sm btn-outline-secondary btn-icon" href="#/workspaces" title="Manage workspaces" aria-label="Manage workspaces" data-bs-toggle="tooltip" data-bs-placement="right"><i class="bi bi-gear"></i></a>
        </div>
        <div class="list-group list-group-flush" data-role="ws-list">
          <div class="list-group-item small text-muted">Loading…</div>
        </div>
        <div class="d-grid gap-2 mt-2" data-role="actions">
          <button class="btn btn-sm btn-success" data-action="create-test">+ Create Test</button>
          <button class="btn btn-sm btn-primary" data-bs-toggle="modal" data-bs-target="#wsWizardGlobal">Wizard…</button>
          <a class="btn btn-sm btn-outline-secondary" href="#/workspaces">Manage…</a>
        </div>
      </div>
    `;
  }

  _renderWorkspaces(wsArr) {
    const list = this.querySelector('[data-role="ws-list"]');
    if (!list) return;
    if (!wsArr || !wsArr.length) { 
      list.innerHTML = '<div class="list-group-item small">No workspaces</div>'; 
      return; 
    }
    const cur = (getState().context && getState().context.workspace) || 'default';
    list.innerHTML = wsArr.map(w => this._renderItem(w, w.slug === cur)).join('');
    // Attach click handlers
    list.querySelectorAll('[data-ws]').forEach(el => {
      el.addEventListener('click', () => this.select(el.getAttribute('data-ws')));
    });
    // Also render offcanvas list if present
    this._renderOffcanvas(wsArr, cur);
    // Load counts only when expanded (list is hidden in collapsed)
    if (!this._isCollapsed()) wsArr.forEach(w => this._loadCounts(w.slug));
    // Setup tooltips for collapsed mode and keyboard nav
    this._applyCollapsedTooltips();
    this._bindKeyboard(list);
    const ocRoot = document.getElementById('offcanvas-ws');
    if (ocRoot) this._bindKeyboard(ocRoot);
  }

  _updateActiveWorkspace(activeWs) {
    const list = this.querySelector('[data-role="ws-list"]');
    if (!list) return;
    // Update active state
    list.querySelectorAll('[data-ws]').forEach(el => {
      const ws = el.getAttribute('data-ws');
      const isActive = ws === activeWs;
      el.classList.toggle('active', isActive);
      el.setAttribute('aria-current', isActive ? 'page' : 'false');
      // Update icon
      const icon = el.querySelector('.bi');
      if (icon) {
        icon.className = isActive ? 'bi bi-record-fill text-primary me-1' : 'bi bi-diagram-3 me-2';
      }
    });
  }

  async refresh() {
    // Deprecated: kept for backward compat, now delegates to action
    await loadWorkspaces();
  }

  _renderItem(w, active) {
    const icon = active ? '<i class="bi bi-record-fill text-primary me-1"></i>' : '<i class="bi bi-diagram-3 me-2"></i>';
    const title = escapeHtml(w.slug);
    return `<div class="list-group-item d-flex align-items-center ${active ? 'active' : ''}" role="button" tabindex="0" data-ws="${escapeHtml(w.slug)}" aria-current="${active?'page':'false'}" title="${title}">${icon}<span class="ws-label text-truncate">${escapeHtml(w.slug)}</span><span class="ms-auto" id="sb-count-${escapeAttr(w.slug)}">…</span></div>`;
  }

  _renderOffcanvas(wsArr, cur) {
    const oc = document.getElementById('offcanvas-ws');
    if (!oc) return;
    oc.innerHTML = wsArr.map(w => {
      const active = w.slug === cur;
      const dot = active ? '<i class="bi bi-record-fill text-primary me-1"></i>' : '<i class="bi bi-diagram-3 me-2"></i>';
      return `<div class="list-group-item d-flex align-items-center ${active?'active':''}" role="button" tabindex="0" data-ws="${escapeHtml(w.slug)}" title="${escapeHtml(w.slug)}">${dot}<span class="ws-label text-truncate">${escapeHtml(w.slug)}</span></div>`;
    }).join('');
    oc.querySelectorAll('[data-ws]').forEach(el => {
      el.addEventListener('click', () => this.select(el.getAttribute('data-ws')));
    });
  }

  async _loadCounts(slug) {
    try {
      const res = await apiFetch('/workspaces/' + encodeURIComponent(slug) + '/stats');
      const st = await res.json();
      if (!res.ok) throw new Error('failed');
      const cnt = st && st.counts ? st.counts : {};
      const badge = document.getElementById('sb-count-' + slug);
      if (badge) {
        const docs = Number(cnt.docs || 0);
        const steps = Number(cnt.steps || 0);
        badge.innerHTML = `<span class="badge rounded-pill text-bg-secondary me-1" title="Docs">D:${docs}</span><span class="badge rounded-pill text-bg-secondary" title="Steps">S:${steps}</span>`;
        const item = badge.closest('.list-group-item');
        if (item) {
          if ((docs + steps) > 0) item.classList.add('has-count');
          else item.classList.remove('has-count');
        }
      }
    } catch (_) {}
  }

  async createTest() {
    const slug = 'test-' + Math.random().toString(36).slice(2,8);
    try {
      const res = await apiFetch('/workspaces', {
        method: 'POST', headers: {'content-type':'application/json'},
        body: JSON.stringify({ slug, name: 'Test ' + slug })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data && data.error || 'failed');
      this.select(slug);
      this._toast('Created workspace: ' + slug);
    } catch (e) {
      this._toast('Failed to create');
      if (isDebugEnabled()) log('sidebar', e);
    }
  }

  select(slug) {
    // Dispatch store action (this will update context internally)
    setWorkspace(slug);
    // Hide offcanvas if open
    try {
      const ocEl = document.getElementById('sidebarOffcanvas');
      if (ocEl && window.bootstrap) {
        window.bootstrap.Offcanvas.getOrCreateInstance(ocEl).hide();
      }
    } catch (_) {}
    // Notify (backward compat event)
    emit(document, EV.WORKSPACE_CHANGE, { workspace: slug });
    this.refresh();
    this._toast('Workspace set: ' + slug);
  }

  _toast(msg) { try { emit(document, EV.TOAST, { message: msg }); } catch(_){} }

  _isCollapsed(){ const el = document.getElementById('sidebar'); return !!(el && el.classList.contains('collapsed')); }

  _watchCollapse(){
    try{
      const host = document.getElementById('sidebar'); if (!host) return;
      this._mo = new MutationObserver(() => {
        const c = host.classList.contains('collapsed');
        try { localStorage.setItem('uamm.sidebarCollapsed', c ? '1' : '0'); } catch(_){}
        try { host.setAttribute('aria-expanded', c ? 'false' : 'true'); } catch(_){}
        try { this.querySelectorAll('[data-action="toggle-sidebar"]').forEach(el => el.setAttribute('aria-pressed', c ? 'true' : 'false')); } catch(_){}
        this._applyCollapsedTooltips();
      });
      this._mo.observe(host, { attributes: true, attributeFilter: ['class'] });
    }catch(_){}
  }

  _applyCollapsedTooltips(){
    try{
      const host = document.getElementById('sidebar'); if (!host) return;
      const collapsed = host.classList.contains('collapsed');
      const items = this.querySelectorAll('[data-ws]');
      if (!window.bootstrap) return;
      // For collapsed: remove any list-item tooltips; enable toolbar tooltips only
      if (collapsed) {
        items.forEach(el => {
          const inst = window.bootstrap.Tooltip.getInstance(el);
          if (inst) inst.dispose();
          el.removeAttribute('data-bs-toggle');
        });
      } else {
        // Expanded: ensure no tooltips on list items
        items.forEach(el => {
          const inst = window.bootstrap.Tooltip.getInstance(el);
          if (inst) inst.dispose();
          el.removeAttribute('data-bs-toggle');
        });
      }
      // Collapsed icon toolbar tooltips
      const collapsedBtns = this.querySelectorAll('[data-role="collapsed-actions"] [data-bs-toggle="tooltip"]');
      collapsedBtns.forEach(el => {
        if (collapsed) {
          window.bootstrap.Tooltip.getOrCreateInstance(el, {
            placement: 'right',
            trigger: 'hover focus',
            delay: { show: 300, hide: 100 },
            container: 'body',
          });
        } else {
          const inst = window.bootstrap.Tooltip.getInstance(el);
          if (inst) inst.dispose();
        }
      });
    }catch(_){}
  }

  _bindKeyboard(root){
    try{
      if (!root || root.dataset.kbdBound === '1') return;
      root.dataset.kbdBound = '1';
      root.addEventListener('keydown', (e) => {
        const target = e.target && e.target.closest && e.target.closest('[data-ws]');
        if (!target) return;
        const items = Array.from(root.querySelectorAll('[data-ws]'));
        const idx = items.indexOf(target);
        if (e.key === 'ArrowDown'){
          e.preventDefault();
          const next = items[Math.min(items.length - 1, idx + 1)] || target;
          next.focus();
        } else if (e.key === 'ArrowUp'){
          e.preventDefault();
          const prev = items[Math.max(0, idx - 1)] || target;
          prev.focus();
        } else if (e.key === 'Home'){
          e.preventDefault();
          if (items[0]) items[0].focus();
        } else if (e.key === 'End'){
          e.preventDefault();
          if (items[items.length - 1]) items[items.length - 1].focus();
        } else if (e.key === 'Enter' || e.key === ' '){
          e.preventDefault();
          const ws = target.getAttribute('data-ws');
          if (ws) this.select(ws);
        }
      });
    }catch(_){}
  }
}

function escapeHtml(s) {
  return (s||'').toString().replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c]));
}

function escapeAttr(s) {
  return escapeHtml(s).replace(/"/g, '&quot;');
}

// no-op: slugs are expected to be safe; IDs use raw slug

customElements.define('uamm-sidebar', UammSidebar);
