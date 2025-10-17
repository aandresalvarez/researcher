import { apiFetch, apiFetchWith } from '../core/api.mjs';
import { select } from '../core/store.mjs';
import { selectRagEnv } from '../core/selectors.mjs';
import { loadRagEnv } from '../core/actions.mjs';
import { getContext } from '../core/context.mjs';
import { EV, emit } from '../core/events.mjs';
import { log, isDebugEnabled } from '../core/debug.mjs';

export class UammRagUpload extends HTMLElement {
  constructor(){ super(); this._mounted = false; }

  connectedCallback(){
    if (this._mounted) return; this._mounted = true;
    this.innerHTML = this._template();
    this._on('#upload-form', 'submit', (e) => this._upload(e));
    // Drag & drop (optional, non-invasive)
    const dz = this.querySelector('[data-role="dropzone"]');
    if (dz){
      ;['dragenter','dragover'].forEach(t=>dz.addEventListener(t, e=>{ e.preventDefault(); dz.classList.add('border-primary'); }));
      ;['dragleave','drop'].forEach(t=>dz.addEventListener(t, e=>{ e.preventDefault(); dz.classList.remove('border-primary'); }));
      dz.addEventListener('drop', (e)=>{
        const input = this.querySelector('#file');
        if (input && e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length){
          input.files = e.dataTransfer.files;
        }
      });
    }
    // Load environment status reactively
    this._envUnsub = select(selectRagEnv, (env) => {
      const out = this.querySelector('#env-status');
      if (!out) return;
      if (env.loading) { out.textContent = 'Loading…'; return; }
      out.innerHTML = this._renderEnv(env);
    });
    try { loadRagEnv(); } catch (_) {}
    const r = this.querySelector('#env-refresh');
    if (r && !r._bound){ r._bound = true; r.addEventListener('click', ()=> loadRagEnv()); }
  }

  disconnectedCallback(){ this._mounted = false; if (this._envUnsub) { try { this._envUnsub(); } catch(_){} this._envUnsub = null; } }

  _template(){
    return `
    <div class="card mb-3">
      <div class="card-header d-flex align-items-center justify-content-between">
        <span>Upload File</span>
      </div>
      <div class="card-body">
        <form id="upload-form" enctype="multipart/form-data">
          <div class="mb-2" data-role="dropzone" style="border:1px dashed var(--bs-secondary); border-radius: .25rem; padding: .5rem;">
            <input type="file" id="file" name="files" class="form-control" required accept=".txt,.md,.markdown,.html,.htm,.pdf,.docx" multiple />
          </div>
          <div class="mb-2"><input type="text" id="filename" name="filename" class="form-control" placeholder="Optional filename override (single file only)" /></div>
          <div class="mb-2"><input type="text" id="ws" name="ws" class="form-control" placeholder="Workspace header (optional)" /></div>
          <div class="mb-2"><input type="password" id="key1" name="key" class="form-control" placeholder="API Key (optional)" /></div>
          <button class="btn btn-primary" type="submit">Upload</button>
        </form>
        <div id="upload-result" class="small mt-2 text-muted"></div>
        <div id="upload-queue" class="list-group list-group-flush small mt-2" style="max-height: 200px; overflow-y: auto;"></div>
      </div>
      <div class="border-top pt-2 mt-2">
        <div class="d-flex align-items-center justify-content-between mb-1">
          <span class="small text-muted">Environment</span>
          <button type="button" id="env-refresh" class="btn btn-sm btn-outline-secondary">Refresh</button>
        </div>
        <div id="env-status" class="small text-muted">Loading…</div>
      </div>
    </div>`;
  }

  async _upload(ev){
    ev.preventDefault();
    const form = this.querySelector('#upload-form');
    const fileInput = this.querySelector('#file');
    const files = (fileInput && fileInput.files) ? Array.from(fileInput.files) : [];
    const out = this.querySelector('#upload-result');
    const key = (this._val('#key1') || '').trim();
    const wsOverride = (this._val('#ws') || '').trim();
    const qEl = this.querySelector('#upload-queue');
    if (!files.length){ if (out) out.textContent = 'Select one or more files'; return; }
    // Pre-check sizes; populate queue
    const items = files.map(f => ({ name: f.name, size: f.size || 0, state: 'queued', el: null, tooLarge: f.size > 2*1024*1024 }));
    if (qEl){
      qEl.innerHTML = items.map(it => this._queueItemHTML(it.name, it.size, it.tooLarge)).join('');
      const rows = qEl.querySelectorAll('[data-role="q-item"]');
      items.forEach((it, idx) => { it.el = rows[idx] || null; });
      // Show the queue
      qEl.style.display = 'block';
    }
    if (out) out.textContent = 'Uploading…';
    try{
      // Decide endpoint: if single file and a filename override present, use single-file endpoint to honor override
      const override = (this._val('#filename') || '').trim();
      let data;
      if (files.length === 1 && override){
        const fd = new FormData();
        fd.append('file', files[0], files[0].name);
        fd.append('filename', override);
        if (items[0].el) this._setUploading(items[0].el);
        const res = await apiFetchWith('/rag/upload-file', { method: 'POST', body: fd }, { workspace: wsOverride || undefined, apiKey: key || undefined });
        data = await res.json();
        if (!res.ok) throw new Error((data && data.error) || 'upload failed');
        if (items[0].el) this._setDone(items[0].el, false);
      } else {
        // Multi-file endpoint
        const fd = new FormData();
        for (const it of items){
          if (it.tooLarge){ if (it.el) this._setDone(it.el, true, 'Too large'); continue; }
          fd.append('files', files.find(f => f.name === it.name));
          if (it.el) this._setUploading(it.el);
        }
        const res = await apiFetchWith('/rag/upload-files', { method: 'POST', body: fd }, { workspace: wsOverride || undefined, apiKey: key || undefined });
        data = await res.json();
        if (!res.ok) throw new Error((data && data.error) || 'upload failed');
        // Mark all non-tooLarge as done
        for (const it of items){ if (!it.tooLarge && it.el) this._setDone(it.el, false); }
      }
      const ing = Number(data && data.ingested || data && data.saved || 0);
      const skp = Number(data && data.skipped || 0);
      const msg = `Uploaded and ingested ${ing} file${ing!==1?'s':''} (skipped ${skp}).`;
      if (ing > 0){
        if (out) out.innerHTML = `<span class=\"text-success\">${this._escape(msg)}</span>`;
        try{ emit(document, EV.TOAST, { message: msg }); }catch(_){ }
      } else {
        if (out) out.innerHTML = `<span class=\"text-warning\">${this._escape('No content ingested. Ensure files are supported and under 2MB.')}</span>`;
        try{ emit(document, EV.TOAST, { message: msg }); }catch(_){ }
      }
      if (out && data && Array.isArray(data.warnings) && data.warnings.length){
        const pretty = data.warnings.map(w => this._prettyWarn(w)).join(', ');
        out.innerHTML += `<div class=\"small text-muted mt-1\">Warnings: ${this._escape(pretty)}</div>`;
      }
      // Notify page listeners
      try{
        const ctx = getContext();
        emit(document, EV.RAG_UPLOAD_DONE, { workspace: ctx.workspace || 'default', result: data, files: items.map(i=>i.name) });
        // Also emit on the upload component itself for direct listeners
        emit(this, EV.RAG_UPLOAD_DONE, { workspace: ctx.workspace || 'default', result: data, files: items.map(i=>i.name) });
      }catch(err){ if(isDebugEnabled()) log('upload', err); }
    }catch(e){
      if (out) out.innerHTML = '<span class="text-danger">Error: ' + (e && e.message || 'failed') + '</span>';
      if (isDebugEnabled()) log('upload', e);
      // Mark all queued/uploading items as error
      if (qEl){ qEl.querySelectorAll('[data-role="q-item"]').forEach(el => this._setDone(el, true, 'Error')); }
    }
  }

  _queueItemHTML(name, size, tooLarge){
    const kb = Math.round((size||0)/102.4)/10; // one decimal KB
    const warn = tooLarge ? '<span class="badge text-bg-warning ms-2">Too large</span>' : '';
    return `<div class="list-group-item d-flex align-items-center justify-content-between" data-role="q-item">
      <div class="text-truncate"><i class="bi bi-file-earmark me-2"></i>${this._escape(name)} <span class="text-muted">(${kb} KB)</span>${warn}</div>
      <div data-role="q-state"><span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span></div>
    </div>`;
  }

  _setUploading(el){ try{ const st = el.querySelector('[data-role="q-state"]'); if(st) st.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>'; }catch(_){}}
  _setDone(el, isError, text){ try{ const st = el.querySelector('[data-role="q-state"]'); if(!st) return; if(isError) st.innerHTML = `<span class="text-danger">${this._escape(text||'Error')}</span>`; else st.innerHTML = '<i class="bi bi-check-circle text-success"></i>'; }catch(_){}}

  // _loadEnv removed in favor of store action + subscription

  _renderEnv(env){
    const ok = (b) => b ? '<i class="bi bi-check-circle text-success"></i>' : '<i class="bi bi-x-circle text-danger"></i>';
    const py = env && env.python || {}; const bin = env && env.binaries || {};
    const ocr = !!(env && env.ocr_enabled);
    const exts = Array.isArray(env && env.allowed_exts) ? env.allowed_exts.join(', ') : '';
    const rows = [
      ['PDF parser (pypdf)', ok(!!py.pypdf)],
      ['DOCX parser (python-docx)', ok(!!py.python_docx)],
      ['OCR libs (pdf2image+pytesseract)', ok(!!py.pdf2image && !!py.pytesseract)],
      ['OCR enabled', ocr ? '<span class="badge text-bg-success">On</span>' : '<span class="badge text-bg-secondary">Off</span>'],
      ['Poppler (pdftoppm)', ok(!!bin.poppler)],
      ['Tesseract', ok(!!bin.tesseract)],
      ['Allowed', this._escape(exts)],
    ];
    return '<div class="row small">' + rows.map(([k,v]) => `<div class="col-6"><span class="text-muted">${this._escape(k)}:</span> ${v}</div>`).join('') + '</div>';
  }

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
}

customElements.define('uamm-rag-upload', UammRagUpload);
