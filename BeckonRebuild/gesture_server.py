"""
Beckon - control your PC with hand gestures.

New architecture (no mediapipe-python, no opencv-python):
    * Hand tracking runs in your browser via MediaPipe's JS build (loaded from a
      CDN - nothing to install).
    * This script is a tiny local web server that (a) serves the page and
      (b) listens for gesture events and presses the matching keys with pynput.

Gestures:
    Open palm   -> Play
    Closed fist -> Pause
    OK sign     -> Win + D (show desktop)

Run:
    pip install pynput
    python gesture_server.py

Your browser opens automatically to http://localhost:8000. Allow camera access
when asked. Press Ctrl+C in this terminal to stop.
"""

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pynput.keyboard import Controller, Key

PORT = 8000
keyboard = Controller()


# ---------------------------------------------------------------------------
# Action handling (this is the only OS-level part)
# ---------------------------------------------------------------------------
class State:
    # Windows only has a single Play/Pause toggle key, so we track whether we
    # think media is playing to keep open=play / fist=pause semantically right.
    is_playing = False


def _media_toggle():
    keyboard.press(Key.media_play_pause)
    keyboard.release(Key.media_play_pause)


def _show_desktop():
    keyboard.press(Key.cmd)          # Key.cmd == Windows key
    keyboard.press('d')
    keyboard.release('d')
    keyboard.release(Key.cmd)


def handle_gesture(gesture):
    """Return a human label if an action fired, else None."""
    if gesture == 'OPEN':
        if not State.is_playing:
            _media_toggle()
            State.is_playing = True
            return 'Play'
    elif gesture == 'FIST':
        if State.is_playing:
            _media_toggle()
            State.is_playing = False
            return 'Pause'
    elif gesture == 'OK':
        _show_desktop()
        return 'Show Desktop'
    return None


# ---------------------------------------------------------------------------
# The page (MediaPipe Hands runs here, in the browser)
# ---------------------------------------------------------------------------
PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Beckon</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#0a0b0d; --panel:#121419; --line:#1f232b;
    --text:#e6e8ec; --muted:#7c828d; --accent:#4ade80; --accent2:#38bdf8;
  }
  *{box-sizing:border-box;margin:0;padding:0;}
  body{
    background:var(--bg); color:var(--text);
    font-family:'DM Mono',ui-monospace,Menlo,Consolas,monospace;
    min-height:100vh; display:flex; align-items:center; justify-content:center;
    padding:24px;
  }
  .wrap{width:100%; max-width:760px;}
  .top{display:flex; align-items:baseline; justify-content:space-between; margin-bottom:14px;}
  h1{font-size:18px; letter-spacing:6px; font-weight:500;}
  .dot{display:inline-block; width:8px; height:8px; border-radius:50%;
       background:var(--muted); margin-right:8px; vertical-align:middle;}
  .dot.live{background:var(--accent); box-shadow:0 0 10px var(--accent);}
  .status{font-size:12px; color:var(--muted);}
  .stage{position:relative; border:1px solid var(--line); border-radius:12px;
         overflow:hidden; background:#000; aspect-ratio:4/3;}
  .output_canvas{width:100%; height:100%; display:block; transform:scaleX(-1);}
  .input_video{display:none;}
  .hud{position:absolute; top:0; left:0; right:0; padding:12px 14px;
       display:flex; justify-content:space-between; align-items:flex-start;
       background:linear-gradient(180deg,rgba(0,0,0,.55),rgba(0,0,0,0));
       font-size:12px; pointer-events:none;}
  .hud .pose{font-size:15px; letter-spacing:2px;}
  .hud .meta{color:var(--muted); margin-top:2px;}
  .fired{color:var(--accent); font-size:14px; min-height:18px; text-align:right;}
  .legend{margin-top:14px; font-size:12px; color:var(--muted); text-align:center;
          letter-spacing:1px;}
  .legend b{color:var(--text); font-weight:500;}
</style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <h1>BECKON</h1>
      <div class="status"><span class="dot" id="dot"></span><span id="status">starting…</span></div>
    </div>
    <div class="stage">
      <video class="input_video" playsinline></video>
      <canvas class="output_canvas"></canvas>
      <div class="hud">
        <div>
          <div class="pose" id="pose">—</div>
          <div class="meta" id="media">media: paused</div>
          <div class="meta" id="fps">— fps</div>
        </div>
        <div class="fired" id="fired"></div>
      </div>
    </div>
    <div class="legend">
      <b>open</b> = play &nbsp;·&nbsp; <b>fist</b> = pause &nbsp;·&nbsp; <b>OK sign</b> = show desktop
    </div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/@mediapipe/camera_utils/camera_utils.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/@mediapipe/drawing_utils/drawing_utils.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/@mediapipe/hands/hands.js"></script>
  <script>
    const videoEl  = document.querySelector('.input_video');
    const canvasEl = document.querySelector('.output_canvas');
    const ctx = canvasEl.getContext('2d');
    const el = id => document.getElementById(id);

    // ---- tuning ----
    const TIPS=[8,12,16,20], PIPS=[6,10,14,18];
    const POSE_STABLE_FRAMES=3, ACTION_COOLDOWN=1200; // ms
    const OK_TOUCH_RATIO=0.4;  // thumb-index gap vs palm size; lower = stricter pinch

    // ---- state ----
    let stablePose=null, stableCount=0, lastSentPose=null;
    let lastActionAt=0;
    let frames=0, fps=0, lastFpsT=performance.now();
    let playing=false;

    function dist(a,b){ return Math.hypot(a.x-b.x, a.y-b.y); }

    function fingersUp(lm){
      let c=0;
      for(let i=0;i<4;i++){ if(lm[TIPS[i]].y < lm[PIPS[i]].y) c++; }
      return c;
    }

    // OK sign: thumb tip and index tip pinched, other three fingers extended
    function isOK(lm){
      const palm = dist(lm[0], lm[9]);
      if(palm <= 0) return false;
      const pinch = dist(lm[4], lm[8]) / palm;
      const midUp  = lm[12].y < lm[10].y;
      const ringUp = lm[16].y < lm[14].y;
      const pinkUp = lm[20].y < lm[18].y;
      return pinch < OK_TOUCH_RATIO && midUp && ringUp && pinkUp;
    }

    function classify(lm){
      if(isOK(lm)) return 'OK';
      const up=fingersUp(lm);
      if(up>=4) return 'OPEN';
      if(up===0) return 'FIST';
      return null;
    }

    async function fire(gesture){
      const now=performance.now();
      if(now-lastActionAt < ACTION_COOLDOWN) return;
      lastActionAt=now;
      try{
        const r=await fetch('/action',{
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({gesture})
        });
        const j=await r.json();
        if(j.fired){
          el('fired').textContent='» '+j.fired;
          setTimeout(()=>{ el('fired').textContent=''; }, 1400);
          if(j.fired==='Play') playing=true;
          if(j.fired==='Pause') playing=false;
          el('media').textContent='media: '+(playing?'playing':'paused');
        }
      }catch(e){
        el('status').textContent='server not reachable';
        el('dot').classList.remove('live');
      }
    }

    function onResults(results){
      frames++;
      const t=performance.now();
      if(t-lastFpsT>500){ fps=Math.round(frames*1000/(t-lastFpsT)); frames=0; lastFpsT=t; el('fps').textContent=fps+' fps'; }

      canvasEl.width=results.image.width;
      canvasEl.height=results.image.height;
      ctx.save();
      ctx.clearRect(0,0,canvasEl.width,canvasEl.height);
      ctx.drawImage(results.image,0,0,canvasEl.width,canvasEl.height);

      let pose=null;
      const hands=results.multiHandLandmarks;
      if(hands && hands.length){
        const lm=hands[0];
        drawConnectors(ctx,lm,HAND_CONNECTIONS,{color:'#4ade80',lineWidth:2});
        drawLandmarks(ctx,lm,{color:'#e6e8ec',lineWidth:1,radius:2});

        pose=classify(lm);
        if(pose && pose===stablePose) stableCount++;
        else { stablePose=pose; stableCount=1; }

        if(pose && stableCount>=POSE_STABLE_FRAMES && pose!==lastSentPose){
          lastSentPose=pose;
          fire(pose);
        }
      } else {
        stablePose=null; stableCount=0; lastSentPose=null;
      }
      ctx.restore();
      el('pose').textContent = pose ? pose : '—';
    }

    const hands=new Hands({locateFile:(f)=>`https://cdn.jsdelivr.net/npm/@mediapipe/hands/${f}`});
    hands.setOptions({maxNumHands:1, modelComplexity:0,
                      minDetectionConfidence:0.7, minTrackingConfidence:0.6});
    hands.onResults(onResults);

    const camera=new Camera(videoEl,{
      onFrame: async ()=>{ await hands.send({image:videoEl}); },
      width:640, height:480
    });
    camera.start()
      .then(()=>{ el('status').textContent='tracking'; el('dot').classList.add('live'); })
      .catch(e=>{ el('status').textContent='camera blocked: '+e.message; });
  </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Web server
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep the terminal quiet

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            self._send(200, PAGE.encode('utf-8'), 'text/html; charset=utf-8')
        else:
            self._send(404, b'not found', 'text/plain')

    def do_POST(self):
        if self.path == '/action':
            length = int(self.headers.get('Content-Length', 0))
            raw = self.rfile.read(length) if length else b'{}'
            try:
                gesture = json.loads(raw).get('gesture')
            except Exception:
                gesture = None
            fired = handle_gesture(gesture) if gesture else None
            self._send(200, json.dumps({'fired': fired}).encode(), 'application/json')
        else:
            self._send(404, b'not found', 'text/plain')


def main():
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    url = f'http://localhost:{PORT}'
    print('=' * 48)
    print('  BECKON is running')
    print('=' * 48)
    print(f'  Open: {url}')
    print('  Your browser should open automatically.')
    print('  Allow camera access when asked.')
    print('  Press Ctrl+C here to stop.')
    print('=' * 48)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopping Beckon.')
        server.shutdown()


if __name__ == '__main__':
    main()
