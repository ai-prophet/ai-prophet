"""Lightweight local dashboard — minimal server with proxy.

Serves HTML at / and proxies /api/* to the core API so CORS is
never an issue regardless of server config. The slug filter is
baked into the HTML at generation time.

Usage:
    ai-prophet dashboard
    ai-prophet dashboard --slug my_experiment
    ai-prophet eval run -m openai:gpt-5.2 --slug test --dashboard
"""

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import httpx

_API_URL = ""
_SLUG = ""
_HTML_BYTES = b""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._proxy(parsed.path[4:], parsed.query)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_HTML_BYTES)

    def _proxy(self, path, query):
        url = f"{_API_URL}{path}"
        if query:
            url += f"?{query}"
        try:
            resp = httpx.get(url, timeout=15)
            data = resp.content
            # Scope /experiments to configured slug
            if _SLUG and path == "/experiments" and resp.status_code == 200:
                items = resp.json()
                if isinstance(items, list):
                    data = json.dumps([e for e in items if e.get("experiment_slug") == _SLUG]).encode()
            self.send_response(resp.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, fmt, *args):
        pass


def open_dashboard(api_url: str, slug: str = "", port: int = 8501):
    """Serve the dashboard and open it in the browser."""
    global _API_URL, _SLUG, _HTML_BYTES
    _API_URL = api_url.rstrip("/")
    _SLUG = slug
    _HTML_BYTES = _HTML.encode()

    import click

    server = HTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    webbrowser.open(f"http://localhost:{port}")
    click.echo(f"  Dashboard: http://localhost:{port}")
    click.echo(f"  Core API:  {_API_URL}")
    if slug:
        click.echo(f"  Experiment: {slug}")


# ---------------------------------------------------------------------------
# Self-contained HTML — hits core API directly via CORS
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PA Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;background:#0a0a0a;color:#e0e0e0;font-family:'JetBrains Mono','SF Mono',monospace;font-size:13px;line-height:1.5}
:root{--bg:#0a0a0a;--fg:#e0e0e0;--green:#22c55e;--red:#ef4444;--yellow:#eab308;--blue:#3b82f6;--dim:#525252;--border:#262626}
main{max-width:960px;margin:0 auto;padding:24px}
.header{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:32px}
.header h1{font-size:14px;font-weight:500;letter-spacing:.05em}
.header .meta{font-size:12px;color:var(--dim)}
.status-bar{display:flex;gap:32px;margin-bottom:24px;padding-bottom:16px;border-bottom:1px solid var(--border)}
.stat-label{color:var(--dim);font-size:11px;margin-bottom:4px}
.exp-info{margin-bottom:24px;font-size:11px;color:var(--dim)}
.exp-info .slug{font-size:12px;margin-bottom:4px}
.section-toggle{display:flex;justify-content:space-between;align-items:center;cursor:pointer;padding-bottom:12px;border-bottom:1px solid var(--border);margin-bottom:12px;margin-top:32px}
.section-toggle .title{font-size:12px;font-weight:500;letter-spacing:.05em}
.section-toggle .count{color:var(--dim);font-size:12px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;color:var(--dim);font-size:11px;font-weight:400;padding:4px 8px 8px 0;border-bottom:1px solid var(--border)}
th.r{text-align:right}
td{padding:6px 8px 6px 0;border-bottom:1px solid var(--border)}
td.r{text-align:right}
td.clip{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:200px}
.empty{color:var(--dim);padding:24px 0;text-align:center}
.pill{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;font-weight:500}
.scroll{max-height:400px;overflow-y:auto}
.card{margin-bottom:12px;padding:12px;background:rgba(255,255,255,.02);border-radius:4px;border:1px solid var(--border)}
.card-hd{display:flex;justify-content:space-between;font-size:11px;margin-bottom:8px}
.reason-row{margin-bottom:6px;padding-left:8px;border-left:2px solid var(--border);font-size:12px}
.reason-row .sub{font-size:11px;color:var(--dim);line-height:1.5;margin-top:2px}
.filter-bar{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap}
.fbtn{padding:4px 8px;font-size:11px;font-family:inherit;cursor:pointer;border:1px solid var(--border);border-radius:4px;background:transparent;color:var(--dim)}
.fbtn.on{background:var(--green);color:var(--bg)}
.hide{display:none}
</style>
</head>
<body>
<main>
  <div class="header">
    <h1>PA DASHBOARD</h1>
    <span class="meta" id="hdr">connecting...</span>
  </div>
  <div id="err" class="hide" style="color:var(--red);margin-bottom:16px;font-size:12px"></div>
  <div id="bar" class="status-bar hide"></div>
  <div id="info" class="exp-info hide"></div>
  <div id="parts"></div>
  <div id="trades"></div>
  <div id="reasoning"></div>
</main>
<script>
const API = '/api';

let exp = null, parts = [], fills = null, reasons = [];
let showFills = false, showReasons = false, rFilter = null;

const $ = id => document.getElementById(id);
const esc = s => { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; };
const fmt = (v, d=4) => { if (v==null) return '-'; const n=+v; return isFinite(n)?n.toLocaleString(undefined,{maximumFractionDigits:d}):'-'; };
const usd = v => { if (v==null) return '-'; const n=+v; return isFinite(n)?n.toLocaleString(undefined,{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:2}):'-'; };
const time = iso => { if(!iso)return '-'; const d=new Date(iso); return isFinite(d)?d.toLocaleString():'-'; };

async function get(path) { const r = await fetch(API + path); return r.ok ? r.json() : null; }

async function load() {
  const list = await get('/experiments');
  if (!list) return;
  let exps = Array.isArray(list) ? list : (list.experiments || []);
  exps.sort((a,b) => (a.status==='RUNNING'?0:1)-(b.status==='RUNNING'?0:1) || (b.last_activity_at||'').localeCompare(a.last_activity_at||''));
  if (!exps.length) { exp = null; $('hdr').textContent = 'no experiments'; return; }
  const id = exps[0].experiment_id;
  const [detail, p, t, prog] = await Promise.all([
    get('/experiments/'+id), get('/experiments/'+id+'/participants'),
    get('/experiments/'+id+'/trades?limit=200'), get('/experiments/'+id+'/progress'),
  ]);
  exp = detail;
  if (prog && exp) { exp._done = prog.completed||0; exp._total = prog.n_ticks||exp.n_ticks; }
  parts = Array.isArray(p) ? p : (p?.participants||[]);
  fills = t;
  $('hdr').textContent = exp.experiment_slug;
}

function render() {
  const bar = $('bar');
  if (!exp) { bar.classList.add('hide'); $('info').classList.add('hide'); $('parts').innerHTML=''; $('trades').innerHTML=''; $('reasoning').innerHTML=''; return; }
  bar.classList.remove('hide');
  const sc = exp.status==='RUNNING'?'var(--green)':exp.status==='COMPLETED'?'var(--blue)':exp.status==='ABORTED'?'var(--red)':'var(--dim)';
  bar.innerHTML = [
    ['STATUS', `<span style="color:${sc}">${esc(exp.status)}</span>`],
    ['TICKS', `${exp._done??exp.completed_ticks??0} / ${exp._total??exp.n_ticks??'?'}`],
    ['PARTICIPANTS', parts.length],
    ['TRADES', fills?.total??fills?.fills?.length??'-'],
  ].map(([l,v])=>`<div><div class="stat-label">${l}</div><div>${v}</div></div>`).join('');

  const info = $('info'); info.classList.remove('hide');
  info.innerHTML = `<div class="slug">${esc(exp.experiment_slug)}</div>
    <div style="font-family:monospace">id: ${esc(exp.experiment_id)}</div>
    ${exp.last_activity_at?'<div style="margin-top:4px">Last activity: '+time(exp.last_activity_at)+'</div>':''}`;

  const pm = {};
  parts.forEach(p => { pm[p.participant_idx] = p.model+':rep'+p.rep; });

  $('parts').innerHTML = parts.length ? `<table>
    <tr><th>IDX</th><th>MODEL</th><th class="r">REP</th></tr>
    ${parts.map(p=>`<tr><td style="color:var(--dim)">${p.participant_idx}</td><td>${esc(p.model)}</td><td class="r">${p.rep}</td></tr>`).join('')}
  </table>` : '<div class="empty">No participants</div>';

  const ff = fills?.fills||fills?.trades||[];
  $('trades').innerHTML = `
    <div class="section-toggle" onclick="showFills=!showFills;render()">
      <div class="title">TRADE HISTORY</div>
      <div class="count">${fills?.total??ff.length} fills ${showFills?'▾':'▸'}</div>
    </div>
    ${showFills ? (!ff.length ? '<div class="empty">No trades yet</div>' : `<div class="scroll"><table>
      <tr><th>MODEL</th><th>MARKET</th><th>ACTION</th><th>SIDE</th><th class="r">SHARES</th><th class="r">PRICE</th><th class="r">NOTIONAL</th><th class="r">TIME</th></tr>
      ${ff.map(f=>{
        const s=+f.shares, p=+f.price, n=isFinite(s)&&isFinite(p)?Math.abs(s)*p:NaN;
        return `<tr>
          <td class="clip" style="max-width:120px">${esc(pm[f.participant_idx]||'#'+f.participant_idx)}</td>
          <td class="clip" title="${esc(f.market_id)}">${esc(f.market_question||f.market_id||'-')}</td>
          <td style="color:${f.action==='BUY'?'var(--green)':'var(--red)'}">${f.action}</td>
          <td style="color:${f.side==='YES'?'var(--blue)':'var(--yellow)'}">${f.side}</td>
          <td class="r">${fmt(f.shares)}</td><td class="r">${fmt(f.price)}</td>
          <td class="r">${usd(n)}</td>
          <td class="r" style="color:var(--dim);font-size:11px">${time(f.filled_at)}</td>
        </tr>`;}).join('')}
    </table></div>`) : ''}`;

  const rf = rFilter===null ? reasons : reasons.filter(r=>r.participant_idx===rFilter);
  $('reasoning').innerHTML = `
    <div class="section-toggle" onclick="toggleReasons()">
      <div class="title">REASONING</div>
      <div class="count">${reasons.length} entries ${showReasons?'▾':'▸'}</div>
    </div>
    ${showReasons ? renderReasons(rf, pm) : ''}`;
}

function renderReasons(entries, pm) {
  let fb = '';
  if (parts.length > 1) {
    fb = '<div class="filter-bar">' +
      `<button class="fbtn ${rFilter===null?'on':''}" onclick="rFilter=null;render()">All</button>` +
      parts.map(p=>`<button class="fbtn ${rFilter===p.participant_idx?'on':''}" onclick="rFilter=${p.participant_idx};render()">${esc(p.model)}:rep${p.rep}</button>`).join('') + '</div>';
  }
  if (!entries.length) return fb + '<div class="empty">No reasoning data</div>';
  return fb + '<div class="scroll">' + entries.map(e => {
    const r = e.reasoning||{}, model = pm[e.participant_idx]||'#'+e.participant_idx;
    let body = '';
    if (r.forecasts) {
      body += '<div style="font-size:11px;color:var(--blue);font-weight:500;margin:6px 0">FORECASTS</div>';
      for (const [mid,f] of Object.entries(r.forecasts)) {
        const py = f.p_yes!=null ? `<span class="pill" style="background:rgba(100,100,255,.15);margin-left:6px">P(YES) = ${(f.p_yes*100).toFixed(1)}%</span>` : '';
        body += `<div class="reason-row"><div>${esc(f.question||mid)}${py}</div>${f.rationale?'<div class="sub">'+esc(f.rationale)+'</div>':''}</div>`;
      }
    }
    if (r.decisions) {
      body += '<div style="font-size:11px;color:var(--green);font-weight:500;margin:6px 0">DECISIONS</div>';
      for (const [mid,d] of Object.entries(r.decisions)) {
        const hold=d.recommendation==='HOLD', buy=(d.recommendation||'').includes('BUY');
        const c=hold?'var(--dim)':buy?'var(--green)':'var(--red)';
        const bg=hold?'rgba(100,100,100,.15)':buy?'rgba(34,197,94,.15)':'rgba(239,68,68,.15)';
        const sz=d.size_usd!=null&&d.size_usd>0?`<span style="margin-left:6px;font-size:11px;color:var(--dim)">${usd(d.size_usd)}</span>`:'';
        body += `<div class="reason-row"><div>${esc(d.question||mid)}<span class="pill" style="background:${bg};color:${c};margin-left:6px">${d.recommendation||'-'}</span>${sz}</div>${d.rationale?'<div class="sub">'+esc(d.rationale)+'</div>':''}</div>`;
      }
    }
    return `<div class="card"><div class="card-hd"><span>${esc(model)}</span><span style="color:var(--dim)">${time(e.tick_id)}</span></div>${body}</div>`;
  }).join('') + '</div>';
}

async function toggleReasons() {
  showReasons = !showReasons;
  if (showReasons && !reasons.length && exp) {
    const d = await get('/experiments/'+exp.experiment_id+'/reasoning');
    if (d?.reasoning) reasons = d.reasoning;
  }
  render();
}

(async () => {
  try { await load(); } catch(e) { $('err').textContent='Error: '+e.message; $('err').classList.remove('hide'); }
  render();
  setInterval(async () => { try { await load(); render(); } catch(e) {} }, 10000);
})();
</script>
</body>
</html>
"""
