import os, json, queue, threading, time
from datetime import datetime
from flask import Flask, render_template, request, Response, jsonify
import requests
from timezonefinder import TimezoneFinder
import pytz
from lunar_python import Solar

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

app = Flask(__name__, static_url_path="/static", static_folder="static", template_folder="templates")

# ---------------- Utils: time zone -> Beijing (UTC+8) ----------------
tf = TimezoneFinder()

def to_beijing_dt(city: str, country: str, date_str: str, time_str: str):
    """
    city, country: plain strings typed by user
    date_str: 'YYYY/MM/DD' | 'YYYY-MM-DD'
    time_str: 'HH:MM' 24h
    return: aware datetime in BJ (UTC+8) + sanitized original tz name
    """
    # naive fallback: use country/city guess with pytz common names
    # We try: (city, country) -> tz by TimezoneFinder with simple map (NOT using coordinates)
    # Since user只填城市/国家，这里采用“国家级兜底 + 北京换算”
    try:
        dt_local = datetime.strptime(date_str.replace("年","/").replace("月","/").replace("日","/").replace("-", "/"), "%Y/%m/%d")
    except:
        dt_local = datetime.utcnow()
    try:
        hh, mm = time_str.split(":")
        dt_local = dt_local.replace(hour=int(hh), minute=int(mm))
    except:
        pass

    # 兜底：用国家常见时区
    country_up = (country or "").strip().upper()
    common = {
        "CHINA":"Asia/Shanghai", "CN":"Asia/Shanghai",
        "USA":"America/New_York", "US":"America/New_York",
        "UNITED STATES":"America/New_York",
        "UK":"Europe/London", "UNITED KINGDOM":"Europe/London",
        "HONG KONG":"Asia/Hong_Kong", "HK":"Asia/Hong_Kong",
        "TAIWAN":"Asia/Taipei", "TW":"Asia/Taipei",
        "SINGAPORE":"Asia/Singapore", "SG":"Asia/Singapore",
        "MALAYSIA":"Asia/Kuala_Lumpur", "MY":"Asia/Kuala_Lumpur",
        "JAPAN":"Asia/Tokyo", "JP":"Asia/Tokyo",
        "KOREA":"Asia/Seoul", "SOUTH KOREA":"Asia/Seoul", "KR":"Asia/Seoul",
        "AUSTRALIA":"Australia/Sydney", "AU":"Australia/Sydney",
        "CANADA":"America/Toronto", "CA":"America/Toronto",
        "GERMANY":"Europe/Berlin", "DE":"Europe/Berlin",
        "FRANCE":"Europe/Paris", "FR":"Europe/Paris",
        "INDIA":"Asia/Kolkata", "IN":"Asia/Kolkata"
    }
    tzname = common.get(country_up, "UTC")
    try:
        tz = pytz.timezone(tzname)
        local_aware = tz.localize(dt_local)
    except Exception:
        local_aware = pytz.UTC.localize(dt_local)

    bj = pytz.timezone("Asia/Shanghai")
    bj_dt = local_aware.astimezone(bj)
    return bj_dt, tzname

# ---------------- BaZi core (year/month/day/hour pillars & helpers) ----------------

HEAVENLY_STEMS = ["Jia", "Yi", "Bing", "Ding", "Wu", "Ji", "Geng", "Xin", "Ren", "Gui"]
EARTHLY_BRANCHES = ["Zi", "Chou", "Yin", "Mao", "Chen", "Si", "Wu", "Wei", "Shen", "You", "Xu", "Hai"]
FIVE_ELEMENTS = ["Wood", "Fire", "Earth", "Metal", "Water"]

def solar_to_bazi(y, m, d, hh):
    """Using lunar-python to get GanZhi; hour pillar computed by standard rule."""
    solar = Solar.fromYmdHms(y, m, d, hh, 0, 0)
    lunar = solar.getLunar()
    y_gz = lunar.getYearInGanZhi()
    m_gz = lunar.getMonthInGanZhi()
    d_gz = lunar.getDayInGanZhi()
    h_gz = lunar.getTimeInGanZhi()
    # Convert to english-friendly with both Han chars and pinyin-ish
    def split_gz(gz):
        # gz like 甲子
        if len(gz) == 2:
            return gz[0], gz[1]
        return gz[0], gz[-1]
    def map_stem(ch):
        idx = "甲乙丙丁戊己庚辛壬癸".find(ch)
        return HEAVENLY_STEMS[idx] if idx>=0 else ch
    def map_branch(ch):
        idx = "子丑寅卯辰巳午未申酉戌亥".find(ch)
        return EARTHLY_BRANCHES[idx] if idx>=0 else ch

    def to_obj(gz):
        s, b = split_gz(gz)
        return {"stem": map_stem(s), "branch": map_branch(b), "han": gz}

    return {
        "year": to_obj(y_gz),
        "month": to_obj(m_gz),
        "day": to_obj(d_gz),
        "hour": to_obj(h_gz)
    }

def element_from_stem(stem_en):
    # very common mapping
    table = {
        "Jia":"Wood", "Yi":"Wood",
        "Bing":"Fire", "Ding":"Fire",
        "Wu":"Earth", "Ji":"Earth",
        "Geng":"Metal", "Xin":"Metal",
        "Ren":"Water", "Gui":"Water"
    }
    return table.get(stem_en, "Earth")

def five_element_distribution(pillars):
    counts = {e:0 for e in FIVE_ELEMENTS}
    for p in ["year","month","day","hour"]:
        e = element_from_stem(pillars[p]["stem"])
        counts[e]+=1
    dom = max(counts, key=lambda k:counts[k])
    return counts, dom

# ---------------- Prompt builder ----------------

SYSTEM_PROMPT = (
"You're an expert Bazi (Four Pillars of Destiny) consultant for non-Chinese users. "
"Explain in friendly, plain English, but keep accurate Chinese terms with pinyin/hanzi in parentheses when useful. "
"Be specific and practical. Avoid mystical exaggeration. Keep a respectful tone.\n"
)

def build_user_prompt(name, gender, bj_dt, city, country, pillars, elem_counts, dominant):
    # build a highly structured prompt to force detailed sections
    lines = []
    lines.append(f"Client: {name or 'Guest'}; Gender: {gender or 'unspecified'}; "
                 f"Birth converted to Beijing time (UTC+8): {bj_dt.strftime('%Y-%m-%d %H:%M')} "
                 f"(from {city}, {country}).")
    lines.append(f"Pillars (GanZhi): "
                 f"Year {pillars['year']['han']} ({pillars['year']['stem']} {pillars['year']['branch']}), "
                 f"Month {pillars['month']['han']}, Day {pillars['day']['han']}, Hour {pillars['hour']['han']}.")
    lines.append(f"Five Elements count: {elem_counts}. Dominant element: {dominant}.")
    lines.append("\nPlease produce a comprehensive, *actionable* reading with H3 headings, exactly in this order:")
    lines.append("### Marriage & Compatibility")
    lines.append("- Give 3-5 **most harmonious** zodiac partners (by Year Branch) and short reasons.")
    lines.append("- Give 2-3 **challenging** matches and why; include practical tips to handle them.")
    lines.append("- One-sentence dynamics summary.")
    lines.append("### Career")
    lines.append("- 4–8 concrete career directions, industries, or role styles that fit the chart.")
    lines.append("- How to lead/collaborate; decision style; growth strategy.")
    lines.append("### Health")
    lines.append("- Organs/systems to watch based on elements; typical symptoms; daily habits.")
    lines.append("- Seasonal focus (Spring/Wood, Summer/Fire, etc.).")
    lines.append("### Wealth")
    lines.append("- Short-term vs long-term money strategy; investing style; risk notes.")
    lines.append("- Side-hustle or asset suggestions tied to elements.")
    lines.append("### Luck Cycles (DaYun & Next 1–10 Years)")
    lines.append("- Describe overall 10-year trend patterns (even if simplified).")
    lines.append("- Give **Year-by-year** highlights for the next 5 years; where possible, add **month windows** (approx).")
    lines.append("### Remedies & Feng Shui")
    lines.append("- Colors, materials, directions, numbers, and simple daily habits to balance the elements.")
    lines.append("- Specific desk/bed orientation or home layout tweaks.")
    lines.append("### Action Plan")
    lines.append("- A numbered checklist (6–10 items) the client can start this month.")
    lines.append("- Close with one encouraging line (no promises, no fortune-telling language).")
    return "\n".join(lines)

# ---------------- DeepSeek Streaming ----------------

def deepseek_stream(messages, temperature=0.7, max_tokens=1200):
    """
    Stream text via DeepSeek compatibility (OpenAI-like).
    Fallback to chunk yielding for robustness.
    """
    if not DEEPSEEK_API_KEY:
        yield "ERROR: Missing DEEPSEEK_API_KEY.\n"
        return
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True
    }

    with requests.post(url, headers=headers, data=json.dumps(payload), stream=True, timeout=(10, 600)) as r:
        r.raise_for_status()
        for raw in r.iter_lines(decode_unicode=True):
            # keep alive
            if not raw:
                yield ""
                continue
            if raw.startswith("data: "):
                data = raw[6:]
            else:
                data = raw
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
                delta = obj["choices"][0]["delta"].get("content", "")
                if delta:
                    yield delta
            except Exception:
                # try best effort
                pass

# ---------------- Routes ----------------

@app.route("/")
def index():
    return render_template("index.html")

@app.post("/api/chart")
def api_chart():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    gender = data.get("gender") or ""
    date = data.get("date") or ""
    time_ = data.get("time") or ""
    city = (data.get("city") or "").strip()
    country = (data.get("country") or "").strip()

    bj_dt, tzname = to_beijing_dt(city, country, date, time_)
    pillars = solar_to_bazi(bj_dt.year, bj_dt.month, bj_dt.day, bj_dt.hour)
    elem_counts, dominant = five_element_distribution(pillars)

    # simple five-element bar for front-end
    resp = {
        "bj_time": bj_dt.strftime("%Y-%m-%d %H:%M"),
        "src_tz": tzname,
        "pillars": pillars,
        "elements": elem_counts,
        "dominant": dominant
    }
    return jsonify(resp)

@app.post("/api/interpret_stream")
def api_interpret_stream():
    """
    SSE-like: stream purely text; front端累积渲染，并按 H3 自动拆卡。
    支持 continue_text：补写不会清空旧内容。
    """
    data = request.get_json(force=True)
    name = data.get("name")
    gender = data.get("gender")
    date = data.get("date")
    time_ = data.get("time")
    city = data.get("city")
    country = data.get("country")
    bj = data.get("bj_time")
    pillars = data.get("pillars")
    elements = data.get("elements")
    dominant = data.get("dominant")
    continue_text = data.get("continue_text") or ""

    try:
        bj_dt = datetime.strptime(bj, "%Y-%m-%d %H:%M")
    except:
        bj_dt = datetime.utcnow()

    sys = {"role":"system","content":SYSTEM_PROMPT}
    user = {"role":"user","content":build_user_prompt(name, gender, bj_dt, city, country, pillars, elements, dominant)}
    msgs = [sys, user]
    if continue_text:
        msgs.append({"role":"user","content":f"Continue expanding the reading from here, without repeating earlier text:\n{continue_text}"})

    def generate():
        # keep-alive ping避免 Gunicorn 超时
        last = time.time()
        for chunk in deepseek_stream(msgs):
            now = time.time()
            if chunk:
                yield chunk
                last = now
            elif now - last > 2:
                # 保持连接
                yield " "
                last = now
        # 结束换行
        yield "\n"

    return Response(generate(), mimetype="text/plain; charset=utf-8")

# health
@app.get("/healthz")
def healthz():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
