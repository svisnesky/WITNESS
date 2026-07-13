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


# Dashboard-editable settings: key -> (default, type). All of these are read
# at use-time in the main loop, so changes apply live — no restart needed.
SETTINGS = {
    "play_sound": (False, bool),            # PC-side beep on kills
    "kill_coalesce_seconds": (8.0, float),  # group kills into one clip window
    "make_match_reels": (True, bool),
    "reel_music": (True, bool),
    "reel_announcer": (True, bool),
    "make_shorts": (True, bool),
    "shorts_labels": (True, bool),
    "make_montage": (True, bool),
    "make_card": (True, bool),
    "capture_exfil_stats": (True, bool),
}

# Human labels for the settings panel, in display order.
SETTINGS_META = [
    ("kill_coalesce_seconds", "Group kills within (seconds)"),
    ("make_match_reels", "Match highlight reels"),
    ("reel_music", "Reel music bed"),
    ("reel_announcer", "Reel announcer version"),
    ("make_shorts", "Vertical Shorts renders"),
    ("shorts_labels", "Shorts kill labels"),
    ("make_montage", "Session montage"),
    ("make_card", "Session match card"),
    ("capture_exfil_stats", "Exfil stats capture"),
    ("play_sound", "PC beep on kill"),
]


class LiveState:
    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.count = 0
        self.started = ""
        self._mono = None
        self.events = deque(maxlen=30)
        self._clip_requested = False
        self.reels = []  # [{label, path}] — per-match highlight reels
        self.replays = []  # [{id, label, path, time}] — per-kill instant replays
        self._replay_seq = 0
        self._cfg = None       # live config dict (bound per session)
        self._save_cb = None   # persists changed settings to disk

    def reset(self):
        """Clear counts/feed for a fresh session (server stays up)."""
        with self._lock:
            self.count = 0
            self.events.clear()
            self._clip_requested = False
            self.reels.clear()
            self.replays.clear()

    def add_replay(self, label, path):
        with self._lock:
            self._replay_seq += 1
            self.replays.append({"id": self._replay_seq, "label": label,
                                 "path": path, "time": time.strftime("%H:%M:%S")})
            if len(self.replays) > 20:
                self.replays.pop(0)

    def get_replay_path(self, rid):
        with self._lock:
            for r in self.replays:
                if r["id"] == rid:
                    return r["path"]
            return None

    def bind_config(self, cfg, save_cb):
        """Attach the live session config so the dashboard can read/change it."""
        with self._lock:
            self._cfg = cfg
            self._save_cb = save_cb

    def get_settings(self):
        with self._lock:
            if self._cfg is None:
                return {}
            return {k: self._cfg.get(k, d) for k, (d, _) in SETTINGS.items()}

    def apply_settings(self, changes: dict):
        """Validate + apply dashboard-changed settings to the live config and
        persist them. Returns the settings dict after applying."""
        with self._lock:
            if self._cfg is None:
                return {}
            clean = {}
            for k, v in changes.items():
                if k not in SETTINGS:
                    continue
                _, typ = SETTINGS[k]
                try:
                    clean[k] = bool(v) if typ is bool else max(0.0, float(v))
                except (TypeError, ValueError):
                    continue
            self._cfg.update(clean)
            if clean and self._save_cb is not None:
                try:
                    self._save_cb(clean)
                except Exception as e:
                    print(f"  [settings] could not persist: {e}")
        return self.get_settings()

    def add_reel(self, label, path):
        with self._lock:
            self.reels.append({"label": label, "path": path,
                               "time": time.strftime("%H:%M")})

    def get_reel_path(self, idx):
        with self._lock:
            if 0 <= idx < len(self.reels):
                return self.reels[idx]["path"]
            return None

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

    def request_clip(self):
        with self._lock:
            self._clip_requested = True

    def pop_clip_request(self) -> bool:
        with self._lock:
            if self._clip_requested:
                self._clip_requested = False
                return True
            return False

    def snapshot(self):
        with self._lock:
            return {"running": self.running, "count": self.count,
                    "started": self.started,
                    "events": list(self.events),
                    "reels": [{"i": i, "label": r["label"], "time": r["time"]}
                              for i, r in enumerate(self.reels)],
                    "replays": [{"i": r["id"], "label": r["label"], "time": r["time"]}
                                for r in reversed(self.replays)]}


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

        def _send_video(self, fp):
            """Stream a video file with HTTP Range support (Safari needs it)."""
            size = os.path.getsize(fp)
            start, end = 0, size - 1
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                if a:
                    start = int(a)
                if b:
                    end = min(int(b), size - 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            length = end - start + 1
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            with open(fp, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def do_GET(self):
            path = self.path.split("?")[0]
            try:
                if path == "/status":
                    self._send(json.dumps(state.snapshot()).encode(),
                               "application/json", cache=False)
                elif path == "/config":
                    self._send(json.dumps({
                        "settings": state.get_settings(),
                        "meta": SETTINGS_META,
                    }).encode(), "application/json", cache=False)
                elif path.startswith("/reel/"):
                    try:
                        fp = state.get_reel_path(int(path.rsplit("/", 1)[1]))
                    except ValueError:
                        fp = None
                    if fp and os.path.exists(fp):
                        self._send_video(fp)
                    else:
                        self.send_error(404)
                elif path.startswith("/replay/"):
                    try:
                        fp = state.get_replay_path(int(path.rsplit("/", 1)[1]))
                    except ValueError:
                        fp = None
                    if fp and os.path.exists(fp):
                        self._send_video(fp)
                    else:
                        self.send_error(404)
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

        def do_POST(self):
            path = self.path.split("?")[0]
            try:
                if path == "/clip":
                    state.request_clip()
                    self._send(b'{"ok":true}', "application/json", cache=False)
                elif path == "/config":
                    n = int(self.headers.get("Content-Length") or 0)
                    try:
                        changes = json.loads(self.rfile.read(n) or b"{}")
                    except ValueError:
                        changes = {}
                    result = state.apply_settings(changes if isinstance(changes, dict) else {})
                    self._send(json.dumps({"settings": result}).encode(),
                               "application/json", cache=False)
                else:
                    self.send_error(404)
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = ThreadingHTTPServer(("0.0.0.0", int(port)), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Kill Feed">
<link rel="apple-touch-icon" href="/skull.png">
<title>Marathon Kill Feed</title>
<style>
  :root { --bg:#0b0f12; --panel:#12181d; --line:#232d34; --text:#e8edf0;
          --muted:#7d8a94; --accent:#d3f24b; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html, body { min-height:100%; }
  body { margin:0; background:var(--bg); color:var(--text); text-align:center;
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
    -webkit-font-smoothing:antialiased; user-select:none;
    padding:calc(20px + env(safe-area-inset-top)) 16px calc(40px + env(safe-area-inset-bottom)); }
  .wrap { max-width:640px; margin:0 auto; }
  header { display:flex; align-items:center; justify-content:center; gap:12px; margin-bottom:6px; }
  header img { height:34px; }
  .status { font-size:.8rem; letter-spacing:.16em; text-transform:uppercase; margin-bottom:18px; }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:7px;
    vertical-align:middle; background:var(--muted); }
  .live .dot { background:#5bd66b; box-shadow:0 0 10px #5bd66b; }
  .big { font-size:28vw; line-height:.9; font-weight:800; font-variant-numeric:tabular-nums; }
  @media(min-width:520px){ .big{ font-size:140px; } }
  .accent { color:var(--accent); }
  .lab { color:var(--muted); font-size:.68rem; letter-spacing:.14em; text-transform:uppercase; margin-top:8px; }
  .sub { color:var(--muted); font-size:.82rem; margin:4px 0 16px; }
  .clipbtn { background:var(--accent); color:#0b0f12; border:none; border-radius:10px;
    padding:14px 28px; font:inherit; font-size:.9rem; font-weight:700; letter-spacing:.08em;
    text-transform:uppercase; cursor:pointer; margin-bottom:16px;
    transition: opacity .15s, transform .1s; }
  .clipbtn:active { transform:scale(.95); opacity:.85; }
  .clipbtn.fired { background:#5bd66b; }
  .btnrow { display:flex; gap:10px; justify-content:center; margin-bottom:16px; }
  .fsbtn { background:var(--panel); color:var(--muted); border:1px solid var(--line);
    border-radius:8px; padding:7px 14px; font:inherit; font-size:.75rem;
    cursor:pointer; }
  .hint { color:var(--muted); font-size:.72rem; margin-top:22px; opacity:.8; }
  .reels { text-align:left; margin-bottom:16px; }
  .reels h3 { color:var(--muted); font-size:.68rem; letter-spacing:.14em;
    text-transform:uppercase; margin:0 0 8px 2px; }
  .reelrow { background:var(--panel); border:1px solid var(--accent); border-radius:10px;
    padding:12px 14px; display:flex; align-items:center; gap:12px; cursor:pointer;
    margin-bottom:8px; }
  .reelrow .play { color:var(--accent); font-size:1.1rem; }
  .reelrow .t { color:var(--muted); font-size:.75rem; margin-left:auto; }
  .modal { display:none; position:fixed; inset:0; background:rgba(0,0,0,.92);
    z-index:50; align-items:center; justify-content:center; flex-direction:column;
    padding:16px; }
  .modal.open { display:flex; }
  .modal video { width:100%; max-width:900px; max-height:75vh; border-radius:12px;
    background:#000; }
  .modal .mlabel { color:var(--text); font-size:.85rem; margin:14px 0 10px; }
  .modal .close { background:var(--panel); color:var(--text); border:1px solid var(--line);
    border-radius:8px; padding:10px 26px; font:inherit; font-size:.8rem; cursor:pointer; }
  .settings { background:var(--panel); border:1px solid var(--line); border-radius:14px;
    padding:18px; max-width:480px; width:100%; max-height:80vh; overflow-y:auto;
    text-align:left; }
  .settings h2 { margin:0 0 14px; font-size:.85rem; letter-spacing:.14em;
    text-transform:uppercase; color:var(--accent); }
  .setrow { display:flex; align-items:center; justify-content:space-between;
    gap:12px; padding:11px 2px; border-bottom:1px solid var(--line); font-size:.85rem; }
  .setrow:last-of-type { border-bottom:none; }
  .setrow input[type=number] { width:76px; background:var(--bg); color:var(--text);
    border:1px solid var(--line); border-radius:6px; padding:7px 9px; font:inherit;
    font-size:.85rem; text-align:center; }
  .switch { position:relative; width:52px; height:30px; flex:none; }
  .switch input { opacity:0; width:0; height:0; }
  .slider { position:absolute; inset:0; background:var(--line); border-radius:15px;
    cursor:pointer; transition:background .15s; }
  .slider:before { content:''; position:absolute; width:24px; height:24px; left:3px;
    top:3px; background:var(--muted); border-radius:50%; transition:transform .15s, background .15s; }
  .switch input:checked + .slider { background:var(--accent); }
  .switch input:checked + .slider:before { transform:translateX(22px); background:#0b0f12; }
  .savedmsg { color:var(--accent); font-size:.75rem; text-align:center; margin-top:10px;
    opacity:0; transition:opacity .3s; }
  .savedmsg.show { opacity:1; }
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
  <div><div class="big accent" id="count">0</div><div class="lab">Kills</div></div>
  <div class="sub" id="sub">&nbsp;</div>
  <div class="btnrow">
    <button class="clipbtn" id="clip" onclick="saveClip()">SAVE CLIP</button>
  </div>
  <div class="btnrow">
    <button class="fsbtn" id="snd" onclick="toggleSound()">SOUND: ON</button>
    <button class="fsbtn" onclick="openSettings()">Settings</button>
    <button class="fsbtn" id="fs" onclick="goFull()">Full screen</button>
  </div>
  <div class="reels" id="reels" style="display:none"><h3>Match Highlights</h3><div id="reellist"></div></div>
  <div class="reels" id="replays" style="display:none"><h3>Instant Replays</h3><div id="replaylist"></div></div>
  <div class="feed" id="feed"><div class="empty">Waiting for kills...</div></div>
  <div class="hint" id="hint">iPad: tap Share &rarr; Add to Home Screen for full screen.</div>
</div>
<div class="modal" id="modal">
  <video id="reelvid" controls playsinline></video>
  <div class="mlabel" id="mlabel"></div>
  <button class="close" onclick="closeReel()">CLOSE</button>
</div>
<div class="modal" id="setmodal">
  <div class="settings">
    <h2>Settings</h2>
    <div id="setlist"></div>
    <div class="savedmsg" id="savedmsg">Saved — applies immediately</div>
  </div>
  <div style="height:14px"></div>
  <button class="close" onclick="closeSettings()">CLOSE</button>
</div>
<script>
  async function tick(){
    try{
      var r = await fetch('/status',{cache:'no-store'});
      var d = await r.json();
      if (d.count > lastCount && lastCount >= 0) ding();
      lastCount = d.count;
      document.getElementById('count').textContent = d.count;
      var st = document.querySelector('.status');
      st.className = 'status' + (d.running ? ' live' : '');
      document.getElementById('statustext').textContent = d.running ? 'RUNNING' : 'STOPPED';
      document.getElementById('sub').textContent = d.started ? 'started '+d.started : '\\u00a0';
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
      var reels = d.reels || [];
      var box = document.getElementById('reels');
      var sig = reels.map(function(r){ return r.i+r.label; }).join('|');
      if (reels.length){
        box.style.display = 'block';
        if (sig !== reelSig){  // only re-render on change so taps aren't eaten
          document.getElementById('reellist').innerHTML = reels.map(function(r){
            return '<div class="reelrow" onclick="openReel('+r.i+',this.dataset.label)" data-label="'+
                   r.label.replace(/"/g,'')+'"><span class="play">&#9658;</span>'+
                   '<span>'+r.label.replace(/</g,'&lt;')+'</span>'+
                   '<span class="t">'+r.time+'</span></div>';
          }).join('');
        }
        if (reels.length > lastReels && lastReels >= 0) openReel(reels.length-1, reels[reels.length-1].label);
        lastReels = reels.length;
      } else { box.style.display='none'; lastReels = 0; }
      reelSig = sig;
      var reps = d.replays || [];
      var rbox = document.getElementById('replays');
      var rsig = reps.map(function(r){ return r.i; }).join('|');
      if (reps.length){
        rbox.style.display = 'block';
        if (rsig !== repSig){
          document.getElementById('replaylist').innerHTML = reps.map(function(r){
            return '<div class="reelrow" onclick="openReplay('+r.i+',this.dataset.label)" data-label="'+
                   r.label.replace(/"/g,'')+'"><span class="play">&#9658;</span>'+
                   '<span>'+r.label.replace(/</g,'&lt;')+'</span>'+
                   '<span class="t">'+r.time+'</span></div>';
          }).join('');
        }
      } else { rbox.style.display='none'; }
      repSig = rsig;
    }catch(err){ document.getElementById('statustext').textContent='OFFLINE'; }
    setTimeout(tick, 1000);
  }
  var lastReels = -1, reelSig = '', repSig = '', lastCount = -1;
  tick();

  // --- kill ding (WebAudio, unlocked by the first tap anywhere) ---
  var audioCtx = null;
  var soundOn = localStorage.getItem('killSound') !== 'off';
  document.getElementById('snd').textContent = 'SOUND: ' + (soundOn ? 'ON' : 'OFF');
  function initAudio(){
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === 'suspended') audioCtx.resume();
    } catch(e){}
  }
  document.addEventListener('pointerdown', initAudio);
  function ding(){
    if (!soundOn || !audioCtx) return;
    var t = audioCtx.currentTime;
    [[880, 0], [1318.5, 0.09]].forEach(function(p){
      var o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.type = 'sine'; o.frequency.value = p[0];
      o.connect(g); g.connect(audioCtx.destination);
      g.gain.setValueAtTime(0.0001, t + p[1]);
      g.gain.exponentialRampToValueAtTime(0.35, t + p[1] + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + p[1] + 0.28);
      o.start(t + p[1]); o.stop(t + p[1] + 0.32);
    });
  }
  function toggleSound(){
    soundOn = !soundOn;
    localStorage.setItem('killSound', soundOn ? 'on' : 'off');
    document.getElementById('snd').textContent = 'SOUND: ' + (soundOn ? 'ON' : 'OFF');
    initAudio();
    if (soundOn) ding();  // audible confirmation it's unlocked + on
  }

  function openReel(i, label){
    var v = document.getElementById('reelvid');
    document.getElementById('mlabel').textContent = label || '';
    v.src = '/reel/'+i;
    document.getElementById('modal').classList.add('open');
    v.play().catch(function(){});
  }
  function openReplay(i, label){
    var v = document.getElementById('reelvid');
    document.getElementById('mlabel').textContent = label || '';
    v.src = '/replay/'+i;
    document.getElementById('modal').classList.add('open');
    v.play().catch(function(){});
  }
  function closeReel(){
    var v = document.getElementById('reelvid');
    v.pause(); v.removeAttribute('src'); v.load();
    document.getElementById('modal').classList.remove('open');
  }

  // --- settings panel ---
  async function openSettings(){
    try {
      var r = await fetch('/config', {cache:'no-store'});
      var d = await r.json();
      var html = d.meta.map(function(m){
        var key = m[0], label = m[1], val = d.settings[key];
        if (typeof val === 'boolean'){
          return '<div class="setrow"><span>'+label+'</span>'+
                 '<label class="switch"><input type="checkbox" data-key="'+key+'"'+
                 (val ? ' checked' : '')+' onchange="saveSetting(this)">'+
                 '<span class="slider"></span></label></div>';
        }
        return '<div class="setrow"><span>'+label+'</span>'+
               '<input type="number" step="0.5" min="0" data-key="'+key+'" value="'+val+'"'+
               ' onchange="saveSetting(this)"></div>';
      }).join('');
      document.getElementById('setlist').innerHTML = html;
      document.getElementById('setmodal').classList.add('open');
    } catch(e){}
  }
  function closeSettings(){
    document.getElementById('setmodal').classList.remove('open');
  }
  async function saveSetting(el){
    var key = el.dataset.key;
    var val = el.type === 'checkbox' ? el.checked : parseFloat(el.value);
    try {
      await fetch('/config', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(Object.fromEntries([[key, val]]))});
      var m = document.getElementById('savedmsg');
      m.classList.add('show');
      setTimeout(function(){ m.classList.remove('show'); }, 1800);
    } catch(e){}
  }

  async function saveClip(){
    var btn = document.getElementById('clip');
    btn.textContent = 'SAVING...';
    btn.classList.add('fired');
    try { await fetch('/clip', {method:'POST'}); } catch(e){}
    setTimeout(function(){ btn.textContent='SAVE CLIP'; btn.classList.remove('fired'); }, 1500);
  }

  var wl = null;
  async function keepAwake(){
    try { if ('wakeLock' in navigator) { wl = await navigator.wakeLock.request('screen'); } } catch(e){}
  }
  document.addEventListener('visibilitychange', function(){
    if (document.visibilityState === 'visible') keepAwake();
  });
  keepAwake();

  function goFull(){
    var el = document.documentElement;
    if (el.requestFullscreen) el.requestFullscreen();
    else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
  }
  if (window.navigator.standalone || window.matchMedia('(display-mode: standalone)').matches){
    document.getElementById('fs').style.display='none';
    document.getElementById('hint').style.display='none';
  }
</script></body></html>"""
