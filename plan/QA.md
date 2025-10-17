UI Smoke Test Checklist

Sidebar & Context
- Load /ui/home with no query params; sidebar lists workspaces.
- Create Test workspace; selection updates counts and τ chip.
- Toggle small-screen offcanvas; selection hides offcanvas.

Playground
- Load /ui; stream a question; observe FT and TOT latencies update.
- Tools, scores, PCN, GoV sections populate; final JSON copies.
- Copy cURL produces valid command.

Observability
- Load /ui/obs; metrics show counts/rates/latency; alerts render.
- Recent steps load; filter by domain and action; view step opens offcanvas.
- Select multiple steps and copy pack to clipboard.

RAG
- Upload a small file; ingest folder (empty path OK); search for a term.

CP
- Enter domain; fetch τ and stats; try with/without admin key.

Evals
- Load suites; select and run; verify result table.
- Add ad-hoc item and run; tuner propose then apply (if allowed).
- Load runs and view a report.

Debug & Flags
- Set localStorage['uamm.debug']=1 to see console logs.
- Optionally set localStorage['uamm.devtools']=1 to view event overlay.
