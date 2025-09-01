import os, json, math, queue, threading, time
from datetime import datetime
from typing import Dict, List

from flask import Flask, render_template, request, Response, jsonify
from timezonefinder import TimezoneFinder
import pytz
import requests
from lunar_python import Solar

app = Flask(__name__)

# ---------- Config ----------
AI_BASE_URL  = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
AI_MODEL     = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
AI_API_KEY   = os.environ.get("DEEPSEEK_API_KEY", "")
REQUEST_TIMEOUT = (10, 600)   # (connect, read)

tf = TimezoneFinder()

# ---------- Utils ----------
def local_to_beijing(date_str: str, time_str: str, city: str, country: str):
    """
    Convert local birth time (provided by user as local civil time) to Beijing time (UTC+8)
    using a best-effort timezone guess from city/country text.
    For free-text city names we cannot geocode here; we assume the user time is local time
    and we only normalize to Asia/Shanghai by offset math using named tz if we can guess.
    Minimal, robust path: try tz by country→fallback to UTC (user-entered local).
    """
    # Minimal map for common countries/cities → tz name (can expand over time)
    simple_tz_map = {
        "china": "Asia/Shanghai",
        "hong kong": "Asia/Hong_Kong",
        "taiwan": "Asia/Taipei",
        "singapore": "Asia/Singapore",
        "malaysia": "Asia/Kuala_Lumpur",
        "japan": "Asia/Tokyo",
        "korea": "Asia/Seoul",
        "south korea": "Asia/Seoul",
        "usa": "America/New_York",   # fallback; later user can specify accurate
        "united states": "America/New_York",
        "uk": "Europe/London",
        "united kingdom": "Europe/London",
        "canada": "America/Toronto",
        "australia": "Australia/Sydney",
        "germany": "Europe/Berlin",
        "france": "Europe/Paris",
        "italy": "Europe/Rome",
        "spain": "Europe/Madrid",
        "india": "Asia/Kolkata",
    }

    key = (country or "").strip().lower()
    tzname = simple_tz_map.get(key)
    if not tzname:
        # try by city
        key2 = (city or "").strip().lower()
        tzname = simple_tz_map.get(key2, "UTC")

    # Parse local datetime
    dt_local = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    try:
        tz_local = pytz.timezone(tzname)
    except Exception:
        tz_local = pytz.UTC

    aware_local = tz_local.localize(dt_local, is_dst=None)
    # Convert to Beijing
    beijing_tz = pytz.timezone("Asia/Shanghai")
    beijing_dt = aware_local.astimezone(beijing_tz)
    return beijing_dt

def sexagenary_from_solar(dt_beijing: datetime):
    """
    Use lunar-python to get stems/branches for year/month/day/hour
    """
    solar = Solar.fromYmdHms(dt_beijing.year, dt_beijing.month, dt_beijing.day,
                             dt_beijing.hour, dt_beijing.minute, dt_beijing.second)
    lunar = solar.getLunar()

    y_gz = lunar.getYearInGanZhi()
    m_gz = lunar.getMonthInGanZhi()
    d_gz = lunar.getDayInGanZhi()
    h_gz = lunar.getTimeZhi()  # 地支（时支）
    h_gan = lunar.getTimeGan() # 天干（时干）
    h_gz_full = f"{h_gan}{h_gz}"

    # year branch for compatibility
    y_branch = lunar.getYearZhi()

    # quick five-element counts (very simplified)
    # Map 天干地支 → 五行
    wuxing_map = {
        '甲':'Wood','乙':'Wood','寅':'Wood','卯':'Wood',
        '丙':'Fire','丁':'Fire','巳':'Fire','午':'Fire',
        '戊':'Earth','己':'Earth','辰':'Earth','丑':'Earth','未':'Earth','戌':'Earth',
        '庚':'Metal','辛':'Metal','申':'Metal','酉':'Metal',
        '壬':'Water','癸':'Water','子':'Water','亥':'Water'
    }

    all_parts = ''.join([y_gz, m_gz, d_gz, h_gz_full])
    counts = {'Wood':0,'Fire':0,'Earth':0,'Metal':0,'Water':0}
    for ch in all_parts:
        if ch in wuxing_map:
            counts[wuxing_map[ch]] += 1
    dom = max(counts, key=counts.get)

    ten_gods_note = "Traditional roles explained in simple English."
    return {
        "year_gz": y_gz,
        "month_gz": m_gz,
        "day_gz": d_gz,
        "hour_gz": h_gz_full,
        "year_branch": y_branch,
        "five": counts,
        "dominant": dom,
        "ten_gods_note": ten_gods_note
    }

def build_ai_prompt(chart: Dict, name: str):
    name = name.strip() if name else "the client"
    y, m, d, h = chart['year_gz'], chart['month_gz'], chart['day_gz'], chart['hour_gz']
    dom = chart['dominant']
    five = chart['five']
    yb = chart['year_branch']

    five_str = ", ".join([f"{k}: {v}" for k,v in five.items()])
    sys = (
        "You are a culturally respectful, plain-English BaZi (Four Pillars) consultant for non-Chinese users. "
        "Explain traditional terms with short glossaries. Be kind, direct, and specific. "
        "Structure output with markdown headings (###) for sections like Personality, Career, Relationships, Health, Wealth, "
        "Marriage & Compatibility, Remedies & Feng Shui, Prioritized Action Checklist, and Luck & Forecast (month-by-month for next 12 months, yearly for 5–10 years). "
        "Avoid any mention of AI or model names."
    )
    usr = f"""
Client name: {name}
Year Pillar: {y}
Month Pillar: {m}
Day Pillar: {d}
Hour Pillar: {h}
Year Branch (zodiac): {yb}

Five-element counts: {five_str}. Dominant element: {dom}.

Please provide:
1) Brief personality based on Day Master & element balance.
2) Career recommendations (industries, roles) and do/don’t.
3) Marriage/compatibility: most harmonious zodiacs, challenging matches, and why (simple terms).
4) Health tips mapped to elements (digestive, respiratory etc. where relevant).
5) Wealth approach (investing style, timing, risk level).
6) Remedies & Feng Shui: colors, materials, directions, habits; practical and safe.
7) Action checklist (1–2 years) with bullet priorities.
8) Luck & Forecast: next 12 months with monthly highlights; 1–5 years and 5–10 years by year themes.

Use clear English. Use ### headings for each major section. Do NOT include a closing line like “Would you like to explore…”.
"""
    return [{"role":"system","content":sys},{"role":"user","content":usr}]

# ---------- SSE reader ----------
def _reader_thread(q: "queue.Queue[str]", url: str, headers: Dict, payload: Dict):
    try:
        with requests.post(url, headers=headers, json=payload, stream=True, timeout=REQUEST_TIMEOUT) as r:
            for raw_line in r.iter_lines(decode_unicode=True):
                if raw_line is None:
                    continue
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    data = line[5:].strip()
                    # normalize to OpenAI-style stream objects
                    q.put(f"data: {data}")
                    if data == "[DONE]":
                        break
    except Exception as e:
        q.put(f"::ERR::{e}")
    finally:
        q.put("::DONE::")

def ai_stream(messages: List[Dict[str,str]]):
    if not AI_API_KEY:
        yield "data: " + json.dumps({"delta":"[Server note] Missing DEEPSEEK_API_KEY.\n"}) + "\n\n"
        yield "data: [DONE]\n\n"; return

    url = f"{AI_BASE_URL.rstrip('/')}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": AI_MODEL, "messages": messages, "temperature": 0.8, "stream": True}

    q: "queue.Queue[str]" = queue.Queue(maxsize=1000)
    t = threading.Thread(target=_reader_thread, args=(q, url, headers, payload), daemon=True)
    t.start()

    idle = 0
    while True:
        try:
            item = q.get(timeout=1.0)   # 1s 更积极的心跳窗口
            idle = 0
            if item == "::DONE::":
                yield "data: [DONE]\n\n"
                break
            if item.startswith("::ERR::"):
                yield "data: " + json.dumps({"delta": f"\n\n[connection note] {item[7:]}\n"}) + "\n\n"
                yield "data: [DONE]\n\n"
                break
            if item.startswith("data: "):
                data = item[6:]
                if data == "[DONE]":
                    yield "data: [DONE]\n\n"
                    break
                # 尝试解析 OpenAI/DeepSeek 流对象
                try:
                    obj = json.loads(data)
                    delta = obj.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if delta:
                        yield "data: " + json.dumps({"delta": delta}) + "\n\n"
                except Exception:
                    # 已经是纯文本
                    yield "data: " + json.dumps({"delta": data}) + "\n\n"
        except queue.Empty:
            idle += 1
            # 定时心跳，保持连接
            yield ": ping\n\n"
            if idle % 5 == 0:
                yield "data: " + json.dumps({"delta": ""}) + "\n\n"

# ---------- Routes ----------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/chart", methods=["POST"])
def api_chart():
    data = request.get_json(force=True)
    name    = (data.get("name") or "").strip()
    gender  = (data.get("gender") or "").strip()
    date    = data.get("date")    # YYYY-MM-DD
    time_s  = data.get("time")    # HH:MM
    city    = (data.get("city") or "").strip()
    country = (data.get("country") or "").strip()

    try:
        bj = local_to_beijing(date, time_s, city, country)
        chart = sexagenary_from_solar(bj)
        out = {
            "beijing": bj.strftime("%Y-%m-%d %H:%M"),
            "name": name,
            "gender": gender,
            "city": city,
            "country": country,
            "chart": chart
        }
        return jsonify({"ok": True, "data": out})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.route("/api/interpret_stream", methods=["POST"])
def api_interpret_stream():
    data = request.get_json(force=True)
    name  = data.get("name") or ""
    chart = data.get("chart")
    cont  = data.get("continue_text") or ""

    if cont:
        messages = [{"role":"system","content":"Continue the previous BaZi reading. Keep the same tone and structure. Do not repeat headings already fully covered."},
                    {"role":"user","content":cont}]
    else:
        messages = build_ai_prompt(chart, name)

    def gen():
        for chunk in ai_stream(messages):
            yield chunk
    return Response(gen(), mimetype="text/event-stream")

if __name__ == "__main__":
    # 本地调试用，Render 会用 gunicorn 启动
    app.run(host="0.0.0.0", port=10000, debug=False)
