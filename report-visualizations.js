(function(){
'use strict';
const brl=n=>new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL',maximumFractionDigits:0}).format(Number(n)||0);
const dateLabel=key=>new Intl.DateTimeFormat('pt-BR',{day:'2-digit',month:'short',timeZone:'UTC'}).format(new Date(key+'T12:00:00Z')).replace('.','');
const monthLabel=key=>new Intl.DateTimeFormat('pt-BR',{month:'short',year:'2-digit',timeZone:'UTC'}).format(new Date(key+'-15T12:00:00Z')).replace('.','');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function ensure(){
 if(document.getElementById('visual-intelligence'))return document.getElementById('visual-intelligence');
 const section=document.createElement('section');section.id='visual-intelligence';section.className='visual-intelligence';
 section.innerHTML='<div class="visual-heading"><div><div class="eyebrow">LEITURA VISUAL · MARCOPÃO</div><h2>O negócio em movimento</h2><p>Transformamos os relatórios capturados em tendências fáceis de entender.</p></div><span class="visual-live">DADOS DO SUPABASE</span></div><div class="visual-kpis" id="visualKpis"></div><div class="visual-grid"><article class="visual-card"><div class="visual-card-head"><div><h3>Ritmo de vendas</h3><p id="dailyVisualCaption">Últimos 7 dias · faturamento por dia</p></div><span class="visual-tag">DIÁRIO</span></div><div id="dailyVisualChart" class="visual-chart"></div><div id="dailyVisualInsight" class="visual-insight"></div></article><article class="visual-card"><div class="visual-card-head"><div><h3>Evolução do faturamento</h3><p id="monthlyVisualCaption">Histórico mensal capturado</p></div><span class="visual-tag">HISTÓRICO</span></div><div id="monthlyVisualChart" class="visual-chart monthly-chart"></div><div id="monthlyVisualInsight" class="visual-insight"></div></article></div><p class="visual-foot" id="visualFoot">Meses e dias sem relatório ficam em aberto. Capturas incompletas não são convertidas em venda zero.</p>';
 const anchor=document.getElementById('businessOverview')||document.getElementById('yearOverview');
 if(anchor&&anchor.parentNode)anchor.parentNode.insertBefore(section,anchor);
 else document.querySelector('.shell')?.appendChild(section);
 return section;
}
function dailyChart(){
 const host=document.getElementById('dailyVisualChart'),insight=document.getElementById('dailyVisualInsight'),caption=document.getElementById('dailyVisualCaption');
 const all=Array.isArray(window.marcopaoDailyHistory)?window.marcopaoDailyHistory:[];
 const windowData=window.marcopaoDailyWindow||{};
 const start=windowData.start,end=windowData.end;
 if(!start||!end){host.innerHTML='<div class="visual-empty">Aguardando relatórios diários.</div>';insight.textContent='A série diária aparecerá após a sincronização.';return;}
 const startDate=new Date(start+'T12:00:00Z'),days=[];
 const byDate=new Map(all.filter(r=>r.date>=start&&r.date<=end).map(r=>[r.date,r]));
 for(let i=0;i<7;i++){const d=new Date(startDate);d.setUTCDate(d.getUTCDate()+i);const key=d.toISOString().slice(0,10);days.push({date:key,row:byDate.get(key)||null});}
 const values=days.filter(d=>d.row&&Number.isFinite(d.row.metrics.total)).map(d=>d.row.metrics.total),max=Math.max(1,...values);
 const avg=values.length?values.reduce((a,b)=>a+b,0)/values.length:0;
 host.innerHTML='<div class="daily-bars" role="img" aria-label="Gráfico de faturamento diário dos últimos sete dias">'+days.map(d=>{
  const v=d.row&&Number.isFinite(d.row.metrics.total)?d.row.metrics.total:null;
  const h=v===null?0:Math.max(3,v/max*100);
  return '<div class="daily-bar-column"><div class="daily-bar-value">'+(v===null?'Sem dado':brl(v))+'</div><div class="daily-bar-stage"><div class="daily-bar '+(v===null?'is-missing':d.row.warnings?.length?'has-warning':'')+'" style="height:'+h+'%" title="'+esc(d.date)+' · '+(v===null?'sem relatório':brl(v))+'"></div></div><div class="daily-bar-label">'+dateLabel(d.date)+'</div></div>';
 }).join('')+'</div>';
 caption.textContent=dateLabel(start)+' a '+dateLabel(end)+' · '+values.length+' dias com valores disponíveis';
 const best=days.filter(d=>d.row).sort((a,b)=>b.row.metrics.total-a.row.metrics.total)[0];
 const missing=7-values.length,review=days.filter(d=>d.row&&d.row.warnings?.length).length;
 insight.innerHTML=best?'<strong>'+dateLabel(best.date)+' foi o melhor dia</strong><span>'+brl(best.row.metrics.total)+' em vendas. Média dos dias com relatório: '+brl(avg)+'. '+(missing?missing+' dia(s) sem captura. ':'')+(review?review+' captura(s) com ressalvas para conferência.':'Capturas disponíveis sem ressalvas registradas.')+'</span>':'<strong>Sem série diária disponível</strong><span>Não há valores suficientes para montar a tendência.</span>';
}
function monthlyChart(){
 const host=document.getElementById('monthlyVisualChart'),insight=document.getElementById('monthlyVisualInsight'),caption=document.getElementById('monthlyVisualCaption');
 const rows=Array.isArray(window.marcopaoMonthlyHistory)?window.marcopaoMonthlyHistory:[];
 if(!rows.length){host.innerHTML='<div class="visual-empty">Aguardando relatórios mensais validados.</div>';insight.textContent='O histórico será desenhado conforme os meses forem capturados.';caption.textContent='Nenhum mês validado disponível';return;}
 const map=new Map(rows.map(r=>[r.month,r.total]));
 const first=rows[0].month,last=rows[rows.length-1].month;
 const [fy,fm]=first.split('-').map(Number),[ly,lm]=last.split('-').map(Number),months=[];
 let y=fy,m=fm;
 while(y<ly||(y===ly&&m<=lm)){const key=y+'-'+String(m).padStart(2,'0');months.push({month:key,total:map.has(key)?map.get(key):null});m++;if(m===13){m=1;y++;}}
 const W=680,H=230,L=42,R=14,T=18,B=40,plotW=W-L-R,plotH=H-T-B;
 const vals=months.filter(x=>x.total!==null).map(x=>x.total),max=Math.max(1,...vals)*1.12,min=Math.min(0,...vals),yPos=v=>T+(max-v)/(max-min||1)*plotH,xPos=i=>L+(months.length===1?plotW/2:i/(months.length-1)*plotW);
 let paths=[],segment=[];
 months.forEach((d,i)=>{if(d.total===null){if(segment.length)paths.push(segment);segment=[];}else segment.push([xPos(i),yPos(d.total),d]);});if(segment.length)paths.push(segment);
 const path=paths.map(seg=>seg.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ')).join(' ');
 const circles=months.map((d,i)=>d.total===null?'<circle cx="'+xPos(i)+'" cy="'+(H-B)+'" r="3" class="chart-missing-dot"><title>'+d.month+': sem relatório</title></circle>':'<circle cx="'+xPos(i)+'" cy="'+yPos(d.total)+'" r="4" class="chart-point"><title>'+d.month+': '+brl(d.total)+'</title></circle>').join('');
 const labels=months.map((d,i)=>i===0||i===months.length-1||i%Math.max(1,Math.ceil(months.length/6))===0?'<text x="'+xPos(i)+'" y="'+(H-12)+'" text-anchor="middle" class="chart-axis-label">'+monthLabel(d.month)+'</text>':'').join('');
 const grid=[0,.5,1].map(p=>{const yy=T+p*plotH;return '<line x1="'+L+'" x2="'+(W-R)+'" y1="'+yy+'" y2="'+yy+'" class="chart-grid-line"/><text x="'+(L-8)+'" y="'+(yy+4)+'" text-anchor="end" class="chart-axis-label">'+(max*(1-p)/1000).toFixed(0)+'k</text>';}).join('');
 host.innerHTML='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Linha de evolução mensal do faturamento">'+grid+'<path d="'+path+'" class="monthly-line"/>'+circles+labels+'</svg>';
 const latest=rows[rows.length-1],previous=rows.length>1?rows[rows.length-2]:null;
 let comparison='Há '+rows.length+' meses com relatório validado, de '+monthLabel(first)+' a '+monthLabel(last)+'.';
 if(previous){const pct=previous.total?((latest.total-previous.total)/previous.total)*100:null;comparison+=' Último mês capturado: '+brl(latest.total)+'. '+(pct===null?'':(pct>=0?'Alta de ':'Queda de ')+Math.abs(pct).toLocaleString('pt-BR',{maximumFractionDigits:1})+'% em relação ao mês capturado anterior ('+monthLabel(previous.month)+').');}
 const gaps=months.filter(x=>x.total===null).length;
 insight.innerHTML='<strong>'+monthLabel(latest.month)+' · '+brl(latest.total)+'</strong><span>'+esc(comparison)+' '+(gaps?gaps+' mês(es) sem relatório no intervalo, exibidos como lacunas.':'Sem lacunas entre os meses capturados.')+'</span>';
 caption.textContent=rows.length+' meses validados · '+monthLabel(first)+' a '+monthLabel(last);
}
function render(){
 ensure();dailyChart();monthlyChart();
 const daily=window.marcopaoDailyWindow||{},monthly=Array.isArray(window.marcopaoMonthlyHistory)?window.marcopaoMonthlyHistory:[];
 const kpis=document.getElementById('visualKpis');
 if(kpis)kpis.innerHTML='<div class="visual-kpi"><span>Faturamento · últimos 7 dias</span><strong>'+brl(daily.total||0)+'</strong><small>'+Number(daily.daysWithReport||0)+' de 7 dias com relatório</small></div><div class="visual-kpi"><span>Média por dia disponível</span><strong>'+brl(daily.daysWithReport?daily.total/daily.daysWithReport:0)+'</strong><small>Não inclui dias sem captura</small></div><div class="visual-kpi"><span>Meses no histórico</span><strong>'+monthly.length+'</strong><small>Somente relatórios mensais validados</small></div><div class="visual-kpi"><span>Último mês capturado</span><strong>'+(monthly.length?brl(monthly[monthly.length-1].total):'—')+'</strong><small>'+(monthly.length?monthLabel(monthly[monthly.length-1].month):'Aguardando captura')+'</small></div>';
 const foot=document.getElementById('visualFoot');if(foot)foot.textContent='Fonte: relatórios validados e capturas diárias com valor identificável no Supabase. Dias e meses sem relatório permanecem como lacunas; capturas incompletas não são tratadas como venda zero.';
}
window.renderMarcopaoVisualizations=render;
window.addEventListener('marcopao:data-updated',render);
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',render);else render();
})();