from __future__ import annotations
import os, json, requests, time, threading, queue
from datetime import datetime
from typing import Dict, Any, Optional, List
from flask import Flask, request, jsonify, Response, stream_with_context
from timezonefinder import TimezoneFinder
import pytz

# ===== Branding =====
APP_NAME = "BAZI Destiny"
APP_TAGLINE = "Ancient Eastern wisdom—clear, practical guidance for modern life."

# OpenAI-compatible endpoint (DeepSeek)
DEEPSEEK_BASE_URL = os.getenv("OPENAI_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL    = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ===== Bazi engine =====
try:
    from lunar_python import Solar
    HAS_LUNAR = True
except Exception:
    HAS_LUNAR = False

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
tf = TimezoneFinder(in_memory=True)

# ===== Tables =====
STEMS_CN = ["甲","乙","丙","丁","戊","己","庚","辛","壬","癸"]
STEMS_PY = ["Jia","Yi","Bing","Ding","Wu","Ji","Geng","Xin","Ren","Gui"]
BRANCHES_CN = ["子","丑","寅","卯","辰","巳","午","未","申","酉","戌","亥"]
BRANCHES_PY = ["Zi","Chou","Yin","Mao","Chen","Si","Wu","Wei","Shen","You","Xu","Hai"]
STEM_TO_PY = dict(zip(STEMS_CN, STEMS_PY))
BRANCH_TO_PY = dict(zip(BRANCHES_CN, BRANCHES_PY))
STEM_TO_ELEMENT = {"甲":"Wood","乙":"Wood","丙":"Fire","丁":"Fire","戊":"Earth","己":"Earth","庚":"Metal","辛":"Metal","壬":"Water","癸":"Water"}
BRANCH_TO_ELEMENT = {"子":"Water","丑":"Earth","寅":"Wood","卯":"Wood","辰":"Earth","巳":"Fire","午":"Fire","未":"Earth","申":"Metal","酉":"Metal","戌":"Earth","亥":"Water"}
YANG_STEMS = {"甲","丙","戊","庚","壬"}
GENERATION = {"Wood":"Fire","Fire":"Earth","Earth":"Metal","Metal":"Water","Water":"Wood"}  # 生
CONTROL    = {"Wood":"Earth","Earth":"Water","Water":"Fire","Fire":"Metal","Metal":"Wood"}  # 克
TEN_GODS_EN = {
    "BiJie":"Peer (Parallel)","JieCai":"Rival (Rob Wealth)",
    "ShiShen":"Talent (Eating God / Output)","ShangGuan":"Performer (Hurting Officer)",
    "ZhengCai":"Direct Wealth","PianCai":"Indirect Wealth",
    "ZhengGuan":"Authority (Direct Officer)","QiSha":"Challenger (Seven Killings)",
    "ZhengYin":"Nurture (Direct Resource)","PianYin":"Inspiration (Indirect Resource)"
}
FIVE_ELEMENTS_COLORS = {"Wood":["green","cyan"],"Fire":["red","orange"],"Earth":["yellow","brown"],"Metal":["white","silver","gold"],"Water":["black","blue"]}
FIVE_ELEMENTS_NUMBERS = {"Wood":[3,8],"Fire":[2,7],"Earth":[5,10],"Metal":[4,9],"Water":[1,6]}

# ===== Helpers =====
def split_ganzhi(gz: str) -> Dict[str,str]:
    return {"stem_cn": gz[0] if gz else "", "branch_cn": gz[1] if gz and len(gz)>1 else ""}

def parity(stem_cn: str) -> str:
    return "Yang" if stem_cn in YANG_STEMS else "Yin"

def ten_god(day_stem: str, other_stem: str) -> str:
    day_el = STEM_TO_ELEMENT.get(day_stem, ""); other_el = STEM_TO_ELEMENT.get(other_stem, "")
    if not day_el or not other_el: return ""
    same = (parity(day_stem) == parity(other_stem))
    if other_el == day_el:  # 比劫
        return TEN_GODS_EN["BiJie"] if same else TEN_GODS_EN["JieCai"]
    if GENERATION[day_el] == other_el:  # 我生他 → 食伤
        return TEN_GODS_EN["ShiShen"] if same else TEN_GODS_EN["ShangGuan"]
    if CONTROL[day_el] == other_el:  # 我克他 → 财
        return TEN_GODS_EN["PianCai"] if same else TEN_GODS_EN["ZhengCai"]
    if CONTROL[other_el] == day_el:  # 他克我 → 官杀
        return TEN_GODS_EN["QiSha"] if same else TEN_GODS_EN["ZhengGuan"]
    if GENERATION[other_el] == day_el:  # 他生我 → 印
        return TEN_GODS_EN["PianYin"] if same else TEN_GODS_EN["ZhengYin"]
    return ""

def to_beijing_from_local(local_dt: datetime, tz_name: str) -> Dict[str, Any]:
    tz_local = pytz.timezone(tz_name)
    dt_localized = tz_local.localize(local_dt, is_dst=None)
    bj = pytz.timezone("Asia/Shanghai")
    dt_bj = dt_localized.astimezone(bj)
    return {"local_iso": dt_localized.isoformat(), "beijing_iso": dt_bj.isoformat(), "beijing": dt_bj}

def geocode_city_country(city: str, country: str) -> Optional[Dict[str, Any]]:
    url = "https://nominatim.openstreetmap.org/search"
    params = {"format":"json","addressdetails":1,"city":city,"country":country,"limit":5}
    headers={"User-Agent": f"{APP_NAME}/1.0"}
    r = requests.get(url, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data: return None
    item = data[0]
    return {"lat": float(item["lat"]), "lon": float(item["lon"]), "display": item.get("display_name","")}

def tz_name_from_latlon(lat: float, lon: float) -> Optional[str]:
    try:
        return tf.timezone_at(lat=lat, lng=lon)
    except Exception:
        return None

# ===== Assets (PPT-like cards + print) =====
LOGO_SVG = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 160 160'>
<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='#c9a86a'/><stop offset='1' stop-color='#7a5a2e'/></linearGradient></defs>
<circle cx='80' cy='80' r='76' fill='#0f0f0f' stroke='url(#g)' stroke-width='4'/>
<g stroke='#c9a86a' stroke-width='4' stroke-linecap='round'>
  <line x1='80' y1='8' x2='80' y2='24'/><line x1='152' y1='80' x2='136' y2='80'/>
  <line x1='80' y1='152' x2='80' y2='136'/><line x1='8' y1='80' x2='24' y2='80'/>
</g>
<path d='M80 28a52 52 0 1 0 0 104c-14 0-26-12-26-26s12-26 26-26 26-12 26-26S94 28 80 28Z' fill='#c9a86a'/>
<circle cx='80' cy='54' r='26' fill='#0f0f0f'/><circle cx='80' cy='106' r='26' fill='#c9a86a'/>
<circle cx='80' cy='54' r='6' fill='#c9a86a'/><circle cx='80' cy='106' r='6' fill='#0f0f0f'/>
</svg>"""

STYLES_CSS = """
*{box-sizing:border-box}
:root{--bg:#0f0e0b;--paper:#f8f6f1;--ink:#1d1a16;--accent:#c9a86a;--accent2:#7a5a2e}
body{margin:0;background:var(--bg);color:var(--ink);font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto}
header{background:linear-gradient(180deg,#171512,#0f0e0b);border-bottom:1px solid #1c1a17;color:#fff}
.container{max-width:1040px;margin:0 auto;padding:20px}
.brand{display:flex;gap:14px;align-items:center}
.logo{width:60px;height:60px}
.title{margin:0;font-weight:800;letter-spacing:.4px}
.tag{margin:.2rem 0 0;color:#e7d7b1}

.card{background:var(--paper);border:1px solid #e7decc;border-radius:18px;padding:24px;margin:18px 0;
      box-shadow:0 16px 40px rgba(0,0,0,.16)}
.card h2{margin:.2rem 0 1rem;font-size:1.6rem}
.card h3{margin:1.2rem 0 .6rem}
.slide{border-left:6px solid var(--accent);padding-left:18px}

.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
label{display:flex;flex-direction:column;gap:6px;font-weight:600}
input,select,button{padding:12px;border:1px solid #d3c6ad;border-radius:12px;background:#fbfaf7}
button.primary{background:var(--accent);color:#221e18;border-color:#b08b4a;cursor:pointer;font-weight:800}
button.primary:hover{filter:brightness(.98)}
button.ghost{background:transparent;border:1px dashed #b08b4a;color:#b08b4a}

.section{margin-top:18px}
.badge{display:inline-block;padding:.25rem .5rem;border:1px solid #e0d7c5;border-radius:.6rem;background:#f3ead8;margin-right:.3rem}
.pill{padding:.35rem .6rem;border-radius:.6rem;border:1px solid #ddd;margin:.2rem .3rem;display:inline-block;background:#fff}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
hr.sep{border:none;border-top:1px dashed #d9cdb5;margin:14px 0}

#slides .slide + .slide{margin-top:18px}
#printHint{font-size:.9rem;color:#6b604c}

@media print{
  body{background:#fff}
  header,.no-print{display:none}
  .card{box-shadow:none;border-color:#ddd}
}
"""

INDEX_HTML = f"""<!doctype html><html lang='en'><head>
<meta charset='utf-8'/><meta name='viewport' content='width=device-width, initial-scale=1'/>
<title>{APP_NAME} — Four Pillars (English)</title>
<link rel='stylesheet' href='/styles.css'/>
</head><body>
<header><div class='container brand'>
  <img src='/logo.svg' class='logo' alt='logo'/>
  <div><h1 class='title'>{APP_NAME}</h1><div class='tag'>{APP_TAGLINE}</div></div>
</div></header>

<main class='container'>
  <div class='card'>
    <h2>Enter Your Birth Details</h2>
    <p style='margin:.4rem 0 .8rem;color:#5a513f'>We convert your local birth time to Beijing Time (UTC+8) automatically for accurate Bazi (Four Pillars) calculation.</p>
    <form id='baziForm' class='no-print'>
      <div class='grid2'>
        <label>Name (optional) <input type='text' id='name' placeholder='Your name'/></label>
        <label>Gender (optional)
          <select id='gender'><option value=''>Prefer not to say</option><option value='male'>Male</option><option value='female'>Female</option></select>
        </label>
      </div>
      <div class='grid2'>
        <label>Date (YYYY-MM-DD) <input required type='date' id='date'/></label>
        <label>Time (24h HH:MM) <input required type='time' id='time'/></label>
      </div>
      <div class='grid2'>
        <label>City <input required type='text' id='city' placeholder='e.g., New York'/></label>
        <label>Country <input required type='text' id='country' placeholder='e.g., United States'/></label>
      </div>
      <div class='section'><button class='primary' type='submit'>Generate & Stream Interpretation</button>
        <button id='btnPrint' class='ghost' type='button' style='margin-left:8px'>Export to PDF</button>
        <span id='printHint' class='no-print'> (We format the page for clean printing.)</span>
      </div>
    </form>
  </div>

  <div id='slides' class='card'></div>
</main>

<script src='/app.js'></script>
</body></html>"""

APP_JS = r"""
function asSlide(title, html){ return `<div class='slide'><h3>${title}</h3>${html}</div>`; }

document.addEventListener('DOMContentLoaded',()=>{
  document.getElementById('baziForm').addEventListener('submit', onSubmit);
  document.getElementById('btnPrint').addEventListener('click', ()=>window.print());
});

async function onSubmit(ev){
  ev.preventDefault();
  const payload={
    name: document.getElementById('name').value,
    gender: document.getElementById('gender').value,
    date: document.getElementById('date').value,
    time: document.getElementById('time').value,
    city: document.getElementById('city').value,
    country: document.getElementById('country').value
  };
  const root=document.getElementById('slides');
  root.innerHTML = asSlide('Progress', `<div class="mono">Step 1/2 — Calculating pillars…</div>`);
  try{
    const res=await fetch('/api/chart', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    const data=await res.json();
    if(!data.ok){ root.innerHTML = asSlide('Error', `<div class="mono" style="color:#b00020">${data.error||'Unknown'}</div>`); return; }
    renderSlides(root, data);
    root.insertAdjacentHTML('beforeend', asSlide('AI Consultation (Streaming)', `<div id="aiBox" class="mono" style="white-space:pre-wrap"></div><div id="aiCtl" style="margin-top:8px"></div>`));
    await streamAI(data, payload.name);
  }catch(e){
    root.innerHTML = asSlide('Error', `<div class="mono" style="color:#b00020">Network/server error.</div>`);
  }
}

/** SSE with threaded relay on server; here我们自动重连一次，然后提供继续按钮 */
async function streamAI(chart, name, continue_text, tried){
  const box=document.getElementById('aiBox');
  const ctl=document.getElementById('aiCtl');
  ctl.innerHTML='';
  let sawDone=false;

  const body={chart, name};
  if(continue_text){ body.continue_text = continue_text; }

  const res=await fetch('/api/interpret_stream', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const reader=res.body.getReader(); const decoder=new TextDecoder('utf-8'); let buffer='';
  while(true){
    const {value,done}=await reader.read(); if(done) break;
    buffer+=decoder.decode(value,{stream:true});
    let parts=buffer.split('\n\n'); buffer=parts.pop();
    for(const chunk of parts){
      if(!chunk.startsWith('data:')) continue;
      const data=chunk.replace(/^data:\s*/,'');
      if(data==='[DONE]'){ sawDone=true; break; }
      try{ const obj=JSON.parse(data); const delta=obj.delta||obj.text||''; box.textContent+=delta; }
      catch{ box.textContent+=data; }
    }
    if(sawDone) break;
  }
  if(!sawDone){
    if(!tried){
      ctl.innerHTML='<span class="mono">Connection dropped — resuming…</span>';
      await streamAI(chart, name, box.textContent, true);
      return;
    }
    ctl.innerHTML = '<button id="resumeBtn" class="primary" style="padding:8px 12px;border-radius:8px;border:1px solid #b08b4a;background:#c9a86a;color:#221e18;cursor:pointer">Continue</button> <span class="mono" style="margin-left:8px">Click to resume from where it stopped.</span>';
    document.getElementById('resumeBtn').onclick = async ()=>{ ctl.innerHTML='<span class="mono">Resuming…</span>'; await streamAI(chart, name, box.textContent, true); };
  }
}

function pill(t){return `<span class='pill'>${t}</span>`}
function bar(label,val,total){const pct=total?Math.round(100*val/total):0;
  return `<div style="margin:6px 0">${label}: ${val}
    <div style="height:8px;background:#eee;border-radius:6px;overflow:hidden">
      <div style="width:${pct}%;height:8px;background:#c9a86a"></div>
    </div></div>`;
}
function renderSlides(root, data){
  const p=data.pillars||[], fe=data.five_elements||{}, luck=data.luck_cycles||[];
  const head = `
    <div><strong>Name:</strong> ${data.input.name||'—'} | <strong>Gender:</strong> ${data.input.gender||'—'}</div>
    <div><strong>City:</strong> ${data.input.city||'—'} | <strong>Country:</strong> ${data.input.country||'—'}</div>`;
  root.innerHTML = asSlide('Your Bazi Chart', head);

  const tz = `
    <div>Detected time zone: <span class='badge mono'>${data.input.timezone}</span></div>
    <div class='mono'>Local birth time: ${data.input.local_iso}</div>
    <div class='mono'>Beijing time (UTC+8): ${data.input.beijing_iso}</div>`;
  root.insertAdjacentHTML('beforeend', asSlide('Time Zone & Conversion', tz));

  const pillars = p.map(x=>`<div><strong>${x.pillar}:</strong> ${pill(x.gz)} ${pill(`${x.stem_py} (${x.stem_cn}) — ${x.stem_el}`)} ${pill(`${x.branch_py} (${x.branch_cn}) — ${x.branch_el}`)}</div>`).join('');
  root.insertAdjacentHTML('beforeend', asSlide('Four Pillars (Ganzhi)', pillars));

  const tg=data.ten_gods||{};
  const tgBlock = `
    <div>${pill(`Month Stem: ${tg.MonthStem||'-'}`)}</div>
    <div>${pill(`Year Stem: ${tg.YearStem||'-'}`)}</div>
    <div>${pill(`Hour Stem: ${tg.HourStem||'-'}`)}</div>`;
  root.insertAdjacentHTML('beforeend', asSlide('Ten Gods (to Day Stem)', tgBlock));

  const totalEl=Object.values(fe).reduce((a,b)=>a+b,0);
  const feBlock = `
    ${bar('Wood',fe.Wood||0,totalEl)}${bar('Fire',fe.Fire||0,totalEl)}${bar('Earth',fe.Earth||0,totalEl)}
    ${bar('Metal',fe.Metal||0,totalEl)}${bar('Water',fe.Water||0,totalEl)}
    <div class='badge'>Dominant element: <strong>${data.main_element||'-'}</strong></div>`;
  root.insertAdjacentHTML('beforeend', asSlide('Five Elements Distribution', feBlock));

  const luckBlock = luck.length?luck.map(x=>`<div>${pill(`Decade #${x.index}`)} starts ≈ age ${x.start_age}, pillar ${x.gz}</div>`).join(''):'<div class="badge">DaYun not available in this build.</div>';
  root.insertAdjacentHTML('beforeend', asSlide('10-Year Luck Cycles (DaYun)', luckBlock));
}
"""

# ===== Routes =====
@app.route("/")
def root_index(): return Response(INDEX_HTML, mimetype="text/html")
@app.route("/styles.css")
def styles(): return Response(STYLES_CSS, mimetype="text/css")
@app.route("/app.js")
def appjs(): return Response(APP_JS, mimetype="application/javascript")
@app.route("/logo.svg")
def logo(): return Response(LOGO_SVG, mimetype="image/svg+xml")

# ===== Chart =====
@app.route("/api/chart", methods=["POST"])
def api_chart():
    try:
        data = request.get_json(force=True)
        name    = (data.get("name") or "").strip()
        gender  = (data.get("gender") or "").strip()
        date_str= data.get("date"); time_str = data.get("time")
        city    = (data.get("city") or "").strip()
        country = (data.get("country") or "").strip()
        if not date_str or not time_str or not city or not country:
            return jsonify({"ok": False, "error": "Please provide date, time, city, and country."}), 400
        try:
            local_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except Exception:
            return jsonify({"ok": False, "error": "Invalid date/time format. Use YYYY-MM-DD and 24-hour HH:MM."}), 400

        geo = geocode_city_country(city, country)
        if not geo:
            return jsonify({"ok": False, "error": "Could not locate the city/country. Try adding state/province."}), 400
        lat, lon = geo["lat"], geo["lon"]

        tz_name = tz_name_from_latlon(lat, lon)
        if not tz_name:
            return jsonify({"ok": False, "error": "Unable to detect time zone for this location."}), 400

        conv = to_beijing_from_local(local_dt, tz_name)
        dt_bj = conv["beijing"]

        if not HAS_LUNAR:
            return jsonify({"ok": False, "error": "Server missing 'lunar-python'."}), 500

        solar = Solar.fromYmdHms(dt_bj.year, dt_bj.month, dt_bj.day, dt_bj.hour, dt_bj.minute, dt_bj.second)
        lunar = solar.getLunar()

        gz_year  = lunar.getYearInGanZhi(); gz_month = lunar.getMonthInGanZhi()
        gz_day   = lunar.getDayInGanZhi();  gz_hour  = lunar.getTimeInGanZhi()

        Y = split_ganzhi(gz_year); M = split_ganzhi(gz_month); D = split_ganzhi(gz_day); H = split_ganzhi(gz_hour)
        pillars = [{"pillar":"Year","gz":gz_year,**Y},{"pillar":"Month","gz":gz_month,**M},{"pillar":"Day","gz":gz_day,**D},{"pillar":"Hour","gz":gz_hour,**H}]
        for p in pillars:
            p["stem_py"]=STEM_TO_PY.get(p["stem_cn"],""); p["branch_py"]=BRANCH_TO_PY.get(p["branch_cn"],"")
            p["stem_el"]=STEM_TO_ELEMENT.get(p["stem_cn"],""); p["branch_el"]=BRANCH_TO_ELEMENT.get(p["branch_cn"],"")

        day_stem = D["stem_cn"]
        ten_gods={"MonthStem":ten_god(day_stem, M["stem_cn"]), "YearStem":ten_god(day_stem, Y["stem_cn"]), "HourStem":ten_god(day_stem, H["stem_cn"])}

        counts = {"Wood":0,"Fire":0,"Earth":0,"Metal":0,"Water":0}
        for p in pillars:
            if p["stem_el"]: counts[p["stem_el"]]+=1
            if p["branch_el"]: counts[p["branch_el"]]+=1
        main_el = max(counts, key=lambda k: counts[k]) if counts else ""

        luck=[]
        try:
            ec=lunar.getEightChar(); is_male=(gender.lower() in ["m","male","man"])
            yun=ec.getYun(is_male, True)
            for i in range(1,7):
                dy=yun.getDaYun(i)
                luck.append({"index":i,"start_age":dy.getStartAge(),"gz":dy.getGanZhi()})
        except Exception:
            luck=[]

        return jsonify({
            "ok": True,
            "input":{
                "name": name, "gender": gender, "city": city, "country": country,
                "lat": lat, "lon": lon, "timezone": tz_name,
                "local_iso": conv["local_iso"], "beijing_iso": conv["beijing_iso"]
            },
            "pillars": pillars,
            "ten_gods": ten_gods,
            "five_elements": counts,
            "main_element": main_el,
            "luck_cycles": luck
        }), 200

    except requests.HTTPError as http_err:
        return jsonify({"ok": False, "error": f"Geocoding error: {http_err.response.text[:200]}"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": f"Server error: {e}"}), 500

# ===== Threaded SSE relay to avoid worker timeouts =====
def _reader_thread(q: "queue.Queue[str]", url: str, headers: dict, payload: dict):
    try:
        with requests.post(url, headers=headers, json=payload, stream=True, timeout=600) as r:
            r.raise_for_status()
            for raw in r.iter_lines(decode_unicode=True):
                if raw:
                    q.put(raw)
            q.put("::DONE::")
    except Exception as e:
        q.put(f"::ERR::{e.__class__.__name__}: {e}")

def deepseek_stream(messages: List[Dict[str,str]]):
    if not DEEPSEEK_API_KEY:
        yield "data: " + json.dumps({"delta":"[Missing DEEPSEEK_API_KEY]\n"}) + "\n\n"
        yield "data: [DONE]\n\n"; return

    url = f"{DEEPSEEK_BASE_URL.rstrip('/')}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": DEEPSEEK_MODEL, "messages": messages, "temperature": 0.8, "stream": True}

    q: "queue.Queue[str]" = queue.Queue(maxsize=1000)
    t = threading.Thread(target=_reader_thread, args=(q, url, headers, payload), daemon=True)
    t.start()

    idle = 0
    while True:
        try:
            item = q.get(timeout=3.0)  # 每 3 秒检查一次
            idle = 0
            if item == "::DONE::":
                yield "data: [DONE]\n\n"; break
            if item.startswith("::ERR::"):
                # 把错误也作为文本发回去，前端会继续“续写”
                yield "data: " + json.dumps({"delta": f"\n\n[connection note] {item[7:]}\n"}) + "\n\n"
                yield "data: [DONE]\n\n"; break
            line = item
            if line.startswith("data: "):
                data = line[6:]
                if data == "[DONE]":
                    yield "data: [DONE]\n\n"; break
                try:
                    obj = json.loads(data)
                    delta = obj.get("choices",[{}])[0].get("delta",{}).get("content","")
                    if delta: yield "data: " + json.dumps({"delta": delta}) + "\n\n"
                except Exception:
                    yield "data: " + json.dumps({"text": data}) + "\n\n"
        except queue.Empty:
            idle += 1
            # 心跳，保证 Gunicorn 不触发超时
            yield ": ping\n\n"
            if idle % 10 == 0:
                # 每 30 秒再发一条前端可见的轻提示
                yield "data: " + json.dumps({"delta": ""}) + "\n\n"

@app.route("/api/interpret_stream", methods=["POST"])
def api_interpret_stream():
    body = request.get_json(force=True)
    chart = body.get("chart"); name = (body.get("name") or "").strip()
    continue_text = body.get("continue_text") or ""
    if not chart or not chart.get("ok"):
        return jsonify({"ok": False, "error": "Chart payload missing."}), 400

    try:
        year_branch_cn = (chart.get("pillars", [])[0] or {}).get("branch_cn","")
    except Exception:
        year_branch_cn = ""

    system_prompt = {
        "role":"system",
        "content":(
            "You are an experienced Bazi (Four Pillars) master. Provide a consultation-style reading with practical, "
            "detailed, actionable advice. Cover: marriage & zodiac compatibility; career & wealth with concrete industries; "
            "forecast for the next 1–5 years and 5–10 years (name specific years/months if suggested); health cautions; "
            "remedies & feng shui (colors, materials, accessories, directions, numbers, habits); and a prioritized action checklist. "
            "Keep a responsible tone—cultural guidance, not medical/financial advice."
        )
    }

    if continue_text:
        user_content = (
            "Continue the following consultation exactly where it stopped. "
            "Do not repeat previous text; keep structure and tone.\n\n"
            "Existing text:\n" + continue_text + "\n\nResume now:\n"
        )
    else:
        user_content = (
            f"Client name: {name or 'N/A'}.\n"
            f"Year Branch (zodiac hint): {year_branch_cn or 'unknown'}.\n"
            "Produce sections (bold headings) in this order: Personality Snapshot; Marriage & Compatibility; Career & Wealth; Health; "
            "Luck Cycles (1–5 years, 5–10 years, with months if possible); Remedies & Feng Shui; Final Strategy.\n\n"
            "Chart JSON:\n" + json.dumps(chart, ensure_ascii=False)
        )

    gen = deepseek_stream([system_prompt, {"role":"user","content": user_content}])
    return Response(stream_with_context(gen), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache, no-transform", "X-Accel-Buffering":"no", "Connection":"keep-alive"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
