import { apiFetch, apiFetchWith } from '../core/api.mjs';
import { getContext } from '../core/context.mjs';
import { EV, emit } from '../core/events.mjs';
import { select } from '../core/store.mjs';
import { selectRagIngested, selectRagStatus } from '../core/selectors.mjs';
import { loadIngested, loadFileStatus, loadFileDetail } from '../core/actions.mjs';

export class UammRagPage extends HTMLElement {
  constructor(){ super(); this._mounted = false; }

  connectedCallback(){
    if (this._mounted) return; this._mounted = true;
    this.innerHTML = this._template();

    // Subscribe to store for ingested files
    this._ingestedUnsub = select(selectRagIngested, (data) => {
      if (!data.loading) this._renderIngested(data.items);
    });

    // Subscribe to store for file status
    this._statusUnsub = select(selectRagStatus, (data) => {
      if (!data.loading) this._renderFileStatus(data.items);
    });

    // Upload handled by <uamm-rag-upload>; listen for completion
    try{
      this._uploadHandler = () => {
        this._refreshSidebarCounts();
        // Add a small delay to allow backend processing
        setTimeout(() => {
          loadIngested();
          loadFileStatus();
        }, 1000);
      };
      document.addEventListener(EV.RAG_UPLOAD_DONE, this._uploadHandler);
    }catch(_){ }
    this._on('#ingest-form', 'submit', (e) => this.ingestFolder(e));
    this._on('#search-form', 'submit', (e) => this.searchCorpus(e));
    try{ this._wsHandler = () => { loadIngested(); loadFileStatus(); }; document.addEventListener(EV.WORKSPACE_CHANGE, this._wsHandler); document.addEventListener(EV.CONTEXT_CHANGE, this._wsHandler); }catch(_){ }

    // Trigger initial loads via actions
    loadIngested().catch(()=>{});
    loadFileStatus().catch(()=>{});
  }

  disconnectedCallback(){
    this._mounted = false;
    // Unsubscribe from store
    if (this._ingestedUnsub) { try { this._ingestedUnsub(); } catch(_){} this._ingestedUnsub = null; }
    if (this._statusUnsub) { try { this._statusUnsub(); } catch(_){} this._statusUnsub = null; }
    try{
      if(this._wsHandler) document.removeEventListener(EV.WORKSPACE_CHANGE, this._wsHandler);
      if(this._wsHandler) document.removeEventListener(EV.CONTEXT_CHANGE, this._wsHandler);
      if(this._uploadHandler) document.removeEventListener(EV.RAG_UPLOAD_DONE, this._uploadHandler);
    }catch(_){ }
  }

  _template(){
    return `
<div class="row">
  <div class="col-lg-6">
    <uamm-rag-upload></uamm-rag-upload>
    <div class="card mb-3">
      <div class="card-header">Ingest Folder</div>
      <div class="card-body">
        <form id="ingest-form">
          <div class="mb-2"><input type="text" id="path" name="path" class="form-control" placeholder="Path under docs_dir (optional)" /></div>
          <div class="mb-2"><input type="text" id="ws2" name="ws" class="form-control" placeholder="Workspace header (optional)" /></div>
          <div class="mb-2"><input type="password" id="key2" name="key" class="form-control" placeholder="API Key (optional)" /></div>
          <button class="btn btn-secondary" type="submit">Ingest</button>
        </form>
        <div id="ingest-result" class="small mt-2 text-muted"></div>
      </div>
    </div>
  </div>
  <div class="col-lg-6">
    <div class="card mb-3">
      <div class="card-header">Search Corpus</div>
      <div class="card-body">
        <form id="search-form">
          <div class="row g-2">
            <div class="col-9"><input type="text" id="q" name="q" class="form-control" placeholder="Query" required /></div>
            <div class="col-3"><button class="btn btn-outline-primary w-100" type="submit">Search</button></div>
          </div>
        </form>
        <div id="search-results" class="mt-3"></div>
      </div>
    </div>
    <div class="card mb-3">
      <div class="card-header d-flex align-items-center justify-content-between">
        <span>Ingested Files (recent)</span>
        <button class="btn btn-sm btn-outline-secondary" type="button" id="ingested-refresh">Refresh</button>
      </div>
      <div class="card-body">
        <div id="ingested-list" class="small text-muted">Loading…</div>
      </div>
    </div>
    <div class="card mb-3">
      <div class="card-header d-flex align-items-center justify-content-between">
        <span>File Status</span>
        <div class="d-flex align-items-center gap-2">
          <select id="filestatus-filter" class="form-select form-select-sm" style="width:auto">
            <option value="">All</option>
            <option value="ready">Ready</option>
            <option value="skipped">Skipped</option>
            <option value="error">Error</option>
          </select>
          <input id="filestatus-search" class="form-control form-control-sm" placeholder="Search..." style="width: 12rem;">
          <button class="btn btn-sm btn-outline-secondary" type="button" id="filestatus-refresh">Refresh</button>
        </div>
      </div>
      <div class="card-body">
        <div id="filestatus-list" class="small text-muted">Loading…</div>
      </div>
    </div>
  </div>
</div>`;
  }

  // Upload is handled by <uamm-rag-upload>

  async ingestFolder(ev){
    ev.preventDefault();
    const path = (this._val('#path') || '').trim();
    const ws = (this._val('#ws2') || '').trim();
    const key = (this._val('#key2') || '').trim();
    const out = this.querySelector('#ingest-result');
    if (out) out.textContent = 'Ingesting…';
    try {
      const res = await apiFetchWith('/rag/ingest-folder', { method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify(path ? { path } : {}) }, { workspace: ws || undefined, apiKey: key || undefined });
      const data = await res.json();
      if (!res.ok) throw new Error((data && data.error) || 'ingest failed');
      const ing = Number(data && data.ingested || 0);
      const skp = Number(data && data.skipped || 0);
      if (out) out.textContent = `OK — ingested ${ing}, skipped ${skp}`;
      this._toast(`Folder ingest complete: ingested ${ing}, skipped ${skp}`);
      this._refreshSidebarCounts();
      await this.loadIngested();
      await this.loadFileStatus();
      if (out && data && Array.isArray(data.warnings) && data.warnings.length){
        const pretty = data.warnings.map(w => this._prettyWarn(w)).join(', ');
        out.innerHTML += `<div class="small text-muted mt-1">Warnings: ${this._escape(pretty)}</div>`;
      }
    } catch (e) { if (out) out.textContent = 'Error: ' + (e && e.message || 'failed'); }
  }

  _renderIngested(docs){
    const out = this.querySelector('#ingested-list');
    const btn = this.querySelector('#ingested-refresh');
    if (btn && !btn._bound){ btn._bound = true; btn.addEventListener('click', ()=> loadIngested()); }
    if (!out) return;
    if (!docs || !docs.length){ out.textContent = 'No documents yet'; return; }
    const rows = docs.map(d => this._renderDocRow(d)).join('');
    out.innerHTML = `<div class="list-group list-group-flush">${rows}</div>`;
    // Bind detail buttons for local file URLs
    this.querySelectorAll('.btn-doc-detail').forEach(btn => {
      if (btn._bound) return; btn._bound = true;
      btn.addEventListener('click', (ev) => {
        ev.preventDefault();
        const p = btn.getAttribute('data-path') || '';
        this._openFileDetail(p);
      });
    });
  }

  async loadIngested(){
    // Deprecated: kept for backward compat, now delegates to action
    await loadIngested();
  }

  _renderFileStatus(files){
    const btn = this.querySelector('#filestatus-refresh');
    if (btn && !btn._bound){ btn._bound = true; btn.addEventListener('click', ()=> loadFileStatus()); }
    const sel = this.querySelector('#filestatus-filter');
    const q = this.querySelector('#filestatus-search');
    if (sel && !sel._bound){ sel._bound = true; sel.addEventListener('change', ()=> this._renderFileStatusList()); }
    if (q && !q._bound){ q._bound = true; q.addEventListener('input', ()=> this._renderFileStatusList()); }
    this._fileStatus = files || [];
    this._renderFileStatusList();
  }

  async loadFileStatus(){
    // Deprecated: kept for backward compat, now delegates to action
    await loadFileStatus();
  }

  _renderFileStatusList(){
    const out = this.querySelector('#filestatus-list');
    const sel = this.querySelector('#filestatus-filter');
    const q = this.querySelector('#filestatus-search');
    const filt = (sel && sel.value) ? sel.value.toLowerCase() : '';
    const term = (q && q.value || '').toLowerCase();
    const files = Array.isArray(this._fileStatus) ? this._fileStatus : [];
    let arr = files;
    if (filt){ arr = arr.filter(f => (f.status||'').toString().toLowerCase() === filt); }
    if (term){ arr = arr.filter(f => {
      const s = ((f.name||'') + ' ' + (f.path||'') + ' ' + (f.reason||''));
      return s.toLowerCase().includes(term);
    }); }
    if (!arr.length){ if(out) out.textContent = 'No files found'; return; }
    const rows = arr.map(f => this._renderFileRow(f)).join('');
    if (out) out.innerHTML = `<div class="list-group list-group-flush">${rows}</div>`;
    // Bind "Details" buttons
    this.querySelectorAll('.btn-file-detail').forEach(btn => {
      if (btn._bound) return; btn._bound = true;
      btn.addEventListener('click', (ev) => {
        ev.preventDefault();
        const p = btn.getAttribute('data-path') || '';
        this._openFileDetail(p);
      });
    });
  }

  _renderFileRow(f){
    const name = f.name || (f.path ? (f.path.split(/[\\\/]/).pop() || f.path) : 'file');
    const when = f.mtime ? new Date(f.mtime * 1000).toLocaleString() : '';
    const badge = this._statusBadge(f.status, f.reason);
    const icon = this._pickIcon(name);
    const path = f.path || '';
    return `<div class="list-group-item d-flex align-items-center">
      <span class="me-2">${icon}</span>
      <div class="flex-grow-1">
        <div class="text-truncate">${this._escape(name)}</div>
        <div class="small text-muted">${this._escape(when)}</div>
      </div>
      <div class="ms-2 d-flex align-items-center gap-2">${badge}<button class="btn btn-sm btn-outline-primary btn-file-detail" data-path="${this._escape(path)}">Details</button></div>
    </div>`;
  }

  _statusBadge(status, reason){
    const s = (status||'').toString();
    const r = (reason||'').toString();
    const t = r ? `${s}${r?(' — '+r):''}` : s;
    const esc = (x)=> this._escape(x);
    if (s === 'ready') return `<span class="badge text-bg-success" title="${esc(t)}">Ready</span>`;
    if (s === 'skipped') return `<span class="badge text-bg-warning" title="${esc(t)}">Skipped</span>`;
    if (s === 'error') return `<span class="badge text-bg-danger" title="${esc(t)}">Error</span>`;
    if (s === 'processing' || s === 'uploaded') return `<span class="badge text-bg-secondary" title="${esc(t)}">${esc(s)}</span>`;
    return `<span class="badge text-bg-secondary" title="${esc(t)}">${esc(s||'unknown')}</span>`;
  }

  async _openFileDetail(path){
    if (!path) return;
    // Delegate to modal host
    const host = document.querySelector('uamm-modal-host');
    if (host && host.openFile) host.openFile(path);
  }

  _renderDocRow(d){
    const title = (d.title && d.title.toString().trim()) || this._deriveTitle(d) || d.id;
    const when = d.ts ? new Date(d.ts * 1000).toLocaleString() : '';
    const url = d.url || '';
    const icon = this._pickIcon(url || title);
    let link = this._escape(title);
    if (url) {
      if (url.startsWith('file:')) {
        const p = url.slice(5);
        link = `${this._escape(title)} <button class="btn btn-sm btn-outline-primary ms-2 btn-doc-detail" data-path="${this._escape(p)}">Details</button>`;
      } else {
        link = `<a href="${this._escape(url)}" target="_blank" class="text-decoration-none">${this._escape(title)}</a>`;
      }
    }
    return `<div class="list-group-item d-flex align-items-center">
      <span class="me-2">${icon}</span>
      <div class="flex-grow-1">
        <div class="text-truncate">${link}</div>
        <div class="small text-muted">${this._escape(when)}</div>
      </div>
    </div>`;
  }

  _deriveTitle(d){
    // Try from meta.path or from file: URL
    try{
      const m = d && d.meta ? d.meta : null;
      let path = '';
      if (typeof m === 'string' && m.startsWith('{')){
        try{ const obj = JSON.parse(m); path = obj && obj.path || ''; }catch(_){ }
      }
      if (!path && d && d.url && d.url.startsWith('file:')){
        path = d.url.slice(5);
      }
      if (path){ const parts = path.split(/[\\\/]/); return parts[parts.length - 1] || path; }
    }catch(_){ }
    return '';
  }

  _pickIcon(name){
    const n = (name || '').toLowerCase();
    const i = (cls) => `<i class="bi ${cls}"></i>`;
    if (n.endsWith('.pdf')) return i('bi-filetype-pdf');
    if (n.endsWith('.docx')) return i('bi-filetype-docx');
    if (n.endsWith('.md') || n.endsWith('.markdown')) return i('bi-markdown');
    if (n.endsWith('.html') || n.endsWith('.htm')) return i('bi-filetype-html');
    if (n.endsWith('.txt')) return i('bi-filetype-txt');
    return i('bi-file-earmark-text');
  }

  async searchCorpus(ev){
    ev.preventDefault();
    const q = (this._val('#q') || '').trim();
    if (!q) return;
    const resEl = this.querySelector('#search-results');
    if (resEl) resEl.innerHTML = '<div class="text-muted small">Searching…</div>';
    try{
      const res = await apiFetch('/rag/search?' + new URLSearchParams({ q }));
      const data = await res.json();
      if (!res.ok) throw new Error((data && data.error) || 'search failed');
      const hits = data.hits || [];
      if (!hits.length) { if (resEl) resEl.innerHTML = '<div class="text-muted">No results</div>'; return; }
      const html = hits.map(h => {
        const snippet = (h.snippet || '').toString();
        const score = (h.score !== undefined) ? `score ${h.score.toFixed ? h.score.toFixed(3) : h.score}` : '';
        let link = '';
        if (h.url) {
          if (h.url.startsWith('file:')) {
            const p = h.url.slice(5);
            link = `<div class=\"small\"><button class=\"btn btn-sm btn-outline-primary btn-doc-detail\" data-path=\"${this._escape(p)}\">Details</button></div>`;
          } else {
            link = `<div class=\"small text-truncate\"><a href=\"${this._escape(h.url)}\" target=\"_blank\">${this._escape(h.url)}</a></div>`;
          }
        }
        return `<div class=\"border rounded p-2 mb-2\"><div class=\"small text-muted\">${score}</div><div>${this._escape(snippet)}</div>${link}</div>`;
      }).join('');
      if (resEl) resEl.innerHTML = html;
      // Bind detail buttons in search results
      this.querySelectorAll('.btn-doc-detail').forEach(btn => {
        if (btn._bound) return; btn._bound = true;
        btn.addEventListener('click', (ev) => {
          ev.preventDefault();
          const p = btn.getAttribute('data-path') || '';
          this._openFileDetail(p);
        });
      });
    }catch(e){ if (resEl) resEl.innerHTML = '<div class="text-danger small">Error: ' + (e && e.message || 'failed') + '</div>'; }
  }

  // no-op retained for backward compat (unused now)
  _headers(){ return {}; }
  _toast(msg){ try{ emit(document, EV.TOAST, { message: msg }); }catch(_){} }

  _on(sel, type, fn){ const el = this.querySelector(sel); if (el) el.addEventListener(type, fn); }
  _val(sel){ const el = this.querySelector(sel); return (el && el.value) || ''; }
  _escape(s){ return (s||'').toString().replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c])); }
  _prettyWarn(code){
    switch(code){
      case 'pdf_parser_missing': return 'PDF parser not installed (pypdf)';
      case 'docx_parser_missing': return 'DOCX parser not installed (python-docx)';
      case 'ocr_deps_missing': return 'OCR dependencies missing (pdf2image+pytesseract and system poppler/tesseract)';
      case 'unsupported_extension': return 'Unsupported file extension';
      default: return code;
    }
  }
  _refreshSidebarCounts(){ try{ const sb = document.querySelector('uamm-sidebar'); if (sb && typeof sb.refresh === 'function') sb.refresh(); }catch(_){} }
}

customElements.define('uamm-rag-page', UammRagPage);
