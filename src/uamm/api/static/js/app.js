// Global UI helpers and sidebar toggler
(function(){
  window.toggleSidebar = function(){
    try{
      var aside = document.getElementById('sidebar');
      if (!aside) return;
      aside.classList.toggle('collapsed');
    }catch(e){}
  };
  // On small screens, toggle opens offcanvas instead
  window.toggleSidebarResponsive = function(){
    try{
      if (window.matchMedia('(max-width: 767px)').matches) {
        var oc = document.getElementById('sidebarOffcanvas');
        if (oc && window.bootstrap) {
          var off = new window.bootstrap.Offcanvas(oc);
          off.toggle();
          return;
        }
      }
      window.toggleSidebar();
    }catch(e){}
  };
  // Sidebar Workspaces loader with counts (Docs/Steps)
  window.sidebarLoadWorkspaces = async function(){
    const el = document.getElementById('sidebar-ws'); if (!el) return; const sp = document.getElementById('sb-spinner'); if (sp) sp.classList.remove('d-none');
    el.innerHTML = '<div class="list-group-item small text-muted">Loading…</div>';
    try{
      const res = await window.ctxFetch('/workspaces');
      const data = await res.json();
      if (!res.ok) throw new Error('failed');
      const ws = data.workspaces || [];
      if (!ws.length){ el.innerHTML = '<div class="list-group-item small">No workspaces</div>'; return; }
      const cur = (window.uammCtx && window.uammCtx.ws) || 'default';
      // Pre-render list with icon and placeholder badge
      el.innerHTML = ws.map(w => {
        const active = w.slug===cur;
        const dot = active ? '<i class="bi bi-record-fill text-primary me-1"></i>' : '<i class="bi bi-diagram-3 me-2"></i>';
        return `<div class="list-group-item d-flex align-items-center ${active?'active':''}" onclick="window.sidebarSelect('${w.slug}')">${dot}<span class="ws-label text-truncate">${escapeHtml(w.slug)}</span><span class="ms-auto" id="sb-count-${w.slug}">…</span></div>`;
      }).join('');
      // Fetch counts in background
      for (const w of ws){
        try{
          const rs = await window.ctxFetch('/workspaces/' + encodeURIComponent(w.slug) + '/stats');
          const st = await rs.json();
          const cnt = st && st.counts ? st.counts : {};
          const badge = document.getElementById('sb-count-' + w.slug);
          if (badge) badge.innerHTML = `<span class="badge rounded-pill text-bg-secondary me-1" data-bs-toggle="tooltip" title="Docs">D:${cnt.docs||0}</span><span class="badge rounded-pill text-bg-secondary" data-bs-toggle="tooltip" title="Steps">S:${cnt.steps||0}</span>`;
        }catch(e){ /* ignore */ }
      }
      if (window.initTooltips) window.initTooltips();
    } catch(e){ el.innerHTML = '<div class="list-group-item small text-danger">Error</div>'; }
    finally { if (sp) sp.classList.add('d-none'); }
  };
  // Active nav highlight
  window.initActiveNavHighlight = function(){ try{ const path = window.location.pathname; document.querySelectorAll('.navbar .nav-link').forEach(a => { if (a.getAttribute('href') && path.startsWith(a.getAttribute('href'))) { a.classList.add('active'); } }); }catch(e){} };
  // Tooltips init
  window.initTooltips = function(){ try{ document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => { new bootstrap.Tooltip(el); }); }catch(e){} };
  // Domain suggestions from metrics
  window.loadDomainSuggestions = async function(){ try { const res = await window.ctxFetch('/metrics'); const data = await res.json(); if (!res.ok) throw new Error('failed'); const byDom = (data.by_domain || {}); const uqDom = (data.uq_by_domain || {}); const keys = Array.from(new Set([...Object.keys(byDom), ...Object.keys(uqDom)])).filter(Boolean).sort(); const dl = document.getElementById('ctxp-domain-list'); if (dl) dl.innerHTML = keys.map(k => `<option value="${escapeHtml(k)}">`).join(''); const domInput = document.getElementById('ctxp-domain'); if (domInput) { const cur = (domInput.value || '').trim().toLowerCase(); if (!cur || cur === 'default') { let best = null; let bestCount = -1; for (const [dom, stats] of Object.entries(byDom)) { const count = (stats && stats.answers) ? stats.answers : 0; if (count > bestCount) { best = dom; bestCount = count; } } if (best) domInput.value = best; } } } catch(e){} };
  // Load CP tau for domain
  window.ctxLoadTau = async function(){ try { var inp = document.getElementById('ctxp-domain'); var dom = (inp && inp.value.trim()) || 'default'; if (!dom || dom==='default') { var pd = document.getElementById('domain'); if (pd && pd.value) dom = pd.value.trim(); var fd = document.getElementById('filter-domain'); if (fd && fd.value) dom = fd.value.trim(); }
      var res = await window.ctxFetch('/cp/threshold?' + new URLSearchParams({domain: dom||'default'})); var data = await res.json(); if (!res.ok) throw new Error('failed'); var tau = (data.tau===null||data.tau===undefined) ? 'n/a' : data.tau; var chip = document.getElementById('ctxp-tau'); if (chip) { chip.textContent = 'τ(' + (dom||'default') + '): ' + tau; chip.classList.remove('text-bg-secondary','text-bg-info','text-bg-success'); chip.classList.add((tau==='n/a')?'text-bg-secondary':'text-bg-success'); } if (inp && (!inp.value || inp.value==='default')) inp.value = dom; } catch(e){ var chip = document.getElementById('ctxp-tau'); if (chip) { chip.textContent = 'τ: n/a'; chip.classList.remove('text-bg-success','text-bg-info'); chip.classList.add('text-bg-secondary'); } } };
  // Sidebar core functions
  window.sidebarSelect = function(slug){ window.uammCtx.ws = slug; localStorage.setItem('uamm.ws', slug); if (window.ctxLoadStats) window.ctxLoadStats(); if (window.sidebarLoadWorkspaces) window.sidebarLoadWorkspaces(); if (window.bootstrap) try { const ocEl=document.getElementById('sidebarOffcanvas'); if (ocEl){ bootstrap.Offcanvas.getOrCreateInstance(ocEl).hide(); } }catch(e){} window.showToast && window.showToast('Workspace set: ' + slug); };
  window.sidebarCreateTest = async function(){ const slug = 'test-' + Math.random().toString(36).slice(2,8); try { const res = await window.ctxFetch('/workspaces', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({slug, name: 'Test '+slug})}); const data=await res.json(); if(!res.ok) throw new Error(data.error||'failed'); window.sidebarSelect(slug); } catch(e){ window.showToast && window.showToast('Failed to create'); } };
  // Page specifics
  window.initHome = async function(){ try{ const res = await window.ctxFetch('/settings'); const data = await res.json(); if (!res.ok) throw new Error('failed'); const s = data.settings || {}; const th = document.getElementById('hs-thresh'); if (th) { th.value = s.accept_threshold ?? 0.85; document.getElementById('hs-thresh-val').textContent = th.value; } const dl = document.getElementById('hs-delta'); if (dl) { dl.value = s.borderline_delta ?? 0.05; document.getElementById('hs-delta-val').textContent = dl.value; } const sn = document.getElementById('hs-snne'); if (sn) { sn.value = s.snne_samples ?? 5; document.getElementById('hs-snne-val').textContent = sn.value; } }catch(e){} };
  window.initPlayground = function(){ try{ const p = new URLSearchParams(window.location.search); const q = p.get('q') || ''; const dom = p.get('domain') || ''; const qel = document.getElementById('question'); const del = document.getElementById('domain'); if (qel && q) qel.value = q; if (del && dom) del.value = dom; if (q && p.get('autostart') === '1') { const form = document.getElementById('ask-form'); if (form && window.startStream) window.startStream(new Event('submit')); } }catch(e){} };
  // Workspaces page helpers: delete confirm
  window.confirmDelete = async function(slug){ const ws = decodeURIComponent(slug); if (!window.confirm(`Delete workspace '${ws}'? This removes metadata; files are kept unless purge is enabled in code.`)) return; try { const res = await window.ctxFetch(`/workspaces/${encodeURIComponent(ws)}/delete`, { method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({purge:false}) }); const data = await res.json(); if (!res.ok) throw new Error(data.error||'failed'); window.showToast && window.showToast('Deleted workspace ' + ws); if (window.loadWorkspaces) window.loadWorkspaces(); if (window.sidebarLoadWorkspaces) window.sidebarLoadWorkspaces(); } catch(e){ window.showToast && window.showToast('Failed to delete'); } };
  // Simple sparkline drawer
  window.drawSpark = function(id, values, color){ try { const svg = document.getElementById(id); if (!svg) return; const w = svg.viewBox.baseVal.width || 140; const h = svg.viewBox.baseVal.height || 30; const n = values.length || 1; const max = Math.max(1, ...values); const step = w / Math.max(1, n-1); let d = ''; values.forEach((v,i)=>{ const x=i*step; const y=h - (v/max)* (h-2) - 1; d += (i===0?`M ${x} ${y}`:` L ${x} ${y}`); }); svg.innerHTML = `<path d="${d}" stroke="${color||'#0d6efd'}" stroke-width="2" fill="none"/>`; } catch(e){} };
  // escape helper
  function escapeHtml(s){ return (s||'').toString().replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c])); }
})();
