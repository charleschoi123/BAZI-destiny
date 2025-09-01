const $ = sel => document.querySelector(sel);
const $$ = sel => document.querySelectorAll(sel);

const state = {
  lastChart: null,
  aiText: "",
  theme: localStorage.getItem("bazi-theme") || "classic",
};

function setTheme(v){
  document.documentElement.setAttribute("data-theme", v);
  localStorage.setItem("bazi-theme", v);
  $("#themeSel").value = v;
}

window.addEventListener("DOMContentLoaded", ()=>{
  setTheme(state.theme);
  $("#themeSel").addEventListener("change", e=> setTheme(e.target.value));
  $("#btnGen").addEventListener("click", onGenerate);
  $("#btnCont").addEventListener("click", onContinue);
  $("#btnExport").addEventListener("click", exportPDF);
});

async function onGenerate(){
  $("#btnGen").disabled = true;
  $("#btnCont").disabled = true;
  $("#cards").innerHTML = "";
  state.aiText = "";

  const payload = {
    name: $("#name").value.trim(),
    gender: $("#gender").value,
    date: $("#date").value,
    time: $("#time").value,
    city: $("#city").value.trim(),
    country: $("#country").value.trim()
  };
  if(!payload.date || !payload.time || !payload.city || !payload.country){
    alert("Please fill date, time, city, country.");
    $("#btnGen").disabled = false;
    return;
  }
  const r = await fetch("/api/chart", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(payload)
  });
  if(!r.ok){ alert("Chart error."); $("#btnGen").disabled=false; return;}
  const chart = await r.json();
  state.lastChart = { ...payload, ...chart };

  // meta
  $("#meta").innerHTML = `
    <div>Beijing time: <b>${chart.bj_time}</b>  ·  Source TZ: ${chart.src_tz}</div>
  `;
  // pillars
  const p = chart.pillars;
  $("#pillars").innerHTML = `
    <div class="pill">Year: <b>${p.year.han}</b> (${p.year.stem} ${p.year.branch})</div>
    <div class="pill">Month: <b>${p.month.han}</b> (${p.month.stem} ${p.month.branch})</div>
    <div class="pill">Day: <b>${p.day.han}</b> (${p.day.stem} ${p.day.branch})</div>
    <div class="pill">Hour: <b>${p.hour.han}</b> (${p.hour.stem} ${p.hour.branch})</div>
  `;
  // 5 elements
  const e = chart.elements;
  const max = Math.max(...Object.values(e));
  $("#five").innerHTML = Object.entries(e).map(([k,v])=>`
    <div class="bar"><label>${k}</label>
      <div class="track"><div class="fill" style="width:${(v/max)*100||1}%"></div></div>
      <span>${v}</span>
    </div>
  `).join("") + `<div class="dom">Dominant: <b>${chart.dominant}</b></div>`;

  await streamAI(false);
  $("#btnGen").disabled = false;
  $("#btnCont").disabled = false;
}

async function onContinue(){
  if(!state.lastChart){return}
  $("#btnCont").disabled = true;
  await streamAI(true);
  $("#btnCont").disabled = false;
}

async function streamAI(isContinue){
  const body = {
    ...state.lastChart,
    continue_text: isContinue ? state.aiText : ""
  };
  const r = await fetch("/api/interpret_stream", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(body)
  });
  if(!r.ok){ appendError("Network/server error."); return; }

  const reader = r.body.getReader();
  const dec = new TextDecoder();
  while(true){
    const {value, done} = await reader.read();
    if(done) break;
    const chunk = dec.decode(value);
    if(chunk){ state.aiText += chunk; renderCards(state.aiText); }
  }
}

function appendError(msg){
  const el = document.createElement("div");
  el.className = "card error";
  el.textContent = msg;
  $("#cards").appendChild(el);
}

function splitByH3(md){
  // split markdown by lines starting with ### 
  const parts = [];
  const lines = md.split(/\r?\n/);
  let cur = null;
  for(const ln of lines){
    const m = ln.match(/^###\s+(.+?)\s*$/);
    if(m){
      if(cur) parts.push(cur);
      cur = {title:m[1].trim(), body:[]};
    }else{
      if(!cur){ cur = {title:"Overview", body:[]}; }
      cur.body.push(ln);
    }
  }
  if(cur) parts.push(cur);
  return parts.filter(p=>p.body.join("").trim().length>0);
}

function mdToHtml(md){
  // very tiny md -> html (bold, list, paragraphs, inline code)
  let h = md
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/`(.+?)`/g, "<code>$1</code>");
  // list
  h = h.replace(/(^|\n)\s*-\s+(.*)/g, (m,pfx,item)=> `${pfx}<li>${item}</li>`);
  h = h.replace(/(<li>.*<\/li>)/gs, "<ul>$1</ul>");
  // paragraphs
  h = h.split(/\n{2,}/).map(p=>`<p>${p}</p>`).join("");
  return h;
}

function renderCards(fullText){
  const cards = $("#cards");
  cards.innerHTML = "";
  const parts = splitByH3(fullText);
  parts.forEach(part=>{
    const el = document.createElement("section");
    el.className = "card ai";
    el.innerHTML = `
      <h3>${part.title}</h3>
      <div class="content">${mdToHtml(part.body.join("\n"))}</div>
    `;
    cards.appendChild(el);
  });
}

function exportPDF(){
  const opt = {
    margin:       8,
    filename:     'bazi-destiny.pdf',
    image:        { type: 'jpeg', quality: 0.98 },
    html2canvas:  { scale: 2, useCORS:true },
    jsPDF:        { unit: 'mm', format: 'a4', orientation: 'portrait' }
  };
  const node = document.querySelector("main.page");
  html2pdf().from(node).set(opt).save();
}
