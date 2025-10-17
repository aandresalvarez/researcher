import { apiFetch, apiFetchWith } from '../core/api.mjs';
import { EV, emit } from '../core/events.mjs';
import { select } from '../core/store.mjs';
import { selectEvalsSuites, selectEvalsRuns, selectEvalsReport, selectEvalsProposal, selectEvalsAdhoc } from '../core/selectors.mjs';
import { evalsLoadSuites, evalsLoadRuns, evalsViewRun, evalsRunSuites, tunerPropose, tunerApply } from '../core/actions.mjs';

export class UammEvalsPage extends HTMLElement {
  constructor(){ super(); this._mounted=false; this._lastProposalId=null; }
  connectedCallback(){ 
    if (this._mounted) return; 
    this._mounted=true; 
    this.innerHTML = this._template(); 
    this._wire(); 
    // Reactive subscriptions
    this._suitesUnsub = select(selectEvalsSuites, (s) => this._renderSuites(s));
    this._runsUnsub = select(selectEvalsRuns, (r) => this._renderRuns(r));
    this._reportUnsub = select(selectEvalsReport, (rp) => this._renderReport(rp));
    this._proposalUnsub = select(selectEvalsProposal, (p) => this._renderProposal(p));
    this._adhocUnsub = select(selectEvalsAdhoc, (a) => this._renderAdhoc(a));
    evalsLoadSuites();
    evalsLoadRuns();
    this.addItem(); 
    // Listen for workspace/context changes
    this._wsHandler = () => { evalsLoadSuites(); evalsLoadRuns(); };
    document.addEventListener(EV.WORKSPACE_CHANGE, this._wsHandler);
    document.addEventListener(EV.CONTEXT_CHANGE, this._wsHandler);
  }
  disconnectedCallback(){ 
    this._mounted=false; 
    if (this._suitesUnsub) { try { this._suitesUnsub(); } catch(_){} this._suitesUnsub=null; }
    if (this._runsUnsub) { try { this._runsUnsub(); } catch(_){} this._runsUnsub=null; }
    if (this._reportUnsub) { try { this._reportUnsub(); } catch(_){} this._reportUnsub=null; }
    if (this._proposalUnsub) { try { this._proposalUnsub(); } catch(_){} this._proposalUnsub=null; }
    if (this._adhocUnsub) { try { this._adhocUnsub(); } catch(_){} this._adhocUnsub=null; }
    if (this._wsHandler) {
      document.removeEventListener(EV.WORKSPACE_CHANGE, this._wsHandler);
      document.removeEventListener(EV.CONTEXT_CHANGE, this._wsHandler);
      this._wsHandler = null;
    }
  }

  _template(){
    return `
<div class="row">
  <div class="col-lg-6">
    <div class="card mb-3">
      <div class="card-header d-flex align-items-center">
        <span>Run Suites</span>
        <button class="btn btn-sm btn-outline-secondary ms-auto" data-action="load-suites">Load Suites</button>
      </div>
      <div class="card-body">
        <div id="suites" class="small text-muted">Loading…</div>
        <div class="form-check form-switch mt-2">
          <input class="form-check-input" type="checkbox" role="switch" id="update-cp" checked>
          <label class="form-check-label" for="update-cp">Update CP reference</label>
        </div>
        <div class="form-check form-switch mt-2">
          <input class="form-check-input" type="checkbox" role="switch" id="use-llm">
          <label class="form-check-label" for="use-llm">Run with LLMs</label>
          <span id="llm-status" class="ms-2 small text-muted">checking…</span>
        </div>
        <div class="mt-2"><input id="admin-key-e" type="password" class="form-control" placeholder="Admin API Key (optional)"></div>
        <div class="mt-2 d-flex gap-2">
          <button class="btn btn-primary" data-action="run-suites">Run Selected</button>
        </div>
        <div class="mt-3" id="suite-result"></div>
      </div>
    </div>
    <div class="card mb-3">
      <div class="card-header">Tuner</div>
      <div class="card-body">
        <div class="row g-2">
          <div class="col-md-6"><input id="target-accept" class="form-control" placeholder="accept_min (e.g., 0.65)"></div>
          <div class="col-md-6"><input id="target-abstain" class="form-control" placeholder="abstain_max (e.g., 0.35)"></div>
          <div class="col-md-6"><input id="target-false" class="form-control" placeholder="false_accept_max (e.g., 0.05)"></div>
          <div class="col-md-6"><input id="target-lat" class="form-control" placeholder="latency_p95_max (s, e.g., 2.5)"></div>
        </div>
        <div class="mt-2 d-flex gap-2">
          <button class="btn btn-outline-primary" data-action="propose">Propose</button>
          <button class="btn btn-outline-success" data-action="apply" id="btn-apply" disabled>Apply</button>
        </div>
        <div class="mt-2" id="tuner-proposal"></div>
      </div>
    </div>
  </div>
  <div class="col-lg-6">
    <div class="card mb-3">
      <div class="card-header">Ad-hoc Items</div>
      <div class="card-body">
        <div id="items" class="mb-2"></div>
        <div class="d-flex gap-2 mb-2">
          <button class="btn btn-sm btn-outline-secondary" data-action="add-item">Add</button>
          <button class="btn btn-sm btn-outline-danger" data-action="clear-items">Clear</button>
        </div>
        <div class="row g-2">
          <div class="col-md-4"><input id="adhoc-ref" class="form-control" placeholder="max_refinements" value="1"></div>
          <div class="col-md-4"><input id="adhoc-turn" class="form-control" placeholder="tool_budget_per_turn" value="2"></div>
          <div class="col-md-4"><input id="adhoc-refb" class="form-control" placeholder="tool_budget_per_refinement" value="2"></div>
        </div>
        <div class="form-check form-switch mt-2">
          <input class="form-check-input" type="checkbox" role="switch" id="adhoc-cp" checked>
          <label class="form-check-label" for="adhoc-cp">Enable CP + use CP decision</label>
        </div>
        <div class="mt-2 d-flex gap-2">
          <button class="btn btn-outline-primary" data-action="run-adhoc">Run Ad-hoc</button>
        </div>
        <div class="mt-3" id="adhoc-result"></div>
      </div>
    </div>
    <div class="card">
      <div class="card-header d-flex align-items-center">
        <span>Recent Runs</span>
        <button class="btn btn-sm btn-outline-secondary ms-auto" data-action="load-runs">Refresh</button>
      </div>
      <div class="card-body"><div id="runs" class="small text-muted">Loading…</div></div>
    </div>
  </div>
</div>`;
  }

  _wire(){
    this._on('[data-action="load-suites"]','click',()=>evalsLoadSuites());
    this._on('[data-action="run-suites"]','click',()=>this.runSuites());
    this._on('[data-action="propose"]','click',()=>this.proposeTuning());
    this._on('[data-action="apply"]','click',()=>tunerApply());
    this._on('[data-action="add-item"]','click',()=>this.addItem());
    this._on('[data-action="clear-items"]','click',()=>this.clearItems());
    this._on('[data-action="run-adhoc"]','click',()=>this.runAdhoc());
    this._on('[data-action="load-runs"]','click',()=>evalsLoadRuns());
    this._checkEnv();
  }

  async _checkEnv(){
    try{
      const res = await fetch('/evals/env');
      const data = await res.json();
      const st = this.querySelector('#llm-status');
      const toggle = this.querySelector('#use-llm');
      const ok = !!(data && data.llm_available);
      if (toggle) toggle.disabled = !ok;
      if (st) st.textContent = ok ? 'LLM available' : 'LLM unavailable';
    }catch(_){
      const st = this.querySelector('#llm-status');
      if (st) st.textContent = 'LLM unavailable';
      const toggle = this.querySelector('#use-llm');
      if (toggle) toggle.disabled = true;
    }
  }

  _llmEnabled(){ const t=this.querySelector('#use-llm'); return !!(t && t.checked && !t.disabled); }

  // Removed older runSuites (batch) in favor of sequential runner below

  

  _renderSuites(state){
    const el = this.querySelector('#suites');
    if (!el) return;
    if (state.loading) { el.textContent = 'Loading…'; return; }
    const suites = state.items || [];
    if (!suites.length) { el.textContent = 'No suites'; return; }
    el.innerHTML = suites.map(s => `
      <div class="form-check">
        <input class="form-check-input suite-select" type="checkbox" id="suite-${this._escape(s.id||s)}" value="${this._escape(s.id||s)}">
        <label class="form-check-label" for="suite-${this._escape(s.id||s)}"><strong>${this._escape(s.id||s)}</strong> ${s.label?('— '+this._escape(s.label)):''} ${s.category?('<span class=\"text-muted\">['+this._escape(s.category)+']</span>'):''}</label>
        ${s.description?('<div class="small text-muted">'+this._escape(s.description)+'</div>'):''}
      </div>`).join('');
  }

  async runSuites(){
    const ids = Array.from(this.querySelectorAll('.suite-select:checked')).map(el => el.value);
    if (!ids.length) { this._toast('Select one or more suites'); return; }
    const update_cp = this.querySelector('#update-cp').checked;
    const key = (this._val('#admin-key-e') || '').trim();
    const out = this.querySelector('#suite-result');
    const prog = this._ensureProgress();
    if (out) out.innerHTML = '';
    // Stream suites via SSE for per-item progress
    try{
      const params = new URLSearchParams({ suites: ids.join(','), update_cp: update_cp? '1':'0', llm: this._llmEnabled()? '1':'0' });
      const url = '/evals/run/stream?' + params.toString();
      const outlet = out;
      const suiteBlocks = {};
      const { source, close } = await import('../core/api.mjs').then(m => m.sse(url, {
        on: {
          ready: (d)=>{ if (prog) prog.textContent = 'Starting…'; },
          suite_start: (d)=>{ if(!d||!d.suite_id) return; if (prog) prog.textContent = `Running ${d.suite_id}…`; const wrap = this._suiteBlock({ suite_id: d.suite_id, label: d.label||'', metrics:{} , records: []}); suiteBlocks[d.suite_id] = wrap; if (outlet) outlet.appendChild(wrap); },
          item: (d)=>{ if(!d||!d.suite_id) return; const wrap = suiteBlocks[d.suite_id]; if (!wrap) return; this._appendItemRow(wrap, d.record, d.index); this._updateSuiteSummary(wrap, d.metrics); if (prog) prog.textContent = `Running ${d.suite_id}… ${d.index}/${d.total}`; },
          suite_done: (d)=>{ if(!d||!d.suite_id) return; const wrap = suiteBlocks[d.suite_id]; if (!wrap) return; this._updateSuiteSummary(wrap, d.metrics); if (prog) prog.textContent = `Completed ${d.suite_id}`; },
          final: (d)=>{ if (prog) prog.textContent = `Completed ${ids.length}/${ids.length}`; close(); }
        },
        onError: ()=>{ if (prog) prog.textContent = 'Error'; }
      }));
    }catch(_){ this._toast('Streaming not available'); }
  }

  _ensureProgress(){
    let el = this.querySelector('#run-progress');
    if (!el){
      el = document.createElement('div');
      el.id = 'run-progress';
      el.className = 'small text-muted mb-2';
      const target = this.querySelector('#suite-result');
      const parent = target ? target.parentElement : this;
      if (parent && target) parent.insertBefore(el, target);
      else (document.body).appendChild(el);
    }
    return el;
  }

  _suiteBlock(suite){
    const m = suite.metrics || {};
    const wrap = document.createElement('div');
    wrap.className = 'border rounded p-2 mb-3';
    wrap.innerHTML = `
      <div class="d-flex align-items-center gap-2 mb-1">
        <h6 class="mb-0">${this._escape(suite.suite_id||'')}</h6>
        <span class="small text-muted">${suite.label?this._escape(suite.label):''}</span>
        <div class="ms-auto d-flex gap-2">
          <button class="btn btn-sm btn-outline-secondary" data-action="copy-json">Copy JSON</button>
          <button class="btn btn-sm btn-outline-secondary" data-action="download-csv">Download CSV</button>
        </div>
      </div>
      <div class="small text-muted mb-2" data-role="summary">Accept ${this._pct(m.accept_rate)} • False Accept ${this._pct(m.false_accept_rate)} • Accuracy ${this._pct(m.accuracy)} • Avg S ${this._num(m.avg_score)}</div>
      <div class="table-responsive"><table class="table table-sm table-hover align-middle mb-0" data-role="table">
        <thead><tr><th>#</th><th>Domain</th><th>Question</th><th>S</th><th>Accepted</th><th>Correct</th><th>CP</th><th>Tools</th><th>Plan+</th><th>Faith</th></tr></thead>
        <tbody>${(suite.records||[]).map((r,idx)=>`<tr><td>${idx+1}</td><td>${this._escape(r.domain||'')}</td><td class="text-truncate" style="max-width:420px;" title="${this._escape(r.question||'')}">${this._escape(r.question||'')}</td><td>${this._num(r.S)}</td><td>${r.accepted}</td><td>${r.correct}</td><td>${r.cp_accept===null?'n/a':r.cp_accept}</td><td>${r.tools||0}</td><td>${r.planning_improved||false}</td><td>${r.faithfulness===null?'n/a':this._num(r.faithfulness)}</td></tr>`).join('')}</tbody>
      </table></div>
    `;
    // Wire buttons
    const copyBtn = wrap.querySelector('[data-action="copy-json"]');
    if (copyBtn) copyBtn.addEventListener('click', () => { try { navigator.clipboard.writeText(JSON.stringify(suite, null, 2)); this._toast('Copied'); } catch(_){} });
    const dlBtn = wrap.querySelector('[data-action="download-csv"]');
    if (dlBtn) dlBtn.addEventListener('click', () => this._downloadCSV(`${suite.suite_id||'suite'}.csv`, suite.records||[]));
    return wrap;
  }

  _appendItemRow(wrap, r, index){
    try{
      const table = wrap.querySelector('table[data-role="table"] tbody');
      if (!table || !r) return;
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${index}</td><td>${this._escape(r.domain||'')}</td><td class="text-truncate" style="max-width:420px;" title="${this._escape(r.question||'')}">${this._escape(r.question||'')}</td><td>${this._num(r.S)}</td><td>${r.accepted}</td><td>${r.correct}</td><td>${r.cp_accept===null?'n/a':r.cp_accept}</td><td>${r.tools||0}</td><td>${r.planning_improved||false}</td><td>${r.faithfulness===null?'n/a':this._num(r.faithfulness)}</td>`;
      table.appendChild(tr);
    }catch(_){ }
  }

  _updateSuiteSummary(wrap, m){
    try{
      const el = wrap.querySelector('[data-role="summary"]');
      if (!el || !m) return;
      el.textContent = `Accept ${this._pct(m.accept_rate)} • False Accept ${this._pct(m.false_accept_rate)} • Accuracy ${this._pct(m.accuracy)} • Avg S ${this._num(m.avg_score)}`;
    }catch(_){ }
  }

  _downloadCSV(name, records){
    try{
      const cols = ['domain','question','S','accepted','correct','cp_accept','tools','planning_improved','faithfulness'];
      const esc = (v)=>`"${String(v??'').replace(/"/g,'""')}"`;
      const lines = [cols.join(',')].concat((records||[]).map(r=>cols.map(c=>esc(r[c])).join(',')));
      const blob = new Blob([lines.join('\n')], {type:'text/csv'});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href=url; a.download=name; document.body.appendChild(a); a.click(); setTimeout(()=>{ URL.revokeObjectURL(url); a.remove(); }, 100);
    }catch(_){ this._toast('Failed to download CSV'); }
  }

  addItem(){
    const wrap = this.querySelector('#items');
    const idx = (wrap && wrap.children.length) ? wrap.children.length + 1 : 1;
    const div = document.createElement('div');
    div.className = 'border rounded p-2 mb-2';
    div.innerHTML = `
      <div class="row g-2 align-items-end">
        <div class="col-md-8"><label class="form-label small">Question</label><input class="form-control item-q"></div>
        <div class="col-md-2"><label class="form-label small">Domain</label><input class="form-control item-dom" placeholder="default"></div>
        <div class="col-md-2"><div class="form-check"><input class="form-check-input item-ok" type="checkbox" id="ok-${idx}"><label class="form-check-label" for="ok-${idx}">Correct</label></div></div>
      </div>`;
    if (wrap) wrap.appendChild(div);
  }
  clearItems(){ const wrap = this.querySelector('#items'); if (wrap) wrap.innerHTML=''; }

  async runAdhoc(){
    const cards = Array.from(this.querySelectorAll('#items .border'));
    if (!cards.length) { this._toast('Add at least one item'); return; }
    const items = cards.map(c => ({ question: (c.querySelector('.item-q').value||'').trim(), domain: (c.querySelector('.item-dom').value||'').trim() || 'default', correct: c.querySelector('.item-ok').checked }));
    const body = {
      items,
      max_refinements: parseInt(this._val('#adhoc-ref') || '0'),
      tool_budget_per_turn: parseInt(this._val('#adhoc-turn') || '0'),
      tool_budget_per_refinement: parseInt(this._val('#adhoc-refb') || '0'),
      cp_enabled: this._el('#adhoc-cp').checked,
      use_cp_decision: this._el('#adhoc-cp').checked,
    };
    const out = this.querySelector('#adhoc-result');
    if (out) out.textContent = 'Running…';
    // Try streaming first; fallback to POST if unavailable
    try{
      const params = new URLSearchParams({
        items: JSON.stringify(items),
        max_refinements: String(body.max_refinements||0),
        tool_budget_per_turn: String(body.tool_budget_per_turn||0),
        tool_budget_per_refinement: String(body.tool_budget_per_refinement||0),
        cp_enabled: body.cp_enabled ? '1' : '0',
      });
      if (body.use_cp_decision !== undefined) params.set('use_cp_decision', body.use_cp_decision ? '1' : '0');
      if (this._llmEnabled()) params.set('llm', '1');
      const url = '/evals/run/adhoc/stream?' + params.toString();
      const prog = this._ensureProgress();
      // Prepare output block
      if (out) out.innerHTML = '';
      const wrap = this._adhocBlock();
      if (out) out.appendChild(wrap);
      const { sse } = await import('../core/api.mjs');
      const { close } = sse(url, {
        on: {
          ready: (d)=>{ if (prog) prog.textContent = 'Starting…'; },
          item: (d)=>{ if (!d) return; this._appendItemRow(wrap, d.record, d.index); this._updateSuiteSummary(wrap, d.metrics); if (prog) prog.textContent = `Running… ${d.index}/${d.total}`; },
          final: (d)=>{ if (prog) prog.textContent = `Completed ${d && d.count ? d.count : items.length}/${items.length}`; try{ import('../core/actions.mjs').then(m => m.evalsLoadRuns && m.evalsLoadRuns()); } catch(_){} close(); },
        },
        onError: ()=>{ if (prog) prog.textContent = 'Error'; }
      });
      return;
    }catch(_){ /* fall back */ }
    // Fallback non-streaming
    if (this._llmEnabled()) body.llm_enabled = true;
    try{ const { evalsRunAdhoc } = await import('../core/actions.mjs'); await evalsRunAdhoc(body); }catch(e){ if (out) out.textContent='Error running ad-hoc'; }
  }

  _adhocBlock(){
    const wrap = document.createElement('div');
    wrap.className = 'border rounded p-2';
    wrap.innerHTML = `
      <div class="small text-muted mb-2" data-role="summary">Starting…</div>
      <div class="table-responsive"><table class="table table-sm table-hover align-middle mb-0" data-role="table">
        <thead><tr><th>#</th><th>Domain</th><th>Question</th><th>S</th><th>Accepted</th><th>Correct</th><th>CP</th><th>Tools</th><th>Plan+</th><th>Faith</th></tr></thead>
        <tbody></tbody>
      </table></div>
    `;
    return wrap;
  }

  _renderRuns(state){
    const el = this.querySelector('#runs');
    if (!el) return;
    if (state.loading) { el.textContent = 'Loading…'; return; }
    const runs = state.items || [];
    if (!runs.length) { el.textContent = 'No runs'; return; }
    el.innerHTML = '<ul class="list-unstyled mb-0">' + runs.map(r => {
      const d = new Date((r.ts||0)*1000);
      return `<li class="mb-2"><code>${this._escape(r.run_id)}</code> — suites ${r.suites} — <span class="text-muted">${d.toLocaleString()}</span> <button class="btn btn-sm btn-outline-secondary ms-2" data-action="view-run" data-id="${this._escape(r.run_id)}">View</button></li>`;
    }).join('') + '</ul>';
    this.querySelectorAll('[data-action=\"view-run\"]').forEach(b => b.addEventListener('click', () => evalsViewRun(b.getAttribute('data-id'))));
  }

  _renderReport(state){
    const out = this.querySelector('#suite-result');
    if (!out) return;
    if (state.loading) { out.textContent = 'Loading…'; return; }
    const data = state.data || {};
    const suites = data.suites || [];
    if (!suites.length) { out.textContent = 'Run empty'; return; }
    out.innerHTML = '<div class="table-responsive"><table class="table table-sm"><thead><tr><th>Suite</th><th>Accept</th><th>False Accept</th><th>Accuracy</th><th>Avg S</th></tr></thead><tbody>' +
      suites.map(s => { const m = s.metrics||{}; return `<tr><td>${this._escape(s.suite_id||'custom')}</td><td>${this._pct(m.accept_rate)}</td><td>${this._pct(m.false_accept_rate)}</td><td>${this._pct(m.accuracy)}</td><td>${this._num(m.avg_score)}</td></tr>`; }).join('') + '</tbody></table></div>';
  }

  _renderAdhoc(a){
    const out = this.querySelector('#adhoc-result');
    if (!out) return;
    if (a.loading) { out.textContent = 'Running…'; return; }
    const data = a.data || {};
    if (!data || !data.metrics) { out.textContent = ''; return; }
    const m = data.metrics || {};
    out.innerHTML = `<div class="small text-muted">run_id ${this._escape(data.run_id||'')}</div>
      <div><strong>Accept</strong> ${this._pct(m.accept_rate)} • <strong>False Accept</strong> ${this._pct(m.false_accept_rate)} • <strong>Accuracy</strong> ${this._pct(m.accuracy)} • <strong>Avg S</strong> ${this._num(m.avg_score)}</div>`;
    if ((data.records||[]).length) {
      out.innerHTML += '<div class="table-responsive mt-2"><table class="table table-sm"><thead><tr><th>Domain</th><th>S</th><th>Accepted</th><th>Correct</th><th>CP</th><th>Tools</th><th>Plan+</th><th>Faith</th></tr></thead><tbody>' +
        data.records.map(r => `<tr><td>${this._escape(r.domain||'')}</td><td>${this._num(r.S)}</td><td>${r.accepted}</td><td>${r.correct}</td><td>${r.cp_accept===null?'n/a':r.cp_accept}</td><td>${r.tools||0}</td><td>${r.planning_improved||false}</td><td>${r.faithfulness===null?'n/a':this._num(r.faithfulness)}</td></tr>`).join('') + '</tbody></table></div>';
    }
  }

  async proposeTuning(){
    const ids = Array.from(this.querySelectorAll('.suite-select:checked')).map(el => el.value);
    if (!ids.length) { this._toast('Select one or more suites'); return; }
    const targets = {};
    const a = parseFloat(this._val('#target-accept') || ''); if (!isNaN(a)) targets.accept_min = a;
    const b = parseFloat(this._val('#target-abstain') || ''); if (!isNaN(b)) targets.abstain_max = b;
    const c = parseFloat(this._val('#target-false') || ''); if (!isNaN(c)) targets.false_accept_max = c;
    const l = parseFloat(this._val('#target-lat') || ''); if (!isNaN(l)) targets.latency_p95_max = l;
    const out = this.querySelector('#tuner-proposal');
    if (out) out.textContent = 'Proposing…';
    try{ await tunerPropose({ suiteIds: ids, targets }); }catch(e){ if (out) out.textContent='Error proposing'; }
  }

  _renderProposal(p){
    const out = this.querySelector('#tuner-proposal');
    const applyBtn = this.querySelector('#btn-apply');
    if (applyBtn) applyBtn.disabled = !p.id;
    if (!out) return;
    if (p.loading) { out.textContent = 'Proposing…'; return; }
    if (!p.id) { out.textContent = ''; return; }
    const patch = p.patch || {};
    const canary = p.canary || [];
    out.innerHTML = '<div><strong>Proposal</strong></div><pre class="small">' + this._escape(JSON.stringify(patch, null, 2)) + '</pre>' +
      '<div><strong>Canary</strong></div><pre class="small">' + this._escape(JSON.stringify(canary, null, 2)) + '</pre>';
  }

  _authHeader(key){ return key ? { 'Authorization': 'Bearer ' + key } : {}; }
  _on(sel, type, fn){ const el = this.querySelector(sel); if (el) el.addEventListener(type, fn); }
  _val(sel){ const el = this.querySelector(sel); return (el && el.value) || ''; }
  _el(sel){ return this.querySelector(sel); }
  _escape(s){ return (s||'').toString().replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c])); }
  _toast(msg){ try{ emit(document, EV.TOAST, { message: msg }); }catch(_){} }
  _pct(v){ return (typeof v === 'number') ? (v*100).toFixed(1)+'%' : 'n/a'; }
  _num(v){ return (typeof v === 'number') ? v.toFixed(3) : 'n/a'; }
}

customElements.define('uamm-evals-page', UammEvalsPage);
