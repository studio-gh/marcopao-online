(function(){
const moneyFmt=n=>new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(Number(n)||0);
const numFmt=n=>new Intl.NumberFormat('pt-BR',{maximumFractionDigits:0}).format(Number(n)||0);
const pctFmt=(n,t)=>t?((Number(n)/Number(t))*100).toLocaleString('pt-BR',{maximumFractionDigits:1})+'%':'—';
const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const hasNum=value=>value!==null&&value!==undefined&&value!==''&&Number.isFinite(Number(value));
const unitOf=item=>String(item.unit||'').toLocaleLowerCase('pt-BR');
const quantity=item=>hasNum(item.quantity)?Number(item.quantity).toLocaleString('pt-BR',{maximumFractionDigits:3})+(unitOf(item)?' '+esc(unitOf(item)):''):'—';
const blocked=/sacola|saco\s+pl[aá]st|embalagem|bobina|etiqueta|filme\s+pvc|papel\s+kraft|fita\s+adesiva/i;
const categoryOf=item=>{
 const n=String(item.name||'').toLocaleLowerCase('pt-BR');
 if(blocked.test(n))return 'Embalagens e insumos';
 if(/p[aã]o|broa|baguete|franc[eê]s|sandu[ií]che|bisnagu|torrada/.test(n))return 'Padaria';
 if(/coxinha|salgad|pastel|empada|quibe|esfiha|croquete|frito|assado|kibe/.test(n))return 'Salgados e lanches';
 if(/caf[eé]|capuccino|cappuccino|suco|guaran[aá]|refrigerante|[áa]gua|cerveja|bebida|mate|chá|leite com/.test(n))return 'Bebidas';
 if(/bolo|doce|sonho|brigitt|brigadeiro|bala|chocolate|biscoito|cookie|rosca|pudim|torta|confe/.test(n))return 'Doces e confeitaria';
 if(/queijo|mu[cç]arela|presunto|mortadela|frango|carne|lingui[cç]a|bacon|frios|requeij[aã]o/.test(n))return 'Frios e proteínas';
 if(/leite|iogurte|manteiga|margarina|creme/.test(n))return 'Laticínios';
 if(/a[cç][uú]car|arroz|feij[aã]o|farinha|[óo]leo|macarr[aã]o|azeite|mercearia/.test(n))return 'Mercearia e ingredientes';
 return 'Outros / revisar';
};
let activeStructuredType=null;
function ensureStructuredFilters(type){
 let root=document.getElementById('structuredFilters');
 if(!root){
  root=document.createElement('div');root.id='structuredFilters';root.className='structured-filters';
  const wrap=document.querySelector('.structured-table-wrap');
  if(wrap)wrap.parentNode.insertBefore(root,wrap);
 }
 const previous={category:document.getElementById('structuredCategoryFilter')?.value||'',unit:document.getElementById('structuredUnitFilter')?.value||'',sort:document.getElementById('structuredSort')?.value||'default'};
 const cats=[...new Set((window.structuredReports?.[type]?.entries||[]).map(categoryOf))].sort((a,b)=>a.localeCompare(b,'pt-BR'));
 const units=[...new Set((window.structuredReports?.[type]?.entries||[]).map(unitOf).filter(Boolean))].sort();
 root.innerHTML='<label>Categoria<select id="structuredCategoryFilter"><option value="">Todas as categorias</option>'+cats.map(x=>'<option>'+esc(x)+'</option>').join('')+'</select></label><label>Unidade<select id="structuredUnitFilter"><option value="">Todas as unidades</option>'+units.map(x=>'<option>'+esc(x)+'</option>').join('')+'</select></label><label>Ordenar por<select id="structuredSort"><option value="default">Ordem original</option><option value="value-desc">Faturamento: maior primeiro</option><option value="value-asc">Faturamento: menor primeiro</option><option value="quantity-desc">Quantidade: maior primeiro</option><option value="quantity-asc">Quantidade: menor primeiro</option><option value="price-desc">Preço: maior primeiro</option><option value="price-asc">Preço: menor primeiro</option><option value="stock-asc">Estoque: menor primeiro</option><option value="name-asc">Produto: A a Z</option></select></label><span id="structuredFilterCount"></span>';
 if(cats.includes(previous.category))document.getElementById('structuredCategoryFilter').value=previous.category;
 if(units.includes(previous.unit))document.getElementById('structuredUnitFilter').value=previous.unit;
 document.getElementById('structuredSort').value=previous.sort;
 if(!document.getElementById('structuredFilterStyles')){
  const style=document.createElement('style');style.id='structuredFilterStyles';
  style.textContent='.structured-filters{display:flex;flex-wrap:wrap;align-items:end;gap:12px;padding:14px 0}.structured-filters label{display:grid;gap:6px;color:var(--muted);font-size:11px;font-weight:700;min-width:170px}.structured-filters select{border:1px solid var(--line);border-radius:9px;background:var(--card);padding:10px;color:var(--ink);font:inherit;font-size:12px}.structured-filters #structuredFilterCount{margin-left:auto;color:var(--muted);font-size:12px;padding:10px 0}.category-chip{display:inline-block;border-radius:6px;padding:4px 7px;background:#eef2e9;color:#385648;font-size:10px;white-space:nowrap}.data-warning{display:inline-block;border-radius:6px;padding:4px 7px;background:#fff0e8;color:#9b3d26;font-size:11px;font-weight:700}';
  document.head.appendChild(style);
 }
 ['structuredCategoryFilter','structuredUnitFilter','structuredSort'].forEach(id=>document.getElementById(id)?.addEventListener('change',()=>window.renderStructuredReport(activeStructuredType)));
}
function kpis(items){document.getElementById('structuredKpis').innerHTML=items.map(x=>'<div class="insight-kpi"><span>'+esc(x.label)+'</span><strong>'+esc(x.value)+'</strong><small>'+esc(x.note||'')+'</small></div>').join('');}
function bars(items,labelFn,valueFn,formatFn,title,subtitle){const max=Math.max(0,...items.map(valueFn));return '<div class="chart-panel"><h3>'+esc(title)+'</h3><div class="chart-caption">'+esc(subtitle)+'</div>'+(items.length?items.map(item=>{const v=valueFn(item),w=max?Math.max(2,v/max*100):0;return '<div class="chart-row"><span class="chart-row-label" title="'+esc(labelFn(item))+'">'+esc(labelFn(item))+'</span><div class="chart-bar-track"><div class="chart-bar" style="width:'+w.toFixed(1)+'%"></div></div><span class="chart-row-value">'+esc(formatFn(v,item))+'</span></div>';}).join(''):'<div class="structured-empty">Sem linhas para visualizar.</div>')+'</div>';}
function table(headers,rows){document.getElementById('structuredThead').innerHTML='<tr>'+headers.map(x=>'<th>'+esc(x)+'</th>').join('')+'</tr>';document.getElementById('structuredTbody').innerHTML=rows||'<tr><td colspan="'+headers.length+'"><div class="structured-empty">Nenhuma linha corresponde à busca.</div></td></tr>';}
function kpi(label,value,note){return {label,value,note};}
window.renderStructuredReport=function(type){
 activeStructuredType=type;
 const rec=window.structuredReports?.[type]||{},all=Array.isArray(rec.entries)?rec.entries:[],query=(document.getElementById('searchFilter').value||'').trim().toLocaleLowerCase('pt-BR');
 ensureStructuredFilters(type);
 const categoryFilter=document.getElementById('structuredCategoryFilter')?.value||'';
 const unitFilter=document.getElementById('structuredUnitFilter')?.value||'';
 const sort=document.getElementById('structuredSort')?.value||'default';
 let rows=all.filter(x=>(!query||[x.name,x.raw,x.group_name,x.unit,categoryOf(x)].some(v=>String(v||'').toLocaleLowerCase('pt-BR').includes(query)))&&(!categoryFilter||categoryOf(x)===categoryFilter)&&(!unitFilter||unitOf(x)===unitFilter));
 const sortField={'value-desc':['value',-1],'value-asc':['value',1],'quantity-desc':['quantity',-1],'quantity-asc':['quantity',1],'price-desc':['unit_price',-1],'price-asc':['unit_price',1],'stock-asc':['stock',1]}[sort];
 if(sort==='name-asc')rows.sort((a,b)=>String(a.name||'').localeCompare(String(b.name||''),'pt-BR'));
 else if(sortField)rows.sort((a,b)=>{const [field,direction]=sortField;const av=Number(a[field]??a.average??0),bv=Number(b[field]??b.average??0);return (av-bv)*direction;});
 const count=document.getElementById('structuredFilterCount');if(count)count.textContent=rows.length+' de '+all.length+' itens';
 document.getElementById('structuredStatus').textContent=rec.status==='validated'?'VALIDADO':'PENDENTE DE CONFERÊNCIA';
 document.getElementById('structuredSubtitle').textContent=(rec.periodStart&&rec.periodEnd?'Período '+rec.periodStart+' a '+rec.periodEnd:rec.date?'Período até '+rec.date:'Período não identificado')+' · '+(rec.branch||'Filial não identificada');
 document.getElementById('structuredRaw').textContent=rec.rawText||all.map(x=>x.raw||x.name||'').join('\n\n');
 let cards=[],chart='',head=[],body='',insight='';
 if(!all.length){
  document.getElementById('structuredTitle').textContent='Relatório sem dados estruturados';
  kpis([kpi('Linhas importadas','0','Nenhum item disponível')]);
  document.getElementById('structuredInsight').innerHTML='<strong>Sem dados</strong><span>Este relatório ainda não possui linhas estruturadas. Nenhum número de exemplo será apresentado.</span>';
  document.getElementById('structuredChart').innerHTML='';
  table(['Relatório'],'<tr><td><div class="structured-empty">Nenhum dado importado.</div></td></tr>');
  document.getElementById('structuredFoot').textContent='Sem dados para análise.';return;
 }
 if(type==='top-value'){
  const items=rows.filter(x=>x.name&&hasNum(x.value)).sort((a,b)=>Number(b.value)-Number(a.value));
  const top=items[0],sum=items.reduce((s,x)=>s+Number(x.value),0),top10=items.slice(0,10).reduce((s,x)=>s+Number(x.value),0);
  document.getElementById('structuredTitle').textContent='O que mais fatura';
  cards=[kpi('Produtos analisados',numFmt(all.length),'Linhas do Top 100'),kpi('Líder em faturamento',top?moneyFmt(top.value):'—',top?.name||'Valor não identificado'),kpi('Participação do líder',top&&sum?pctFmt(top.value,sum):'—','Sobre a soma das linhas capturadas'),kpi('Concentração Top 10',sum?pctFmt(top10,sum):'—','Soma dos dez primeiros no ranking')];
  insight=top?'<strong>Leitura rápida</strong><span><b>'+esc(top.name)+'</b> lidera com '+moneyFmt(top.value)+'. Os dez primeiros somam '+moneyFmt(top10)+'. É um sinal de concentração de receita, não de margem ou lucro.</span>':'<strong>Limite dos dados</strong><span>Não foi identificado faturamento nas linhas consultadas.</span>';
  chart=bars(items.slice(0,10),x=>x.name,x=>Number(x.value),v=>moneyFmt(v),'Top 10 por faturamento','Valores do relatório 3, sem estimativas.');
  head=['#','Produto','Categoria','Faturamento','Quantidade','Preço informado'];
  body=items.map((x,i)=>'<tr><td><span class="table-badge">'+esc(String(x.rank??i+1).padStart(2,'0'))+'</span></td><td style="text-align:left"><strong>'+esc(x.name)+'</strong></td><td><span class="category-chip">'+esc(categoryOf(x))+'</span></td><td>'+moneyFmt(x.value)+'</td><td>'+quantity(x)+'</td><td>'+(hasNum(x.unit_price)?moneyFmt(x.unit_price):'—')+'</td></tr>').join('');
 } else if(type==='top-quantity'){
  const items=rows.filter(x=>x.name&&hasNum(x.quantity)).sort((a,b)=>Number(a.rank??9999)-Number(b.rank??9999));
  const top=items[0],units=new Set(items.map(unitOf).filter(Boolean));
  document.getElementById('structuredTitle').textContent='O que mais sai';
  cards=[kpi('Produtos analisados',numFmt(all.length),'Linhas do Top 100'),kpi('Líder em volume',top?quantity(top):'—',top?.name||'Quantidade não identificada'),kpi('Unidades de medida',numFmt(units.size),'KG, UN e outras coexistem'),kpi('Linhas com quantidade',numFmt(items.length),'Unidades preservadas')];
  insight='<strong>Como interpretar</strong><span>O ranking pode misturar KG e UN. Não somamos unidades incompatíveis nem comparamos barras de produtos medidos de formas diferentes. A posição original e a unidade foram preservadas.</span>';
  chart='<div class="chart-panel"><h3>Top 10 por posição no relatório</h3><div class="chart-caption">Quantidade e unidade aparecem juntas, evitando comparação enganosa entre KG e UN.</div>'+items.slice(0,10).map((x,i)=>'<div class="product-row"><span class="rank-num">'+esc(String(x.rank??i+1).padStart(2,'0'))+'</span><div class="product-info"><strong>'+esc(x.name)+'</strong><span>'+quantity(x)+'</span></div><strong>'+(hasNum(x.unit_price)?moneyFmt(x.unit_price):'—')+'</strong></div>').join('')+'</div>';
  const categoryCounts=items.reduce((acc,x)=>(acc[categoryOf(x)]=(acc[categoryOf(x)]||0)+1,acc),{});
  const leadingCategory=Object.entries(categoryCounts).sort((a,b)=>b[1]-a[1])[0];
  insight='<strong>Leitura por categoria</strong><span>Categoria com mais itens reconhecidos: <b>'+esc(leadingCategory?.[0]||'Não identificada')+'</b> ('+numFmt(leadingCategory?.[1]||0)+' produtos). É uma classificação automática pelo nome, útil para explorar o mix, não uma categoria oficial do cadastro.</span>';
  head=['#','Produto','Categoria','Quantidade','Preço informado'];
  body=items.map((x,i)=>'<tr><td><span class="table-badge">'+esc(String(x.rank??i+1).padStart(2,'0'))+'</span></td><td style="text-align:left"><strong>'+esc(x.name)+'</strong></td><td><span class="category-chip">'+esc(categoryOf(x))+'</span></td><td>'+quantity(x)+'</td><td>'+(hasNum(x.unit_price)?moneyFmt(x.unit_price):'—')+'</td></tr>').join('');
 } else if(type==='ticket'){
  const items=rows.filter(x=>x.name&&hasNum(x.unit_price??x.value)),price=x=>Number(x.unit_price??x.value);
  const high=[...items].sort((a,b)=>price(b)-price(a))[0],low=[...items].sort((a,b)=>price(a)-price(b))[0];
  document.getElementById('structuredTitle').textContent='Preço e quantidade por produto';
  cards=[kpi('Itens reconhecidos',numFmt(all.length),'Linhas do relatório 10'),kpi('Itens com preço',numFmt(items.length),'Preço extraído da captura'),kpi('Maior preço informado',high?moneyFmt(price(high)):'—',high?.name||'Não identificado'),kpi('Menor preço informado',low?moneyFmt(price(low)):'—',low?.name||'Não identificado')];
  insight='<strong>Leitura rápida</strong><span>O relatório 10 lista preços e quantidades por produto. Ele não é o ticket médio por cupom da loja. Use para inspecionar preços e volumes, sem confundir com o indicador financeiro do relatório 2.</span>';
  chart=bars([...items].sort((a,b)=>price(b)-price(a)).slice(0,10),x=>x.name,price,v=>moneyFmt(v),'Dez maiores preços','Preço nominal; unidades de medida podem variar.');
  head=['Produto','Categoria','Preço informado','Quantidade','Unidade'];
  body=items.map(x=>'<tr><td style="text-align:left"><strong>'+esc(x.name)+'</strong></td><td><span class="category-chip">'+esc(categoryOf(x))+'</span></td><td>'+moneyFmt(price(x))+'</td><td>'+(hasNum(x.quantity)?Number(x.quantity).toLocaleString('pt-BR',{maximumFractionDigits:3}):'—')+'</td><td>'+esc(unitOf(x)||'—')+'</td></tr>').join('');
 } else {
  const items=rows.filter(x=>x.name&&!blocked.test(x.name)&&(hasNum(x.value)||hasNum(x.quantity))).sort((a,b)=>Number(b.value??0)-Number(a.value??0)),withValue=items.filter(x=>hasNum(x.value)),top=withValue[0];
  document.getElementById('structuredTitle').textContent='Produtos que movem a produção';
  cards=[kpi('Linhas capturadas',numFmt(all.length),'Relatório 13 antes dos filtros'),kpi('Produtos operacionais',numFmt(items.length),'Embalagens e apoio excluídos'),kpi('Líder por faturamento',top?moneyFmt(top.value):'Não disponível',top?.name||'Faturamento ainda não estruturado'),kpi('Linhas com quantidade',numFmt(items.filter(x=>hasNum(x.quantity)).length),'Unidade de medida preservada')];
  insight=top?'<strong>Leitura rápida</strong><span><b>'+esc(top.name)+'</b> lidera o faturamento reconhecido com '+moneyFmt(top.value)+'. Antes de alterar compras, compare com estoque, custo e perdas. O relatório não mede lucro sozinho.</span>':'<strong>Limite dos dados</strong><span>Não há faturamento estruturado suficiente para ordenar a produção. Exibimos apenas os campos capturados.</span>';
  chart=bars(withValue.slice(0,10),x=>x.name,x=>Number(x.value),v=>moneyFmt(v),'Top 10 por faturamento reconhecido','Itens de embalagem e apoio excluídos do ranking operacional.');
  const suspiciousStock=x=>hasNum(x.stock)&&(Number(x.stock)<0||Math.abs(Number(x.stock))>100000);
  head=['#','Produto','Categoria','Faturamento','Quantidade','Preço','Estoque','Custo'];
  body=items.map((x,i)=>'<tr><td><span class="table-badge">'+esc(String(x.rank??i+1).padStart(2,'0'))+'</span></td><td style="text-align:left"><strong>'+esc(x.name)+'</strong></td><td><span class="category-chip">'+esc(categoryOf(x))+'</span></td><td>'+(hasNum(x.value)?moneyFmt(x.value):'—')+'</td><td>'+quantity(x)+'</td><td>'+(hasNum(x.unit_price)?moneyFmt(x.unit_price):'—')+'</td><td>'+(suspiciousStock(x)?'<span class="data-warning" title="Valor negativo ou fora da faixa plausível: conferir no sistema de estoque">Conferir dado</span>':(hasNum(x.stock)?Number(x.stock).toLocaleString('pt-BR',{maximumFractionDigits:3})+' '+esc(unitOf(x)):'—'))+'</td><td>'+(hasNum(x.cost)?moneyFmt(x.cost):'—')+'</td></tr>').join('');
 }
 kpis(cards);document.getElementById('structuredInsight').innerHTML=insight;document.getElementById('structuredChart').innerHTML=chart;table(head,body);
 const date=rec.importedAt&&typeof window.formatReportDate==='function'?window.formatReportDate(rec.importedAt):'horário não disponível';
 document.getElementById('structuredFoot').textContent='Fonte: '+(rec.reportType||type)+' · '+all.length+' linhas · importação '+date+' · status '+(rec.status||'needs_review')+'. Dados pendentes de conferência humana; não usar isoladamente para decidir compras.';
};
})();