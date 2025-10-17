import { apiFetch } from '../core/api.mjs';
import { setContext } from '../core/context.mjs';
import { EV, emit } from '../core/events.mjs';

export class UammWizard extends HTMLElement {
  constructor(){ super(); this._mounted = false; }

  connectedCallback(){
    if (this._mounted) return; this._mounted = true;
    this.innerHTML = this._template();
    const btn = this.querySelector('[data-action="create"]');
    if (btn) btn.addEventListener('click', () => this._run());
  }

  disconnectedCallback(){ this._mounted = false; }

  _template(){
    return `
    <div class="modal fade" id="wsWizardGlobal" tabindex="-1" aria-labelledby="wsWizardGlobalLabel" aria-hidden="true">
      <div class="modal-dialog modal-lg">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title" id="wsWizardGlobalLabel">Create Workspace — Wizard</h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
          </div>
          <div class="modal-body">
            <div class="row g-2">
              <div class="col-md-4"><label class="form-label small">Slug</label><input id="g-wz-slug" class="form-control" placeholder="team-abc"></div>
              <div class="col-md-8"><label class="form-label small">Name</label><input id="g-wz-name" class="form-control" placeholder="Team ABC"></div>
              <div class="col-12 form-check"><input id="g-wz-auto" class="form-check-input" type="checkbox" checked><label class="form-check-label" for="g-wz-auto">Auto root under data/workspaces/&lt;slug&gt;</label></div>
              <div class="col-12"><label class="form-label small">Custom root</label><input id="g-wz-root" class="form-control" placeholder="/abs/path/within/allowed/bases"></div>
              <div class="col-md-6"><label class="form-label small">Editor key label</label><input id="g-wz-label" class="form-control" value="editor-ui"></div>
              <div class="col-md-6"><label class="form-label small">Seed</label><select id="g-wz-seed" class="form-select"><option value="readme">README excerpt</option><option value="hello">Hello workspace</option><option value="none">Do not seed</option></select></div>
            </div>
            <div id="g-wz-status" class="small text-muted mt-2"></div>
          </div>
          <div class="modal-footer">
            <button class="btn btn-outline-secondary" data-bs-dismiss="modal">Close</button>
            <button class="btn btn-primary" data-action="create">Create</button>
          </div>
        </div>
      </div>
    </div>`;
  }

  async _run(){
    const slug = (this._el('g-wz-slug').value || '').trim();
    if (!slug) { this._toast('Slug is required'); return; }
    const name = (this._el('g-wz-name').value || slug).trim();
    const auto = this._el('g-wz-auto').checked;
    const given = (this._el('g-wz-root').value || '').trim();
    const root = auto ? ('data/workspaces/' + slug) : (given || null);
    const label = (this._el('g-wz-label').value || 'editor-ui').trim();
    const seed = this._el('g-wz-seed').value;
    const out = this._el('g-wz-status');
    out.textContent = 'Creating…';
    try{
      // Create workspace
      let res = await apiFetch('/workspaces', { method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({ slug, name, root }) });
      let data = await res.json();
      if (!res.ok) {
        res = await apiFetch('/workspaces', { method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({ slug, name }) });
        data = await res.json();
        if (!res.ok) throw new Error((data && data.error) || 'failed');
      }
      // Issue editor key (best-effort)
      try {
        const kres = await apiFetch(`/workspaces/${encodeURIComponent(slug)}/keys`, { method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({ role:'editor', label }) });
        const kd = await kres.json();
        if (kres.ok && kd.api_key){
          // Persist in legacy localStorage for continuity with existing UI
          try { localStorage.setItem('uamm.key', kd.api_key); } catch(_){}
        }
      } catch(_){}
      // Seed doc (best-effort)
      if (seed !== 'none'){
        const title = seed==='readme' ? 'README excerpt' : 'Hello workspace';
        const text = seed==='readme' ? 'This workspace was bootstrapped for testing UAMM UI flows.' : `Welcome to ${slug}!`;
        try { await apiFetch('/rag/docs', { method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({ title, text }) }); } catch(_){}
      }
      // Switch context, refresh
      setContext({ workspace: slug });
      try { const comp = document.querySelector('uamm-sidebar'); if (comp && comp.refresh) comp.refresh(); } catch(_){}
      out.innerHTML = `Done. <strong>${slug}</strong> ready. <a href="/ui?ws=${encodeURIComponent(slug)}">Open Playground</a>`;
      this._toast('Workspace created: ' + slug);
    } catch(e){ out.textContent = 'Error: ' + (e && e.message ? e.message : 'failed'); }
  }

  _el(id){ return this.querySelector('#' + id); }
  _toast(msg){ try{ emit(document, EV.TOAST, { message: msg }); }catch(_){} }
}

customElements.define('uamm-wizard', UammWizard);
