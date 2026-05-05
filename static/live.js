const root = document.querySelector('.admin-shell');
const publicId = root.dataset.publicId;
const role = root.dataset.role;
const fixedCourtId = root.dataset.courtId ? Number(root.dataset.courtId) : null;
let state = null;
let clickCount = 0;
let clickTimer = null;
let lastAnnouncement = '';
let voicesEnabled = true;
let lastServerByMatch = {};
let startServerAnnouncedByMatch = {};
let socket = null;
let manualDrafts = {};
let manualEditOpen = null;
let warnedByCourt = {};

async function post(url, body=null){
  const opts = {method:'POST'};
  if(body){opts.headers={'Content-Type':'application/json'};opts.body=JSON.stringify(body)}
  const res = await fetch(url, opts);
  state = await res.json();
  render();
  return state;
}
async function load(){
  const res = await fetch(`/api/t/${publicId}/state`);
  state = await res.json();
  render();
}
function connectSocket(){
  if(typeof io === 'undefined') return false;
  socket = io({transports:['websocket','polling']});
  socket.on('connect',()=>{socket.emit('join_tournament',{public_id:publicId});});
  socket.on('state',(newState)=>{state=newState; render();});
  socket.on('connect_error',()=>{console.warn('Live-Sync verbindet neu…')});
  return true;
}
function requestLiveState(){
  if(socket && socket.connected) socket.emit('request_state',{public_id:publicId});
  else load();
}
function fmt(sec){sec=Math.max(0,sec||0);const m=Math.floor(sec/60);const s=sec%60;return `${m}:${String(s).padStart(2,'0')}`}
function fmtSpeak(sec){
  sec=Math.max(0,sec||0);
  const m=Math.floor(sec/60);
  const s=sec%60;
  const parts=[];
  if(m===1) parts.push('1 Minute');
  else if(m>1) parts.push(`${m} Minuten`);
  if(s===1) parts.push('1 Sekunde');
  else if(s>1 || parts.length===0) parts.push(`${s} Sekunden`);
  return parts.join(' und ');
}

function timerContext(){
  if(!state) return null;
  if(fixedCourtId){
    return state.courts.find(c=>c.id===fixedCourtId) || null;
  }
  return null;
}

function esc(s){return String(s??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}
function say(text){
  if(role !== 'controller') return;
  if(!voicesEnabled || !('speechSynthesis' in window)) return;
  if(text===lastAnnouncement) return;
  lastAnnouncement=text;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang='de-DE'; u.rate=1.05;
  speechSynthesis.speak(u);
  toast(text);
}
function toast(text){const old=document.querySelector('.announcement'); if(old) old.remove(); const el=document.createElement('div'); el.className='announcement'; el.textContent=text; document.body.appendChild(el); setTimeout(()=>el.remove(),2600)}
function scoreForSpeech(points){
  let text = String(points ?? '').replace(/Tiebreak/i, 'Tiebreak ');
  text = text.replace(/\s*:\s*/g, ' zu ');
  text = text.replace(/[.;]/g, ' ');
  text = text.replace(/\s+/g, ' ').trim();
  return text;
}
function shortScoreText(m, includeServer=false){
  if(m.status==='finished') return `${m.winner_name}, ${m.score[0]} zu ${m.score[1]}`;
  const score = scoreForSpeech(m.points);
  if(includeServer && m.server_name) return `${score}, Aufschlag ${m.server_name}`;
  return score;
}
function serverText(m){
  return m?.server_name ? `${scoreForSpeech(m.points)}, Aufschlag ${m.server_name}` : shortScoreText(m);
}
function syncServerMemory(){
  if(!state?.matches) return;
  state.matches.forEach(m=>{
    if(lastServerByMatch[m.id] === undefined) lastServerByMatch[m.id] = m.server_name || '';
  });
}
function announceStartServers(){
  if(!state?.courts) return;
  activeCourtItems().forEach(c=>{
    const m = c.match;
    if(!m || m.status==='finished') return;
    if(!startServerAnnouncedByMatch[m.id]){
      say(serverText(m));
      startServerAnnouncedByMatch[m.id] = true;
      lastServerByMatch[m.id] = m.server_name || '';
    }
  });
}
function backFromController(){
  const fallback = `/t/${publicId}`;
  try{
    const ref = document.referrer ? new URL(document.referrer) : null;
    if(ref && ref.origin === location.origin && ref.href !== location.href){ location.href = ref.pathname + ref.search + ref.hash; return; }
    if(history.length>1){ history.back(); return; }
  }catch(e){}
  location.href = fallback;
}
function maybeWarn(){
  if(role !== 'controller') return;
  const c = timerContext();
  if(!c) return;
  const key = `${state.active_round}:${c.id || 'global'}`;
  if(c.warning_due && warnedByCourt[key] !== true){
    warnedByCourt[key] = true;
    say(`Noch ${fmtSpeak(c.remaining_seconds)}`);
  }
  if(!c.warning_due && !c.timer_running){
    Object.keys(warnedByCourt).forEach(k=>{ if(k.endsWith(`:${c.id || 'global'}`)) delete warnedByCourt[k]; });
  }
}
function render(){
  if(!state) return;
  document.documentElement.dataset.theme = 'light';
  document.querySelectorAll('[data-round]').forEach(e=>e.textContent=state.active_round);
  const tc = timerContext();
  document.querySelectorAll('[data-timer]').forEach(e=>{ if(tc) e.textContent=tc.is_final?'Finale':fmt(tc.remaining_seconds); });
  document.querySelectorAll('[data-action="start"]').forEach(e=>e.textContent=tc?.paused?'Weiter':'Start');
  document.querySelectorAll('[data-action="stop"]').forEach(e=>e.textContent='Pause');
  document.querySelectorAll('[data-bracket-link]').forEach(e=>e.href=`/bracket/${publicId}`);
  maybeWarn();
  syncServerMemory();
  renderControllerLinks(); renderCourts(); renderTable(); renderMatches();
  if(role==='controller'){ const c=timerContext(); if(c && (c.timer_running || c.is_final)) announceStartServers(); }
}
function activeCourtItems(){
  let courts = state.courts;
  if(role==='controller' && fixedCourtId) courts = courts.filter(c=>c.id===fixedCourtId);
  return courts;
}
function renderControllerLinks(){
  const box=document.querySelector('[data-controller-links]'); if(!box||!state) return;
  const returnTo = encodeURIComponent(location.pathname + location.search);
  box.innerHTML=state.courts.map(c=>`<a href="/controller/${c.controller_token}?return_to=${returnTo}">${esc(c.name)} Controller öffnen</a>`).join('');
}
function renderCourts(){
  const wrap=document.querySelector('[data-courts]'); if(!wrap||!state) return;
  wrap.innerHTML=activeCourtItems().map(c=>{
    const m=c.match;
    if(!m) return `<section class="court-card"><h2>${esc(c.name)}</h2><p class="muted">Kein Match in dieser Runde.</p></section>`;
    const courtRunning = c.timer_running || false;
    const courtRemaining = c.remaining_seconds ?? state.remaining_seconds;
    const controls = role!=='public' && (m.is_final || courtRunning) && !(courtRemaining<=0 && !m.is_final);
    return `<section class="court-card ${m.is_final?'final':''}">
      <h2>${esc(c.name)} <span class="phase">${m.is_final?'Finale':esc(m.phase)}</span></h2>
      <div class="court-timer">${m.is_final ? 'Finale ohne Zeitlimit' : fmt(c.remaining_seconds ?? 0)} · ${c.timer_running ? 'läuft' : (c.paused ? 'pausiert' : 'bereit')}</div>
      <label>Zählweise<select ${!controls?'disabled':''} onchange="setMode(${m.id},this.value)">${Object.entries(state.scoring_modes).map(([k,v])=>`<option value="${k}" ${m.scoring_mode===k?'selected':''}>${esc(v)}</option>`).join('')}</select></label>
      <div class="big-score">${esc(m.points)}</div>
      <div class="meta"><span>Games ${m.games[0]} : ${m.games[1]}</span><span>Sätze ${m.sets[0]} : ${m.sets[1]}</span></div>
      <div class="serve">Aufschlag: <strong>${esc(m.server_name || '')}</strong>${m.in_tiebreak ? ' · Tiebreak' : ''}<br><small class="muted">Reguläre Padel-Reihenfolge: ein Spieler serviert ein komplettes Game.</small></div>
      ${m.status==='finished'?`<div class="coach"><strong>Gewinner: ${esc(m.winner_name)}</strong><br>Wertung: ${m.score[0]} : ${m.score[1]}</div>`:''}
      <div class="teams"><div class="team"><h3>${esc(m.team_a.name)}</h3><p>${m.team_a.players.map(esc).join('<br>')}</p>${controls?`<button class="primary" onclick="point(${m.id},0)">Punkt links</button>`:''}</div><div class="team"><h3>${esc(m.team_b.name)}</h3><p>${m.team_b.players.map(esc).join('<br>')}</p>${controls?`<button class="primary" onclick="point(${m.id},1)">Punkt rechts</button>`:''}</div></div>
      ${controls?`<div class="actions" style="margin-top:12px"><button onclick="undo(${m.id})">Undo</button>${m.is_final?`<button onclick="finishMatch(${m.id})">Finale beenden</button>`:''}${role==='admin'?`<button class="danger" onclick="resetMatch(${m.id})">Reset</button>`:''}</div><div class="remote-hint">Volume-Up: 1x links · 2x rechts · 3x Undo. Ansage: im Game nur Punkte, nach Game der Gamestand, nach Satz der Satzstand.</div>`:`${role!=='public'?`<div class="remote-hint">Zählen ist erst möglich, wenn die Runde läuft. Nach Zeitablauf werden keine Punkte mehr angenommen.</div>`:''}`}
    </section>`
  }).join('');
}
function renderTable(){
  const box=document.querySelector('[data-table]'); if(!box||!state) return;
  box.innerHTML=`<table><thead><tr><th>#</th><th>Team</th><th>Sp</th><th>Pkt</th><th>S/U/N</th><th>Games</th><th>Diff</th><th>Coach</th></tr></thead><tbody>${state.table.map((r,i)=>`<tr class="${i===0?'leader':''}"><td>${i+1}</td><td><a href="/player/${r.id}">${esc(r.name)}</a><br><small>${r.player_links ? r.player_links.map(p=>`<a href="/spieler/${p.id}">${esc(p.name)}</a>`).join(' / ') : r.players.map(esc).join(' / ')}</small></td><td>${r.played}</td><td><strong>${r.points}</strong></td><td>${r.wins}/${r.draws}/${r.losses}</td><td>${r.for}:${r.against}</td><td>${r.diff}</td><td>${r.winrate}%</td></tr>`).join('')}</tbody></table>`;
}
function renderMatches(){
  const box=document.querySelector('[data-matches]'); if(!box||!state) return;

  // Wichtig: Die Live-Aktualisierung läuft jede Sekunde. Wenn gerade ein manueller
  // Endstand eingegeben wird, darf das Formular nicht neu gerendert werden,
  // sonst verschwinden Fokus und Eingabe nach ca. 1 Sekunde.
  if(role==='admin' && manualEditOpen !== null && box.querySelector('.manual-editor.open')) return;

  box.innerHTML=`<table><thead><tr><th>Runde</th><th>Court</th><th>Teams</th><th>Phase</th><th>Status</th><th>Wertung</th>${role==='admin'?'<th>Manuell</th>':''}</tr></thead><tbody>${state.matches.map(m=>{
    const draft = manualDrafts[m.id] || {score1:m.score[0], score2:m.score[1]};
    return `<tr><td>${m.round_no}</td><td>${esc(m.court)}</td><td>${esc(m.team_a.name)} - ${esc(m.team_b.name)}</td><td>${esc(m.phase)}</td><td>${esc(m.status)}</td><td>${m.score[0]}:${m.score[1]}</td>${role==='admin'?`<td><div class="manual-editor ${manualEditOpen===m.id?'open':''}" data-manual-editor="${m.id}">${manualEditOpen===m.id?`<form class="inline-result" onsubmit="manualScore(event,${m.id})"><input name="s1" type="number" min="0" value="${esc(draft.score1)}" oninput="rememberManualDraft(${m.id}, this.form)"><span>:</span><input name="s2" type="number" min="0" value="${esc(draft.score2)}" oninput="rememberManualDraft(${m.id}, this.form)"><button>Speichern</button><button type="button" onclick="cancelManualScore(${m.id})">Abbrechen</button></form>`:`<button type="button" onclick="openManualScore(${m.id})">Endstand eintragen</button>`}</div></td>`:''}</tr>`;
  }).join('')}</tbody></table>`;
}
function openManualScore(id){
  const m = state.matches.find(x=>x.id===id);
  if(!m) return;
  manualEditOpen = id;
  manualDrafts[id] = manualDrafts[id] || {score1:m.score[0], score2:m.score[1]};
  renderMatches();
  const form = document.querySelector(`[data-manual-editor="${id}"] form`);
  form?.querySelector('input[name="s1"]')?.focus();
}
function rememberManualDraft(id, form){
  manualDrafts[id] = {score1: form.s1.value, score2: form.s2.value};
}
function cancelManualScore(id){
  delete manualDrafts[id];
  if(manualEditOpen===id) manualEditOpen = null;
  renderMatches();
}
function point(id,team){
  const before = state.matches.find(m=>m.id===id);
  const beforeServer = before?.server_name || '';
  post(`/api/t/${publicId}/match/${id}/point/${team}`).then(()=>{
    const after = state.matches.find(m=>m.id===id);
    if(!after) return;
    const afterServer = after.server_name || '';
    const serverChanged = beforeServer !== afterServer;
    lastServerByMatch[id] = afterServer;
    if(after.sets[0]!==before.sets[0] || after.sets[1]!==before.sets[1]) say(`Sätze ${after.sets[0]} zu ${after.sets[1]}${serverChanged && afterServer ? `, Aufschlag ${afterServer}` : ''}`);
    else if(after.games[0]!==before.games[0] || after.games[1]!==before.games[1]) say(`Games ${after.games[0]} zu ${after.games[1]}${serverChanged && afterServer ? `, Aufschlag ${afterServer}` : ''}`);
    else say(shortScoreText(after, false));
  });
}
function undo(id){post(`/api/t/${publicId}/match/${id}/undo`).then(()=>{const m=state.matches.find(x=>x.id===id); if(m) say(shortScoreText(m));})}
function resetMatch(id){post(`/api/t/${publicId}/match/${id}/reset`)}
function finishMatch(id){post(`/api/t/${publicId}/match/${id}/finish`).then(()=>{const m=state.matches.find(x=>x.id===id); if(m) say(shortScoreText(m));})}
function setMode(id,mode){post(`/api/t/${publicId}/match/${id}/mode`,{mode})}
async function manualScore(ev,id){
  ev.preventDefault();
  const form = ev.currentTarget;
  rememberManualDraft(id, form);
  const fd=new FormData(form);
  const score1=Number(fd.get('s1'));
  const score2=Number(fd.get('s2'));
  if(Number.isNaN(score1)||Number.isNaN(score2)||score1<0||score2<0){toast('Bitte gültigen Spielstand eingeben.'); return;}

  // Wichtig: nicht post() verwenden, weil post() sofort rendert, während
  // manualEditOpen noch gesetzt ist. Dadurch blieb die Tabelle nach dem
  // ersten Speichern im offenen Formularzustand hängen. Erst speichern,
  // dann Formularstatus zurücksetzen, dann neu rendern.
  const res = await fetch(`/api/t/${publicId}/match/${id}/manual-score`,{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({score1,score2})
  });
  state = await res.json();
  delete manualDrafts[id];
  if(manualEditOpen===id) manualEditOpen=null;
  const box=document.querySelector('[data-matches]');
  if(box) box.innerHTML='';
  render();
  toast(`Endstand ${score1}:${score2} gespeichert.`);
}
function currentControllerMatch(){
  if(!state) return null;
  const c = activeCourtItems()[0]; return c?.match || null;
}
function handleVolume(){
  if(role!=='controller') return;
  clickCount++; clearTimeout(clickTimer);
  clickTimer=setTimeout(()=>{const m=currentControllerMatch(); const c=timerContext(); if(!m || (!m.is_final && (!c?.timer_running || (c?.remaining_seconds??0)<=0))){clickCount=0;return} if(clickCount===1) point(m.id,0); else if(clickCount===2) point(m.id,1); else undo(m.id); clickCount=0;},350);
}
// 🔥 FIX: robuster Volume Button Listener
document.addEventListener('keydown', (e) => {
  console.log("KEYDOWN:", e.key, e.code);

  if (
    e.key === "AudioVolumeUp" ||
    e.code === "AudioVolumeUp" ||
    e.key === "VolumeUp" ||
    e.code === "VolumeUp" ||
    e.key === "Unidentified"
  ) {
    e.preventDefault();
	
	if (e.repeat) return;

    handleVolume();
  }
});

//document.addEventListener('keyup', (e) => {
//  if (
//    (e.key && e.key.includes("Volume")) ||
//    (e.code && e.code.includes("Volume"))
//  ) {
//    handleVolume();
//  }
//});
document.addEventListener('click',()=>{voicesEnabled=true},{once:true});
document.querySelector('[data-action="start"]')?.addEventListener('click',()=>{
  const url = fixedCourtId ? `/api/t/${publicId}/court/${fixedCourtId}/start` : `/api/t/${publicId}/start`;
  post(url);
});
document.querySelector('[data-action="stop"]')?.addEventListener('click',()=>{
  const url = fixedCourtId ? `/api/t/${publicId}/court/${fixedCourtId}/stop` : `/api/t/${publicId}/stop`;
  post(url);
});
document.querySelector('[data-action="endRound"]')?.addEventListener('click',()=>post(`/api/t/${publicId}/end-round`).then(()=>{startServerAnnouncedByMatch={}; warnedByCourt={}; toast('Runde gespeichert. Nächste Runde bereit.');}));
document.querySelector('[data-action="nextRound"]')?.addEventListener('click',()=>post(`/api/t/${publicId}/next-round`).then(()=>{startServerAnnouncedByMatch={}; warnedByCourt={};}));
connectSocket(); load(); setInterval(requestLiveState,1000);
