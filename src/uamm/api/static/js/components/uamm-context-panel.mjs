import { apiFetch } from '../core/api.mjs';
import { getContext } from '../core/context.mjs';
import { EV } from '../core/events.mjs';
import { log, isDebugEnabled } from '../core/debug.mjs';
import { select } from '../core/store.mjs';
import { selectStats, selectContext } from '../core/selectors.mjs';

export class UammContextPanel extends HTMLElement {
  constructor(){
    super();
    this._mounted = false;
    this._domainInputSelector = this.getAttribute('data-domain-input') || '#domain';
  }

  connectedCallback(){
    if (this._mounted) return;
    this._mounted = true;
    this.innerHTML = this._template();
    // Wire events
    const btn = this.querySelector('[data-action="reload-tau"]');
    if (btn) btn.addEventListener('click', () => this.loadTau());
    const inp = this.querySelector('[data-role="domain"]');
    if (inp) inp.addEventListener('change', () => this.loadTau());
    // Subscribe to store stats
    this._statsUnsub = select(selectStats, (stats) => {
      this._setText('[data-role="docs"]', stats.docs || 0);
      this._setText('[data-role="steps"]', stats.steps || 0);
      try {
        const paths = stats.paths || {};
        const text = [paths.docs_dir || '', paths.db_path || ''].filter(Boolean).join(' • ');
        this._setText('[data-role="path"]', text);
      } catch(_){}
    });
    // Subscribe to context
    this._ctxUnsub = select(selectContext, (ctx) => {
      this._setText('[data-role="ws"]', ctx.workspace || 'default');
    });
    // Listen for backward compat events
    this._wsHandler = () => this.refresh();
    this._ctxHandler = () => this.refresh();
    document.addEventListener(EV.WORKSPACE_CHANGE, this._wsHandler);
    document.addEventListener(EV.CONTEXT_CHANGE, this._ctxHandler);
    // Initial load
    this.refresh();
  }

  disconnectedCallback(){
    this._mounted = false;
    // Unsubscribe from store
    if (this._statsUnsub) { try { this._statsUnsub(); } catch(_){} this._statsUnsub = null; }
    if (this._ctxUnsub) { try { this._ctxUnsub(); } catch(_){} this._ctxUnsub = null; }
    // Clean up event listeners
    if (this._wsHandler) {
      document.removeEventListener(EV.WORKSPACE_CHANGE, this._wsHandler);
      this._wsHandler = null;
    }
    if (this._ctxHandler) {
      document.removeEventListener(EV.CONTEXT_CHANGE, this._ctxHandler);
      this._ctxHandler = null;
    }
  }

  _template(){
    return `
      <div class="alert alert-light border d-flex align-items-center gap-3 flex-wrap" role="alert">
        <div><strong>Workspace</strong>: <span data-role="ws">default</span></div>
        <div class="vr"></div>
        <div><strong>Docs</strong>: <span data-role="docs">0</span></div>
        <div><strong>Steps</strong>: <span data-role="steps">0</span></div>
        <div class="text-muted small" data-role="path"></div>
        <div class="ms-auto d-flex align-items-center gap-2">
          <span class="badge text-bg-info" data-role="tau">τ: n/a</span>
          <input data-role="domain" class="form-control form-control-sm" placeholder="domain" value="default" style="width: 140px;" list="uamm-domain-list"/>
          <datalist id="uamm-domain-list"></datalist>
          <button class="btn btn-sm btn-outline-secondary" data-action="reload-tau" title="Refresh τ">⟳</button>
          <button class="btn btn-sm btn-outline-secondary" data-bs-toggle="modal" data-bs-target="#ctxModal" title="Set API Key">Key</button>
        </div>
      </div>
    `;
  }

  async refresh(){
    // Only load domain suggestions and tau — stats come from store via actions
    await Promise.all([ this.loadDomainSuggestions(), this.loadTau() ]);
  }

  async loadStats(ws){
    try{
      const res = await apiFetch('/workspaces/' + encodeURIComponent(ws) + '/stats');
      const data = await res.json();
      if (!res.ok) throw new Error('failed');
      const counts = data.counts || {};
      this._setText('[data-role="docs"]', counts.docs || 0);
      this._setText('[data-role="steps"]', counts.steps || 0);
      const paths = data.paths || {};
      const text = [paths.docs_dir || '', paths.db_path || ''].filter(Boolean).join(' • ');
      this._setText('[data-role="path"]', text);
    } catch(e){ if (isDebugEnabled()) log('ctx-panel', e); }
  }

  async loadDomainSuggestions(){
    try{
      const res = await apiFetch('/metrics');
      const data = await res.json();
      if (!res.ok) throw new Error('failed');
      const byDom = data.by_domain || {};
      const uqDom = data.uq_by_domain || {};
      const keys = Array.from(new Set([...Object.keys(byDom), ...Object.keys(uqDom)])).filter(Boolean).sort();
      const dl = this.querySelector('#uamm-domain-list');
      if (dl) dl.innerHTML = keys.map(k => `<option value="${escapeHtml(k)}">`).join('');
      // If no domain set, pick top by answers
      const domInput = this.querySelector('[data-role="domain"]');
      if (domInput) {
        const cur = (domInput.value || '').trim().toLowerCase();
        if (!cur || cur === 'default') {
          let best = null; let bestCount = -1;
          for (const [dom, stats] of Object.entries(byDom)) {
            const count = stats && stats.answers ? stats.answers : 0;
            if (count > bestCount) { best = dom; bestCount = count; }
          }
          if (best) domInput.value = best;
        }
      }
    } catch(e){ if (isDebugEnabled()) log('ctx-panel', e); }
  }

  _resolveDomain(){
    const inp = this.querySelector('[data-role="domain"]');
    let dom = (inp && inp.value.trim()) || 'default';
    if (!dom || dom === 'default') {
      const ext1 = document.querySelector(this._domainInputSelector);
      if (ext1 && ext1.value) dom = (ext1.value || '').trim();
      const fd = document.getElementById('filter-domain');
      if (fd && fd.value) dom = (fd.value || '').trim();
    }
    return dom || 'default';
  }

  async loadTau(){
    const dom = this._resolveDomain();
    try{
      const res = await apiFetch('/cp/threshold?' + new URLSearchParams({ domain: dom }));
      const data = await res.json();
      if (!res.ok) throw new Error('failed');
      const tau = (data.tau===null||data.tau===undefined) ? 'n/a' : data.tau;
      const chip = this.querySelector('[data-role="tau"]');
      if (chip) {
        chip.textContent = 'τ(' + (dom||'default') + '): ' + tau;
        chip.classList.remove('text-bg-secondary','text-bg-info','text-bg-success');
        chip.classList.add((tau==='n/a')?'text-bg-secondary':'text-bg-success');
      }
    } catch(e){
      const chip = this.querySelector('[data-role="tau"]');
      if (chip) {
        chip.textContent = 'τ: n/a';
        chip.classList.remove('text-bg-success','text-bg-info');
        chip.classList.add('text-bg-secondary');
      }
      if (isDebugEnabled()) log('ctx-panel', e);
    }
  }

  _setText(sel, val){
    const el = this.querySelector(sel);
    if (el) el.textContent = String(val);
  }
}

function escapeHtml(s){
  return (s||'').toString().replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c]));
}

customElements.define('uamm-context-panel', UammContextPanel);
