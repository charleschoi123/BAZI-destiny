from __future__ import annotations
import os, json, requests, threading, queue
from datetime import datetime
from typing import Dict, Any, List, Optional
from flask import Flask, request, jsonify, Response, stream_with_context
from timezonefinder import TimezoneFinder
import pytz

APP_NAME    = "BAZI Destiny"
APP_TAGLINE = "Ancient Eastern wisdom — clear, practical guidance for modern life."

# DeepSeek / OpenAI 兼容
AI_BASE_URL = os.getenv("OPENAI_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
AI_API_KEY  = os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
AI_MODEL    = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

try:
    from lunar_python import Solar
    HAS_LUNAR = True
except Exception:
    HAS_LUNAR = False

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
tf = TimezoneFinder(in_memory=True)

STEMS_CN = ["甲","乙","丙","丁","戊","己","庚","辛","壬","癸"]
STEMS_PY = ["Jia","Yi","Bing","Ding","Wu","Ji","Geng","Xin","Ren","Gui"]
BRANCHES_CN = ["子","丑","寅","卯","辰","巳","午","未","申","酉","戌","亥"]
BRANCHES_PY = ["Zi","Chou","Yin","Mao","Chen","Si","Wu","Wei","Shen","You","Xu","Hai"]
STEM_TO_PY = dict(zip(STEMS_CN, STEMS_PY))
BRANCH_TO_PY = dict(zip(BRANCHES_CN, BRANCHES_PY))
STEM_TO_ELEMENT = {"甲":"Wood","乙":"Wood","丙":"Fire","丁":"Fire","戊":"Earth","己":"Earth","庚":"Metal","辛":"Metal","壬":"Water","癸":"Water"}
BRANCH_TO_ELEMENT = {"子":"Water","丑":"Earth","寅":"Wood","卯":"Wood","辰":"Earth","巳":"Fire","午":"Fire","未":"Earth","申":"Metal","酉":"Metal","戌":"Earth","亥":"Water"}
YANG_STEMS = {"甲","丙","戊","庚","壬"}
GENERATION = {"Wood":"Fire","Fire":"Earth","Earth":"Metal","Metal":"Water","Water":"Wood"}
CONTROL    = {"Wood":"Earth","Earth":"Water","Water":"Fire","Fire":"Metal","Metal":"Wood"}
TEN_GODS_EN = {
    "BiJie":"Peer (Parallel)","JieCai":"Rival (Rob Wealth)",
    "ShiShen":"Talent (Eating God / Output)","ShangGuan":"Performer (Hurting Officer)",
    "ZhengCai":"Direct Wealth","PianCai":"Indirect Wealth",
    "ZhengGuan":"Authority (Direct Officer)","QiSha":"Challenger (Seven Killings)",
    "ZhengYin":"Nurture (Direct Resource)","PianYin":"Inspiration (Indirect Resource)"
}

def split_ganzhi(gz: str) -> Dict[str,str]:
    return {"stem_cn": gz[0] if gz else "", "branch_cn": gz[1] if gz and len(gz)>1 else ""}

def parity(stem_cn: str) -> str:
    return "Yang" if stem_cn in YANG_STEMS else "Yin"

def ten_god(day_stem: str, other_stem: str) -> str:
    day_el = STEM_TO_ELEMENT.get(day_stem, ""); other_el = STEM_TO_ELEMENT.get(other_stem, "")
    if not day_el or not other_el: return ""
    same = (parity(day_stem) == parity(other_stem))
    if other_el == day_el:
        return TEN_GODS_EN["BiJie"] if same else TEN_GODS_EN["JieCai"]
    if GENERATION[day_el] == other_el:
        return TEN_GODS_EN["ShiShen"] if same else TEN_GODS_EN["ShangGuan"]
    if CONTROL[day_el] == other_el:
        return TEN_GODS_EN["PianCai"] if same else TEN_GODS_EN["ZhengCai"]
    if CONTROL[other_el] == day_el:
        return TEN_GODS_EN["QiSha"] if same else TEN_GODS_EN["ZhengGuan"]
    if GENERATION[other_el] == day_el:
        return TEN_GODS_EN["PianYin"] if same else TEN_GODS_EN["ZhengYin"]
    return ""

def geocode_city_country(city: str, country: str) -> Optional[Dict[str, Any]]:
    url = "https://nominatim.openstreetmap.org/search"
    params = {"format":"json","addressdetails":1,"city":city,"country":country,"limit":5}
    r = requests.get(url, params=params, headers={"User-Agent": f"{APP_NAME}/1.0"}, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data: return None
    item = data[0]
    return {"lat": float(item["lat"]), "lon": float(item["lon"])}

def tz_name_from_latlon(lat: float, lon: float) -> Optional[str]:
    try:
        return tf.timezone_at(lat=lat, lng=lon)
    except Exception:
        return None

def to_beijing_from_local(local_dt: datetime, tz_name: str) -> Dict[str, Any]:
    tz_local = pytz.timezone(tz_name)
    dt_localized = tz_local.localize(local_dt, is_dst=None)
    bj = pytz.timezone("Asia/Shanghai")
    dt_bj = dt_localized.astimezone(bj)
    return {"local_iso": dt_localized.isoformat(), "beijing_iso": dt_bj.isoformat(), "beijing": dt_bj}

# ---------- UI 资源 ----------
LOGO_SVG = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 160 160'>
<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='#ffb25e'/><stop offset='1' stop-color='#ff5e5e'/></linearGradient></defs>
<circle cx='80' cy='80' r='76' fill='#0c0c0e' stroke='url(#g)' stroke-width='4'/>
<path d='M80 28a52 52 0 1 0 0 104c-14 0-26-12-26-26s12-26 26-26 26-12 26-26S94 28 80 28Z' fill='url(#g)'/>
<circle cx='80' cy='54' r='26' fill='#0c0c0e'/><circle cx='80' cy='106' r='26' fill='url(#g)'/>
<circle cx='80' cy='54' r='6' fill='url(#g)'/><circle cx='80' cy='106' r='6' fill='#0c0c0e'/>
</svg>"""

STYLES_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap');
*{box-sizing:border-box}
:root{
  --bg:#0c0c0e; --hero:#141421; --hero2:#281f3a;
  --paper:#ffffff; --muted:#6b6f76; --ink:#0f1215;
  --brand:#ff8a3d; --brand2:#ff4d4f; --line:#e8eaee; --shadow:0 10px 30px rgba(14,16,20,.12);
}
html,body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,system-ui,Segoe UI,Roboto,Arial}
.container{max-width:1080px;margin:0 auto;padding:0 20px}
header.hero{
  background:radial-gradient(1200px 600px at 30% -10%, var(--hero2), transparent 60%),
             radial-gradient(1200px 600px at 80% -10%, #1e2545, transparent 60%),
             linear-gradient(180deg, var(--hero), #0c0c0e 70%);
  color:#fff; padding:60px 0 40px; border-bottom:1px solid #1a1a25;
}
.brand{display:flex;align-items:center;gap:14px}
.logo{width:56px;height:56px}
.brand h1{margin:0;font-size:24px;letter-spacing:.3px}
.tag{margin:6px 0 0;opacity:.85}
.hero-main{display:grid;grid-template-columns:1.2fr .8fr;gap:24px;align-items:end;margin-top:26px}
.hero h2{font-size:56px;line-height:1.05;margin:10px 0;font-weight:800}
.hero p{margin:0 0 16px;font-size:18px;opacity:.92}
.badge{display:inline-block;padding:.35rem .6rem;border:1px solid #ffffff2a;border-radius:999px;backdrop-filter:blur(6px);background:#ffffff14;color:#fff;margin-right:8px}

.main{padding:26px 0 64px}
.card{background:var(--paper);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow);padding:20px;margin:14px 0}
.card h3{margin:2px 0 10px;font-size:20px}
.card .sub{color:var(--muted);font-size:14px;margin:-4px 0 10px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
label{display:flex;flex-direction:column;font-weight:600;color:#2c3137}
input,select,button{padding:12px;border:1px solid #dfe3e9;border-radius:12px;background:#fff}
button.primary{background:linear-gradient(90deg,var(--brand),var(--brand2));color:#fff;border:0;font-weight:800;cursor:pointer}
button.primary:hover{filter:brightness(.98)}
.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.kpi{display:flex;gap:10px;flex-wrap:wrap}
.kpi .pill{padding:.35rem .6rem;border-radius:10px;border:1px solid var(--line);background:#fafbfc}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
hr.sep{border:none;border-top:1px dashed #e6e9ef;margin:14px 0}

.bar{height:8px;background:#eef1f6;border-radius:6px;overflow:hidden}
.bar > i{display:block;height:8px;background:linear-gradient(90deg,var(--brand),var(--brand2))}
.tagline{display:flex;gap:8px;align-items:center;flex-wrap:wrap}

.markdown h3{margin:14px 0 6px}
.markdown p{margin:8px 0}
.markdown ul{margin:6px 0 6px 18px; padding:0}
.markdown li{margin:4px 0}

footer{color:#8b9097;font-size:12px;text-align:center;margin:40px 0 10px}
"""

INDEX_HTML = f"""<!doctype html><html lang='en'><head>
<meta charset='utf-8'/><meta name='viewport' content='width=device-width, initial-scale=1'/>
<title>{APP_NAME} — Four Pillars (English)</title>
<link rel='stylesheet' href='/styles.css'/>
<script src="https://cdn.jsdelivr.net/npm/html2pdf.js@0.10.1/dist/html2pdf.bundle.min.js"></script>
</head><body>

<header class="hero">
  <div class="container">
    <div class="brand">
      <img src="/logo.svg" class="logo" alt="logo"/>
      <div>
        <h1>BaZi Destiny</h1>
        <div class="tag">{APP_TAGLINE}</div>
      </div>
    </div>
    <div class="hero-main">
      <div>
        <h2>Discover Your <span style="background:linear-gradient(90deg,var(--brand),var(--brand2));-webkit-background-clip:text;background-clip:text;color:transparent">Destiny</span></h2>
        <p>Unlock the ancient wisdom of Chinese astrology with modern analysis. Explore your Four Pillars and gain practical insights.</p>
        <div class="tagline">
          <span class="badge">English-only, culture friendly</span>
          <span class="badge">Auto time-zone to Beijing</span>
          <span class="badge">Personalized reading</span>
        </div>
      </div>
      <div class="card" style="backdrop-filter:blur(6px); background:#ffffff10; border-color:#ffffff33; color:#fff">
        <h3 style="color:#fff;margin-top:0">Quick Start</h3>
        <div class="sub" style="color:#fff">Enter your birth details</div>
        <form id="baziForm">
          <div class="grid2">
            <label style="color:#fff">Name (optional)<input type="text" id="name" placeholder="Your name"/></label>
            <label style="color:#fff">Gender (optional)
              <select id="gender"><option value="">Prefer not to say</option><option value="male">Male</option><option value="female">Female</option></select>
            </label>
          </div>
          <div class="grid2">
            <label style="color:#fff">Date <input required type="date" id="date"/></label>
            <label style="color:#fff">Time <input required type="time" id="time"/></label>
          </div>
          <div class="grid2">
            <label style="color:#fff">City <input required type="text" id="city" placeholder="e.g., Guangzhou"/></label>
            <label style="color:#fff">Country <input required type="text" id="country" placeholder="e.g., China"/></label>
          </div>
          <div class="row" style="margin-top:10px">
            <button class="primary" type="submit">Generate & Stream Interpretation</button>
            <button id="exportPDF" type="button" style="border:1px solid #ffffff55;background:transparent;color:#fff;border-radius:12px;padding:12px">Export PDF</button>
          </div>
        </form>
      </div>
    </div>
  </div>
</header>

<main class="main">
  <div class="container" id="results"></div>
</main>

<footer>© BAZI Destiny — cultural guidance only.</footer>

<script src="/app.js"></script>
</body></html>"""

APP_JS = r"""
// ===== 工具 =====
function mdToHtml(md){
  if(!md) return '';
  let html = md.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  html = html.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  html = html.replace(/^###\s*(.+)$/gm,'<h3>$1</h3>');
  html = html.replace(/^##\s*(.+)$/gm,'<h3>$1</h3>');
  html = html.replace(/^- (.+)$/gm,'<li>$1</li>');
  html = html.replace(/(<li>[\s\S]*?<\/li>)/g,'<ul>$1</ul>');
  html = html.replace(/\n{2,}/g,'</p><p>');
  return `<p>${html}</p>`.replace(/<p><\/p>/g,'');
}
function pctBar(label,val,sum){
  const pct = Math.round(100*(val||0)/(sum||1));
  return `<div style="margin:6px 0"><div class="row" style="justify-content:space-between"><div>${label}</div><div>${val||0}</div></div>
  <div class="bar"><i style="width:${pct}%"></i></div></div>`;
}
// 全局 AI 状态
window.aiState = { raw:'', done:false };

const ICON = {
  tz:'🕰️', pillars:'🧱', gods:'🔰', elements:'🧪', luck:'⏳', love:'💞', ai:'✨',
  career:'💼', health:'🩺', wealth:'💰', remedy:'🧿', action:'✅'
};

function card(title, sub, body, icon){
  return `<section class="card">
    <div class="row" style="justify-content:space-between">
      <div class="row" style="gap:8px"><div style="font-size:20px">${icon||''}</div><h3>${title}</h3></div>
    </div>
    ${sub?`<div class="sub">${sub}</div>`:''}
    <div>${body||''}</div>
  </section>`;
}

function renderAll(data){
  const box = document.getElementById('results');
  const p = data.pillars||[], fe=data.five_elements||{}, luck=data.luck_cycles||[];
  const totalEl = Object.values(fe).reduce((a,b)=>a+b,0)||1;

  const overview = `
    <div class="kpi">
      <span class="pill"><b>Name</b> ${data.input.name||'—'}</span>
      <span class="pill"><b>Gender</b> ${data.input.gender||'—'}</span>
      <span class="pill"><b>City</b> ${data.input.city||'—'}</span>
      <span class="pill"><b>Country</b> ${data.input.country||'—'}</span>
      <span class="pill"><b>Time Zone</b> ${data.input.timezone}</span>
    </div>
    <hr class="sep"/>
    <div class="row" style="gap:20px;flex-wrap:wrap">
      <div><div class="sub">Local time</div><div class="pill">${data.input.local_iso}</div></div>
      <div><div class="sub">Beijing time (UTC+8)</div><div class="pill">${data.input.beijing_iso}</div></div>
    </div>`;
  const pillars = p.map(x=>`<span class="pill"><b>${x.pillar}</b> ${x.gz} — ${x.stem_el}/${x.branch_el}</span>`).join(' ');
  const tg = data.ten_gods||{};
  const tgBlock = `
    <div class="kpi">
      <span class="pill">Month: ${tg.MonthStem||'-'}</span>
      <span class="pill">Year: ${tg.YearStem||'-'}</span>
      <span class="pill">Hour: ${tg.HourStem||'-'}</span>
    </div>`;
  const feBlock = `
    ${pctBar('Wood',fe.Wood,totalEl)}
    ${pctBar('Fire',fe.Fire,totalEl)}
    ${pctBar('Earth',fe.Earth,totalEl)}
    ${pctBar('Metal',fe.Metal,totalEl)}
    ${pctBar('Water',fe.Water,totalEl)}
    <div class="row" style="gap:8px;margin-top:6px"><span class="pill"><b>Dominant</b> ${data.main_element||'-'}</span></div>`;
  const luckBlock = (luck && luck.length?luck:[]).map(x=>`<span class="pill"><b>#${x.index}</b> start age ${x.start_age} — ${x.gz}</span>`).join(' ');
  const yearBranch = (p.find(x=>x.pillar==='Year')||{}).branch_cn || '';

  box.innerHTML =
    card('Overview','Time-zone conversion & inputs', overview, ICON.tz) +
    card('Four Pillars (GanZhi)','Stem/Branch & elements', `<div class="kpi">${pillars}</div>`, ICON.pillars) +
    card('Ten Gods (to Day Stem)','Traditional roles in simple English', tgBlock, ICON.gods) +
    card('Five Elements Distribution','Counts from pillars (stem + branch)', feBlock, ICON.elements) +
    card('10-Year Luck Cycles (DaYun)','First few decades', `<div class="kpi">${luckBlock||'—'}</div>`, ICON.luck) +
    card('Marriage & Zodiac Matching','Based on Year Branch', `<div class="kpi"><span class="pill"><b>Year Branch</b> ${yearBranch||'—'}</span></div>`, ICON.love) +
    // “AI 输出暂存卡”，结束后会拆分成多卡
    card('Your Personalized BaZi Reading','', `<div id="aiBox" class="markdown"></div><div id="aiCtl" class="row"></div>`, ICON.ai);
}

// ===== 事件绑定 =====
document.addEventListener('DOMContentLoaded', ()=>{
  document.getElementById('baziForm').addEventListener('submit', onSubmit);
  document.getElementById('exportPDF').onclick = ()=>{
    const area = document.getElementById('results');
    const opt = {
      margin:[10,10,10,10], filename:'bazi-destiny.pdf',
      image:{type:'jpeg',quality:0.98}, html2canvas:{scale:2,useCORS:true},
      jsPDF:{unit:'mm',format:'a4',orientation:'portrait'}
    };
    html2pdf().from(area).set(opt).save();
  };
});

async function onSubmit(ev){
  ev.preventDefault();
  const payload={
    name:document.getElementById('name').value,
    gender:document.getElementById('gender').value,
    date:document.getElementById('date').value,
    time:document.getElementById('time').value,
    city:document.getElementById('city').value,
    country:document.getElementById('country').value
  };
  const box = document.getElementById('results');
  box.innerHTML = `<section class="card"><h3>Working…</h3><div class="sub">Step 1/2 — calculating pillars</div></section>`;
  try{
    const res = await fetch('/api/chart',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data = await res.json();
    if(!data.ok){ box.innerHTML = `<section class="card"><h3>Error</h3><div class="sub" style="color:#b00020">${data.error||'Unknown error'}</div></section>`; return; }
    renderAll(data);
    await streamAI(data, payload.name);
  }catch(e){
    box.innerHTML = `<section class="card"><h3>Network / server error</h3></section>`;
  }
}

// ===== AI 流式 & H3 分卡 =====
function tidyEnding(text){
  if(!text) return text;
  return text.replace(/\n+Would you like to explore any area in more depth\?\s*$/i,'')
             .replace(/\n+Would you like to (?:dive|go) deeper.*?\?\s*$/i,'')
             .replace(/\n+Let me know if you want.*?\.\s*$/i,'');
}

// 把 H3 标题分段 => {title, html}
function splitByH3(html){
  const tmp = document.createElement('div');
  tmp.innerHTML = html;
  const nodes = Array.from(tmp.childNodes);
  const sections = [];
  let cur = {title:'Overview', parts:[]};
  nodes.forEach(n=>{
    if(n.tagName && n.tagName.toLowerCase()==='h3'){
      if(cur.parts.length) sections.push(cur);
      cur = {title: n.textContent.trim(), parts:[]};
    }else{
      cur.parts.push(n.outerHTML || n.textContent);
    }
  });
  if(cur.parts.length) sections.push(cur);
  return sections.map(s=>({title:s.title, html:s.parts.join('')}));
}

// 归一化标题，用于给卡片挑选图标/顺序
function normalizeTitle(t){
  const x = t.toLowerCase();
  if(/marriage|relationship|partner|compat/i.test(x)) return {key:'marriage', icon:'💞', title:'Marriage & Compatibility'};
  if(/career|work|profession/i.test(x)) return {key:'career', icon:'💼', title:'Career'};
  if(/health|well[- ]?being|diet|exercise/i.test(x)) return {key:'health', icon:'🩺', title:'Health'};
  if(/wealth|money|finance|investment|property/i.test(x)) return {key:'wealth', icon:'💰', title:'Wealth'};
  if(/remed|feng\s*shui|color|habit|direction/i.test(x)) return {key:'remedy', icon:'🧿', title:'Remedies & Feng Shui'};
  if(/action|checklist|plan|priority/i.test(x)) return {key:'action', icon:'✅', title:'Prioritized Action Checklist'};
  if(/luck|cycle|forecast|year|month/i.test(x)) return {key:'forecast', icon:'⏳', title:'Luck & Forecast'};
  return {key:'other', icon:'✨', title:t};
}

function renderAICardsFromRaw(raw){
  const container = document.getElementById('results');
  // 先把原 AI 占位卡删掉
  const aiCard = document.getElementById('aiBox')?.closest('.card');
  if(aiCard) aiCard.remove();

  const html = mdToHtml(raw);
  const secs = splitByH3(html);
  if(!secs.length){
    // 没有 H3，就整段显示为一张卡
    container.insertAdjacentHTML('beforeend', card('Your Personalized BaZi Reading','', html, '✨'));
    return;
  }

  // 映射 -> 规范标题 + 图标
  const normalized = secs.map(s=>{
    const m = normalizeTitle(s.title || 'Section');
    return {...m, html:s.html};
  });

  // 期望顺序
  const order = ['marriage','career','health','wealth','remedy','action','forecast','other'];
  normalized.sort((a,b)=>order.indexOf(a.key)-order.indexOf(b.key));

  // 依次插卡
  normalized.forEach(sec=>{
    container.insertAdjacentHTML('beforeend',
      card(sec.title,'', sec.html, sec.icon));
  });
}

async function streamAI(chart, name, continue_text, tried){
  const box = document.getElementById('aiBox');
  const ctl = document.getElementById('aiCtl');

  if(continue_text){
    window.aiState.raw = continue_text;
  }else{
    window.aiState = { raw:'', done:false };
    box.innerHTML = '';
  }
  ctl.innerHTML = '';

  const body = { chart, name };
  if(continue_text) body.continue_text = continue_text;

  const res = await fetch('/api/interpret_stream', {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)
  });

  if(!res.ok){
    ctl.innerHTML = '<span class="sub" style="color:#b00020">Server busy — tap Continue.</span>';
    const btn = document.createElement('button');
    btn.className = 'primary'; btn.textContent = 'Continue';
    btn.onclick = ()=>streamAI(chart, name, box.textContent, true);
    ctl.appendChild(btn);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let sawDone = false;

  while(true){
    const {value, done} = await reader.read();
    if(done) break;

    buffer += decoder.decode(value, {stream:true});
    let chunks = buffer.split('\n\n');
    buffer = chunks.pop();

    for(const chunk of chunks){
      if(chunk.startsWith('data:')){
        const data = chunk.replace(/^data:\s*/,'');
        if(data==='[DONE]'){ sawDone=true; break; }
        try{
          const obj = JSON.parse(data);
          const delta = obj.delta || obj.text || '';
          if(delta){
            window.aiState.raw += delta;
            box.innerHTML = mdToHtml(window.aiState.raw); // 流式预览
          }
        }catch{
            window.aiState.raw += data;
            box.innerHTML = mdToHtml(window.aiState.raw);
        }
      }
    }
    if(sawDone) break;
  }

  // 流结束：清理结尾句，并按 H3 拆成多卡
  window.aiState.raw = tidyEnding(window.aiState.raw);
  window.aiState.done = true;
  renderAICardsFromRaw(window.aiState.raw);

  if(!sawDone && !tried){
    // 极少数情况下断开，这里提供继续写按钮（不清空）
    const container = document.getElementById('results');
    container.insertAdjacentHTML('beforeend',
      card('Continue Reading','','<div class="sub">Connection dropped — you can resume.</div><button id="resumeBtn" class="primary">Continue</button>','✨'));
    document.getElementById('resumeBtn').onclick = async ()=>{
      await streamAI(chart, name, (document.getElementById('results').textContent||''), true);
    };
  }
}
"""

# ============== Flask 路由 ==============
@app.route("/")
def root_index(): return Response(INDEX_HTML, mimetype="text/html")
@app.route("/styles.css")
def styles(): return Response(STYLES_CSS, mimetype="text/css")
@app.route("/app.js")
def appjs(): return Response(APP_JS, mimetype="application/javascript")
@app.route("/logo.svg")
def logo(): return Response(LOGO_SVG, mimetype="image/svg+xml")
@app.route("/favicon.ico")
def favicon(): return Response(LOGO_SVG, mimetype="image/svg+xml")

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
            return jsonify({"ok": False, "error": "Invalid date/time format. Use YYYY-MM-DD and HH:MM (24h)."}), 400

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
                "timezone": tz_name, "local_iso": conv["local_iso"], "beijing_iso": conv["beijing_iso"]
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

# ====== AI Stream (SSE) ======
def _reader_thread(q: "queue.Queue[str]", url: str, headers: dict, payload: dict):
    try:
        with requests.post(url, headers=headers, json=payload, stream=True, timeout=600) as r:
            r.raise_for_status()
            for raw in r.iter_lines(decode_unicode=True):
                if raw: q.put(raw)
            q.put("::DONE::")
    except Exception as e:
        q.put(f"::ERR::{e.__class__.__name__}: {e}")

def ai_stream(messages: List[Dict[str,str]]):
    if not AI_API_KEY:
        yield "data: " + json.dumps({"delta":"[Missing API key]\n"}) + "\n\n"
        yield "data: [DONE]\n\n"; return
    url = f"{AI_BASE_URL.rstrip('/')}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": AI_MODEL, "messages": messages, "temperature": 0.8, "stream": True}
    q: "queue.Queue[str]" = queue.Queue(maxsize=1000)
    t = threading.Thread(target=_reader_thread, args=(q, url, headers, payload), daemon=True)
    t.start()
    idle=0
    while True:
        try:
            item=q.get(timeout=2.0); idle=0
            if item=="::DONE::": yield "data: [DONE]\n\n"; break
            if item.startswith("::ERR::"):
                yield "data: " + json.dumps({"delta": f"\n\n[connection note] {item[7:]}\n"}) + "\n\n"
                yield "data: [DONE]\n\n"; break
            if item.startswith("data: "):
                data=item[6:]
                if data=="[DONE]": yield "data: [DONE]\n\n"; break
                try:
                    obj=json.loads(data)
                    delta=obj.get("choices",[{}])[0].get("delta",{}).get("content","")
                    if delta: yield "data: " + json.dumps({"delta": delta}) + "\n\n"
                except Exception:
                    yield "data: " + json.dumps({"text": data}) + "\n\n"
        except queue.Empty:
            idle+=1; yield ": ping\n\n"  # keep-alive
            if idle%5==0: yield "data: " + json.dumps({"delta": ""}) + "\n\n"

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
            "Tone: clear, warm, culturally respectful. This is cultural guidance, not medical or financial advice."
        )
    }
    if continue_text:
        user_content = "Continue the following consultation exactly where it stopped.\n\n" + continue_text + "\n\nResume now:\n"
    else:
        user_content = (
            f"Client name: {name or 'N/A'}.\n"
            f"Year Branch (zodiac hint): {year_branch_cn or 'unknown'}.\n"
            "Use Markdown with **bold** section titles and bullet points. Use H3 headings for main sections.\n\n"
            "Chart JSON:\n" + json.dumps(chart, ensure_ascii=False)
        )
    gen = ai_stream([system_prompt, {"role":"user","content": user_content}])
    return Response(stream_with_context(gen), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache, no-transform", "X-Accel-Buffering":"no", "Connection":"keep-alive"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
