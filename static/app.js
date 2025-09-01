/* helpers */
function $(s){return document.querySelector(s);}
function card(title, subtitle, inner, icon){
  return `
  <section class="card slide">
    <div class="card-head">
      <span class="icon">${icon||"✨"}</span>
      <div>
        <h3>${title}</h3>
        ${subtitle?`<p class="muted">${subtitle}</p>`:""}
      </div>
    </div>
    <div class="card-body">${inner}</div>
  </section>`;
}
function mdToHtml(md){ try { return marked.parse(md); } catch { return md.replace(/\n/g,"<br/>"); } }

/* tidy: remove unwanted trailing questions */
function tidyEnding(text){
  if(!text) return text;
  return text
    .replace(/\n+Would you like to explore any area in more depth\?\s*$/i,'')
    .replace(/\n+Would you like to (?:dive|go) deeper.*?\?\s*$/i,'')
    .replace(/\n+Let me know if you want.*?\.\s*$/i,'');
}

/* split by ### into sections */
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

function normalizeTitle(t){
  const x = (t||'').toLowerCase();
  if(/marriage|relationship|partner|compat/i.test(x)) return {key:'marriage', icon:'💞', title:'Marriage & Compatibility'};
  if(/career|work|profession/i.test(x))           return {key:'career',   icon:'💼', title:'Career'};
  if(/health|well[- ]?being|diet|exercise/i.test(x)) return {key:'health',   icon:'🩺', title:'Health'};
  if(/wealth|money|finance|invest/i.test(x))      return {key:'wealth',   icon:'💰', title:'Wealth'};
  if(/remed|feng\s*shui|color|habit|direction/i.test(x)) return {key:'remedy', icon:'🧿', title:'Remedies & Feng Shui'};
  if(/action|checklist|plan|priority/i.test(x))   return {key:'action',   icon:'✅', title:'Prioritized Action Checklist'};
  if(/luck|cycle|forecast|year|month/i.test(x))   return {key:'forecast', icon:'⏳', title:'Luck & Forecast'};
  return {key:'other', icon:'✨', title: t||'Reading'};
}

function renderAICardsFromRaw(raw){
  const container = $('#results');
  container.querySelectorAll('.slide').forEach(x=>x.remove());

  const html = mdToHtml(raw);
  const secs = splitByH3(html);
  if(!secs.length){
    container.insertAdjacentHTML('beforeend', card('Your Reading','', html, '✨'));
    return;
  }
  const normalized = secs.map(s=>({ ...normalizeTitle(s.title), html:s.html }));
  const order = ['marriage','career','health','wealth','remedy','action','forecast','other'];
  normalized.sort((a,b)=>order.indexOf(a.key)-order.indexOf(b.key));
  normalized.forEach(sec=>{
    container.insertAdjacentHTML('beforeend', card(sec.title,'', sec.html, sec.icon));
  });
}

/* global ai state */
window.aiState = { raw:'', done:false, chart:null, name:'' };

/* form submit */
$('#form').addEventListener('submit', async (e)=>{
  e.preventDefault();
  $('#errBox').style.display='none';
  $('#results').innerHTML='';
  $('#aiCard').style.display='block';
  $('#aiBox').innerHTML='';
  $('#aiCtl').style.display='flex';

  const payload = {
    name: $('#name').value.trim(),
    gender: $('#gender').value,
    date: ($('#date').value || '').slice(0,10),
    time: $('#time').value || '00:00',
    city: $('#city').value.trim(),
    country: $('#country').value.trim()
  };

  try{
    const r = await fetch('/api/chart',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d = await r.json();
    if(!d.ok) throw new Error(d.error||'Failed to build chart');
    const c = d.data.chart;

    /* Basic chart cards */
    const head = `
      <div class="grid-2">
        <div><strong>Year</strong><div>${c.year_gz}</div></div>
        <div><strong>Month</strong><div>${c.month_gz}</div></div>
        <div><strong>Day</strong><div>${c.day_gz}</div></div>
        <div><strong>Hour</strong><div>${c.hour_gz}</div></div>
      </div>
      <p class="muted">Converted to Beijing Time: ${d.data.beijing}.</p>
    `;
    $('#results').insertAdjacentHTML('beforeend', card('Four Pillars (BaZi)','Heavenly Stems & Earthly Branches', head, '📜'));

    const five = c.five;
    const bars = Object.keys(five).map(k=>(
      `<div class="bar">
         <div class="bar-name">${k}</div>
         <div class="bar-track"><span style="width:${(five[k]||0)*20}%"></span></div>
       </div>`
    )).join('');
    $('#results').insertAdjacentHTML('beforeend', card('Five Elements Distribution', `Dominant: ${c.dominant}`, bars, '🀄'));

    window.aiState.chart = c;
    window.aiState.name  = payload.name;
    window.aiState.raw   = '';
    window.aiState.done  = false;

    // kick off streaming
    streamAI(window.aiState.chart, window.aiState.name, '');
  }catch(err){
    const box = $('#errBox');
    box.style.display='block';
    box.textContent = `Error: ${err.message||err}`;
  }
});

/* Continue always available */
$('#aiCtl').addEventListener('click', (e)=>{
  if(e.target && e.target.id==='btnCont'){
    streamAI(window.aiState.chart, window.aiState.name, window.aiState.raw||$('#aiBox').textContent||'');
  }
});

/* streaming */
async function streamAI(chart, name, continue_text){
  const aiCard = $('#aiCard');
  const box = $('#aiBox');
  $('#aiCtl').style.display='flex';
  aiCard.style.display='block';

  if(continue_text){
    window.aiState.raw = continue_text;
  }else{
    window.aiState.raw = '';
    box.innerHTML='';
  }

  const body = { chart, name };
  if(continue_text) body.continue_text = continue_text;

  const res = await fetch('/api/interpret_stream',{
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)
  });
  if(!res.ok){ return; }

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
        if(data==='[DONE]'){ sawDone = true; break; }
        try{
          const obj = JSON.parse(data);
          const delta = obj.delta || obj.text || '';
          if(delta){
            window.aiState.raw += delta;
            box.innerHTML = mdToHtml(window.aiState.raw);
          }
        }catch{
          window.aiState.raw += data;
          box.innerHTML = mdToHtml(window.aiState.raw);
        }
      }
    }
    if(sawDone) break;
  }

  window.aiState.raw = tidyEnding(window.aiState.raw);
  window.aiState.done = true;
  renderAICardsFromRaw(window.aiState.raw);
  aiCard.style.display='none';
}

/* export PDF (true PDF) */
$('#btnExport').addEventListener('click', ()=>{
  const opt = {
    margin: 10,
    filename: 'bazi-destiny.pdf',
    image: { type: 'jpeg', quality: 0.98 },
    html2canvas: { scale: 2, useCORS: true },
    jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' }
  };
  const clone = document.createElement('div');
  clone.className = 'pdf-root';
  clone.innerHTML = document.querySelector('main.container').outerHTML;
  document.body.appendChild(clone);
  html2pdf().set(opt).from(clone).save().then(()=>clone.remove());
});
