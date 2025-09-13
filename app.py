# app.py  —— Simplified Image Yield Dashboard (NAS-friendly)
# Features:
# - Poll & ingest images once (path is PK), display stats by station
# - Time presets (1h/1d/1w) auto-fill Start/End; periods auto-refresh
# - Station is single-select; Start/End are manual (click "Search")
# - Show totals, OK, NG, Yield, Last Seen, Last Image, and last N NG images
# - Dark mode toggle (🌙 Dark / ☀️ Light)
# - Thumbnails via Pillow (fast page load)
#
# ENV you may care:
#   WATCH_DIR=/data
#   DB_PATH=/state/data.sqlite
#   FILENAME_REGEX='^(?P<pass>OK|NG)-(?P<date>\d{8})-(?P<time>\d{6})-(?P<count>\d+)\.(?:jpg|jpeg|png)$'
#   POLL_INTERVAL_SEC=60
#   RECENT_MTIME_MIN=1440
#   MIN_FILE_AGE_SEC=2
#   NG_PREVIEW_COUNT=3
#   THUMB_MAX_DIM=512

import os, re, sqlite3, time, hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from apscheduler.schedulers.background import BackgroundScheduler
from PIL import Image

# =======================
# Config (ENV)
# =======================
WATCH_DIR = Path(os.getenv("WATCH_DIR", "/data")).resolve()
DB_PATH = Path(os.getenv("DB_PATH", "/state/data.sqlite")).resolve()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

FILENAME_REGEX = os.getenv(
    "FILENAME_REGEX",
    r"^(?P<pass>OK|NG)-(?P<date>\d{8})-(?P<time>\d{6})-(?P<count>\d+)\.(?:jpg|jpeg|png)$",
)
NAME_RE = re.compile(FILENAME_REGEX, re.IGNORECASE)

WATCH_MODE = os.getenv("WATCH_MODE", "poll")
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "60"))
RECENT_MTIME_MIN = int(os.getenv("RECENT_MTIME_MIN", "10"))
MIN_FILE_AGE_SEC = int(os.getenv("MIN_FILE_AGE_SEC", "2"))

# Fixed +08:00 for simplicity (fits Asia/Taipei)
LOCAL_TZ = timezone(timedelta(hours=8))

THUMB_DIR = Path("/state/thumbs").resolve()
THUMB_DIR.mkdir(parents=True, exist_ok=True)
THUMB_MAX_DIM = int(os.getenv("THUMB_MAX_DIM", "512"))

# station/model layers: /data/<station>/<model>/filename
PATH_LAYERS = 2

# show recent N NG images per-station
NG_PREVIEW_COUNT = int(os.getenv("NG_PREVIEW_COUNT", "3"))

print(f"[INFO] WATCH_DIR={WATCH_DIR}")
print(f"[INFO] Using regex: {FILENAME_REGEX}")
print(
    f"[INFO] Poll every {POLL_INTERVAL_SEC}s; recent mtime window={RECENT_MTIME_MIN} min; NG preview={NG_PREVIEW_COUNT}"
)

# =======================
# DB
# =======================
conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
conn.execute(
    """
CREATE TABLE IF NOT EXISTS images(
  path TEXT PRIMARY KEY,
  station TEXT,
  model TEXT,
  pass   TEXT,           -- OK/NG/NULL
  job_count INTEGER,
  ts     TEXT,           -- ISO with tz
  mtime  REAL,
  ingested_at TEXT
);
"""
)
conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON images(ts);")
conn.execute("CREATE INDEX IF NOT EXISTS idx_station_ts ON images(station, ts);")
conn.execute("CREATE INDEX IF NOT EXISTS idx_pass_ts ON images(pass, ts);")
conn.commit()


# =======================
# Helpers
# =======================
def parse_path(p: Path) -> Tuple[Optional[str], Optional[str]]:
    try:
        rel = p.resolve().relative_to(WATCH_DIR)
    except Exception:
        return (None, None)
    parts = rel.parts
    if len(parts) < PATH_LAYERS + 1:
        return (None, None)
    return parts[0], parts[1]


def parse_filename(name: str, mtime: float):
    m = NAME_RE.match(name)
    if not m:
        return (None, None, datetime.fromtimestamp(mtime, tz=LOCAL_TZ))
    pas = m.group("pass").upper()
    try:
        jobc = int(m.group("count"))
    except:
        jobc = None
    dt = datetime.strptime(m.group("date") + m.group("time"), "%Y%m%d%H%M%S").replace(
        tzinfo=LOCAL_TZ
    )
    return (pas, jobc, dt)


def should_consider_file(p: Path, now_ts: float) -> bool:
    if not p.is_file():
        return False
    ext = p.suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png"):
        return False
    try:
        st = p.stat()
    except FileNotFoundError:
        return False
    mtime = st.st_mtime
    if (now_ts - mtime) < MIN_FILE_AGE_SEC:
        return False
    if (now_ts - mtime) > RECENT_MTIME_MIN * 60:
        return False
    return True


def scan_poll_once() -> Dict[str, Any]:
    now_ts = time.time()
    scanned = 0
    added = 0
    if not WATCH_DIR.exists():
        return {"scanned": 0, "added": 0, "note": f"watch dir not found: {WATCH_DIR}"}

    cur = conn.cursor()
    existing = set(r[0] for r in cur.execute("SELECT path FROM images"))

    for root, _, files in os.walk(WATCH_DIR):
        for fn in files:
            p = Path(root) / fn
            if not should_consider_file(p, now_ts):
                continue
            scanned += 1
            sp = str(p.resolve())
            if sp in existing:
                continue
            try:
                station, model = parse_path(p)
                st = p.stat()
                pas, jobc, dt = parse_filename(p.name, st.st_mtime)
                conn.execute(
                    "INSERT OR IGNORE INTO images(path, station, model, pass, job_count, ts, mtime, ingested_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        sp,
                        station,
                        model,
                        pas,
                        jobc,
                        dt.isoformat(),
                        st.st_mtime,
                        datetime.now(tz=LOCAL_TZ).isoformat(),
                    ),
                )
                added += 1
            except Exception:
                # keep scanning
                pass
    conn.commit()
    return {"scanned": scanned, "added": added}


def parse_time_range(
    start: Optional[str], end: Optional[str], period: Optional[str]
) -> Tuple[datetime, datetime]:
    now = datetime.now(tz=LOCAL_TZ).replace(second=0, microsecond=0)
    if period in ("1h", "1d", "1w"):
        if period == "1h":
            s = now - timedelta(hours=1)
        elif period == "1d":
            s = now - timedelta(days=1)
        else:
            s = now - timedelta(weeks=1)
        return (s, now)

    def parse_inp(x: str) -> datetime:
        try:
            if len(x) == 16 and "T" in x:
                return datetime.strptime(x, "%Y-%m-%dT%H:%M").replace(tzinfo=LOCAL_TZ)
            dt = datetime.fromisoformat(x)
            return dt if dt.tzinfo else dt.replace(tzinfo=LOCAL_TZ)
        except Exception:
            return now

    if start and end:
        s = parse_inp(start)
        e = parse_inp(end)
        if e <= s:
            e = s + timedelta(minutes=1)
        return (s, e)
    return (now - timedelta(days=1), now)


def query_rows(s: datetime, e: datetime, station: Optional[str]) -> List[Tuple]:
    where = ["ts >= ? AND ts < ?"]
    params: List[Any] = [s.isoformat(), e.isoformat()]
    if station and station != "ALL":
        where.append("station = ?")
        params.append(station)
    sql = f"SELECT path, station, pass, ts FROM images WHERE {' AND '.join(where)}"
    cur = conn.cursor()
    return list(cur.execute(sql, params))


def aggregate_by_station(rows: List[Tuple]) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for path, station, pas, ts_iso in rows:
        st = station or "Unknown"
        try:
            ts = datetime.fromisoformat(ts_iso)
        except:
            ts = datetime.now(tz=LOCAL_TZ)
        node = data.setdefault(
            st,
            {
                "totals": {
                    "total": 0,
                    "ok": 0,
                    "ng": 0,
                    "rate": None,
                    "latest_ts": None,
                },
                "last": None,
                "last_ng": None,
                "last_ngs": [],  # collect all NG here; trim later
            },
        )

        node["totals"]["total"] += 1
        if pas == "OK":
            node["totals"]["ok"] += 1
        elif pas == "NG":
            node["totals"]["ng"] += 1
            node["last_ngs"].append({"path": path, "ts": ts})

        lt = node["totals"]["latest_ts"]
        if (lt is None) or (ts > lt):
            node["totals"]["latest_ts"] = ts

        if (node["last"] is None) or (ts > node["last"]["ts"]):
            node["last"] = {"path": path, "ts": ts}

        if pas == "NG":
            if (node["last_ng"] is None) or (ts > node["last_ng"]["ts"]):
                node["last_ng"] = {"path": path, "ts": ts}

    # finalize: yield, iso strings, trim NG list
    for st, node in data.items():
        ok = node["totals"]["ok"]
        ng = node["totals"]["ng"]
        denom = ok + ng
        node["totals"]["rate"] = (ok / denom) if denom > 0 else None

        if node["totals"]["latest_ts"]:
            node["totals"]["latest_ts"] = node["totals"]["latest_ts"].isoformat()
        if node["last"]:
            node["last"]["ts"] = node["last"]["ts"].isoformat()
        if node["last_ng"]:
            node["last_ng"]["ts"] = node["last_ng"]["ts"].isoformat()

        if node.get("last_ngs"):
            node["last_ngs"].sort(key=lambda x: x["ts"], reverse=True)
            node["last_ngs"] = node["last_ngs"][:NG_PREVIEW_COUNT]
            for it in node["last_ngs"]:
                if isinstance(it["ts"], datetime):
                    it["ts"] = it["ts"].isoformat()
    return data


def ensure_under_watch(path_str: str) -> Path:
    p = Path(path_str).resolve()
    if not str(p).startswith(str(WATCH_DIR)):
        raise HTTPException(status_code=400, detail="Path outside watch dir")
    return p


def thumb_path_for(path: Path) -> Path:
    h = hashlib.sha1(str(path).encode("utf-8")).hexdigest()
    return THUMB_DIR / f"{h}.jpg"


# =======================
# Web
# =======================
app = FastAPI(title="Image Yield Dashboard")

INDEX_HTML = """
<!doctype html><html><head><meta charset="utf-8">
<title>Image Yield Dashboard</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:ui-sans-serif,system-ui,Segoe UI,Helvetica,Arial;padding:20px;max-width:1200px;margin:0 auto}
.card{border:1px solid #ddd;border-radius:12px;padding:16px;margin-bottom:16px;background:#fff}
h2{margin:0 0 12px 0}
.controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.controls .spacer {flex-grow: 1;}
.ng-list {display: flex;flex-direction: row;gap: 20px;align-items: flex-start;overflow-x: auto;}
input,select,button{padding:8px}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;background:#eee;margin-left:8px}
.kpis{display:flex;gap:12px;flex-wrap:wrap}
.kpis .k{border:1px solid #ddd;border-radius:10px;padding:8px 12px;background:#fafafa}
img.thumb{max-width:200px;max-height:140px;display:block}
.path{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;color:#555;word-break:break-all;margin-top:4px}
.grid{display:grid;grid-template-columns:250px 1fr;gap:12px}
.small{font-size:12px;color:#666}
hr{border:none;border-top:1px solid #eee;margin:16px 0}
button.pill{border:1px solid #ccc;border-radius:999px;padding:6px 10px;background:#fafafa}
select{min-width:150px}

/* —— Dark mode overrides —— */
[data-theme="dark"] body{ background:#121212; color:#eaeaea; }
[data-theme="dark"] .card{ background:#1e1e1e; border-color:#333; }
[data-theme="dark"] th{ background:#222; color:#ddd; }
[data-theme="dark"] .badge{ background:#333; color:#ddd; }
[data-theme="dark"] .kpis .k{ border-color:#333; background:#181818; }
[data-theme="dark"] .small{ color:#aaa; }
[data-theme="dark"] input,[data-theme="dark"] select,[data-theme="dark"] button{
  background:#1b1b1b; color:#eaeaea; border:1px solid #333;
}
[data-theme="dark"] button.pill{ background:#191919; border-color:#444; }
[data-theme="dark"] table{ border-color:#333; }
[data-theme="dark"] th, [data-theme="dark"] td{ border-color:#333; }
[data-theme="dark"] .path{ color:#aaa; }
</style></head><body>
<h2>Image Yield Dashboard <span id="badge" class="badge"></span></h2>

<div class="card">
  <div class="controls">
    <label>Station
      <select id="station" style="min-width:100px" onchange="loadData()"></select>
    </label>

    <label>Period
      <select id="period" style="min-width:100px" onchange="onPeriodChange()">
        <option value="1h" selected>Last 1h</option>
        <option value="1d">Last 1d</option>
        <option value="1w">Last 1w</option>
        <option value="custom">Custom</option>
      </select>
    </label>

    <label>Start <input type="datetime-local" id="start"></label>
    <label>End <input type="datetime-local" id="end"></label>

    <button onclick="loadData()">Search</button>
    <button onclick="triggerScan()">Rescan</button>

    <div class="spacer"></div>
    <button id="themeBtn" class="pill" onclick="toggleTheme()">🌙 Dark</button>
  </div>
  <div class="small" id="hint">
    Note: Station/Period auto-refresh; Custom time changes require “Search”. All images are kept. “Rescan” may take time.
  </div>
</div>

<div id="content"></div>

<script>
function getPreferredTheme(){
  const t = localStorage.getItem('theme');
  if (t) return t;
  return (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
}
function applyTheme(t){
  document.documentElement.setAttribute('data-theme', t);
  localStorage.setItem('theme', t);
  const btn = document.getElementById('themeBtn');
  if (btn) btn.textContent = (t==='dark' ? '☀️ Light' : '🌙 Dark');
}
function toggleTheme(){
  const cur = document.documentElement.getAttribute('data-theme') || getPreferredTheme();
  applyTheme(cur === 'dark' ? 'light' : 'dark');
}

async function fetchJSON(url, opts){ const r = await fetch(url, opts||{}); return await r.json(); }
function pad(n){return String(n).padStart(2,'0')}
function isoLocalForInput(d){ return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}` }
function nowFloor(){ const d=new Date(); d.setSeconds(0,0); return d; }

function applyPeriodToInputs(period){
  const end = nowFloor();
  let start = new Date(end);
  if(period==='1h') start.setHours(end.getHours()-1);
  else if(period==='1d') start.setDate(end.getDate()-1);
  else if(period==='1w') start.setDate(end.getDate()-7);
  document.getElementById('start').value = isoLocalForInput(start);
  document.getElementById('end').value = isoLocalForInput(end);
}

function onPeriodChange(){
  const p = document.getElementById('period').value;
  if(p==='custom') return;
  applyPeriodToInputs(p);
  loadData(); // periods auto-refresh
}

function setBadge(total, sISO, eISO){
  const b = document.getElementById('badge');
  if(sISO && eISO){
    const s = new Date(sISO).toLocaleString();
    const e = new Date(eISO).toLocaleString();
    b.textContent = `Records: ${total||0} ｜ Range: ${s} → ${e}`;
  } else {
    b.textContent = `Records: ${total||0}`;
  }
}

function qs(params){
  const p = new URLSearchParams();
  for(const k in params){
    const v = params[k];
    if(v===null || v===undefined) continue;
    if(typeof v==='string' && v.trim()==='') continue;
    p.set(k, v);
  }
  return p.toString();
}

function buildImgURL(path){ return '/thumb?path='+encodeURIComponent(path); }
function fmtRate(x){ return (x==null)?'—':(x*100).toFixed(1)+'%'; }
function fmtTs(x){ return x? new Date(x).toLocaleString() : '—'; }

async function loadMeta(){
  const meta = await fetchJSON('/api/meta');
  const sel = document.getElementById('station');
  sel.innerHTML = '';
  const all = document.createElement('option'); all.value='ALL'; all.textContent='ALL'; sel.appendChild(all);
  (meta.stations||[]).forEach(s=>{
    const o=document.createElement('option'); o.value=s; o.textContent=s; sel.appendChild(o);
  });
  sel.value = 'ALL';
}

async function loadData(){
  const station = document.getElementById('station').value;
  const period = document.getElementById('period').value;
  const start = document.getElementById('start').value;
  const end = document.getElementById('end').value;

  const url = '/api/stats?'+qs({
    station: station==='ALL'? null : station,
    period: period==='custom'? null : period,
    start: start || null,
    end: end || null
  });

  const data = await fetchJSON(url, {method:'POST'});
  setBadge(data.total_count, data.start, data.end);

  const c = document.getElementById('content');
  c.innerHTML = '';

  const stations = data.stations || {};
  const keys = Object.keys(stations);
  if(keys.length===0){ c.innerHTML='<div class="card">No data</div>'; return; }

  // Sort by yield (asc) so worse stations appear first
  keys.sort((a,b)=>{
    const ra = stations[a].totals.rate ?? 1e9;
    const rb = stations[b].totals.rate ?? 1e9;
    return (ra - rb);
  });

  for(const st of keys){
    const node = stations[st];
    const t = node.totals || {};
    const last = node.last;
    const lastNgList = node.last_ngs || [];

    function prettyPath(p){
      if(!p) return '';
      let s = p.replace(/^\\/data\\//,'');      // 去掉 /data/
      s = s.replace(/\\.(jpg|jpeg|png)$/i,'');  // 去掉扩展名
      return s;
    }

    const lastImg = last
      ? `<img class="thumb" src="${buildImgURL(last.path)}"><div class="path">${prettyPath(last.path)}</div>`
      : '—';

    let lastNgImgs = '—';
    if (lastNgList.length > 0) {
      const items = lastNgList.map((it) => `
        <div style="margin-right:12px; text-align:center">
          <img class="thumb" src="${buildImgURL(it.path)}">
          <div class="path">${prettyPath(it.path)}</div>
        </div>
      `).join('');
      lastNgImgs = `<div class="ng-list">${items}</div>`;
    }

    const html = `
      <div class="card">
        <div class="kpis">
          <div class="k">Station: <b>${st}</b></div>
          <div class="k">Total: <b>${t.total||0}</b></div>
          <div class="k">OK: <b>${t.ok||0}</b></div>
          <div class="k">NG: <b>${t.ng||0}</b></div>
          <div class="k">Yield: <b>${fmtRate(t.rate)}</b></div>
          <div class="k">Last Seen: <b>${fmtTs(t.latest_ts)}</b></div>
        </div>
        <hr/>
        <div class="grid">
          <div>
            <div class="small">Last Image</div>
            ${lastImg}
          </div>
          <div>
            <div class="small">Last NG Images</div>
            ${lastNgImgs}
          </div>
        </div>
      </div>
    `;
    c.insertAdjacentHTML('beforeend', html);
  }
}

async function triggerScan(){
  const r = await fetchJSON('/api/scan', {method:'POST'});
  alert('Scan complete: new '+ (r.added||0) +', checked '+ (r.scanned||0));
  loadData();
}

// init
applyTheme(getPreferredTheme());
loadMeta().then(()=>{
  document.getElementById('period').value='1h';
  const p = document.getElementById('period').value;
  if(p!=='custom') applyPeriodToInputs(p);
  loadData();
});
</script>
</body></html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX_HTML


@app.get("/api/meta")
def api_meta():
    cur = conn.cursor()
    stations = sorted(
        set(
            x[0]
            for x in cur.execute(
                "SELECT DISTINCT station FROM images WHERE station IS NOT NULL"
            )
        )
    )
    return JSONResponse({"stations": stations})


@app.post("/api/scan")
def api_scan():
    return JSONResponse(scan_poll_once())


@app.post("/api/stats")
def api_stats(
    station: Optional[str] = Query(default=None),
    period: Optional[str] = Query(default=None, pattern="^(1h|1d|1w)?"),
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
):
    s, e = parse_time_range(start, end, period)
    rows = query_rows(s, e, station)
    agg = aggregate_by_station(rows)
    return JSONResponse(
        {
            "stations": agg,
            "total_count": len(rows),
            "start": s.isoformat(),
            "end": e.isoformat(),
        }
    )


@app.get("/img")
def get_image(path: str):
    p = ensure_under_watch(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(p))


@app.get("/thumb")
def get_thumb(path: str):
    p = ensure_under_watch(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    tp = THUMB_DIR / (hashlib.sha1(str(p).encode("utf-8")).hexdigest() + ".jpg")
    if not tp.exists():
        try:
            img = Image.open(str(p)).convert("RGB")
            w, h = img.size
            if max(w, h) > THUMB_MAX_DIM:
                if w >= h:
                    nh = int(h * THUMB_MAX_DIM / w)
                    img = img.resize((THUMB_MAX_DIM, nh))
                else:
                    nw = int(w * THUMB_MAX_DIM / h)
                    img = img.resize((nw, THUMB_MAX_DIM))
            tp.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(tp), "JPEG", quality=85)
        except Exception:
            return FileResponse(str(p))
    return FileResponse(str(tp))


# =======================
# Scheduler
# =======================
scheduler = BackgroundScheduler()
if WATCH_MODE.lower() == "poll":
    scheduler.add_job(scan_poll_once, "interval", seconds=POLL_INTERVAL_SEC)
scheduler.start()

if __name__ == "__main__":
    try:
        scan_poll_once()
    except Exception:
        pass
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000)
