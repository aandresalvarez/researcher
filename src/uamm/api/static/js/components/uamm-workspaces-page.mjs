import { apiFetch, apiFetchWith } from '../core/api.mjs';
import { getContext, setContext } from '../core/context.mjs';
import { EV, emit } from '../core/events.mjs';
import { select } from '../core/store.mjs';
import { selectWorkspaces, selectContext, selectWsDash, selectWsAdminKeys, selectWsAdminPacks, selectWsAdminPreview } from '../core/selectors.mjs';
import { loadWorkspaces as actionLoadWorkspaces, loadWorkspaceDash, loadWorkspaceTrend, wsLoadKeys, wsIssueKey, wsDeactivateKey, wsDeleteWorkspace, wsLoadPolicyPacks, wsApplyOverlay, wsApplyPack, wsPreviewPack } from '../core/actions.mjs';

export class UammWorkspacesPage extends HTMLElement {
  constructor(){ super(); this._mounted=false; }
  connectedCallback(){
    if (this._mounted) return;
    this._mounted=true;
    this.innerHTML=this._tpl();
    this._wire();
    this._prefillSession();
    this.loadWorkspaces();
    this.loadWsDash();
    // Reactive: workspaces list from store
    this._wsListUnsub = select(selectWorkspaces, (data) => {
      const body = this.querySelector('#ws-body');
      if (!body) return;
      if (data.loading) { body.innerHTML = '<tr><td colspan="4" class="text-muted">Loading…</td></tr>'; return; }
      const arr = data.list || [];
      if (!arr.length) { body.innerHTML = '<tr><td colspan="4" class="text-muted">No workspaces</td></tr>'; return; }
      body.innerHTML = arr.map(ws => `<tr>
        <td>${this._esc(ws.slug||'')}</td>
        <td>${this._esc(ws.name||'')}</td>
        <td class="text-truncate" style="max-width:300px;">${this._esc(ws.root||'')}</td>
        <td class="text-end"><div class="btn-group btn-group-sm" role="group">
          <button class="btn btn-outline-primary" data-action="use-ws" data-ws="${this._esc(ws.slug)}">Use</button>
          <button class="btn btn-outline-secondary" data-action="keys-ws" data-ws="${this._esc(ws.slug)}">Keys</button>
          <button class="btn btn-outline-danger" data-action="del-ws" data-ws="${this._esc(ws.slug)}">Delete</button>
        </div></td>
      </tr>`).join('');
      // Wire buttons
      this.querySelectorAll('[data-action="use-ws"]').forEach(b => b.addEventListener('click', ()=> this._useWs(b.getAttribute('data-ws'))));
      this.querySelectorAll('[data-action="keys-ws"]').forEach(b => b.addEventListener('click', ()=> this._loadKeys(b.getAttribute('data-ws'))));
      this.querySelectorAll('[data-action="del-ws"]').forEach(b => b.addEventListener('click', ()=> this._confirmDelete(b.getAttribute('data-ws'))));
    });
    try { actionLoadWorkspaces(); } catch (_) {}
    // Reactive: selected workspace dash + trend
    this._ctxUnsub = select(selectContext, (ctx) => {
      this._updateDashLinks(ctx.workspace || 'default');
      loadWorkspaceDash();
      loadWorkspaceTrend();
    });
    this._dashUnsub = select(selectWsDash, (dash) => {
      const last = dash.last_step_ts ? new Date(dash.last_step_ts*1000).toLocaleString() : '—';
      this._set('#wsdash-last', last);
      const doc = dash.doc_latest;
      this._set('#wsdash-doc', doc ? ((doc.title || doc.id || 'doc') + (doc.ts?(' • ' + new Date(doc.ts*1000).toLocaleString()):'')) : '—');
      try { if (window.drawSpark) { window.drawSpark('spark-docs', dash.trend.docs || [], '#6c757d'); window.drawSpark('spark-steps', dash.trend.steps || [], '#0d6efd'); } } catch(_){}
    });
    // React to external workspace/context changes (e.g., via sidebar or modal)
    this._wsListener = () => { try { this._prefillSession(); this.loadWsDash(); } catch(_){} };
    document.addEventListener(EV.WORKSPACE_CHANGE, this._wsListener);
    document.addEventListener(EV.CONTEXT_CHANGE, this._wsListener);
  }
  disconnectedCallback(){
    this._mounted=false;
    if (this._wsListUnsub) { try { this._wsListUnsub(); } catch(_){} this._wsListUnsub = null; }
    if (this._ctxUnsub) { try { this._ctxUnsub(); } catch(_){} this._ctxUnsub = null; }
    if (this._dashUnsub) { try { this._dashUnsub(); } catch(_){} this._dashUnsub = null; }
    if (this._keysUnsub) { try { this._keysUnsub(); } catch(_){} this._keysUnsub = null; }
    if (this._packsUnsub) { try { this._packsUnsub(); } catch(_){} this._packsUnsub = null; }
    if (this._previewUnsub) { try { this._previewUnsub(); } catch(_){} this._previewUnsub = null; }
    if (this._wsListener) {
      document.removeEventListener(EV.WORKSPACE_CHANGE, this._wsListener);
      document.removeEventListener(EV.CONTEXT_CHANGE, this._wsListener);
      this._wsListener = null;
    }
  }

  _tpl(){
    return `
<div class="row">
  <div class="col-lg-5">
    <div class="card mb-3">
      <div class="card-header d-flex align-items-center"><span>Session Settings</span></div>
      <div class="card-body">
        <div class="row g-2 align-items-end">
          <div class="col-md-6"><label class="form-label small">Current Workspace</label><input id="sess-ws" class="form-control" list="ws-dlist" placeholder="default"><datalist id="ws-dlist"></datalist></div>
          <div class="col-md-6"><label class="form-label small">API Key</label><input id="sess-key" type="password" class="form-control" placeholder="wk_…"></div>
          <div class="col-12 d-flex gap-2">
            <button class="btn btn-outline-primary" data-action="sess-save">Save</button>
            <button class="btn btn-outline-secondary" data-action="sess-clear">Clear Key</button>
            <button class="btn btn-outline-secondary ms-auto" data-action="sess-load">Load Workspaces</button>
          </div>
        </div>
        <div class="small text-muted mt-2">These settings apply only to this browser session.</div>
      </div>
    </div>

    <div class="card mb-3">
      <div class="card-header d-flex align-items-center">
        <span>Selected Workspace — Dashboard</span>
        <button class="btn btn-sm btn-outline-secondary ms-auto" data-action="dash-refresh">Refresh</button>
      </div>
      <div class="card-body">
        <div class="row g-2">
          <div class="col-6"><div class="small text-muted">Last Activity</div><div id="wsdash-last">—</div></div>
          <div class="col-6"><div class="small text-muted">Latest Doc</div><div id="wsdash-doc">—</div></div>
        </div>
        <div class="mt-2 d-flex gap-2">
          <a id="wsdash-play" class="btn btn-sm btn-outline-primary" href="#">Open Playground</a>
          <a id="wsdash-obs" class="btn btn-sm btn-outline-secondary" href="#">Recent Steps</a>
          <a id="wsdash-rag" class="btn btn-sm btn-outline-secondary" href="#">Manage Docs</a>
          <a id="wsdash-cp" class="btn btn-sm btn-outline-secondary" href="#">View CP</a>
        </div>
        <div class="row mt-3 g-2 align-items-center">
          <div class="col-6"><div class="small text-muted">Docs (last 7d)</div><svg id="spark-docs" viewBox="0 0 140 30" width="140" height="30"></svg></div>
          <div class="col-6"><div class="small text-muted">Steps (last 7d)</div><svg id="spark-steps" viewBox="0 0 140 30" width="140" height="30"></svg></div>
          <div class="col-12 d-flex align-items-center gap-2">
            <label for="wsdash-days" class="form-label small mb-0">Period</label>
            <select id="wsdash-days" class="form-select form-select-sm" style="width: 90px;"><option value="7" selected>7 days</option><option value="14">14 days</option><option value="30">30 days</option></select>
          </div>
        </div>
      </div>
    </div>

    <div class="card mb-3">
      <div class="card-header d-flex align-items-center">
        <span>Create Workspace</span>
        <div class="ms-auto d-flex gap-2">
          <button class="btn btn-sm btn-outline-success" data-action="create-test">Create Test Workspace</button>
          <button class="btn btn-sm btn-outline-secondary" data-bs-toggle="modal" data-bs-target="#wsWizardGlobal">Wizard…</button>
        </div>
      </div>
      <div class="card-body">
        <form id="ws-create">
          <div class="mb-2"><input id="slug" class="form-control" placeholder="slug (required)" required /></div>
          <div class="mb-2"><input id="name" class="form-control" placeholder="name (optional)" /></div>
          <div class="mb-2"><input id="root" class="form-control" placeholder="filesystem root (optional)" /></div>
          <button class="btn btn-primary" type="submit">Create</button>
        </form>
        <div id="ws-create-result" class="small mt-2 text-muted"></div>
      </div>
    </div>
  </div>

  <div class="col-lg-7">
    <div class="card mb-3">
      <div class="card-header d-flex align-items-center">
        <span>All Workspaces</span>
        <button class="btn btn-sm btn-outline-secondary ms-auto" data-action="list-refresh">Refresh</button>
      </div>
      <div class="card-body">
        <div class="table-responsive">
          <table class="table table-sm table-hover align-middle">
            <thead><tr><th>Slug</th><th>Name</th><th>Root</th><th class="text-end">Actions</th></tr></thead>
            <tbody id="ws-body"><tr><td colspan="4" class="text-muted">Loading…</td></tr></tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">Selected Workspace — Keys <span class="text-muted">(<span id="keys-ws">—</span>)</span></div>
      <div class="card-body">
        <form id="key-issue" class="row g-2 align-items-end">
          <div class="col-md-3"><input id="key-role" class="form-control" placeholder="role (admin|editor|viewer)" /></div>
          <div class="col-md-5"><input id="key-label" class="form-control" placeholder="label" /></div>
          <div class="col-md-4"><button class="btn btn-outline-primary w-100" type="submit">Issue Key</button></div>
          <input type="hidden" id="key-ws-hidden" />
          <div class="col-12 mt-2"><input id="key-admin" type="password" class="form-control" placeholder="Admin API Key (optional)" /></div>
        </form>
        <div id="key-issued" class="small mt-2"></div>
        <div id="keys-list" class="mt-3"></div>
      </div>
    </div>

    <div class="card mt-3">
      <div class="card-header">Selected Workspace — Policy Overlay</div>
      <div class="card-body">
        <div class="row g-2">
          <div class="col-md-3"><label class="form-label small">Accept Threshold</label><input id="pol-acc" class="form-control" placeholder="e.g. 0.85"></div>
          <div class="col-md-3"><label class="form-label small">Borderline Δ</label><input id="pol-delta" class="form-control" placeholder="e.g. 0.05"></div>
          <div class="col-md-3"><label class="form-label small">Tools/Turn</label><input id="pol-tools-turn" class="form-control" placeholder="e.g. 4"></div>
          <div class="col-md-3"><label class="form-label small">Tools/Refine</label><input id="pol-tools-ref" class="form-control" placeholder="e.g. 2"></div>
          <div class="col-12"><label class="form-label small">Tools requiring approval (comma-separated)</label><input id="pol-approve" class="form-control" placeholder="WEB_FETCH,TABLE_QUERY"></div>
        </div>
        <div class="mt-2 d-flex gap-2">
          <button class="btn btn-outline-primary" data-action="overlay-apply">Apply Overlay</button>
          <button class="btn btn-outline-secondary" data-action="overlay-clear">Clear</button>
        </div>
        <div id="pol-status" class="small text-muted mt-2"></div>
      </div>
    </div>

    <div class="card mt-3">
      <div class="card-header d-flex align-items-center">
        <span>Selected Workspace — Policy Packs</span>
        <button class="btn btn-sm btn-outline-secondary ms-auto" data-action="packs-load">Load Packs</button>
      </div>
      <div class="card-body">
        <div class="row g-2 align-items-end">
          <div class="col-md-6"><label class="form-label small">Policy Pack</label><select id="pol-pack" class="form-select"></select></div>
          <div class="col-md-6 d-grid"><button class="btn btn-outline-primary" data-action="packs-apply">Apply Pack</button></div>
        </div>
        <div class="mt-3"><div class="small text-muted">Differences vs current applied:</div><pre id="pol-diff" class="small bg-light p-2 border rounded" style="max-height: 240px; overflow:auto;">—</pre></div>
      </div>
    </div>
  </div>
</div>`;
  }

  _wire(){
    this._on('[data-action="sess-save"]','click',()=>this._sessSave());
    this._on('[data-action="sess-clear"]','click',()=>this._sessClear());
    this._on('[data-action="sess-load"]','click',()=>this._sessLoadList());
    this._on('[data-action="dash-refresh"]','click',()=>{ loadWorkspaceDash(); loadWorkspaceTrend(); });
    this._on('#wsdash-days','change',()=>{ const sel = this.querySelector('#wsdash-days'); const d = sel ? parseInt(sel.value||'7') : 7; loadWorkspaceTrend(d); });
    this._on('#ws-create','submit',(e)=>this._createWs(e));
    this._on('[data-action="create-test"]','click',()=>this._createTestWs());
    this._on('[data-action="list-refresh"]','click',()=>actionLoadWorkspaces());
    this._on('#key-issue','submit',(e)=>this._issueKey(e));
    this._on('[data-action="overlay-apply"]','click',()=>this._applyOverlay());
    this._on('[data-action="overlay-clear"]','click',()=>this._clearOverlay());
    this._on('[data-action="packs-load"]','click',()=>this._loadPacks());
    this._on('[data-action="packs-apply"]','click',()=>this._applyPack());
  }

  _prefillSession(){
    try{
      const ctx = getContext();
      const wsEl = this.querySelector('#sess-ws'); if (wsEl) wsEl.value = ctx.workspace || 'default';
      const keyEl = this.querySelector('#sess-key'); if (keyEl) keyEl.value = ctx.apiKey || '';
    }catch(_){}
  }

  async _sessLoadList(){
    const dl = this.querySelector('#ws-dlist'); if (!dl) return; dl.innerHTML='';
    try{ const res = await apiFetch('/workspaces'); const data=await res.json(); if(!res.ok) throw new Error('failed'); dl.innerHTML=(data.workspaces||[]).map(w=>`<option value="${this._esc(w.slug)}">`).join(''); this._toast('Loaded workspaces'); }catch(_){ this._toast('Failed to load'); }
  }
  _sessSave(){ try{ const ws=(this._val('#sess-ws')||'default').trim(); const key=(this._val('#sess-key')||'').trim(); setContext({ workspace: ws, apiKey: key }); this._toast('Session updated'); this.loadWsDash(); }catch(_){ this._toast('Failed to save'); } }
  _sessClear(){ try{ const el=this.querySelector('#sess-key'); if(el) el.value=''; setContext({ apiKey: '' }); this._toast('Key cleared'); }catch(_){} }

  async loadWorkspaces(){ try { await actionLoadWorkspaces(); } catch(_){} }

  async _createWs(ev){ ev && ev.preventDefault && ev.preventDefault(); const slug=this._val('#slug').trim(); const name=this._val('#name').trim(); const root=this._val('#root').trim(); const out=this.querySelector('#ws-create-result'); if (out) out.textContent='Creating…'; try{ const res=await apiFetch('/workspaces',{method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({slug, name, root: root||undefined})}); const data=await res.json(); if(!res.ok) throw new Error(data.error||'failed'); if(out) out.textContent='Created ' + (data.workspace?.slug || slug); await actionLoadWorkspaces(); }catch(e){ if(out) out.textContent='Error: ' + e.message; } }

  async _createTestWs(){ const slug = 'test-' + new Date().toISOString().replace(/[:T.-]/g,'').slice(0,14).toLowerCase(); this.querySelector('#slug').value = slug; this.querySelector('#name').value = 'Test ' + slug; this.querySelector('#root').value = ''; await this._createWs(new Event('submit')); setContext({ workspace: slug }); this._toast('Test workspace created: ' + slug); this.loadWsDash(); }

  _useWs(slug){ setContext({ workspace: slug }); const inp=this.querySelector('#sess-ws'); if(inp) inp.value=slug; this.loadWsDash(); this._toast('Workspace set: ' + slug); }

  async _confirmDelete(slug){ if (!confirm(`Delete workspace '${slug}'? This removes metadata; files are kept unless purge is enabled in code.`)) return; try{ await wsDeleteWorkspace(slug); this._toast('Deleted workspace ' + slug); }catch(_){ this._toast('Failed to delete'); } }

  async loadWsDash(){ const ws = getContext().workspace || 'default'; this._updateDashLinks(ws); try { await loadWorkspaceDash(); await loadWorkspaceTrend(); } catch(_){} }
  async _loadTrend(){ const daysSel = this.querySelector('#wsdash-days'); const d = daysSel ? parseInt(daysSel.value||'7') : 7; try { await loadWorkspaceTrend(d); } catch(_){} }
  _updateDashLinks(ws){ ['play','obs','rag','cp'].forEach(k => { const a=this.querySelector('#wsdash-'+k); if(!a) return; if (k==='play') a.href = '/ui?ws='+encodeURIComponent(ws); else a.href = '#/'+(k==='play'?'':k); }); }

  async _loadKeys(slug){ const ws = slug; this._set('#keys-ws', ws); this._set('#key-ws-hidden', ws); const list = this.querySelector('#keys-list'); if (list) list.innerHTML = '<div class="text-muted small">Loading…</div>'; if (this._keysUnsub) { try { this._keysUnsub(); } catch(_){} this._keysUnsub=null; } this._keysUnsub = select(selectWsAdminKeys, (st) => { if (!st || st.ws !== ws) return; const keys = st.items || []; if (list) list.innerHTML = keys.length ? ('<ul class="list-unstyled mb-0">' + keys.map(k => `<li class=\"mb-1\"><code>${this._esc(k.id)}</code> — ${this._esc(k.role)} ${k.active?'<span class=\"badge text-bg-success\">active</span>':'<span class=\"badge text-bg-secondary\">inactive</span>'} <button class=\"btn btn-sm btn-outline-danger ms-2\" data-action=\"deactivate\" data-id=\"${this._esc(k.id)}\">Deactivate</button></li>`).join('') + '</ul>') : '<div class="text-muted small">No keys</div>'; this.querySelectorAll('[data-action=\"deactivate\"]').forEach(b => b.addEventListener('click', ()=> this._deactivateKey(ws, b.getAttribute('data-id')))); }); const admin=(this._val('#key-admin')||'').trim(); await wsLoadKeys(ws, admin||undefined); }

  async _issueKey(ev){ ev.preventDefault(); const ws=this._val('#key-ws-hidden').trim(); const role=this._val('#key-role').trim(); const label=this._val('#key-label').trim(); const out=this.querySelector('#key-issued'); if (out) out.textContent = 'Issuing…'; try{ const admin=(this._val('#key-admin')||'').trim(); await wsIssueKey(ws, role, label, admin||undefined); if (out) out.textContent='Issued key (if token returned, copy securely)'; }catch(e){ if (out) out.textContent='Error: ' + e.message; } }

  async _deactivateKey(ws, id){ try{ const admin=(this._val('#key-admin')||'').trim(); await wsDeactivateKey(ws, id, admin||undefined); }catch(_){ this._toast('Failed to deactivate key'); } }

  async _applyOverlay(){ const ws = this._val('#key-ws-hidden').trim(); const out=this.querySelector('#pol-status'); if (out) out.textContent='Applying…'; try{ const overlay={}; const v=(id,fn)=>{ const s=(this._val('#'+id)||'').trim(); if (s) overlay[fn||id] = id.startsWith('pol-') ? (id==='pol-acc'||id==='pol-delta'? parseFloat(s) : parseInt(s)) : s; }; const acc=(this._val('#pol-acc')||'').trim(); if (acc) overlay.accept_threshold=parseFloat(acc); const dl=(this._val('#pol-delta')||'').trim(); if (dl) overlay.borderline_delta=parseFloat(dl); const tt=(this._val('#pol-tools-turn')||'').trim(); if (tt) overlay.tool_budget_per_turn=parseInt(tt); const tr=(this._val('#pol-tools-ref')||'').trim(); if (tr) overlay.tool_budget_per_refinement=parseInt(tr); const appr=(this._val('#pol-approve')||'').trim(); if (appr) overlay.tools_requiring_approval=appr.split(',').map(s=>s.trim()).filter(Boolean); await wsApplyOverlay(ws, overlay); if (out) out.textContent='Applied overlay'; }catch(_){ if (out) out.textContent='Error applying'; } }
  _clearOverlay(){ ['pol-acc','pol-delta','pol-tools-turn','pol-tools-ref','pol-approve'].forEach(id => { const el=this.querySelector('#'+id); if (el) el.value=''; }); const out=this.querySelector('#pol-status'); if (out) out.textContent=''; }

  async _loadPacks(){ const sel=this.querySelector('#pol-pack'); if (!sel) return; sel.innerHTML='<option value="">Loading…</option>'; if (this._packsUnsub) { try { this._packsUnsub(); } catch(_){} this._packsUnsub=null; } this._packsUnsub = select(selectWsAdminPacks, (st) => { if (!sel) return; if (st.loading) { sel.innerHTML='<option value="">Loading…</option>'; return; } const packs=(st.items||[]).map(p => `<option value="${this._esc(p)}">${this._esc(p)}</option>`).join(''); sel.innerHTML='<option value="">Select a policy pack…</option>'+packs; }); await wsLoadPolicyPacks(); }
  async _applyPack(){ const ws=this._val('#key-ws-hidden').trim(); const sel=this.querySelector('#pol-pack'); if (!sel||!ws){ this._toast('Select a workspace'); return; } const name=sel.value; if (!name){ this._toast('Choose a pack'); return; } try{ await wsApplyPack(ws, name); this._toast('Applied pack ' + name); await this._previewPack(); }catch(_){ this._toast('Failed to apply'); } }
  async _previewPack(){ const ws=this._val('#key-ws-hidden').trim(); const sel=this.querySelector('#pol-pack'); const out = this.querySelector('#pol-diff'); if (!sel||!ws||!out) return; const name=sel.value; if (!name){ out.textContent='—'; return; } if (this._previewUnsub) { try { this._previewUnsub(); } catch(_){} this._previewUnsub=null; } this._previewUnsub = select(selectWsAdminPreview, (st) => { if (st.loading) { out.textContent='Loading…'; return; } out.textContent = st.data ? JSON.stringify(st.data.diff||{}, null, 2) : '—'; }); await wsPreviewPack(ws, name); }

  _on(sel,ev,fn){ const el=this.querySelector(sel); if (el) el.addEventListener(ev, fn); }
  _set(sel, v){ const el=this.querySelector(sel); if (el) el.textContent = v; }
  _val(sel){ const el=this.querySelector(sel); return (el && el.value) || ''; }
  _esc(s){ return (s||'').toString().replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c])); }
  _toast(msg){ try{ emit(document, EV.TOAST, { message: msg }); }catch(_){} }
}

customElements.define('uamm-workspaces-page', UammWorkspacesPage);
