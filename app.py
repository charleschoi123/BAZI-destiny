from __future__ import annotations
import os, json, requests
from datetime import datetime
from typing import Dict, Any, Optional, List
from flask import Flask, request, jsonify, Response, stream_with_context
from timezonefinder import TimezoneFinder
import pytz

# ========== DeepSeek 配置（OpenAI 兼容接口）==========
DEEPSEEK_BASE_URL = os.getenv("OPENAI_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL    = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_LABEL    = os.getenv("AI_SOURCE_LABEL", "DeepSeek")

# ========== 八字引擎 ==========
try:
    from lunar_python import Solar
    HAS_LUNAR = True
except Exception:
    HAS_LUNAR = False

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
tf = TimezoneFinder(in_memory=True)

# ---------------- 基础表 ----------------
STEMS_CN = ["甲","乙","丙","丁","戊","己","庚","辛","壬","癸"]
STEMS_PY = ["Jia","Yi","Bing","Ding","Wu","Ji","Geng","Xin","Ren","Gui"]
BRANCHES_CN = ["子","丑","寅","卯","辰","巳","午","未","申","酉","戌","亥"]
BRANCHES_PY = ["Zi","Chou","Yin","Mao","Chen","Si","Wu","Wei","Shen","You","Xu","Hai"]
STEM_TO_PY = dict(zip(STEMS_CN, STEMS_PY))
BRANCH_TO_PY = dict(zip(BRANCHES_CN, BRANCHES_PY))

STEM_TO_ELEMENT = {"甲":"Wood","乙":"Wood","丙":"Fire","丁":"Fire","戊":"Earth","己":"Earth","庚":"Metal","辛":"Metal","壬":"Water","癸":"Water"}
BRANCH_TO_ELEMENT = {"子":"Water","丑":"Earth","寅":"Wood","卯":"Wood","辰":"Earth","巳":"Fire","午":"Fire","未":"Earth","申":"Metal","酉":"Metal","戌":"Earth","亥":"Water"}
YANG_STEMS = {"甲","丙","戊","庚","壬"}
GENERATION = {"Wood":"Fire","Fire":"Earth","Earth":"Metal","Metal":"Water","Water":"Wood"}   # 生
CONTROL    = {"Wood":"Earth","Earth":"Water","Water":"Fire","Fire":"Metal","Metal":"Wood"}   # 克

TEN_GODS_EN = {
    "BiJie":"Peer (Parallel)","JieCai":"Rival (Rob Wealth)",
    "ShiShen":"Talent (Eating God / Output)","ShangGuan":"Performer (Hurting Officer)",
    "ZhengCai":"Direct Wealth","PianCai":"Indirect Wealth",
    "ZhengGuan":"Authority (Direct Officer)","QiSha":"Challenger (Seven Killings)",
    "ZhengYin":"Nurture (Direct Resource)","PianYin":"Inspiration (Indirect Resource)"
}
FIVE_ELEMENTS_COLORS = {"Wood":["green","cyan"],"Fire":["red","orange"],"Earth":["yellow","brown"],"Metal":["white","silver","gold"],"Water":["black","blue"]}
FIVE_ELEMENTS_NUMBERS = {"Wood":[3,8],"Fire":[2,7],"Earth":[5,10],"Metal":[4,9],"Water":[1,6]}

# ---------------- 工具函数 ----------------
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

def split_ganzhi(gz: str) -> Dict[str,str]:
    return {"stem_cn": gz[0] if gz else "", "branch_cn": gz[1] if gz and len(gz)>1 else ""}

def parity(stem_cn: str) -> str:
    return "Yang" if stem_cn in YANG_STEMS else "Yin"

def ten_god(day_stem: str, other_stem: str) -> str:
    day_el = STEM_TO_ELEMENT.get(day_stem, ""); other_el = STEM_TO_ELEMENT.get(other_stem, "")
    if not day_el or not other_el: return ""
    same = (parity(day_stem) == parity(other_stem))
    if other_el == day_el: return TEN_GODS_EN["BiJie"] if same else TEN_GODS_EN["JieCai"]
    if GENERATION[day_el] == other_el: return TEN_GODS_EN["ShiShen"] if same else TEN_GODS_EN["ShangGuan"]
    if CONTROL[day_el] == other_el: return TEN_GODS_EN["PianCai"] if same else TEN_GODS_EN["ZhengCai"]
    if CONTROL[other_el] == day_el: return TEN_GODS_EN["QiSha"] if same else TEN_GODS_EN["ZhengGuan"]
    if GENERATION[other_el] == day_el: return TEN_GODS_EN["PianYin"] if same else TEN_GODS_EN["ZhengYin"]
    return ""

# ---------------- 内嵌静态资源（无文件夹）----------------
LOGO_SVG = "<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 64 64'><defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='#c7a46c'/><stop offset='1' stop-color='#8c6a3d'/></linearGradient></defs><rect x='2' y='2' width='60' height='60' rx='12' fill='#111' stroke='url(#g)' stroke-width='3'/><g fill='url(#g)' transform='translate(32 32)'><circle r='18' fill='none' stroke='url(#g)' stroke-width='2'/><path d='M0,-14 A14,14 0 1,1 0,14 A7,7 0 1,0 0,-14Z'/></g></svg>"
STYLES_CSS = "*{box-sizing:border-box}body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,Noto Sans,sans-serif;margin:0;background:#faf8f5;color:#2a2a2a}header{background:#111;color:#fff;padding:16px 0;border-bottom:2px solid #c7a46c}.container{max-width:980px;margin:0 auto;padding:16px}.brand{display:flex;gap:12px;align-items:center;padding:0 16px}.logo{width:48px;height:48px}h1{margin:0;font-size:1.4rem}.card{background:#fff;border:1px solid #e6dfd2;border-radius:12px;padding:16px 16px 20px;margin-top:16px;box-shadow:0 2px 6px rgba(0,0,0,.04)}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}label{display:flex;flex-direction:column;gap:6px;font-weight:600}input,select,button{padding:10px;border:1px solid #d9d0bf;border-radius:10px;background:#fcfbf9}button.primary{background:#c7a46c;color:#111;border-color:#b69053;cursor:pointer}button.primary:hover{filter:brightness(.98)}.inline{display:flex;gap:8px}.results{margin:10px 0}.result-item{padding:8px;border:1px solid #ddd;border-radius:8px;margin-bottom:6px;cursor:pointer;background:#fff}.result-item:hover{background:#f5efe6}.mapWrap{margin-top:8px}#map{width:100%;height:260px;border-radius:12px;border:1px solid #e0d6c4}.actions{margin-top:12px}.badge{display:inline-block;padding:.2rem .5rem;border-radius:.5rem;background:#efe7d8;margin-right:.3rem}.pill{padding:.4rem .6rem;border-radius:.6rem;border:1px solid #ddd;margin:.2rem .3rem;display:inline-block}.section{margin-top:1.25rem}.mono{font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono','Courier New', monospace;}footer{padding:16px;text-align:center;color:#555}.tiny{font-size:.8rem}@media (max-width:720px){.grid2{grid-template-columns:1fr}}"
INDEX_HTML = f"<!doctype html><html lang='en'><head><meta charset='utf-8'/><meta name='viewport' content='width=device-width, initial-scale=1'/><title>Bazi Global — Four Pillars (English)</title><link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'/><link rel='stylesheet' href='/styles.css'/></head><body><header><div class='brand'><img src='/logo.svg' alt='logo' class='logo'/><div><h1>Bazi Global</h1><p>Four Pillars in clear English — with automatic time zone & location</p></div></div></header><main class='container'><div class='card'><h2>Enter Your Birth Details</h2><form id='baziForm'><div class='grid2'><label>Date (YYYY-MM-DD) <input required type='date' id='date'/></label><label>Time (24h HH:MM) <input required type='time' id='time'/></label></div><div class='grid2'><label>Gender (optional)<select id='gender'><option value=''>Prefer not to say</option><option value='male'>Male</option><option value='female'>Female</option></select></label><label>Place search (City / Country)<div class='inline'><input type='text' id='place' placeholder='e.g., London, UK'/><button type='button' id='searchBtn'>Search</button></div></label></div><div id='searchResults' class='results'></div><div id='mapWrap' class='mapWrap'><div id='map'></div><p class='help'>Tip: Search a city, then click on the map to fine-tune the exact birth location.</p></div><input type='hidden' id='lat'/><input type='hidden' id='lon'/><div class='actions'><button type='submit' class='primary'>Generate Chart & AI Interpretation</button></div></form></div><div id='output' class='card' style='display:none;'></div></main><footer><p class='tiny'>Powered by {DEEPSEEK_LABEL}. Cultural guidance only.</p></footer><script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'></script><script src='/app.js'></script></body></html>"
APP_JS = r"""let map, marker;
function initMap(){map=L.map('map').setView([0,0],2);L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; OpenStreetMap contributors'}).addTo(map);map.on('click',(e)=>setPoint(e.latlng.lat,e.latlng.lng,'Custom point'));}
function setPoint(lat,lon,label){if(marker) map.removeLayer(marker);marker=L.marker([lat,lon]).addTo(map).bindPopup(label||'Selected').openPopup();document.querySelector('#lat').value=lat.toFixed(6);document.querySelector('#lon').value=lon.toFixed(6);}
async function searchPlace(){const q=document.querySelector('#place').value.trim();const out=document.querySelector('#searchResults');out.innerHTML='';if(!q) return;const url=`https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(q)}`;const res=await fetch(url,{headers:{'Accept-Language':'en'}});const data=await res.json();if(!Array.isArray(data)||!data.length){out.innerHTML='<div class=\"tiny\">No results. Try another spelling.</div>';return;}data.slice(0,5).forEach(item=>{const div=document.createElement('div');div.className='result-item';div.textContent=item.display_name;div.onclick=()=>{map.setView([+item.lat,+item.lon],8);setPoint(+item.lat,+item.lon,item.display_name);};out.appendChild(div);});}
async function onSubmit(ev){ev.preventDefault();const date=document.querySelector('#date').value;const time=document.querySelector('#time').value;const gender=document.querySelector('#gender').value;const place=document.querySelector('#place').value;const lat=document.querySelector('#lat').value;const lon=document.querySelector('#lon').value;const output=document.querySelector('#output');if(!lat||!lon){alert('Please search and pick your birth location on the map.');return;}output.style.display='block';output.innerHTML='<div class=\"mono\">Step 1/2 — Calculating your pillars…</div>';try{const res=await fetch('/api/chart',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({date,time,gender,place,lat,lon})});const data=await res.json();if(!data.ok){output.innerHTML=`<div class=\"mono\" style=\"color:#b00020\">Error: ${data.error||'Unknown'}</div>`;return;}renderResult(data);output.insertAdjacentHTML('beforeend','<div class=\"section\"><div class=\"mono\">Step 2/2 — AI interpretation (streaming)…</div><div id=\"aiBox\" class=\"mono\" style=\"white-space:pre-wrap\"></div></div>');await streamAI(data);}catch(e){output.innerHTML='<div class=\"mono\" style=\"color:#b00020\">Network/server error.</div>';}}
async function streamAI(chart){const aiBox=document.getElementById('aiBox');const res=await fetch('/api/interpret_stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chart})});const reader=res.body.getReader();const decoder=new TextDecoder('utf-8');let buffer='';while(true){const {value,done}=await reader.read();if(done) break;buffer+=decoder.decode(value,{stream:true});let parts=buffer.split('\\n\\n');buffer=parts.pop();for(const chunk of parts){if(!chunk.startsWith('data:')) continue;const data=chunk.replace(/^data:\\s*/,'');if(data==='[DONE]') return;try{const obj=JSON.parse(data);const delta=obj.delta||obj.text||'';aiBox.textContent+=delta;}catch{aiBox.textContent+=data;}}}}
function pill(t){return `<span class='pill'>${t}</span>`}
function renderResult(data){const out=document.querySelector('#output'), p=data.pillars||[], fe=data.five_elements||{}, luck=data.luck_cycles||[];const timeBlock=`<div class='section'><h3>Time Zone & Conversion</h3><div>Detected time zone: <span class='badge mono'>${data.input.timezone}</span></div><div class='mono'>Local birth time: ${data.input.local_iso}</div><div class='mono'>Beijing time (UTC+8): ${data.input.beijing_iso}</div></div>`;const pillarsBlock=`<div class='section'><h3>Four Pillars (Ganzhi)</h3>${p.map(x=>`<div><strong>${x.pillar}:</strong> ${pill(x.gz)} ${pill(`${x.stem_py} (${x.stem_cn}) — ${x.stem_el}`)} ${pill(`${x.branch_py} (${x.branch_cn}) — ${x.branch_el}`)}</div>`).join('')}</div>`;const tg=data.ten_gods||{};const tgBlock=`<div class='section'><h3>Ten Gods (relative to Day Stem)</h3><div>${pill(`Month Stem: ${tg.MonthStem||'-'}`)}</div><div>${pill(`Year Stem: ${tg.YearStem||'-'}`)}</div><div>${pill(`Hour Stem: ${tg.HourStem||'-'}`)}</div><p class='tiny'>These are interpreted roles of other stems relative to your Day Stem in Five-Element relationships.</p></div>`;function bar(label,val,total){const pct=total?Math.round(100*val/total):0;return `<div>${label}: ${val} <div style=\"height:8px;background:#eee;border-radius:6px;overflow:hidden\"><div style=\"width:${pct}%;height:8px;background:#c7a46c\"></div></div></div>`}const totalEl=Object.values(data.five_elements||{}).reduce((a,b)=>a+b,0);const feBlock=`<div class='section'><h3>Five Elements Distribution</h3>${bar('Wood',data.five_elements.Wood||0,totalEl)}${bar('Fire',data.five_elements.Fire||0,totalEl)}${bar('Earth',data.five_elements.Earth||0,totalEl)}${bar('Metal',data.five_elements.Metal||0,totalEl)}${bar('Water',data.five_elements.Water||0,totalEl)}<div class='badge'>Dominant element: <strong>${data.main_element||'-'}</strong></div></div>`;const luckBlock=`<div class='section'><h3>10-Year Luck Cycles (DaYun)</h3>${luck.length?luck.map(x=>`<div>${pill(`Decade #${x.index}`)} starts around age ${x.start_age}, pillar ${x.gz}</div>`).join(''):'<div class=\"tiny\">Unavailable in this build of the library.</div>'}</div>`;out.innerHTML=`<h2>Your Bazi Chart</h2><div><strong>Place:</strong> ${data.input.place||'(map point)'} | <strong>Gender:</strong> ${data.input.gender||'—'}</div>${timeBlock}${pillarsBlock}${tgBlock}${feBlock}${luckBlock}`;}
document.addEventListener('DOMContentLoaded',()=>{initMap();document.querySelector('#searchBtn').addEventListener('click',searchPlace);document.querySelector('#baziForm').addEventListener('submit',onSubmit);});
"""

# ---------- 路由（无文件夹） ----------
@app.route("/")
def root_index(): return Response(INDEX_HTML, mimetype="text/html")

@app.route("/styles.css")
def styles(): return Response(STYLES_CSS, mimetype="text/css")

@app.route("/app.js")
def appjs(): return Response(APP_JS, mimetype="application/javascript")

@app.route("/logo.svg")
def logo(): return Response(LOGO_SVG, mimetype="image/svg+xml")

# ---------- 计算接口（本地排盘） ----------
@app.route("/api/chart", methods=["POST"])
def api_chart():
    try:
        data = request.get_json(force=True)
        date_str = data.get("date"); time_str = data.get("time")
        gender  = data.get("gender") or ""; place = data.get("place") or ""
        lat     = float(data.get("lat"));  lon  = float(data.get("lon"))

        tz_name = tz_name_from_latlon(lat, lon)
        if not tz_name: return jsonify({"ok": False, "error": "Unable to detect time zone from the selected location."}), 400
        if not date_str or not time_str: return jsonify({"ok": False, "error": "Please provide both date and time."}), 400
        try:
            local_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except Exception:
            return jsonify({"ok": False, "error": "Invalid date/time format. Use YYYY-MM-DD and 24-hour HH:MM."}), 400

        conv = to_beijing_from_local(local_dt, tz_name); dt_bj = conv["beijing"]

        if not HAS_LUNAR: return jsonify({"ok": False, "error": "Server missing 'lunar-python'. Ensure dependencies installed."}), 500

        solar = Solar.fromYmdHms(dt_bj.year, dt_bj.month, dt_bj.day, dt_bj.hour, dt_bj.minute, dt_bj.second)
        lunar = solar.toLunar()

        gz_year  = lunar.getYearInGanZhi(); gz_month = lunar.getMonthInGanZhi()
        gz_day   = lunar.getDayInGanZhi();  gz_hour  = lunar.getTimeInGanZhi()

        Y = split_ganzhi(gz_year); M = split_ganzhi(gz_month); D = split_ganzhi(gz_day); H = split_ganzhi(gz_hour)
        pillars = [{"pillar":"Year","gz":gz_year,**Y},{"pillar":"Month","gz":gz_month,**M},{"pillar":"Day","gz":gz_day,**D},{"pillar":"Hour","gz":gz_hour,**H}]
        for p in pillars:
            p["stem_py"]=STEM_TO_PY.get(p["stem_cn"],""); p["branch_py"]=BRANCH_TO_PY.get(p["branch_cn"],"")
            p["stem_el"]=STEM_TO_ELEMENT.get(p["stem_cn"],""); p["branch_el"]=BRANCH_TO_ELEMENT.get(p["branch_cn"],"")

        day_stem=D["stem_cn"]
        ten_gods={"MonthStem":ten_god(day_stem, M["stem_cn"]), "YearStem":ten_god(day_stem, Y["stem_cn"]), "HourStem":ten_god(day_stem, H["stem_cn"])}

        counts = {"Wood":0,"Fire":0,"Earth":0,"Metal":0,"Water":0}
        for p in pillars:
            if p["stem_el"]: counts[p["stem_el"]]+=1
            if p["branch_el"]: counts[p["branch_el"]]+=1
        main_el = max(counts, key=lambda k: counts[k]) if counts else ""

        luck=[]
        try:
            ec=lunar.getEightChar(); is_male=(str(gender).strip().lower() in ["m","male","man"]); yun=ec.getYun(is_male, True)
            for i in range(1,7):
                dy=yun.getDaYun(i)
                luck.append({"index":i,"start_age":dy.getStartAge(),"gz":dy.getGanZhi()})
        except Exception:
            luck=[]

        return jsonify({
            "ok":True,
            "input":{"place":place,"lat":lat,"lon":lon,"timezone":tz_name,"gender":gender,"local_iso":conv["local_iso"],"beijing_iso":conv["beijing_iso"]},
            "pillars":pillars,
            "ten_gods":ten_gods,
            "five_elements":counts,
            "main_element":main_el,
            "lucky":{"colors":FIVE_ELEMENTS_COLORS.get(main_el,[]),"numbers":FIVE_ELEMENTS_NUMBERS.get(main_el,[])},
            "luck_cycles":luck,
            "notes":["Local birth time was converted to Beijing Time (UTC+8) for calculation.","Interpretations are cultural insights, not medical or financial advice."]
        }),200

    except Exception as e:
        return jsonify({"ok": False, "error": f"Server error: {e}"}), 500

# ---------- DeepSeek 流式代理 ----------
def deepseek_stream(messages: List[Dict[str,str]]):
    if not DEEPSEEK_API_KEY:
        yield "data: " + json.dumps({"error":"Missing DEEPSEEK_API_KEY"}) + "\n\n"
        yield "data: [DONE]\n\n"
        return
    url = f"{DEEPSEEK_BASE_URL.rstrip('/')}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": DEEPSEEK_MODEL, "messages": messages, "temperature": 0.7, "stream": True}
    with requests.post(url, headers=headers, json=payload, stream=True, timeout=300) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line: 
                continue
            if line.startswith("data: "):
                data = line[len("data: "):]
                if data == "[DONE]":
                    yield "data: [DONE]\n\n"; break
                try:
                    obj = json.loads(data)
                    delta = obj.get("choices",[{}])[0].get("delta",{}).get("content","")
                    if delta:
                        yield "data: " + json.dumps({"delta": delta}) + "\n\n"
                except Exception:
                    yield "data: " + json.dumps({"text": data}) + "\n\n"

@app.route("/api/interpret_stream", methods=["POST"])
def api_interpret_stream():
    body = request.get_json(force=True)
    chart = body.get("chart")
    if not chart or not chart.get("ok"):
        return jsonify({"ok": False, "error": "Chart payload missing."}), 400

    prompt = {"role":"system","content":"You are a Bazi expert who explains Four Pillars for non-Chinese audiences in simple, friendly English. Avoid fatalistic language; provide practical, culturally sensitive guidance."}
    user = {"role":"user","content":(
        "Write an interpretation with sections: Personality, Career, Relationships, Health, Wealth. "
        "Explain dominant Five Elements and Ten Gods briefly. "
        "If luck cycles exist, summarize the next 3 decades. "
        "Chart JSON:\n"+json.dumps(chart, ensure_ascii=False)
    )}

    gen = deepseek_stream([prompt, user])
    return Response(stream_with_context(gen), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
