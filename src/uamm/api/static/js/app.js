// Minimal global UI helpers kept for legacy templates (no new deps)
(function(){
  // Sidebar collapse or offcanvas on small screens
  window.toggleSidebar = function(){
    try{
      var aside = document.getElementById('sidebar');
      if (!aside) return;
      aside.classList.toggle('collapsed');
      var collapsed = aside.classList.contains('collapsed');
      try { localStorage.setItem('uamm.sidebarCollapsed', collapsed ? '1' : '0'); } catch(_) {}
      try {
        var btn = document.getElementById('sidebarToggle');
        if (btn) btn.setAttribute('aria-pressed', collapsed ? 'true' : 'false');
        aside.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      } catch(_){}
    }catch(e){}
  };
  window.toggleSidebarResponsive = function(){
    try{
      if (window.matchMedia('(max-width: 767px)').matches) {
        var oc = document.getElementById('sidebarOffcanvas');
        if (oc && window.bootstrap) {
          window.bootstrap.Offcanvas.getOrCreateInstance(oc).toggle();
          return;
        }
      }
      window.toggleSidebar();
    }catch(e){}
  };

  // Active nav highlight
  window.initActiveNavHighlight = function(){
    try{
      var path = window.location.pathname;
      var hash = window.location.hash || '';
      document.querySelectorAll('.navbar .nav-link').forEach(function(a){
        var href = a.getAttribute('href');
        a.classList.remove('active');
        if (!href) return;
        if (href.startsWith('#/')) {
          if (hash === href || hash.startsWith(href + '?')) a.classList.add('active');
        } else {
          if (path.startsWith(href)) a.classList.add('active');
        }
      });
      // Sync toggle aria state on load
      try{
        var aside = document.getElementById('sidebar');
        var btn = document.getElementById('sidebarToggle');
        if (aside && btn) btn.setAttribute('aria-pressed', aside.classList.contains('collapsed') ? 'true' : 'false');
        if (aside) aside.setAttribute('aria-expanded', aside.classList.contains('collapsed') ? 'false' : 'true');
      }catch(_){}
    }catch(e){}
  };

  // Bootstrap tooltips initializer
  window.initTooltips = function(){
    try{
      document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function(el){ new bootstrap.Tooltip(el); });
    }catch(e){}
  };

  // Simple sparkline (used by Workspaces page)
  window.drawSpark = function(id, values, color){
    try{
      var svg = document.getElementById(id); if (!svg) return;
      var w = svg.viewBox.baseVal.width || 140;
      var h = svg.viewBox.baseVal.height || 30;
      var arr = Array.isArray(values) ? values : [1];
      var n = arr.length || 1;
      var max = Math.max(1, ...arr);
      var step = w / Math.max(1, n-1);
      var d = '';
      arr.forEach(function(v,i){
        var x = i*step; var y = h - (v/max)*(h-2) - 1;
        d += (i===0 ? ('M '+x+' '+y) : (' L '+x+' '+y));
      });
      svg.innerHTML = '<path d="'+d+'" stroke="'+(color||'#0d6efd')+'" stroke-width="2" fill="none"/>';
    }catch(e){}
  };
})();
