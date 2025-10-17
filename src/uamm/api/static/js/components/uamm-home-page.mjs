import { apiFetch } from '../core/api.mjs';
import { getContext, setContext } from '../core/context.mjs';
import { EV, emit } from '../core/events.mjs';
import { select } from '../core/store.mjs';
import { selectWorkspaces, selectSettings } from '../core/selectors.mjs';
import { loadWorkspaces, loadSettings, applySettings } from '../core/actions.mjs';

export class UammHomePage extends HTMLElement {
  constructor(){ super(); this._mounted=false; }
  connectedCallback(){
    if (this._mounted) return;
    this._mounted=true;
    this.innerHTML=this._template();
    this._wire();
    this._prefill();
    // Reactive: settings/workspaces
    this._settingsUnsub = select(selectSettings, (st) => {
      const s = st.data || {};
      const set = (id,val,lid)=>{ const el=this.querySelector('#'+id); if(el){ el.value = val; const l=this.querySelector('#'+lid); if(l) l.textContent = String(val); } };
      if (Object.keys(s).length){
        set('hs-thresh', s.accept_threshold ?? 0.85, 'hs-thresh-val');
        set('hs-delta', s.borderline_delta ?? 0.05, 'hs-delta-val');
        set('hs-snne', s.snne_samples ?? 5, 'hs-snne-val');
      }
    });
    this._wsListUnsub = select(selectWorkspaces, (data) => {
      const el = this.querySelector('#hs-ws-list'); if (!el) return;
      if (data.loading) { el.innerHTML = '<div class="list-group-item text-muted small">Loading…</div>'; return; }
      const ws = data.list || [];
      if (!ws.length){ el.innerHTML = '<div class="list-group-item small">No workspaces yet. Create one.</div>'; return; }
      el.innerHTML = ws.map(w => `<button type="button" class="list-group-item list-group-item-action d-flex justify-content-between align-items-center" data-ws="${this._esc(w.slug)}">${this._esc(w.slug)}<span class="badge text-bg-light">${this._esc(w.name||'')}</span></button>`).join('');
      el.querySelectorAll('[data-ws]').forEach(b => b.addEventListener('click', ()=> this._selectWs(b.getAttribute('data-ws'))));
    });
    try { loadSettings(); } catch(_){}
    try { loadWorkspaces(); } catch(_){}
    // Listen for workspace/context changes
    this._wsHandler = () => this._loadWorkspaces();
    document.addEventListener(EV.WORKSPACE_CHANGE, this._wsHandler);
    document.addEventListener(EV.CONTEXT_CHANGE, this._wsHandler);
  }
  disconnectedCallback(){
    this._mounted=false;
    if (this._wsHandler) {
      document.removeEventListener(EV.WORKSPACE_CHANGE, this._wsHandler);
      document.removeEventListener(EV.CONTEXT_CHANGE, this._wsHandler);
      this._wsHandler = null;
    }
  }

  _template(){
    return `
<div class="card mb-3">
  <div class="card-body">
    <h3 class="mb-1">Welcome to UAMM</h3>
    <p id="home-hero-sub" class="text-muted mb-3">Choose a workspace to get started.</p>
    <div class="d-flex gap-2 flex-wrap">
      <a id="hero-open" class="btn btn-primary disabled" href="#" aria-disabled="true">Open Playground</a>
      <a id="hero-docs" class="btn btn-outline-secondary disabled" href="#" aria-disabled="true">Manage Docs</a>
      <a id="hero-steps" class="btn btn-outline-secondary disabled" href="#" aria-disabled="true">Recent Steps</a>
      <a class="btn btn-outline-secondary" href="#/workspaces">Manage Workspaces</a>
    </div>
  </div>
</div>
<div class="row">
  <div class="col-lg-3">
    <div class="card mb-3">
      <div class="card-header d-flex align-items-center">
        <span>Workspaces</span>
        <button class="btn btn-sm btn-outline-secondary ms-auto" data-action="ws-refresh">Refresh</button>
      </div>
      <div class="list-group list-group-flush" id="hs-ws-list">
        <div class="list-group-item text-muted small">Loading…</div>
      </div>
      <div class="card-body d-grid gap-2">
        <button class="btn btn-sm btn-outline-success" data-action="ws-create-test">Create Test Workspace</button>
        <button class="btn btn-sm btn-outline-primary" data-bs-toggle="modal" data-bs-target="#wsWizardGlobal">Advanced…</button>
      </div>
    </div>
  </div>
  <div class="col-lg-9">
    <div class="card mb-3">
      <div class="card-header">Quick Start</div>
      <div class="card-body">
        <div class="row g-2 align-items-end">
          <div class="col-md-9">
            <label class="form-label small">Question</label>
            <input id="hs-q" class="form-control" placeholder="Ask something…">
          </div>
          <div class="col-md-2">
            <label class="form-label small">Domain</label>
            <input id="hs-domain" class="form-control" placeholder="default" value="default">
          </div>
          <div class="col-md-1 d-grid">
            <button class="btn btn-primary" data-action="go">Go</button>
          </div>
        </div>
        <div class="mt-3">
          <a class="btn btn-sm btn-outline-secondary" href="#/rag">Manage Docs</a>
          <a class="btn btn-sm btn-outline-secondary" href="#/obs">Recent Steps</a>
          <a class="btn btn-sm btn-outline-secondary" href="#/cp">CP Thresholds</a>
          <a class="btn btn-sm btn-outline-secondary" href="#/evals">Evals & Tuning</a>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-header">Configuration</div>
      <div class="card-body">
        <div class="row g-3">
          <div class="col-md-4">
            <label class="form-label">Accept Threshold</label>
            <input id="hs-thresh" class="form-range" type="range" min="0" max="1" step="0.01" value="0.85">
            <div class="small text-muted">Higher = more conservative accept.</div>
            <div><code id="hs-thresh-val">0.85</code></div>
          </div>
          <div class="col-md-4">
            <label class="form-label">Borderline Δ</label>
            <input id="hs-delta" class="form-range" type="range" min="0" max="0.5" step="0.01" value="0.05">
            <div class="small text-muted">Wider Δ triggers more refinements.</div>
            <div><code id="hs-delta-val">0.05</code></div>
          </div>
          <div class="col-md-4">
            <label class="form-label">SNNE Samples</label>
            <input id="hs-snne" class="form-range" type="range" min="1" max="10" step="1" value="5">
            <div class="small text-muted">More samples → smoother uncertainty.</div>
            <div><code id="hs-snne-val">5</code></div>
          </div>
        </div>
        <div class="mt-2 d-flex gap-2">
          <button class="btn btn-outline-primary" data-action="apply-config">Apply (Global)</button>
          <a class="btn btn-outline-secondary" href="/ui/docs#evals">Learn more</a>
        </div>
        <div id="hs-config-status" class="small text-muted mt-2"></div>
      </div>
    </div>
  </div>
</div>`;
  }

  _wire(){
    const $ = (s)=>this.querySelector(s);
    this._on('[data-action="ws-refresh"]','click',()=>loadWorkspaces());
    this._on('[data-action="ws-create-test"]','click',()=>this._createTest());
    this._on('[data-action="go"]','click',()=>this._go());
    const st = $('#hs-thresh'), sd=$('#hs-delta'), ss=$('#hs-snne');
    if (st) st.addEventListener('input', ()=> this._sync('hs-thresh','hs-thresh-val'));
    if (sd) sd.addEventListener('input', ()=> this._sync('hs-delta','hs-delta-val'));
    if (ss) ss.addEventListener('input', ()=> this._sync('hs-snne','hs-snne-val'));
    this._on('[data-action="apply-config"]','click',()=>this._applyConfig());
  }

  _prefill(){
    try{
      const ws = (getContext().workspace || 'default');
      const sub = this.querySelector('#home-hero-sub');
      const open = this.querySelector('#hero-open');
      const docs = this.querySelector('#hero-docs');
      const steps = this.querySelector('#hero-steps');
      if (sub) sub.textContent = ws ? `Current workspace: ${ws}` : 'Choose a workspace to get started.';
      const setBtn = (el, href)=>{ if (!el) return; el.href = href; el.classList.remove('disabled'); el.setAttribute('aria-disabled','false'); };
      // Keep Playground as server route; other links use SPA routes
      setBtn(open, '/ui?ws=' + encodeURIComponent(ws));
      setBtn(docs, '#/rag');
      setBtn(steps, '#/obs');
    }catch(_){}
  }

  async _loadSettings(){ try { await loadSettings(); } catch(_){} }

  async _loadWorkspaces(){ try { await loadWorkspaces(); } catch(_){} }

  async _createTest(){
    const slug = 'test-' + Math.random().toString(36).slice(2,8);
    try{
      const res = await apiFetch('/workspaces', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({slug, name: 'Test '+slug})});
      const data = await res.json(); if(!res.ok) throw new Error(data.error||'failed');
      this._selectWs(slug);
      this._loadWorkspaces();
      this._toast('Workspace set: ' + slug);
    }catch(_){ this._toast('Failed to create'); }
  }

  _selectWs(slug){ setContext({ workspace: slug }); this._prefill(); }
  _go(){
    const q = (this.querySelector('#hs-q').value||'').trim();
    const dom = (this.querySelector('#hs-domain').value||'').trim() || 'default';
    const ws = getContext().workspace || 'default';
    const params = new URLSearchParams({ ws, domain: dom });
    if (q) { params.set('q', q); params.set('autostart','1'); }
    window.location.href = '/ui?' + params.toString();
  }

  async _applyConfig(){
    const changes = {
      accept_threshold: parseFloat(this.querySelector('#hs-thresh').value),
      borderline_delta: parseFloat(this.querySelector('#hs-delta').value),
      snne_samples: parseInt(this.querySelector('#hs-snne').value)
    };
    const el = this.querySelector('#hs-config-status');
    el.textContent = 'Applying…';
    try{ await applySettings(changes); el.textContent = 'Applied'; }catch(_){ el.textContent = 'Error applying settings'; }
  }

  _sync(id,lid){ const v = this.querySelector('#'+id).value; const l=this.querySelector('#'+lid); if(l) l.textContent = v; }
  _on(sel,ev,fn){ const el=this.querySelector(sel); if (el) el.addEventListener(ev, fn); }
  disconnectedCallback(){ if (this._wsListUnsub) { try { this._wsListUnsub(); } catch(_){} this._wsListUnsub=null; } if (this._settingsUnsub) { try { this._settingsUnsub(); } catch(_){} this._settingsUnsub=null; } }
  _esc(s){ return (s||'').toString().replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c])); }
  _toast(msg){ try{ emit(document, EV.TOAST, { message: msg }); }catch(_){} }
}

customElements.define('uamm-home-page', UammHomePage);
