"""A tiny built-in web dashboard so a phone/iPad/second screen can watch the
kill feed live over the local network. No dependencies (stdlib http.server).

Open http://<PC-IP>:<port> in the device's browser (same Wi-Fi). The page polls
/status once a second; the recorder updates a shared LiveState as kills happen.
"""

import json
import os
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class LiveState:
    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.count = 0
        self.started = ""
        self._mono = None
        self.events = deque(maxlen=30)

    def set_running(self, running):
        with self._lock:
            self.running = running
            if running:
                self.started = time.strftime("%H:%M")
                self._mono = time.monotonic()

    def record(self, count, tag, text):
        with self._lock:
            self.count = count
            self.events.appendleft(
                {"time": time.strftime("%H:%M:%S"), "tag": tag, "text": (text or "")[:60]})

    def snapshot(self):
        with self._lock:
            dur = int(time.monotonic() - self._mono) if self._mono else 0
            return {"running": self.running, "count": self.count,
                    "started": self.started, "duration_s": dur,
                    "events": list(self.events)}


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def start_web(state, port, base_dir):
    imgs = {"/skull.png": "marathon_skull.png",
            "/wordmark.png": "marathon_wordmark.png"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass  # quiet

        def _send(self, body, ctype, cache=True):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            if not cache:
                self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            try:
                if path == "/status":
                    self._send(json.dumps(state.snapshot()).encode(),
                               "application/json", cache=False)
                elif path in imgs:
                    fp = os.path.join(base_dir, imgs[path])
                    if os.path.exists(fp):
                        with open(fp, "rb") as f:
                            self._send(f.read(), "image/png")
                    else:
                        self.send_error(404)
                else:
                    self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8", cache=False)
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = ThreadingHTTPServer(("0.0.0.0", int(port)), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Marathon Kill Feed</title>
<style>
  :root { --bg:#0b0f12; --panel:#12181d; --line:#232d34; --text:#e8edf0;
          --muted:#7d8a94; --accent:#d3f24b; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body { margin:0; background:var(--bg); color:var(--text); text-align:center;
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
    padding:20px 16px 40px; -webkit-font-smoothing:antialiased; }
  .wrap { max-width:640px; margin:0 auto; }
  header { display:flex; align-items:center; justify-content:center; gap:12px; margin-bottom:6px; }
  header img { height:34px; }
  .status { font-size:.8rem; letter-spacing:.16em; text-transform:uppercase; margin-bottom:18px; }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:7px;
    vertical-align:middle; background:var(--muted); }
  .live .dot { background:#5bd66b; box-shadow:0 0 10px #5bd66b; }
  .count { font-size:26vw; line-height:.9; font-weight:800; color:var(--accent); }
  @media(min-width:520px){ .count{ font-size:150px; } }
  .sub { color:var(--muted); font-size:.85rem; margin:6px 0 22px; }
  .feed { text-align:left; display:flex; flex-direction:column; gap:8px; }
  .row { background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:11px 14px; display:flex; align-items:center; gap:12px; }
  .row.precision { border-color:var(--accent); }
  .badge { font-size:.62rem; letter-spacing:.1em; text-transform:uppercase; padding:3px 8px;
    border-radius:5px; background:#1c2630; color:var(--muted); white-space:nowrap; }
  .precision .badge { background:var(--accent); color:#0b0f12; }
  .finisher .badge { background:#f5a623; color:#0b0f12; }
  .assist .badge { background:#37cabb; color:#0b0f12; }
  .row .t { color:var(--muted); font-size:.75rem; margin-left:auto; font-variant-numeric:tabular-nums; }
  .skull { height:18px; vertical-align:middle; }
  .empty { color:var(--muted); padding:30px; }
</style></head><body><div class="wrap">
  <header><img src="/wordmark.png" alt="MARATHON"></header>
  <div class="status" id="status"><span class="dot"></span><span id="statustext">CONNECTING</span></div>
  <div class="count" id="count">0</div>
  <div class="sub" id="sub">&nbsp;</div>
  <div class="feed" id="feed"><div class="empty">Waiting for kills...</div></div>
</div>
<script>
  function fmt(s){ var m=Math.floor(s/60), x=s%60; return m+":"+(x<10?"0":"")+x; }
  async function tick(){
    try{
      var r = await fetch('/status',{cache:'no-store'});
      var d = await r.json();
      document.getElementById('count').textContent = d.count;
      var st = document.querySelector('.status');
      st.className = 'status' + (d.running ? ' live' : '');
      document.getElementById('statustext').textContent = d.running ? 'RUNNING' : 'STOPPED';
      document.getElementById('sub').textContent =
        (d.started ? 'since '+d.started+'  ·  ' : '') + fmt(d.duration_s) + ' elapsed';
      var feed = document.getElementById('feed');
      if(!d.events.length){ feed.innerHTML = '<div class="empty">Waiting for kills...</div>'; }
      else {
        feed.innerHTML = d.events.map(function(e){
          var sk = e.tag==='precision' ? '<img class="skull" src="/skull.png">' : '';
          return '<div class="row '+e.tag+'"><span class="badge">'+e.tag+'</span>'+sk+
                 '<span>'+e.text.replace(/</g,'&lt;')+'</span>'+
                 '<span class="t">'+e.time+'</span></div>';
        }).join('');
      }
    }catch(err){ document.getElementById('statustext').textContent='OFFLINE'; }
    setTimeout(tick, 1000);
  }
  tick();
</script></body></html>"""
