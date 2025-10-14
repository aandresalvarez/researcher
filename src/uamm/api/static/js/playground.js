// Playground streaming and UI helpers
(function(){
  let es = null;
  let t0 = null;
  let ft = null;

  window.startStream = function(ev){
    ev && ev.preventDefault && ev.preventDefault();
    const q = document.getElementById('question').value.trim();
    const domain = document.getElementById('domain').value.trim() || 'default';
    const wsInput = document.getElementById('workspace').value.trim();
    const ws = wsInput || (window.getCtxWs && window.getCtxWs()) || 'default';
    const ref = document.getElementById('ref').value.trim();
    const mem = document.getElementById('mem').value.trim();
    const delta = document.getElementById('delta').value.trim();
    if (!q) return;
    resetOutputs();
    const params = {question: q, domain, workspace: ws};
    if (ref) params.max_refinements = ref;
    if (mem) params.memory_budget = mem;
    if (delta) params.borderline_delta = delta;
    const url = `/ui/agent/stream?` + new URLSearchParams(params);
    es = new EventSource(url);
    t0 = performance.now();
    ft = null;
    setStatus('Connecting…', 'secondary');
    toggleButtons(true);
    es.addEventListener('ready', () => setStatus('Streaming…', 'primary'));
    es.addEventListener('token', (e) => {
      const data = JSON.parse(e.data || '{}');
      appendToken(data.text || '');
      if (!ft) {
        ft = performance.now();
        document.getElementById('ft-lat').textContent = Math.round(ft - t0) + ' ms';
      }
    });
    es.addEventListener('tool', (e) => { try { appendTool(JSON.parse(e.data||'{}')); } catch(_){} });
    es.addEventListener('score', (e) => { try { renderScores(JSON.parse(e.data||'{}')); } catch(_){} });
    es.addEventListener('pcn', (e) => { try { renderPCN(JSON.parse(e.data||'{}')); } catch(_){} });
    es.addEventListener('gov', (e) => { try { renderGoV(JSON.parse(e.data||'{}')); } catch(_){} });
    es.addEventListener('error', () => { setStatus('Error', 'danger'); window.showToast && window.showToast('Streaming error'); });
    es.addEventListener('final', (e) => {
      try {
        const d = JSON.parse(e.data||'{}');
        document.getElementById('final-json').textContent = JSON.stringify(d, null, 2);
      } catch(_){}
      const total = performance.now() - t0;
      document.getElementById('tot-lat').textContent = Math.round(total) + ' ms';
      setStatus('done', 'success');
      window.stopStream();
    });
  };

  window.stopStream = function(){
    if (es) { try { es.close(); } catch(_){} es = null; }
    toggleButtons(false);
  };

  function resetOutputs(){
    ['answer','tools-log','scores','pcn','gov','final-json'].forEach(id => {
      const el = document.getElementById(id); if (!el) return; if (id==='tools-log') el.innerHTML=''; else el.textContent='';
    });
    const ftEl = document.getElementById('ft-lat'); if (ftEl) ftEl.textContent = '–';
    const totEl = document.getElementById('tot-lat'); if (totEl) totEl.textContent = '–';
  }

  function appendToken(t){ const el = document.getElementById('answer'); if (el) el.textContent += (t + ' '); }
  function appendTool(d){ const el = document.getElementById('tools-log'); if (!el) return; const li = document.createElement('li'); const name = d && d.name ? d.name : 'tool'; const status = d && d.status ? d.status : ''; const ts = new Date().toLocaleTimeString(); li.textContent = `[${ts}] ${name} — ${status}`; el.appendChild(li); }
  function renderScores(d){ const el = document.getElementById('scores'); if (!el) return; if (!d||typeof d!=='object'){ el.textContent=''; return;} const s1=d.s1??d.snne??''; const s2=d.s2??''; const fs=d.final_score??d.S??''; const tau=d.tau??d.cp_tau??''; el.textContent=`SNNE: ${s1}  Verifier: ${s2}  Final: ${fs}  tau: ${tau}`; }
  function renderPCN(d){ const el = document.getElementById('pcn'); if (el) el.textContent = JSON.stringify(d||{}); }
  function renderGoV(d){ const el = document.getElementById('gov'); if (el) el.textContent = JSON.stringify(d||{}); }
  function setStatus(text, kind){ const el = document.getElementById('status'); if (el){ el.textContent = text; el.className = 'badge text-bg-' + (kind||'secondary'); } }
  function toggleButtons(streaming){ const s=document.getElementById('btn-start'); const p=document.getElementById('btn-stop'); if (s) s.disabled=streaming; if (p) p.disabled=!streaming; }

  window.resetPlaygroundForm = function(){ const f=document.getElementById('ask-form'); if (f) f.reset(); resetOutputs(); setStatus('Idle','secondary'); };
  window.copyFinalJson = function(){ try{ const txt = document.getElementById('final-json').textContent || ''; navigator.clipboard.writeText(txt); }catch(_){} };
  window.copyCurl = function(){ try{ const origin=window.location.origin; const url=origin+'/agent/answer'; const q=document.getElementById('question').value.trim(); const domain=document.getElementById('domain').value.trim()||'default'; const key=document.getElementById('key').value.trim(); const ws=document.getElementById('workspace').value.trim(); const ref=document.getElementById('ref').value.trim(); const mem=document.getElementById('mem').value.trim(); const delta=document.getElementById('delta').value.trim(); const body={question:q,domain,use_memory:true,memory_budget:parseInt(mem||'8'),max_refinements:parseInt(ref||'2'),borderline_delta:parseFloat(delta||'0.05'),stream:false}; let cmd=`curl -s -X POST ${url} \\\n+  -H 'content-type: application/json' \\\n+  -d '${JSON.stringify(body)}'`; if (key){ cmd+=` \\\n+  -H 'Authorization: Bearer ${key}'`; } if (ws){ cmd+=` \\\n+  -H 'X-Workspace: ${ws}'`; } navigator.clipboard.writeText(cmd); }catch(_){} };
})();

