'use strict';
const $ = (s, root = document) => root.querySelector(s);
const app = $('#app');
const modal = $('#modal');
const state = {data:null, route:'todos', id:null, season:null, run:null, tab:'plan', customer:'', records:[], knowledge:null, documents:[]};
let routeGeneration = 0;
const labels = {queued:'排队中',running:'研制中',waiting_input:'等待补充',completed:'待技术复核',limited:'受限结果',failed:'需要处理',cancelled:'已取消'};
const kinds = {inspection:'田间巡查',operation:'实际作业',incident:'异常提报',review:'季末复盘'};
const icons = {
  leaf:'M20 4c-8-2-15 2-15 8a7 7 0 0 0 7 7c6 0 8-7 8-15ZM5 21l10-10M10 16v-5m0 5h5',
  todo:'M8 4h12v17H4V4h4m1-2h6v5H9zM8 12l2 2 4-4M8 18h8',
  users:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.87M16 3a4 4 0 0 1 0 8M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
  plan:'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Zm0 0v6h6M8 13h8M8 17h5',
  field:'M3 20h18M4 16l4-3 4 3 4-3 4 3M4 10l4-3 4 3 4-3 4 3M12 3v4',
  book:'M12 7c-3-3-7-3-10-2v15c4-1 7-1 10 2m0-15c3-3 7-3 10-2v15c-4-1-7-1-10 2Zm0 0v15',
  arrow:'M5 12h14m-5-5 5 5-5 5',back:'M19 12H5m5-5-5 5 5 5',plus:'M12 5v14M5 12h14',
  check:'M5 12l4 4L19 6',clock:'M12 8v4l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z',
  info:'M12 11v6m0-10v1M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z',pin:'M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0ZM15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
  edit:'m16 3 5 5L9 20l-6 1 1-6Zm-2 2 5 5',download:'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',
};
function icon(name, size=18){return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${icons[name]||icons.leaf}"/></svg>`;}
function esc(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function num(value){return value===null||value===undefined||value===''?'未知':Number(value).toLocaleString('zh-CN',{maximumFractionDigits:4});}
function date(value){return value?new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'—';}
function notice(text,type=''){return `<div class="notice ${type}">${icon('info',16)}<div>${esc(text)}</div></div>`;}
function badge(run){const status=run.formal_id?'已技术确认':labels[run.status]||run.status; const c=run.formal_id?'green':({completed:'green',waiting_input:'amber',failed:'red',running:'blue'}[run.status]||'');return `<span class="badge ${c}">${esc(status)}</span>`;}
function empty(text,action=''){return `<div class="empty">${icon('leaf',30)}<p>${esc(text)}</p>${action}</div>`;}
function button(label,action,id='',cls=''){return `<button class="btn ${cls}" data-action="${action}" data-id="${esc(id)}">${label}</button>`;}
function toast(message,error=false){const el=$('#toast');el.textContent=message;el.className='visible'+(error?' error':'');clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.className='',5500);}
async function api(path,options={}){
  const res=await fetch('/api'+path,{credentials:'same-origin',...options,headers:{'Content-Type':'application/json',...options.headers},body:options.body===undefined?undefined:JSON.stringify(options.body)});
  const data=await res.json().catch(()=>({detail:'服务器没有返回可读取结果'}));
  if(!res.ok){if(res.status===401&&path!='/login'){state.data=null;renderLogin();}const message=Array.isArray(data.detail)?data.detail.map(d=>d.loc.slice(1).join('.')+'：'+d.msg).join('；'):data.detail;throw new Error(message||'操作失败，请稍后重试');}
  return data;
}
const isSR=()=>state.data?.user.role==='SR';
const pendingCreates = new Map();
async function createRun(body){
  const signature=JSON.stringify(body);
  if(!pendingCreates.has(signature))pendingCreates.set(signature,crypto.randomUUID());
  const result=await api('/research-runs',{method:'POST',headers:{'Idempotency-Key':pendingCreates.get(signature)},body});
  pendingCreates.delete(signature);
  return result;
}
const filtered=items=>items.filter(item=>!state.customer||(item.customer_id||state.data.seasons.find(s=>s.id===item.season_id)?.customer_id)===state.customer);
function customerFilter(){return `<select id="customer-filter" class="filter-select" aria-label="筛选客户"><option value="">所有负责客户</option>${state.data.customers.map(c=>`<option value="${esc(c.id)}" ${state.customer===c.id?'selected':''}>${esc(c.name)}</option>`).join('')}</select>`;}
function header(eyebrow,title,subtitle,action=''){return `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1>${subtitle?`<p>${subtitle}</p>`:''}</div>${action}</div>`;}
function scope(s){return `<div class="scope"><span>${esc(s.customer_name)}</span><span>${esc(s.plot_name)}</span><span>${s.year} 种植季 · ${esc(s.crop)}</span></div>`;}
function go(route,id=''){location.hash=route+(id?'/'+id:'');}
function modalOpen(title,body){
  modal.innerHTML=`<div class="modal-head"><h2 id="modal-title">${title}</h2><button class="modal-close" data-action="close-modal" aria-label="关闭">×</button></div><div class="modal-body">${body}</div>`;
  const form=$('form',modal);
  if(form){
    form.dataset.runId=state.run?.id||'';
    form.dataset.runRevision=String(state.run?.revision||0);
    form.dataset.stageCount=String(state.run?.draft?.stages?.length||0);
    form.dataset.seasonId=state.season?.id||'';
    form.dataset.routeGeneration=String(routeGeneration);
  }
  modal.showModal();
}

function renderLogin(){
  app.innerHTML=`<div class="login-page"><section class="login-art"><div class="brand"><div class="brand-logo">${icon('leaf',24)}</div><div><div class="brand-name">AgriPlan</div><div class="brand-sub">GROW WITH EVIDENCE</div></div></div><h1>每一季新计划，<br>都有经验可循。</h1><p>把历年方案、本季变化与专业判断连接起来，<br>让技术服务的每一步有据可查。</p><div class="eyebrow">FIELD NOTES / SEASON 2026</div></section><section class="login-main"><div class="login-box"><div class="eyebrow">YOUR SEASON STARTS HERE</div><h2>进入服务工作台</h2><p>选择演示身份，体验完整的本季方案流程。</p><form id="login-form"><div class="field"><label for="username">工作账号</label><select id="username" name="username"><option value="sr_chen">陈晓 · SR 技术经理（两位客户）</option><option value="fr_zhou">周禾 · FR 一线农艺师</option><option value="sr_liu">刘宁 · SR 技术经理（权限隔离演示）</option></select></div><div class="field"><label for="password">登录密码</label><input id="password" type="password" name="password" autocomplete="current-password" value="agriplan-demo" required maxlength="200"></div><button class="btn primary" type="submit">进入工作台 ${icon('arrow',16)}</button></form>${notice('本地 MVP · 默认密码 agriplan-demo。数据、产品参数与行情均为演示，实际农艺方案需专业复核。')}<p class="footer-note">AGRIPLAN / 农业技术服务工作台 · MVP 0.1</p></div></section></div>`;
}
function shell(body){
  const d=state.data;
  const nav=[['todos','todo','我的待办'],['customers','users','客户与地块'],['research','plan','方案研制'],['field','field','田间服务'],['knowledge','book','知识问答']];
  const selected=state.route==='season'?'customers':state.route==='run'?'research':state.route;
  const count=d.runs.filter(r=>r.status==='waiting_input'||['completed','limited'].includes(r.status)&&!r.formal_id).length;
  app.innerHTML=`<div class="shell"><aside class="sidebar"><a href="#todos" class="brand"><div class="brand-logo">${icon('leaf',23)}</div><div><div class="brand-name">AgriPlan</div><div class="brand-sub">农业技术服务</div></div></a><p class="nav-label">工作空间 / WORKSPACE</p><nav aria-label="主导航">${nav.map(([key,i,label])=>`<a href="#${key}" class="nav-link ${selected===key?'active':''}" ${selected===key?'aria-current="page"':''}>${icon(i,19)}<span>${label}</span>${key==='todos'&&count?`<span class="nav-count">${count}</span>`:''}</a>`).join('')}</nav><div class="sidebar-footer"><strong>让每一季的经验留下来。</strong>本季资料 → 草稿与复核<br>现场记录 → 下一季参考<div class="divider"></div>LOCAL MVP · 0.1.0</div></aside><div class="workspace"><header class="topbar"><div class="breadcrumb">工作空间 <span>/</span> <strong>${nav.find(x=>x[0]===selected)?.[2]||'工作台'}</strong></div><div class="user-chip"><div class="avatar">${esc(d.user.name[0])}</div><span>${esc(d.user.name)} <span class="role">/ ${d.user.role}</span></span><button data-action="logout">退出</button></div></header><main class="page"><div class="mode-strip"><span class="dot"></span>${d.mode==='llm'?'模型模式 · 规划与草稿生成':'规则演示模式 · 未调用真实模型'}<span> / </span>演示资料与报价 · 非生产农艺依据</div>${body}<div class="footer-note">AGRIPLAN · 研究建议由 SR 复核，技术确认与执行放行分别管理</div></main></div></div>`;
}
function overview(){
  const runs=filtered(state.data.runs), seasons=filtered(state.data.seasons);
  const waiting=runs.filter(r=>r.status==='waiting_input');
  const ready=runs.filter(r=>['completed','limited'].includes(r.status)&&!r.formal_id);
  const active=runs.filter(r=>['queued','running','failed'].includes(r.status));
  const tasks=[...waiting,...ready,...active];
  const fieldArt=`<svg viewBox="0 0 290 230" fill="none" aria-hidden="true"><circle cx="211" cy="62" r="26" fill="#d5dfc8"/><path d="M-20 180Q140 70 330 153V260H-20Z" fill="#d1ddc0"/><path d="M-20 210Q150 105 320 195V260H-20Z" fill="#b6c99c"/><path d="M0 240Q190 126 320 220M35 245Q190 150 320 242M92 250Q205 177 320 261" stroke="#91ab76" stroke-width="1.2"/><path d="M127 164V76m0 44c-22-2-24-21-24-21 21-1 24 21 24 21Zm0-17c21-1 25-22 25-22-22-1-25 22-25 22Zm0 40c24 0 31-22 31-22-24-2-31 22-31 22Z" stroke="#718e56" stroke-width="2"/><path d="M77 182v-46m0 18c-16-2-19-17-19-17 16 0 19 17 19 17Zm0 11c16-1 20-17 20-17-17 0-20 17-20 17Z" stroke="#819b64" stroke-width="1.5"/></svg>`;
  return `${header('SEASON WORKSPACE','我的待办','把注意力，放在需要专业判断的地方。',customerFilter())}<section class="hero"><div class="hero-copy"><div class="eyebrow">NEW SEASON, GROUNDED IN EXPERIENCE</div><h1>把经验，带进这一季。</h1><p>从同地块的历年方案出发，结合本季变化，<br>形成有依据、可复核的种植建议。</p><a href="#customers" class="btn primary">查看客户与地块 ${icon('arrow',15)}</a></div><div class="hero-art"><div class="hero-tag">FIELD / 2026</div>${fieldArt}</div></section><div class="overview-grid"><section class="card"><div class="card-head"><h2>需要我处理</h2><span class="muted">跨客户任务 · 同一业务记录</span></div><div class="stat-tabs"><div class="stat-tab">待补充信息<b>${waiting.length}</b></div><div class="stat-tab">待技术复核<b>${ready.length}</b></div><div class="stat-tab">研制及异常<b>${active.length}</b></div></div>${tasks.length?tasks.map(r=>`<div class="task-row"><div class="task-icon ${r.status==='waiting_input'?'amber':''}">${icon(r.status==='waiting_input'?'clock':'plan',18)}</div><div class="grow"><div class="task-title">${esc(r.customer_name)} · ${esc(r.plot_name)}</div><div class="task-meta">${r.year} ${esc(r.crop)} / ${esc(r.goal.slice(0,28))}</div></div>${badge(r)}<a href="#run/${r.id}" class="btn small">处理 ${icon('arrow',13)}</a></div>`).join(''):empty('当前没有待处理研究，从本季资料开始。','<a class="text-btn" href="#customers">选择地块并开始研制 →</a>')}</section><section class="card"><div class="card-head"><h2>正在服务的地块</h2><span class="muted">${seasons.length} 个种植季</span></div>${seasons.map(s=>`<div class="season-mini"><div class="row between"><h3>${esc(s.plot_name)}</h3><a href="#season/${s.id}" class="text-btn" aria-label="查看${esc(s.plot_name)}">${icon('arrow',16)}</a></div><div class="row">${icon('pin',12)}${esc(s.customer_name)} · ${esc(s.region)}</div><div class="row mt"><span class="badge">${s.year} · ${esc(s.crop)}</span><span>${num(s.facts.area_mu)} 亩</span><span>${s.facts.rate_kg_per_mu===null?'用量待确认':'已确认专业用量'}</span></div></div>`).join('')}</section></div>`;
}
function customers(){return `${header('CUSTOMERS & PLOTS','客户与地块','让每个地块、每个种植季都有清晰的上下文。',`<div class="row">${customerFilter()}${isSR()?button(icon('plus',15)+' 新建客户地块','new-customer','','primary'):''}</div>`)}<div class="cards-grid">${filtered(state.data.seasons).map(s=>`<article class="card customer-card"><div class="card-pad"><div class="row between"><div class="customer-symbol">${icon('field',22)}</div><span class="badge">${s.year} 种植季</span></div><h2>${esc(s.plot_name)}</h2><p class="muted small">${esc(s.customer_name)} · ${esc(s.region)} · ${esc(s.crop)}</p><div class="customer-meta"><div><span>本季面积</span><strong>${num(s.facts.area_mu)} <small>亩</small></strong></div><div><span>公司合作预算</span><strong>${num(s.facts.budget_cny)} <small>元</small></strong></div></div><div class="row gap8"><span class="badge ${s.facts.rate_kg_per_mu===null?'amber':'green'}">${s.facts.rate_kg_per_mu===null?'需确认专业参数':'本季用量已确认'}</span><span class="badge">资料 R${s.revision}</span></div></div><div class="card-footer"><span class="muted small">${s.facts.soil_ph===null?'测土资料待补 · 可先起草':'已记录土壤 pH'}</span><a href="#season/${s.id}" class="text-btn">查看本季 ${icon('arrow',15)}</a></div></article>`).join('')}</div>`;}
function researchList(){
  const runs=filtered(state.data.runs);
  return `${header('PLAN RESEARCH','方案研制','研制、讨论与修改，始终绑定具体地块和种植季。',`<div class="row">${customerFilter()}<a href="#customers" class="btn primary">${icon('plus',15)} 发起研制</a></div>`)}<section class="card">${runs.length?`<div class="table-wrap"><table><thead><tr><th>客户 / 地块</th><th>研制目标</th><th>状态</th><th>版本</th><th>发起时间</th><th></th></tr></thead><tbody>${runs.map(r=>`<tr><td><strong>${esc(r.customer_name)}</strong><div class="muted small">${esc(r.plot_name)} · ${r.year} ${esc(r.crop)}</div></td><td>${esc(r.goal.slice(0,45))}</td><td>${badge(r)}</td><td><span class="mono">${r.candidate_version?'C'+r.candidate_version:'—'}</span></td><td class="muted nowrap">${date(r.created_at)}</td><td><a href="#run/${r.id}" class="text-btn">详情 ${icon('arrow',14)}</a></td></tr>`).join('')}</tbody></table></div>`:empty('尚未发起研究，先确认本季已知资料。','<a href="#customers" class="btn">选择地块</a>')}</section>`;
}
function materialRow(material){
  const key='material.'+material.id+'.';
  return `<div class="material-row" data-material-id="${esc(material.id)}"><div class="row between"><h3>追加公司产品</h3><button type="button" class="text-btn" data-action="remove-material">移除明细</button></div><div class="form-grid mt"><div class="field full"><label for="material-product-${esc(material.id)}">产品 · 演示目录</label><select id="material-product-${esc(material.id)}" name="${key}product_id">${state.data.products.map(p=>`<option value="${esc(p.id)}" ${p.id===material.product_id?'selected':''}>${esc(p.name)} · ${esc(p.price)} 元/kg</option>`).join('')}</select></div><div class="field"><label for="material-rate-${esc(material.id)}">确认用量 · kg/亩</label><input id="material-rate-${esc(material.id)}" name="${key}rate_kg_per_mu" type="number" min="0" max="1000" step="0.0001" value="${esc(material.rate_kg_per_mu)}" placeholder="未知时留空"></div><div class="field"><label for="material-source-${esc(material.id)}">专业用量的确认依据</label><input id="material-source-${esc(material.id)}" name="${key}rate_source" maxlength="1000" ${material.rate_kg_per_mu!==null&&material.rate_kg_per_mu!==undefined&&material.rate_kg_per_mu!==''?'required':''} value="${esc(material.rate_source)}"></div></div></div>`;
}
function addMaterialRow(form){
  const list=$('[data-material-list]',form),rows=[...form.querySelectorAll('[data-material-id]')];
  if(rows.length>=19)throw new Error('最多可追加19条产品明细');
  const used=[form.elements.product_id.value,...rows.map(row=>$('select',row).value)];
  const product=state.data.products.find(p=>!used.includes(p.id));
  if(!product)throw new Error('当前目录的产品均已添加，同一产品不能重复');
  list.insertAdjacentHTML('beforeend',materialRow({id:crypto.randomUUID(),product_id:product.id,rate_kg_per_mu:null,rate_source:''}));
}
function removeMaterialRow(row){row?.remove();}
function parseMaterials(data){
  const rows=new Map();
  for(const [key,value] of Object.entries(data)){
    const match=/^material\.([A-Za-z0-9_-]{1,60})\.(product_id|rate_kg_per_mu|rate_source)$/.exec(key);
    if(!match)continue;
    if(!rows.has(match[1]))rows.set(match[1],{id:match[1],rate_kg_per_mu:null,rate_source:''});
    rows.get(match[1])[match[2]]=match[2]==='rate_kg_per_mu'&&value===''?null:value;delete data[key];
  }
  const materials=[...rows.values()],products=[data.product_id,...materials.map(m=>m.product_id)];
  if(materials.length>19||new Set(products).size!==products.length)throw new Error('最多追加19条明细，产品不能重复（含主产品）');
  if(materials.some(m=>m.rate_kg_per_mu!==null&&!m.rate_source.trim()))throw new Error('已填写的追加产品用量必须有确认依据');
  return materials;
}
function factForm(s){const f=s.facts,disabled=isSR()?'':'disabled';return `<form id="facts-form" data-season-id="${esc(s.id)}" data-season-revision="${esc(s.revision)}"><fieldset ${disabled} class="form-fieldset"><div class="form-grid"><div class="field"><label for="area">面积 · 亩</label><input id="area" name="area_mu" type="number" step="0.0001" min="0.0001" max="1000000" required value="${esc(f.area_mu)}"></div><div class="field"><label for="budget">公司合作预算 · 元</label><input id="budget" name="budget_cny" type="number" min="0" step="0.01" value="${esc(f.budget_cny)}" placeholder="未知时留空"></div><div class="field full"><label for="variety">品种 / 客户硬要求</label><input id="variety" name="variety" value="${esc(f.variety)}" maxlength="160" placeholder="保留客户原意，不擅自换种"></div><div class="field"><label for="product">主产品 · 演示目录</label><select id="product" name="product_id">${state.data.products.map(p=>`<option value="${p.id}" ${p.id===f.product_id?'selected':''}>${esc(p.name)} · ${p.price} 元/kg</option>`).join('')}</select></div><div class="field"><label for="rate">SR 确认用量 · kg/亩</label><input id="rate" name="rate_kg_per_mu" type="number" min="0" max="1000" step="0.0001" value="${esc(f.rate_kg_per_mu)}" placeholder="关键缺项可在研究时补问"></div><div class="field full"><label for="rate-source">专业用量的确认依据</label><input id="rate-source" name="rate_source" value="${esc(f.rate_source)}" maxlength="1000" placeholder="有用量时必须填写；示例参数不可用于生产"></div><div class="field full"><h3>追加公司合作产品</h3><p class="muted small">逐行记录用量与来源；同一产品只添加一次，未知用量可留空。</p><div data-material-list>${(f.additional_materials||[]).map(materialRow).join('')}</div>${isSR()?'<button type="button" class="btn small mt" data-action="add-material">添加产品明细</button>':''}</div><div class="field"><label for="ph">土壤 pH · 可选</label><input id="ph" name="soil_ph" type="number" min="0" max="14" step="0.01" value="${esc(f.soil_ph)}" placeholder="未知时留空，不会补零"></div><div class="field"><label for="storage">本季仓储条件</label><select id="storage" name="storage"><option value="unknown" ${f.storage==='unknown'?'selected':''}>尚未确认</option><option value="no" ${f.storage==='no'?'selected':''}>无可用仓储</option><option value="yes" ${f.storage==='yes'?'selected':''}>有可用仓储（需另评估成本）</option></select></div><div class="field full"><label for="note">本季备注与条件</label><textarea id="note" name="note" maxlength="4000">${esc(f.note)}</textarea></div><div class="field full"><label for="source">本次资料来源与确认说明</label><input id="source" name="source" required maxlength="1000" value="SR 本季调查确认"><small>点击保存产生新资料版本；既有研究快照保留，旧草稿需重新研制。</small></div></div>${isSR()?'<div class="form-actions"><button class="btn primary" type="submit">保存并确认本季资料</button></div>':''}</fieldset></form>`;}
function historyCards(items){return items.length?items.map(h=>`<article class="history-card"><div class="row between"><div><div class="reference-label">${esc(h.relation)}</div><h3>${h.year} · ${esc(h.version)} ${esc(h.summary)}</h3></div><span class="badge">${esc(h.region)} · ${esc(h.crop)}</span></div>${h.differences?.length?`<div class="table-wrap"><table><thead><tr><th>本季变化</th><th>历史记录</th><th>本季已知资料</th></tr></thead><tbody>${h.differences.map(d=>`<tr><td>${esc(d.label)}</td><td>${esc(valueText(d.historical))}</td><td>${esc(valueText(d.current))}</td></tr>`).join('')}</tbody></table></div>`:''}<p class="muted small mt">可参考：${esc(h.measures?.join('；'))}</p><ul>${(h.limitations||[]).map(t=>`<li>${esc(t)}</li>`).join('')}</ul><div class="muted small mt mono">引用 ${esc(h.reference_id)}</div></article>`).join(''):empty('没有往季参考也可以继续起草；会明确说明依据和限制。');}
const reportMethods={text_pattern:'本地文字匹配',pdf_text_pattern:'PDF 文本层匹配',manual_transcription:'人工转录'};
const reportStatuses={uploaded:'已上传 · 尚未提取',extracted:'已提取 · 待 SR 核对',manual_required:'需人工转录',confirmed:'已确认进入事实'};
const originalStatuses={available:'原件完整',missing:'原件缺失',hash_mismatch:'原件哈希不符'};
function reportOriginal(id,label='查看 / 下载原件'){return `<a class="text-btn" href="/api/reports/${encodeURIComponent(id)}/original" target="_blank" rel="noopener">${label} ${icon('download',13)}</a>`;}
function reportCandidate(candidate,confirmed=false){
  if(!candidate)return '<p class="small muted mt">尚无可确认的土壤 pH 候选，请核对原件后人工转录。</p>';
  const fields={'检测字段':'土壤 pH','原始读数':candidate.raw_value,'规范值':candidate.value??'未能规范化','原单位':candidate.original_unit,'规范单位':candidate.normalized_unit??'未知','原件地块':candidate.plot_name,'采样日期':candidate.sampled_on||'未记录','页码':candidate.page,'定位':candidate.locator,'原文摘录':candidate.excerpt};
  return `<div class="report-candidate"><span class="badge ${confirmed?'green':'amber'}">${candidate.identity==='manual_candidate'?'人工转录候选':'自动文字候选'} · ${confirmed?'原候选已由 SR 确认':'尚需 SR 核对'}</span><dl class="report-fields">${Object.entries(fields).map(([key,value])=>`<div><dt>${key}</dt><dd>${esc(value??'未记录')}</dd></div>`).join('')}</dl>${candidate.signal?`<p class="small muted mt">${confirmed?'原候选复核信号（确认前记录）':'复核信号'}：${esc(candidate.signal)}</p>`:''}</div>`;
}
function reportSourceCards(sources){
  return sources.length?sources.map(source=>`<article class="report-source"><div class="row between"><h3>${esc(source.filename)}</h3><span class="badge green">SR 已确认检测来源</span></div><p class="small mt">土壤 pH ${esc(source.value)} · 原单位 ${esc(source.original_unit)} → ${esc(source.normalized_unit)} · 采样 ${esc(source.sampled_on)}</p><p class="small mt">第 ${esc(source.page)} 页 / ${esc(source.locator)}<br>${esc(source.excerpt)}</p><p class="small muted mt">${esc(reportMethods[source.method]||source.method)} · 确认输入 R${esc(source.input_revision)}<br>提取 ${esc(source.extraction_id)}</p><p class="small mono review-hash mt">SHA-256 ${esc(source.sha256)}</p>${reportOriginal(source.report_id)}</article>`).join(''):notice('此版本尚无 SR 已确认的检测报告来源。');
}
function reportSection(season){
  return `<section class="card mt report-section"><div class="card-head"><h2>报告原件与检测候选</h2><span class="muted">当前仅映射土壤 pH</span></div><div class="card-pad">${notice('提取结果只作为候选。SR 核对原件、单位、地块、采样日期和定位后，另行确认才写入新事实 R。图片或扫描 PDF 需人工转录，当前没有自动 OCR。','info')}${isSR()?`<form id="report-upload-form" data-season-id="${esc(season.id)}" data-season-revision="${esc(season.revision)}" data-route-generation="${routeGeneration}" class="report-upload mt"><div class="field"><label for="report-file">上传检测报告原件</label><input id="report-file" name="report_file" type="file" accept=".txt,.md,.pdf,.png,.jpg,.jpeg,text/plain,application/pdf,image/png,image/jpeg" required><small>UTF-8 txt / md、文本层 PDF、PNG / JPEG；单件最多 8 MiB。上传后另行点击提取。</small></div><button class="btn" type="submit">保存报告原件</button></form>`:''}${(season.reports||[]).map(report=>{
    const extraction=report.current_extraction,editable=isSR()&&report.status!=='confirmed'&&report.original_status==='available';
    const confirmable=editable&&report.can_confirm===true&&!!extraction?.candidate&&!(extraction.issues||[]).some(issue=>issue.severity==='blocking');
    return `<article class="report-card"><div class="row between"><h3>${esc(report.filename)}</h3><span class="badge ${report.status==='confirmed'?'green':'amber'}">${esc(reportStatuses[report.status]||report.status)}</span></div><div class="small muted mt">${esc(report.mime_type)} · ${num(report.size_bytes)} 字节 · 报告修订 ${esc(report.revision)}${report.confirmed_input_revision!==null&&report.confirmed_input_revision!==undefined?' · 确认输入 R'+esc(report.confirmed_input_revision):''}</div><p class="small mono review-hash mt">SHA-256 ${esc(report.sha256)}</p><div class="row between mt"><span class="badge ${report.original_status==='available'?'':'red'}">${esc(originalStatuses[report.original_status]||'原件状态未知')}</span>${reportOriginal(report.id)}</div>${reportCandidate(extraction?.candidate,report.status==='confirmed')} ${(report.status==='confirmed'?[]:extraction?.issues||[]).map(issue=>notice((issue.severity==='blocking'?'阻塞：':'提醒：')+issue.message,issue.severity==='blocking'?'error':'')).join('')}${report.status==='manual_required'?notice('当前候选存在缺项或不符，请查看原件并人工修正。'):''}${editable?`<div class="form-actions">${button(extraction?'重新提取文字候选':'提取文字候选','report-extract',report.id)}${button('人工转录 / 修正候选','report-manual',report.id)}${confirmable?button('核对并确认 pH 进入事实','report-confirm',report.id,'primary'):''}</div>`:''}<details class="report-history"><summary>提取历史与按页文本 · ${(report.extractions||[]).length} 次</summary>${(report.extractions||[]).map(item=>`<section class="report-extraction"><h4>第 ${esc(item.sequence)} 次 · ${esc(reportMethods[item.method]||item.method)}</h4><p class="small muted">${esc(item.id)} · ${date(item.created_at)}</p>${item.id!==extraction?.id?reportCandidate(item.candidate):''}${(item.issues||[]).map(issue=>`<p class="small mt">${esc(issue.severity==='blocking'?'阻塞':'提醒')}：${esc(issue.message)}</p>`).join('')}${(item.pages||[]).map(page=>`<details class="report-page"><summary>第 ${esc(page.page)} 页纯文本</summary><pre class="document-body">${esc(page.text)}</pre></details>`).join('')}</section>`).join('')}</details></article>`;
  }).join('')||'<p class="small muted mt">本季尚未上传检测报告。</p>'}<div class="divider"></div><h3>当前事实的检测报告来源</h3>${reportSourceCards(season.report_sources||[])}</div></section>`;
}
function seasonPage(){
  const s=state.season;if(!s)return empty('正在读取本季资料…');
  const runs=state.data.runs.filter(r=>r.season_id===s.id);
  return `<a href="#customers" class="back">${icon('back',14)} 返回客户与地块</a><div class="page-heading"><div><div class="eyebrow">PLOT SEASON / R${s.revision}</div><h1>${esc(s.plot_name)} · 本季资料</h1>${scope(s)}</div><div class="row">${isSR()?button('新建下一季','new-season',s.id):''}${button('查看市场演示','market',s.id)}</div></div><div class="detail-grid"><section class="card"><div class="card-head"><h2>已知条件与专业参数</h2><span class="muted">未知留空 · 来源可查</span></div><div class="card-pad">${factForm(s)}</div></section><div class="stack"><section class="card"><div class="card-head"><h2>开始本季研制</h2>${icon('plan',18)}</div><div class="card-pad">${isSR()?`<form id="start-form" data-season-id="${esc(s.id)}" data-season-revision="${esc(s.revision)}"><div class="field"><label for="goal">目标与约束</label><textarea id="goal" name="goal" required maxlength="4000">参考同地块历年方案，结合本季条件形成可编辑草稿，遵守客户品种要求与合作预算。</textarea></div><fieldset class="required-facts"><legend>本次必须确认的资料 · 可选</legend>${Object.entries({budget_cny:'公司合作预算',variety:'品种要求',storage:'仓储条件',soil_ph:'土壤 pH'}).map(([key,label])=>`<label class="check"><input type="checkbox" name="required_field" value="${key}">${label}</label>`).join('')}<small class="muted">仅对勾选且未知的资料补问；专业用量缺失会自动补问。</small></fieldset><label class="check"><input type="checkbox" name="include_market"><span>同时比较市场演示报价<br><small class="muted">仅作内部辅助研究，不是真实行情</small></span></label><button type="submit" class="btn primary">开始研制 ${icon('arrow',15)}</button><p class="muted small mt">使用已保存的资料 R${s.revision}；专业参数缺失会补问，其余按本次勾选项确认。</p></form>`:notice('FR 可以查看技术资料和保存现场记录，方案研制与技术确认由 SR 负责。','info')}</div></section>${runs.length?`<section class="card"><div class="card-head"><h2>本季研究记录</h2></div>${runs.map(r=>`<a class="task-row" href="#run/${r.id}"><div class="grow"><div class="task-title">${esc(r.goal.slice(0,32))}</div><div class="task-meta">${date(r.created_at)}</div></div>${badge(r)}</a>`).join('')}</section>`:''}${s.plans.length?`<section class="card"><div class="card-head"><h2>已确认技术版本</h2></div><div class="card-pad">${s.plans.map(p=>`<p class="mb"><strong>P${p.version}</strong> · ${date(p.created_at)}<br><small class="muted">技术已确认 / 商务未接入 / 执行未放行</small></p>`).join('')}</div></section>`:''}</div></div>${reportSection(s)}<section class="card mt"><div class="card-head"><h2>同地块历史与获准参考</h2><span class="muted">优先同地块 · 其他案例不等于专业适配</span></div>${historyCards(s.history)}</section>`;
}
function progress(run){const partial=!run.draft&&run.state?.partial_candidate;return `<section class="card"><div class="card-head"><h2>${run.status==='waiting_input'?'需要补充确认资料':run.status==='failed'?'研究需要处理':'研究进度'}</h2>${badge(run)}</div><div class="card-pad">${run.error?notice(run.error,'error'):notice('研究状态与动作已持久保存。关闭页面不会取消任务；关键用量需要 SR 明确确认。','info')}${partial?`<div class="mt">${notice('已保留暂存候选，仅供处理研究缺口，不可技术确认；补齐问题后明确恢复或新建研究。')}${(run.state._review_feedback||[]).map(issue=>`<p class="small mt"><strong>${esc(issue.code)}</strong> · ${esc(issue.field)}<br>关闭条件：${esc(issue.close_condition||'由 SR 核实缺口')}</p>`).join('')}</div>`:''}<div class="timeline">${run.actions.map(a=>`<div class="timeline-item"><span class="timeline-dot">✓</span><div><h3>${esc(a.purpose)}</h3><p>${date(a.created_at)} · ${esc(a.type)}</p></div></div>`).join('')||'<p class="muted">等待 Worker 领取任务…</p>'}</div>${run.status==='waiting_input'?`<div class="mt">${button('回答待确认问题','tab','discussion','primary')}</div>`:''}</div></section>`;}
function evidenceCards(items,currentStatuses=[]){
  const currentById=new Map(currentStatuses.map(item=>[item.id,item]));
  const references=[...items,...currentStatuses.filter(item=>!items.some(e=>e.id===item.id)).map(item=>item.document||{id:item.id,title:'资料引用',body:'原引用正文未保存在此快照中。'})];
  const errors={EVIDENCE_REVOKED:'已撤回',EVIDENCE_EXPIRED:'已失效或尚未生效',EVIDENCE_NOT_FOUND:'当前找不到资料',EVIDENCE_NOT_PUBLISHED:'未发布或已被新版替代',EVIDENCE_NOT_APPLICABLE:'当前条件不适用',EVIDENCE_SCOPE:'当前无资料范围权限'};
  return references.map(e=>{
    const current=currentById.get(e.id),doc=current?.document;
    const label=current?(errors[current.error]||(current.status==='published'?'当前可用':documentStatuses[current.status]||current.status||'状态未知')):'';
    return `<div class="evidence"><h3>${esc(e.title)}</h3><p>${esc(e.body)}</p><small>${esc(e.source)} / ${esc(e.locator)} / ${esc(e.version)} · ${esc(e.id)}</small>${current?`<div class="mt"><span class="badge ${current.error?'red':'green'}">当前状态：${esc(label)}</span>${doc?.revoked_at?`<p>撤回于 <time datetime="${esc(doc.revoked_at)}" title="${esc(doc.revoked_at)}">${date(doc.revoked_at)}</time> · 撤回人 ${esc(doc.revoked_by||'未记录')}</p>`:''}${doc?.publication_note?`<p>${current.status==='revoked'?'撤回原因':'当前审核说明'}：${esc(doc.publication_note)}</p>`:''}</div>`:''}</div>`;
  }).join('');
}
function marketHTML(m){return `${notice(m.limitation)}<div class="table-wrap mt"><table><thead><tr><th>演示收购方</th><th>报价 元/kg</th><th>运输 / 扣价</th><th>净到手价</th></tr></thead><tbody>${m.eligible.map(q=>`<tr><td><strong>${esc(q.buyer)}</strong><div class="muted small">含水率 14% · ${date(q.as_of)}</div></td><td>${esc(q.price)}</td><td>${esc(q.freight)} / ${esc(q.deduction)}</td><td><strong>${esc(q.net_cny_per_kg)}</strong>${m.best_quote_id===q.id?' <span class="badge green">已知口径较优</span>':''}</td></tr>`).join('')}</tbody></table></div>${!m.eligible.length?empty('没有符合比较条件的报价，保留受限结果。'):''}<div class="mt small muted">${m.excluded.map(q=>`<p>未纳入 ${esc(q.buyer)}：${esc(q.reason)}</p>`).join('')}<p class="mt">仓储：${m.storage==='no'?'无可用仓储，不提供储粮选项':m.storage==='yes'?'已确认可用；仍需评估仓储与风险成本':'未知，不默认推荐储粮'}。报价为合成演示，不代表当前市场。</p></div>`;}
function materialsTable(run){
  const p=run.draft,c=p.calculation,f=run.snapshot.facts;
  const lines=[{id:'main',product_name:p.product.name,rate_kg_per_mu:f.rate_kg_per_mu,rate_source:f.rate_source,price_cny_per_kg:p.product.price,total_kg:c.total_kg,cost_cny:c.cost_cny},...(c.additional_lines||[])];
  return `<div class="table-wrap materials-table mt"><table><caption>公司合作产品逐行核算</caption><thead><tr><th>产品</th><th>用量 kg/亩</th><th>数量 kg</th><th>单价 元/kg</th><th>小计 元</th><th>确认依据</th></tr></thead><tbody>${lines.map((line,i)=>`<tr><td>${esc(line.product_name)}<div class="muted small">${i===0?'主产品':'追加产品'}</div></td><td>${num(line.rate_kg_per_mu)}</td><td>${num(line.total_kg)}</td><td>${num(line.price_cny_per_kg)}</td><td>${num(line.cost_cny)}</td><td>${esc(line.rate_source)}${i>0?`<div class="muted small mt">目录 v${esc(run.snapshot.additional_products?.[line.id]?.revision??'未记录')} · 本季 R${esc(line.input_refs?.fact_revision??run.snapshot.revision)}</div>`:''}</td></tr>`).join('')}</tbody><tfoot><tr><th colspan="4">公司合作合计 · 元</th><td colspan="2"><strong>${num(c.cooperation_total_cny??c.cost_cny)}</strong></td></tr></tfoot></table></div><p class="small muted mt">合作合计仅含上述公司产品；客户完整成本中的未知费用仍需另行核实。</p>`;
}
function planTab(run){
  if(!run.draft)return progress(run);
  const p=run.draft,c=p.calculation,f=run.snapshot.facts,references=referencedEvidence(run);
  return `<div class="detail-grid"><div class="stack"><section class="card"><div class="card-head"><h2>本季主建议 <span class="muted">/ C${run.candidate_version}</span></h2><span class="badge ${run.formal_id?'green':''}">${run.formal_id?'已技术确认 P'+run.formal.version:'可编辑草稿'}</span></div><div class="card-pad">${notice(generationLabel(p)+'；生成内容仍需 SR 复核，不代表已专业验证。','info')}<p class="plan-summary">${esc(p.summary)}</p>${structuredPlan(p)}<p class="muted small mt">品种与客户要求：${esc(p.variety)}</p><div class="metrics"><div class="metric"><div class="label">本季面积</div><div class="value">${num(f.area_mu)} <span class="unit">亩</span></div></div><div class="metric"><div class="label">主产品数量</div><div class="value">${num(c.total_kg)} <span class="unit">kg</span></div></div><div class="metric"><div class="label">公司合作合计</div><div class="value">${num(c.cooperation_total_cny??c.cost_cny)} <span class="unit">元</span></div></div></div><p class="muted small">${esc(p.product.name)} / ${num(f.rate_kg_per_mu)} kg/亩 × ${num(f.area_mu)} 亩 × ${p.product.price} 元/kg</p>${materialsTable(run)}<div class="divider"></div><h3>阶段安排 · 待 SR 专业复核</h3><div class="stages">${p.stages.map((s,i)=>`<div class="stage"><div class="stage-number">0${i+1}</div><div><h3>${esc(s.name)} <small class="muted">/ ${esc(s.timing)}</small></h3><p>${esc(s.action)}</p></div></div>`).join('')}</div></div></section>${p.market?`<section class="card"><div class="card-head"><h2>内部市场辅助研究</h2><span class="badge amber">演示分支</span></div><div class="card-pad">${marketHTML(p.market)}</div></section>`:''}</div><div class="stack"><section class="card"><div class="card-head"><h2>依据与边界</h2></div><div class="card-pad"><span class="badge">${p.history_refs.length} 条历史参考</span><span class="badge">${Array.isArray(p.evidence_refs)?p.evidence_refs.length:references.evidence.length} 条资料引用</span><ul class="warning-list">${p.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul><div class="divider"></div><h3>确定性计算血缘</h3><p class="small muted mt pre">专业参数来源：${esc(f.rate_source)}<br>事实 R${p.input_revision} · 目录 v${p.product.revision}<br>${esc(c.formula)}<br>${esc(c.rule_version)} · ${esc(c.rounding)}<br><span class="mono">${esc(c.id)}</span></p></div></section><section class="card"><div class="card-head"><h2>本地资料引用</h2></div><div class="card-pad">${evidenceCards(references.evidence,references.statuses)}</div></section></div></div>`;
}
function questionControl(q){
  const id='answer-value-'+q.id,type=q.answer_type||'number';
  const label=({budget_cny:'公司合作预算 · 元',variety:'品种 / 客户硬要求',storage:'仓储条件',soil_ph:'土壤 pH'})[q.field]||(type==='number'?'本季用量 · kg/亩':'本次确认内容');
  const input=type==='select'?`<select id="${esc(id)}" name="value" required><option value="">请选择确认结果</option>${(q.options||[]).map(o=>`<option value="${esc(o.value)}">${esc(o.label)}</option>`).join('')}</select>`:type==='text'?`<input id="${esc(id)}" name="value" type="text" required maxlength="160">`:`<input id="${esc(id)}" name="value" type="number" min="0" ${q.field==='budget_cny'?'':q.field==='soil_ph'?'max="14"':'max="1000"'} step="${['budget_cny','soil_ph'].includes(q.field)?'0.01':'0.0001'}" required>`;
  return `<div class="field"><label for="${esc(id)}">${esc(label)}</label>${input}</div>`;
}
const discussionModes={rule_question_explanation:'规则解释',semantic_model_explanation:'模型语义解释',model_edit_preview:'模型修改预览',manual_edit_preview:'手工修改预览'};
function hasDependencyChanges(run){return !!run.runtime?.affected_modules?.length;}
function canPropose(run){return isSR()&&['completed','limited'].includes(run.status)&&!!run.draft&&!run.formal_id&&!run.formal&&!run.stale&&!hasDependencyChanges(run);}
const moduleLabels={history:'历史检索',evidence:'获准资料',quantity:'逐行数量',budget:'逐行金额与合计',market:'市场演示',candidate:'候选草稿'};
const moduleStatuses={ready:'就绪',stale:'失效',pending:'待处理',not_requested:'未请求'};
const runtimeFields={area_mu:'面积',rate_kg_per_mu:'确认用量',product_id:'主产品',additional_materials:'追加产品明细',budget_cny:'合作预算',variety:'品种要求',soil_ph:'检测数据',storage:'仓储条件',note:'备注',evidence_catalog:'资料目录',product_catalog:'产品目录',report_refs:'检测来源'};
function canRecover(run,intent){
  const r=run.runtime;
  return isSR()&&!run.formal_id&&!run.formal&&!!r&&!['queued','running'].includes(run.status)&&r.remaining_refreshes>0&&r.generation<r.max_generations&&
    (intent==='refresh'?r.can_refresh===true:intent==='repair'&&['failed','limited'].includes(run.status)&&r.can_repair===true);
}
function runtimeImpact(run){
  const r=run.runtime,list=(items,labels)=>esc((items||[]).map(item=>labels[item]||item).join('、')||'无');
  return `<div class="runtime-budget"><span>当前代次 <strong>${esc(r.generation)} / ${esc(r.max_generations)}</strong></span><span>本代动作 <strong>${esc(r.actions_used)} / ${esc(r.max_actions)}</strong></span><span>剩余恢复 ${esc(r.remaining_refreshes)} 次</span></div><dl class="runtime-impact"><div><dt>当前确认输入</dt><dd>R${esc(r.current_input_revision)} · 本研究固定 R${esc(run.snapshot?.revision)}</dd></div><div><dt>检测到的变化</dt><dd>${list(r.changed_fields,runtimeFields)}</dd></div><div><dt>需重新处理</dt><dd>${list(r.affected_modules,moduleLabels)}</dd></div><div><dt>仍可复用</dt><dd>${list(r.reusable_modules,moduleLabels)}</dd></div></dl>${r.limitation?notice(valueText(r.limitation)):''}`;
}
function runtimePanel(run){
  const r=run.runtime;if(!r)return '';
  const preview=hasDependencyChanges(run)&&!['queued','running'].includes(run.status)&&!run.formal_id&&!run.formal;
  return `<section class="card runtime-panel"><div class="card-head"><h2>研究模块与恢复</h2><span class="muted">固定模块 · 有限恢复</span></div><div class="card-pad">${runtimeImpact(run)}${preview?`<div class="mt">${notice((r.changed_fields||[]).length?'事实或适用来源已变化。请查看影响并明确刷新；读取页面、重新审查不会更新研究输入。':'部分模块暂无可用结果。请查看影响后明确刷新，重新检查当前输入与来源。','error')}</div>`:''}${(r.remaining_refreshes<=0||r.generation>=r.max_generations)&&!run.formal_id&&!run.formal?`<div class="mt">${notice('恢复预算已用尽；如需继续，请发起新研究。')}</div>`:''}${!run.draft&&run.candidate_version?`<div class="mt">${notice(`旧草稿 C${run.candidate_version} 已保留在历史修订中。当前尚无新草稿，重新生成后需另行复核。`,'info')}</div>`:''}<div class="runtime-modules">${(r.modules||[]).map(m=>`<article class="runtime-module"><div class="row between gap8"><h3>${esc(moduleLabels[m.name]||m.name)}</h3><span class="badge ${{ready:'green',stale:'amber',pending:'blue'}[m.status]||''}">${esc(moduleStatuses[m.status]||m.status)}</span></div><p class="small mt">${m.reused?'已复用':'未复用'} · 输入 R${esc(m.input_revision??'未记录')}</p><p class="small muted">结果引用：<span class="mono">${esc(m.result_ref?valueText(m.result_ref):'暂无结果')}</span></p><p class="small muted mt">原因：${esc(m.reason||'未记录')}</p></article>`).join('')}</div>${run.formal_id||run.formal?notice('正式技术方案 P 保留原内容；调整请发起新研究。','info'):!isSR()?notice('FR 可查看研究模块与结果；恢复需由负责 SR 提交。','info'):''}${canRecover(run,'refresh')||canRecover(run,'repair')?`<div class="form-actions">${canRecover(run,'refresh')?button('查看影响并刷新','refresh-run',run.id,'primary'):''}${canRecover(run,'repair')?button('有限补查与重建','repair-run',run.id):''}</div>`:''}</div></section>`;
}
function recoveryDialog(intent){
  const run=state.run;if(!run||!canRecover(run,intent))return;
  const r=run.runtime,repair=intent==='repair';
  modalOpen(repair?'有限补查与重建 · 确认影响':'刷新研究 · 确认影响',`<form id="recovery-form">${runtimeImpact(run)}<p class="small mt">${repair?'重新检查获准资料并重建候选；其他模块只在仍适用时复用。':'采用当前已确认事实与适用来源，按模块检查后复用有效结果，并重新生成候选。'}</p><div class="mt">${notice(`${run.candidate_version?'旧草稿 C'+run.candidate_version+' 保留在历史修订中。':'已保存的研究轨迹保留。'}提交后当前草稿暂时清空，等待新 C 生成；旧的未回答问题会被替换。恢复会使用一个剩余代次，新草稿仍需 SR 复核。`,'info')}</div><p class="small muted mt">研究 ${esc(run.id)} · 修订 ${esc(run.revision)} · 当前输入 R${esc(r.current_input_revision)}<br><span class="mono review-hash">上下文 ${esc(r.context_hash)}</span></p><div class="form-actions"><button type="button" class="btn" data-action="close-modal">取消</button><button class="btn primary" type="submit">${repair?'确认有限补查':'确认刷新并重建'}</button></div></form>`);
  Object.assign($('form',modal).dataset,{inputRevision:String(r.current_input_revision),contextHash:r.context_hash,intent});
}
function proposalText(text){return `<p class="pre">${esc(text?.summary)}</p>${(text?.stages||[]).map((stage,i)=>`<div class="proposal-stage"><strong>${i+1}. ${esc(stage.name)}</strong><p>${esc(stage.timing)}</p><p class="pre">${esc(stage.action)}</p></div>`).join('')}`;}
function proposalCard(proposal,run){
  const binding=proposal.binding||{},content=proposal.content||{},review=content.preview_review;
  const applicable=canPropose(run)&&proposal.run_id===run.id&&proposal.status==='pending'&&binding.run_revision===run.revision&&binding.input_revision===run.snapshot.revision&&binding.candidate_version===run.candidate_version&&!binding.formal_id;
  return `<article class="proposal-card"><div class="row between gap8"><h3>修改预览</h3><span class="badge ${proposal.status==='applied'?'green':'amber'}">${proposal.status==='applied'?'已保存为 C'+esc(proposal.applied_version):applicable?'待 SR 确认保存':'仅供查看 · 绑定已变化或无修改权限'}</span></div><p class="small muted mt">原研究 ${esc(proposal.run_id)} · 输入 R${esc(binding.input_revision)} · 候选 C${esc(binding.candidate_version)} · 研究修订 ${esc(binding.run_revision)}<br><span class="mono review-hash">内容 ${esc(binding.content_hash)}</span></p><div class="proposal-diff"><section><h4>修改前</h4>${proposalText(content.before)}</section><section><h4>修改后</h4>${proposalText(content.after)}</section></div><p class="small mt">SR 明确理由：${esc(content.reason||'未记录')}</p><h4 class="mt">影响范围</h4><ul class="warning-list">${(content.impact||[]).map(item=>`<li>${esc(valueText(item))}</li>`).join('')||'<li>未记录</li>'}</ul><div class="proposal-review"><h4>预览质量门槛</h4>${notice(review?review.can_confirm?'预览检查未发现阻塞项；保存时重新评价，技术确认仍需 SR 判断。':'预览存在质量门槛；保存后仍需处理，不能直接技术确认。':'未记录预览评价，保存时需要重新评价。',review?.can_confirm?'info':'')}${(review?.issues||[]).map(issue=>`<p class="small mt"><strong>${esc(issue.message)}</strong><br>关闭条件：${esc(issue.close_condition||'由 SR 复核')}</p>`).join('')}</div>${applicable?`<form id="apply-proposal-form-${esc(proposal.id)}" data-run-id="${esc(proposal.run_id)}" data-proposal-id="${esc(proposal.id)}" data-run-revision="${esc(binding.run_revision)}" data-route-generation="${routeGeneration}"><div class="form-actions"><button class="btn primary" type="submit">确认保存为新草稿</button></div></form>`:proposal.status==='pending'?notice('此预览不能应用到当前草稿。请基于当前对象和版本重新提出修改。'):''}</article>`;
}
function discussionTab(run){
  const open=run.questions.filter(q=>q.status==='open');
  return `<div class="stack">${open.length?notice('还需确认 '+open.length+' 个问题；每项保存后保留其余问题，全部确认后继续研究。','info'):''}${open.map(q=>`<section class="question"><h3>${icon('clock',17)} 资料待确认</h3><p>${esc(q.prompt)}</p>${q.purpose?`<p class="small mt">用途：${esc(q.purpose)}</p>`:''}${q.impact?`<p class="small muted mt">影响：${esc(q.impact)}</p>`:''}${isSR()?`<form id="answer-form-${esc(q.id)}" data-run-id="${esc(run.id)}" data-question="${esc(q.id)}" data-revision="${esc(q.revision)}" data-input-revision="${esc(run.snapshot.revision)}"><div class="form-grid">${questionControl(q)}<div class="field"><label for="answer-source-${esc(q.id)}">依据与确认来源</label><input id="answer-source-${esc(q.id)}" name="source" required maxlength="1000" placeholder="说明适用规则或确认依据"></div></div><div class="form-actions"><button class="btn primary" type="submit">保存本项确认</button></div></form>`:notice('等待负责 SR 回答。FR 无权确认研究输入。')}</section>`).join('')}<section class="card"><div class="card-head"><h2>方案讨论</h2><span class="muted">固定当前对象与版本 · 询问不修改方案</span></div><div class="card-pad">${notice(state.data.mode==='llm'?'模型基于本研究固定上下文解释；询问不改草稿。SR 可提出修改，审阅预览后另行确认保存。':'规则演示模式提供快照解释；SR 可手工提出修改预览，审阅后另行确认保存。','info')}${run.draft?`<p class="small mt">当前主产品小计 ${num(run.draft.calculation?.cost_cny)} 元 · 公司合作合计 ${num(run.draft.calculation?.cooperation_total_cny??run.draft.calculation?.cost_cny)} 元；追加产品以逐行核算表为准。</p>`:''}${(run.messages||[]).map(m=>`<article class="message"><div class="question-text">${esc(m.message)}</div><div class="answer-text">${esc(m.answer)}</div><small class="muted">${date(m.created_at)} · ${esc(answerCategories[m.category]||'快照解释')} · ${esc(discussionModes[m.mode]||m.mode||'旧消息：解释方式未记录')}<br>版本 ${esc(JSON.stringify(m.versions||{}))} · 引用 ${esc((m.refs||[]).join(' / '))}</small></article>`).join('')}<form id="discuss-form" data-run-id="${esc(run.id)}" data-run-revision="${esc(run.revision??0)}" data-route-generation="${routeGeneration}" class="mt"><div class="field"><label for="discussion-message">询问当前方案或核算依据</label><textarea id="discussion-message" name="message" required maxlength="4000" placeholder="例如：为什么调整了本季方案？合作合计怎么算的？"></textarea></div><div class="form-actions"><button class="btn" type="submit">${state.data.mode==='llm'?'查看模型解释':'查看规则解释'} ${icon('arrow',14)}</button>${canPropose(run)?`<button type="button" class="btn" data-action="propose-edit" data-id="${esc(run.id)}">提出修改</button>`:''}</div></form>${(run.proposals||[]).map(proposal=>proposalCard(proposal,run)).join('')}</div></section>${run.questions.some(q=>q.status==='answered')?`<section class="card"><div class="card-head"><h2>已确认的问题</h2></div><div class="card-pad">${run.questions.filter(q=>q.status==='answered').map(q=>`<p class="small">${icon('check',14)} ${esc(q.prompt)}：${esc(q.answer.value)} · ${esc(q.answer.source)}</p>`).join('')}</div></section>`:''}</div>`;
}
function auditTab(run){return `<div class="stack">${run.formal?`<section class="card card-pad"><h2>技术方案 P${run.formal.version}</h2><p class="muted small mt">输入 R${run.formal.input_revision} · ${date(run.formal.created_at)} · 固定内容保留</p><div class="status-triplet"><span>技术：已确认</span><span>商务：未接入</span><span>执行：未放行</span></div></section>`:''}<section class="card"><div class="card-head"><h2>草稿修订与明确修改理由</h2><span class="muted">理由缺失时保留未记录</span></div>${run.versions.length?run.versions.map(v=>`<details class="version-box"><summary>C${v.version} · ${esc(v.reason)} <small class="muted">${date(v.created_at)}</small></summary><p>${esc(v.content.summary)}</p><p>阶段：${esc(v.content.stages.map(s=>s.name).join(' / '))}</p><p>主产品小计 ${esc(v.content.calculation.cost_cny)} 元 · 公司合作合计 ${esc(v.content.calculation.cooperation_total_cny??v.content.calculation.cost_cny)} 元 · 事实 R${v.content.input_revision}</p></details>`).join(''):empty('草稿尚未形成')}</section><section class="card"><div class="card-head"><h2>确认与业务记录</h2></div><div class="card-pad"><div class="timeline">${run.audit.map(a=>`<div class="timeline-item"><span class="timeline-dot">•</span><div><h3>${esc(({research_started:'主动发起研制',answer_saved:'确认回答并登记恢复',draft_edited:'明确修改草稿',technical_confirmed:'SR 技术确认',research_cancelled:'取消研究'})[a.event]||a.event)}</h3><p>${date(a.created_at)} · ${esc(JSON.stringify(a.payload))}</p></div></div>`).join('')}</div></div></section></div>`;}
function runPage(){
  const run=state.run;if(!run)return empty('正在读取研究任务…');
  const s=run.snapshot;
  const editable=canPropose(run);
  const confirmable=editable&&run.review?.can_confirm===true&&run.review.candidate_version===run.candidate_version;
  const tabs=[['facts','本季资料'],['history','历史参考'],['plan','方案与依据'],['discussion','对话与待确认'],['audit','确认与变更记录']];
  const body=state.tab==='facts'?`<section class="card"><div class="card-head"><h2>本次研究固定输入 R${s.revision}</h2><a href="#season/${run.season_id}" class="text-btn">查看 / 修改当前资料 →</a></div><div class="table-wrap"><table><tbody>${Object.entries({'面积 · 亩':s.facts.area_mu,'公司合作预算 · 元':s.facts.budget_cny,'品种要求':s.facts.variety,'土壤 pH':s.facts.soil_ph,'主产品用量 · kg/亩':s.facts.rate_kg_per_mu,'用量依据':s.facts.rate_source,'产品目录':s.product.name+' / v'+s.product.revision,'追加产品明细':valueText((s.facts.additional_materials||[]).map(m=>({...m,product:s.additional_products?.[m.id]?.name||m.product_id}))), '客户原始备注':s.facts.note}).map(([k,v])=>`<tr><th>${k}</th><td class="pre">${esc(v??'未知')}</td></tr>`).join('')}</tbody></table></div></section><section class="card mt"><div class="card-head"><h2>SR 已确认检测来源 · 固定本次输入</h2></div><div class="card-pad">${reportSourceCards(s.report_refs||[])}</div></section>`:state.tab==='history'?`<section class="card"><div class="card-head"><h2>本次研制引用的历史</h2><span class="muted">历史身份与本季事实分开</span></div>${historyCards(run.state.history||[])}</section>`:state.tab==='discussion'?discussionTab(run):state.tab==='audit'?auditTab(run):planTab(run);
  return `<a href="#research" class="back">${icon('back',14)} 返回研制列表</a><div class="page-heading"><div><div class="eyebrow">PLAN RESEARCH / ${run.mode==='llm'?'MODEL PLANNER':'RULE DEMO'}</div><div class="title-row"><h1>本季技术方案</h1>${badge(run)}</div>${scope(s)}</div><div class="row gap8">${run.draft?button(icon('download',14)+' 导出草稿','export',run.id):''}${editable?button(icon('edit',14)+' 编辑草稿','edit-draft',run.id):''}${confirmable?button('SR 技术确认','confirm',run.id,'primary'):''}${editable?button('重新审查','review',run.id):''}${isSR()&&['queued','running','waiting_input'].includes(run.status)?button('取消研制','cancel',run.id,'danger'):''}${isSR()&&['completed','failed','limited','cancelled'].includes(run.status)?button('重新研制','restart',run.id):''}</div></div>${run.stale?notice(`当前资料已更新至 R${run.current_fact_revision}。此研究仍引用 R${s.revision}，不能确认旧草稿，请查看模块影响后明确刷新，或发起新研究。`,'error'):''}${run.formal?notice(`已保存技术方案 P${run.formal.version}。商务系统尚未接入、执行未放行；修改需重新研制并再次技术确认。`,'info'):''}${runtimePanel(run)}${reviewPanel(run)}<div class="tabs" role="tablist" aria-label="任务详情">${tabs.map(([key,label])=>`<button class="tab ${state.tab===key?'active':''}" role="tab" aria-selected="${state.tab===key}" data-action="tab" data-id="${key}">${label}${key==='discussion'&&run.questions.some(q=>q.status==='open')?' · '+run.questions.filter(q=>q.status==='open').length:''}</button>`).join('')}</div>${body}`;
}
function fieldPage(){return `${header('FIELD SERVICE','田间服务','SR / FR 共用现场实际。临时巡田也可以直接记录。',customerFilter())}<div class="detail-grid"><section class="card"><div class="card-head"><h2>现场记录</h2><span class="muted">${filtered(state.records).length} 条实际记录</span></div>${filtered(state.records).length?filtered(state.records).map(r=>`<article class="record"><div class="row between"><h3>${esc(r.plot_name)} <small class="muted">/ ${esc(r.customer_name)}</small></h3><span class="badge ${r.kind==='incident'?'amber':'green'}">${kinds[r.kind]}</span></div><p>${esc(r.notes)}</p><div class="record-meta">实际时间 ${date(r.observed_at)} · ${esc(r.actor_name)} / ${r.actor_role}<br>录入时间 ${date(r.created_at)} · ${r.plan_id?'关联技术确认版本，非执行批准':'未关联技术方案的临时现场记录'}</div></article>`).join(''):empty('还没有现场记录，可从右侧新增一次巡田。')}</section><section class="card"><div class="card-head"><h2>记录现场实际</h2>${icon('edit',18)}</div><div class="card-pad"><form id="record-form" class="stack"><div class="field"><label for="record-season">客户 / 地块 / 种植季</label><select id="record-season" name="season_id" required>${filtered(state.data.seasons).map(s=>`<option value="${s.id}">${esc(s.customer_name)} / ${esc(s.plot_name)} / ${s.year}</option>`).join('')}</select></div><div class="field"><label for="record-kind">记录类型</label><select id="record-kind" name="kind">${Object.entries(kinds).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></div><div class="field"><label for="observed">实际到场 / 作业时间</label><input id="observed" name="observed_at" type="datetime-local" step="1" value="${localNow()}" max="${localNow()}" required></div><div class="field"><label for="record-plan">明确关联的技术版本 · 可选</label><select id="record-plan" name="plan_id">${planOptions(filtered(state.data.seasons)[0]?.id)}</select><small>只关联实际使用的版本；不根据当前版本自动推断。</small></div><div class="field"><label for="record-notes">观察、实际操作或异常</label><textarea id="record-notes" name="notes" rows="5" required maxlength="4000" placeholder="记录真实发生的情况；计划安排不能代替实际。"></textarea></div>${notice('本页保存现场实际，不批准采购、农事执行或正式方案变更。')}<button class="btn primary" type="submit">保存现场记录</button></form></div></section></div>`;}
function localNow(){const d=new Date();return new Date(d-d.getTimezoneOffset()*60000).toISOString().slice(0,19);}
function knowledgePage(){return `<div class="qa-layout"><div class="qa-heading"><div class="customer-symbol">${icon('book',27)}</div><div class="eyebrow">KNOWLEDGE, WITH A SOURCE</div><h1>先找到依据，再形成判断。</h1><p>查询获准共享资料，客户资料按责任范围单独查看。</p></div><section class="card card-pad"><form id="knowledge-form"><div class="field"><label for="knowledge-question">想了解什么？</label><textarea id="knowledge-question" name="message" required maxlength="4000" rows="3" placeholder="例如：历史方案的用量可以直接沿用到本季吗？"></textarea></div><div class="row between mt"><span class="muted small">本地关键词检索 · 非向量 RAG / 模型问答</span><button class="btn primary" type="submit">检索资料 ${icon('arrow',15)}</button></div></form></section><div class="suggestions">${['历史用量可以直接沿用吗？','合作预算和完整成本有什么区别？','含水率和运输费用如何影响报价？'].map(q=>`<button class="suggestion" data-action="ask-knowledge" data-id="${esc(q)}">${esc(q)}</button>`).join('')}</div>${state.knowledge?`<section class="card card-pad"><h2>资料中的解释</h2><div class="knowledge-answer mt">${esc(state.knowledge.answer)}</div><div class="divider"></div><h3>引用来源</h3>${evidenceCards(state.knowledge.evidence)}${notice(state.knowledge.limitation)}</section>`:notice('检索仅使用当前已发布且有效的共享资料。演示资料保留演示身份，超出资料范围的判断需专业复核。','info')}${documentList()}</div>`;}
function render(){if(!state.data)return renderLogin();const views={todos:overview,customers,research:researchList,season:seasonPage,run:runPage,field:fieldPage,knowledge:knowledgePage};shell((views[state.route]||overview)());}
async function loadRoute(){
  if(!state.data)return;
  const generation=++routeGeneration;
  const [route='todos',id]=location.hash.slice(1).split('/');
  if((route!==state.route||id!==state.id)&&modal.open)modal.close();
  state.route=route||'todos';state.id=id;
  if(route==='run'){
    if(state.run?.id!==id){state.tab='plan';state.run=null;render();}
    const run=await api('/runs/'+encodeURIComponent(id));
    if(generation!==routeGeneration||!state.data)return;
    state.run=run;
  }
  if(route==='season'){
    if(state.season?.id!==id){state.season=null;render();}
    const season=await api('/seasons/'+encodeURIComponent(id));
    if(generation!==routeGeneration||!state.data)return;
    state.season=season;
  }
  if(route==='field'){
    const records=await api('/field-records');
    if(generation!==routeGeneration||!state.data)return;
    state.records=records;
  }
  if(route==='knowledge'){
    const documents=await api('/documents');
    if(generation!==routeGeneration||!state.data)return;
    state.documents=documents;
  }
  if(generation!==routeGeneration||!state.data)return;
  render();window.scrollTo(0,0);
}
async function refresh(){state.data=await api('/bootstrap');await loadRoute();}
function newCustomer(){modalOpen('新建客户与地块',`<form id="customer-form"><div class="form-grid"><div class="field"><label for="customer-name">客户名称</label><input id="customer-name" name="name" required maxlength="120"></div><div class="field"><label for="plot-name">地块名称</label><input id="plot-name" name="plot_name" required maxlength="120"></div><div class="field"><label for="region">地区</label><input id="region" name="region" required maxlength="120" value="吉林"></div><div class="field"><label for="year">种植年份</label><input id="year" name="year" type="number" min="2000" max="2100" value="2026" required></div><div class="field"><label for="crop">作物</label><select id="crop" name="crop"><option>玉米</option><option>水稻</option><option>小麦</option></select></div><div class="field"><label for="new-area">本季面积 · 亩</label><input id="new-area" name="area_mu" type="number" min="0.0001" max="1000000" step="0.0001" required></div></div><p class="muted small mt">新客户由当前 SR 负责；专业参数留待本季资料确认，不会自动照搬历史。</p><div class="form-actions"><button type="button" class="btn" data-action="close-modal">取消</button><button type="submit" class="btn primary">创建并完善本季资料</button></div></form>`);}
function editDraft(){const p=state.run.draft;modalOpen('编辑草稿 · 新增修订版本',`<form id="draft-form"><div class="stack"><div class="field"><label for="draft-summary">主建议表述</label><textarea id="draft-summary" name="summary" required maxlength="4000" rows="4">${esc(p.summary)}</textarea></div>${p.stages.map((s,i)=>`<div class="form-grid"><div class="field"><label for="stage-name-${i}">阶段 ${i+1} 名称</label><input id="stage-name-${i}" name="stage_name_${i}" value="${esc(s.name)}" required maxlength="100"></div><div class="field"><label for="stage-time-${i}">安排 / 时点</label><input id="stage-time-${i}" name="stage_timing_${i}" value="${esc(s.timing)}" required maxlength="200"></div><div class="field full"><label for="stage-action-${i}">阶段措施</label><textarea id="stage-action-${i}" name="stage_action_${i}" required maxlength="1000">${esc(s.action)}</textarea></div></div>`).join('')}<div class="field"><label for="draft-reason">本次明确修改理由 · 可选</label><input id="draft-reason" name="reason" maxlength="2000" placeholder="如客户采购条件；未填写时保存“未记录”"></div>${notice('修改保留前后版本。面积、用量、产品或预算变更，请先更新本季资料并重新研制；关键数字由工具核算。')}<div class="form-actions"><button type="button" class="btn" data-action="close-modal">取消</button><button type="submit" class="btn primary">保存新草稿版本</button></div></div></form>`);}
function draftMarkdown(r){const p=r.draft,s=r.snapshot;return `# AgriPlan ${s.year} 本季技术方案\n\n${s.customer_name} / ${s.plot_name} / ${s.crop}\n\n生成来源：${generationLabel(p)}；草稿 C${r.candidate_version}；输入 R${s.revision}；${r.formal?'技术确认 P'+r.formal.version:'未技术确认'}\n\n> 演示资料与参数，不用于生产处方。技术确认不等于商务批准或执行放行。\n\n${p.summary}\n\n${structuredExport(r)}\n\n## 确认输入与核算\n\n- 面积 ${s.facts.area_mu} 亩\n- 主产品用量 ${s.facts.rate_kg_per_mu} kg/亩\n- 依据 ${s.facts.rate_source}\n- 产品 ${p.product.name} / 目录 v${p.product.revision}\n- 单价 ${p.product.price} CNY/kg\n- 主产品数量 ${p.calculation.total_kg} kg\n- 主产品小计 ${p.calculation.cost_cny} CNY
${(p.calculation.additional_lines||[]).map(line=>`- 追加产品 ${line.product_name} / 用量 ${line.rate_kg_per_mu} kg/亩 / 数量 ${line.total_kg} kg / 单价 ${line.price_cny_per_kg} CNY/kg / 小计 ${line.cost_cny} CNY / 依据 ${line.rate_source} / 输入引用 ${valueText(line.input_refs||{})}`).join('\n')}
- 公司合作合计 ${p.calculation.cooperation_total_cny??p.calculation.cost_cny} CNY\n- 公式 ${p.calculation.formula}\n- 规则 ${p.calculation.rule_version} / ${p.calculation.rounding}\n- 计算引用 ${p.calculation.id}\n\n## 阶段安排\n\n${p.stages.map(s=>`### ${s.name}\n\n${s.timing}\n\n${s.action}`).join('\n\n')}\n\n## 限制\n\n${p.warnings.map(w=>'- '+w).join('\n')}\n\n## 历史与依据\n\n${(r.state.history||[]).map(h=>`- ${h.year} ${h.version} / ${h.relation} / ${h.reference_id}；${h.limitations.join('；')}`).join('\n')}\n\n${referencedEvidence(r).evidence.map(e=>`- ${e.id} ${e.title} / ${e.source} / ${e.locator} / ${e.version}`).join('\n')}\n`;}
function proposeEdit(){
  const run=state.run;if(!canPropose(run))return;
  const p=run.draft,model=state.data.mode==='llm';
  modalOpen('提出修改 · 先预览再保存',`<form id="proposal-form"><div class="stack">${notice(`绑定研究 ${run.id} · 输入 R${run.snapshot.revision} · 草稿 C${run.candidate_version}。只修改摘要和阶段文字；事实、核算、产品与引用需通过本季资料和重新研制处理。`,'info')}<div class="field"><label for="proposal-message">明确修改请求</label><textarea id="proposal-message" name="message" required maxlength="4000" placeholder="说明希望怎样调整本草稿的文字"></textarea></div>${model?`<div class="field"><label for="proposal-edit-mode">预览方式</label><select id="proposal-edit-mode" name="edit_mode"><option value="model">由模型起草修改</option><option value="manual">手工明确修改</option></select></div>`:'<input type="hidden" name="edit_mode" value="manual">'}<fieldset class="form-fieldset proposal-manual" data-manual-edit ${model?'hidden disabled':''}><legend>手工修改文字</legend><div class="stack mt"><div class="field"><label for="proposal-summary">修改后的主建议表述</label><textarea id="proposal-summary" name="summary" required maxlength="4000" rows="4">${esc(p.summary)}</textarea></div>${p.stages.map((s,i)=>`<div class="form-grid"><div class="field"><label for="proposal-name-${i}">阶段 ${i+1} 名称</label><input id="proposal-name-${i}" name="stage_name_${i}" value="${esc(s.name)}" required maxlength="100"></div><div class="field"><label for="proposal-time-${i}">安排 / 时点</label><input id="proposal-time-${i}" name="stage_timing_${i}" value="${esc(s.timing)}" required maxlength="200"></div><div class="field full"><label for="proposal-action-${i}">阶段措施</label><textarea id="proposal-action-${i}" name="stage_action_${i}" required maxlength="1000">${esc(s.action)}</textarea></div></div>`).join('')}</div></fieldset><div class="field"><label for="proposal-reason">SR 明确修改理由 · 可选</label><input id="proposal-reason" name="reason" maxlength="2000" placeholder="仅记录此处明确填写的理由；留空保存“未记录”"></div>${notice(model?'模型只提供文字建议，需审阅差异后确认保存；输入不足时需先补充资料。':'规则演示模式不推测文字修改，请手工填写完整摘要和阶段。')}<div class="form-actions"><button type="button" class="btn" data-action="close-modal">取消</button><button type="submit" class="btn primary">生成修改预览</button></div></div></form>`);
}
function currentDiscussion(runId,generation){return generation===routeGeneration&&state.route==='run'&&state.id===runId&&state.run?.id===runId;}
function currentReportSeason(id,generation){return generation===routeGeneration&&state.route==='season'&&state.id===id&&state.season?.id===id;}
async function updateReports(id,generation,form){
  if(!currentReportSeason(id,generation))return;
  if(form&&$('form',modal)===form)modal.close();
  await loadRoute();
}
async function uploadReportFile(form){
  const file=form.elements.report_file?.files?.[0],extension=file?.name.split('.').pop().toLowerCase();
  const mime={txt:'text/plain',md:'text/plain',pdf:'application/pdf',png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg'}[extension];
  if(!file||!file.size||file.size>8*1024*1024)throw new Error('请选择非空且最多8MiB的原件');
  if(!mime)throw new Error('仅支持txt、md、PDF、PNG或JPEG原件');
  const result=await fetch('/api/seasons/'+encodeURIComponent(form.dataset.seasonId)+'/reports?filename='+encodeURIComponent(file.name),
    {method:'POST',credentials:'same-origin',headers:{'Content-Type':mime},body:file});
  const body=await result.json().catch(()=>({detail:'服务器未返回报告结果'}));
  if(!result.ok)throw new Error(typeof body.detail==='string'?body.detail:'报告原件保存失败，请核对格式和访问权限');
  await updateReports(form.dataset.seasonId,Number(form.dataset.routeGeneration));
}
function reportDialog(id,confirming){
  const season=state.season,report=season?.reports?.find(item=>item.id===id);
  if(!isSR()||!report||report.status==='confirmed'||report.original_status!=='available')return;
  const candidate=report.current_extraction?.candidate;
  if(confirming&&!report.can_confirm)return;
  const content=confirming?`${reportCandidate(candidate)}${notice('确认仅将本候选pH写入新事实R；旧研究将显示输入过期，旧正式方案不会被改写。')}<div class="field"><label for="report-confirm-source">SR 对照原件核对说明</label><input id="report-confirm-source" name="source" required maxlength="1000" placeholder="说明已核对原件、地块、采样日期、单位与值"></div>`:
    `${notice('人工转录仅保存新候选。请对照原件填写，保留原单位、页码和摘录；缺项或不符仍不能确认。')}<div class="form-grid"><div class="field"><label for="report-raw">原检测值</label><input id="report-raw" name="raw_value" required maxlength="100" value="${esc(candidate?.raw_value||'')}"></div><div class="field"><label for="report-unit">原单位</label><input id="report-unit" name="original_unit" maxlength="40" value="${esc(candidate?.original_unit||'')}" placeholder="pH / 无量纲；按原件填写"></div><div class="field full"><label for="report-plot">原件地块名称或ID</label><input id="report-plot" name="plot_name" maxlength="160" value="${esc(candidate?.plot_name||'')}"></div><div class="field"><label for="report-sampled">采样日期</label><input id="report-sampled" name="sampled_on" type="date" value="${esc(candidate?.sampled_on||'')}"></div><div class="field"><label for="report-page">原件页码</label><input id="report-page" name="page" type="number" min="1" max="1000" required value="${esc(candidate?.page||1)}"></div><div class="field full"><label for="report-locator">表格 / 行定位</label><input id="report-locator" name="locator" required maxlength="500" value="${esc(candidate?.locator||'')}"></div><div class="field full"><label for="report-excerpt">原件摘录</label><textarea id="report-excerpt" name="excerpt" required maxlength="2000">${esc(candidate?.excerpt||'')}</textarea></div></div>`;
  modalOpen(confirming?'核对报告并确认新事实':'人工转录 · 保存新候选',`<form id="${confirming?'report-confirm-form':'report-manual-form'}"><div class="stack"><p>${esc(report.filename)} · 本季R${esc(season.revision)} · 报告修订${esc(report.revision)}</p>${reportOriginal(id)}${content}<div class="form-actions"><button type="button" class="btn" data-action="close-modal">取消</button><button type="submit" class="btn primary">${confirming?'确认并保存新事实版本':'保存人工候选'}</button></div></div></form>`);
  Object.assign($('form',modal).dataset,{reportId:id,reportRevision:String(report.revision),seasonId:season.id,seasonRevision:String(season.revision),extractionId:report.current_extraction?.id||'',routeGeneration:String(routeGeneration)});
}
async function updateDiscussion(runId,generation,form){
  if(!currentDiscussion(runId,generation))return;
  if(form&&$('form',modal)===form)modal.close();
  state.tab='discussion';await loadRoute();
}
function exportDraft(){const r=state.run,s=r.snapshot,text=draftMarkdown(r);
  const url=URL.createObjectURL(new Blob([text],{type:'text/markdown;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=`AgriPlan_${s.plot_name}_${s.year}_C${r.candidate_version}.md`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
document.addEventListener('click',async event=>{
  const target=event.target.closest('[data-action]');if(!target)return;
  const {action,id}=target.dataset;
  try{
    if(action==='logout'){await api('/logout',{method:'POST'});routeGeneration++;pendingCreates.clear();state.data=null;state.customer='';state.run=null;state.season=null;state.knowledge=null;state.documents=[];modal.close();renderLogin();}
    if(action==='add-material')addMaterialRow(target.closest('form'));
    if(action==='remove-material')removeMaterialRow(target.closest('[data-material-id]'));
    if(action==='new-customer')newCustomer();
    if(action==='new-season')newSeason();
    if(action==='new-document')documentEditor();
    if(action==='revise-document')documentEditor(state.documents.find(d=>d.id===id));
    if(action==='publish-document'||action==='revoke-document')documentTransition(id,action==='publish-document'?'publish':'revoke');
    if(action==='close-modal')modal.close();
    if(action==='tab'){state.tab=id;render();}
    if(action==='edit-draft')editDraft();
    if(action==='propose-edit'&&state.run?.id===id)proposeEdit();
    if((action==='refresh-run'||action==='repair-run')&&state.run?.id===id)recoveryDialog(action==='repair-run'?'repair':'refresh');
    if(action==='report-manual')reportDialog(id,false);
    if(action==='report-confirm')reportDialog(id,true);
    if(action==='report-extract'){
      const season=state.season,report=season?.reports?.find(item=>item.id===id),generation=routeGeneration;
      if(!isSR()||!report||report.status==='confirmed')return;
      await api('/reports/'+encodeURIComponent(id)+'/extract',{method:'POST',body:{expected_revision:report.revision}});
      await updateReports(season.id,generation);
    }
    if(action==='export')exportDraft();
    if(action==='market'){const m=await api('/seasons/'+encodeURIComponent(id)+'/market');modalOpen('市场报价比较 · 演示',marketHTML(m));}
    if(action==='confirm'){const result=await api('/runs/'+id+'/confirm',{method:'POST',body:{expected_revision:state.run.revision}});toast('已保存技术方案 P'+result.version+'；执行尚未放行');state.tab='audit';await refresh();}
    if(action==='review'){const run=state.run;if(run?.id!==id)return;await api('/runs/'+encodeURIComponent(id)+'/review',{method:'POST',body:{expected_revision:run.revision}});toast('已重新审查当前草稿');await refresh();}
    if(action==='cancel'){await api('/runs/'+id+'/cancel',{method:'POST',body:{expected_revision:state.run.revision}});toast('已取消研究，已保存资料与轨迹保留');await refresh();}
    if(action==='restart'){const run=state.run;const s=await api('/seasons/'+run.season_id);const r=await createRun({season_id:s.id,expected_revision:s.revision,goal:run.goal,include_market:!!run.state.include_market,required_fields:run.snapshot.required_fields||[]});await refresh();go('run',r.id);}
    if(action==='ask-knowledge'){state.knowledge=await api('/knowledge',{method:'POST',body:{message:id}});render();$('#knowledge-question').value=id;}
  }catch(error){toast(error.message,true);}
});
document.addEventListener('change',event=>{if(event.target.id==='proposal-edit-mode'){const fieldset=$('[data-manual-edit]',event.target.form);fieldset.hidden=fieldset.disabled=event.target.value!=='manual';}if(event.target.id==='doc-file')importDocumentFile(event.target);if(event.target.id==='customer-filter'){state.customer=event.target.value;render();}if(event.target.id==='record-season'){$('#record-plan').innerHTML=planOptions(event.target.value);}if(event.target.id==='rate'){$('#rate-source').required=event.target.value!=='';}if(event.target.name?.endsWith('.rate_kg_per_mu')){const row=event.target.closest('[data-material-id]');$('[name$=".rate_source"]',row).required=event.target.value!=='';}});
document.addEventListener('submit',async event=>{
  const form=event.target;if(!(form instanceof HTMLFormElement))return;event.preventDefault();
  const submit=$('[type=submit]',form), formData=new FormData(form),data=Object.fromEntries(formData);if(submit?.disabled)return;if(submit)submit.disabled=true;
  try{
    if(form.id==='recovery-form'){
      if(!isSR())return;
      const runId=form.dataset.runId,generation=Number(form.dataset.routeGeneration);
      try{
        const result=await api('/runs/'+encodeURIComponent(runId)+'/refresh',{method:'POST',body:{expected_revision:Number(form.dataset.runRevision),expected_input_revision:Number(form.dataset.inputRevision),expected_context_hash:form.dataset.contextHash,intent:form.dataset.intent}});
        if(!currentDiscussion(runId,generation))return;
        const run=await api('/runs/'+encodeURIComponent(runId));
        if(!currentDiscussion(runId,generation))return;
        if($('form',modal)===form)modal.close();
        const summary=state.data.runs?.find(item=>item.id===runId);
        if(summary)for(const key of ['status','revision','candidate_version','formal_id','error'])if(key in run)summary[key]=run[key];
        state.run=run;state.tab='plan';render();
        toast(result.duplicate?'此恢复已登记，显示同一代研究进度':'恢复已登记，正在重建新草稿');
      }catch(error){if(currentDiscussion(runId,generation))toast(error.message,true);}
      return;
    }
    if(form.id==='login-form'){await api('/login',{method:'POST',body:data});state.data=await api('/bootstrap');state.customer='';location.hash='todos';await loadRoute();}
    if(form.id==='report-upload-form')await uploadReportFile(form);
    if(form.id==='report-confirm-form'||form.id==='report-manual-form'){
      const confirming=form.id==='report-confirm-form',body=confirming?
        {expected_revision:Number(form.dataset.reportRevision),expected_input_revision:Number(form.dataset.seasonRevision),extraction_id:form.dataset.extractionId,source:data.source}:
        {...data,expected_revision:Number(form.dataset.reportRevision),page:Number(data.page)};
      await api('/reports/'+encodeURIComponent(form.dataset.reportId)+(confirming?'/confirm':'/candidates'),{method:'POST',body});
      await updateReports(form.dataset.seasonId,Number(form.dataset.routeGeneration),form);
    }
    if(form.id==='customer-form'){data.year=Number(data.year);const c=await api('/customers',{method:'POST',body:data});modal.close();toast('客户与本季资料已创建');await refresh();go('season',c.season_id);}
    if(form.id==='facts-form'){const source=data.source;delete data.source;for(const k of ['budget_cny','soil_ph','rate_kg_per_mu'])if(data[k]==='')data[k]=null;data.budget_basis='company_cooperation_amount';data.additional_materials=parseMaterials(data);await api('/seasons/'+encodeURIComponent(form.dataset.seasonId),{method:'PUT',body:{expected_revision:Number(form.dataset.seasonRevision),facts:data,source}});toast('已保存新的本季资料版本');await refresh();}
    if(form.id==='start-form'){const r=await createRun({season_id:form.dataset.seasonId,expected_revision:Number(form.dataset.seasonRevision),goal:data.goal,include_market:data.include_market==='on',required_fields:formData.getAll('required_field')});await refresh();go('run',r.id);}
    if(form.id==='answer-form'||form.id.startsWith('answer-form-')){const result=await api('/runs/'+encodeURIComponent(form.dataset.runId)+'/answers',{method:'POST',body:{question_id:form.dataset.question,question_revision:Number(form.dataset.revision),expected_input_revision:Number(form.dataset.inputRevision),value:data.value,source:data.source}});toast(result.status==='waiting_input'?'回答已保存，请继续确认其余资料':'回答已保存，恢复已登记');if(state.run?.id===form.dataset.runId&&result.status==='queued')state.tab='plan';await refresh();}
    if(form.id==='draft-form'){const stages=Array.from({length:Number(form.dataset.stageCount)},(_,i)=>({name:data['stage_name_'+i],timing:data['stage_timing_'+i],action:data['stage_action_'+i]}));await api('/runs/'+form.dataset.runId+'/draft',{method:'PUT',body:{expected_revision:Number(form.dataset.runRevision),summary:data.summary,stages,reason:data.reason}});modal.close();toast('已保存新的草稿修订');await refresh();}
    if(form.id==='discuss-form'||form.id==='proposal-form'){
      const runId=form.dataset.runId,generation=Number(form.dataset.routeGeneration),body={message:data.message,intent:form.id==='proposal-form'?'change_request':'question',expected_revision:Number(form.dataset.runRevision)};
      if(form.id==='proposal-form'){
        body.reason=data.reason||'';
        if(data.edit_mode==='manual')body.proposed_edit={summary:data.summary,stages:Array.from({length:Number(form.dataset.stageCount)},(_,i)=>({name:data['stage_name_'+i],timing:data['stage_timing_'+i],action:data['stage_action_'+i]}))};
      }
      await api('/runs/'+encodeURIComponent(runId)+'/discuss',{method:'POST',body});
      await updateDiscussion(runId,generation,form.id==='proposal-form'?form:null);
    }
    if(form.id.startsWith('apply-proposal-form-')){
      const runId=form.dataset.runId,generation=Number(form.dataset.routeGeneration);
      const result=await api('/runs/'+encodeURIComponent(runId)+'/proposals/'+encodeURIComponent(form.dataset.proposalId)+'/apply',{method:'POST',body:{expected_revision:Number(form.dataset.runRevision)}});
      if(currentDiscussion(runId,generation)){toast((result.duplicate?'已保存过此预览，草稿为 C':'已保存新草稿 C')+result.candidate_version+'；技术确认需另行复核');await updateDiscussion(runId,generation);}
    }
    if(form.id==='record-form'){data.observed_at=new Date(data.observed_at).toISOString();data.plan_id=data.plan_id||null;await api('/field-records',{method:'POST',body:data});toast('现场实际已保存');await refresh();}
    if(form.id==='next-season-form'){data.year=Number(data.year);const result=await api('/seasons/'+form.dataset.seasonId+'/next',{method:'POST',body:data});modal.close();toast('下一种植季已创建，专业参数需重新确认');await refresh();go('season',result.season_id);}
    if(form.id==='knowledge-form'){state.knowledge=await api('/knowledge',{method:'POST',body:data});render();$('#knowledge-question').value=data.message;}
    if(form.id==='document-form'){
      const claims=data.supported_claims.split('\n').map(s=>s.trim()).filter(Boolean);
      if(claims.length>20||claims.some(s=>s.length>2000))throw new Error('支持主张最多20条，每条不超过2000字');
      const conditions=(data.conditions||'').split('\n').map(s=>s.trim()).filter(Boolean);
      if(conditions.length>20||conditions.some(s=>s.length>1000))throw new Error('适用条件最多20条，每条不超过1000字');
      const body={title:data.title,body:data.body,kind:data.kind,purpose:data.purpose,crop:data.crop,region:data.region,source:data.source,locator:data.locator,customer_id:data.customer_id||null,demo:data.demo==='on',supported_claims:claims,conditions,valid_from:data.valid_from?new Date(data.valid_from).toISOString():null,valid_until:data.valid_until?new Date(data.valid_until).toISOString():null};
      const id=form.dataset.documentId;
      if(id)body.expected_revision=Number(form.dataset.documentRevision);
      await api('/documents'+(id?'/'+encodeURIComponent(id)+'/versions':''),{method:'POST',body});
      toast(id?'已保存新的资料草稿版本':'已保存资料草稿，发布后才可用作依据');
      if(Number(form.dataset.routeGeneration)===routeGeneration){modal.close();await loadRoute();}
    }
    if(form.id==='document-transition-form'){
      await api('/documents/'+encodeURIComponent(form.dataset.documentId)+'/'+form.dataset.transition,{method:'POST',body:{expected_revision:Number(form.dataset.documentRevision),note:data.note}});
      toast(form.dataset.transition==='publish'?'资料已发布':'资料已撤回，当前依据将重新检查');
      if(Number(form.dataset.routeGeneration)===routeGeneration){modal.close();await loadRoute();}
    }
  }catch(error){toast(error.message,true);}finally{if(submit)submit.disabled=false;}
});
window.addEventListener('hashchange',()=>loadRoute().catch(e=>toast(e.message,true)));
setInterval(async()=>{
  if(!state.data||state.route!=='run'||!['queued','running'].includes(state.run?.status)||modal.open||['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName))return;
  const generation=routeGeneration,runId=state.run.id;
  try{
    const run=await api('/runs/'+runId);
    if(generation!==routeGeneration||state.run?.id!==runId||!state.data)return;
    if(run.revision!==state.run.revision){
      const data=await api('/bootstrap');
      if(generation!==routeGeneration||state.run?.id!==runId||!state.data)return;
      state.run=run;state.data=data;render();
    }
  }catch(e){toast(e.message,true);}
},1500);
api('/bootstrap').then(async data=>{state.data=data;await loadRoute();}).catch(()=>renderLogin());

function newSeason(){
  const s=state.season;
  modalOpen('同一地块 · 新建下一种植季',`<form id="next-season-form"><p class="muted small mb">${esc(s.customer_name)} / ${esc(s.plot_name)}。已确认往季方案可供参考，预算及专业用量不自动沿用。</p><div class="form-grid"><div class="field"><label for="next-year">新种植年份</label><input id="next-year" name="year" type="number" min="${s.year+1}" max="2100" value="${s.year+1}" required></div><div class="field"><label for="next-area">本季确认面积 · 亩</label><input id="next-area" name="area_mu" type="number" step="0.0001" min="0.0001" max="1000000" value="${esc(s.facts.area_mu)}" required></div><div class="field full"><label for="next-crop">本季作物</label><select id="next-crop" name="crop">${['玉米','水稻','小麦'].map(c=>`<option ${c===s.crop?'selected':''}>${c}</option>`).join('')}</select></div></div><div class="form-actions"><button type="button" class="btn" data-action="close-modal">取消</button><button type="submit" class="btn primary">确认面积并创建种植季</button></div></form>`);
}

function planOptions(seasonId){
  return '<option value="">不关联具体版本 · 临时或待核实记录</option>'+(state.data.plans||[]).filter(p=>p.season_id===seasonId).map(p=>`<option value="${p.id}">技术 P${p.version} · 确认于 ${date(p.created_at)}</option>`).join('');
}

const answerCategories={cost:'金额与预算',history:'历史变化',business:'合同商务',rate:'用量依据',risk:'限制与风险',version:'版本与状态',evidence:'资料依据',scope:'当前解释范围',semantic:'语义解释',needs_facts:'需补充事实',change_request:'明确修改请求'};
const documentStatuses={draft:'草稿',published:'已发布',revoked:'已撤回',superseded:'已被新版替代'};
const purposes={plan_research:'方案研制',knowledge:'知识问答',market:'市场参考'};
const valueText=value=>typeof value==='object'&&value!==null?JSON.stringify(value):String(value??'未知');
const canManageDocument=doc=>isSR()&&doc.created_by===state.data.user.id;

function generationLabel(plan){
  const generation=plan.generation;
  const modes={rule_generated:'规则生成（演示草稿）',model_generated:'模型生成（待 SR 专业复核）'};
  if(!generation||!modes[generation.mode])return '旧版本：生成方式未记录';
  return modes[generation.mode]+(generation.contract_version?' · 契约 '+generation.contract_version:'')+(generation.model?' · 模型 '+generation.model:'');
}

function referencedEvidence(run){
  const refs=run.draft?.evidence_refs;
  return {evidence:(run.state.evidence||[]).filter(doc=>!Array.isArray(refs)||refs.includes(doc.id)),
    statuses:(run.evidence_statuses||[]).filter(doc=>!Array.isArray(refs)||refs.includes(doc.id))};
}

function structuredExport(run){
  const p=run.draft,review=run.review;
  const measures=(p.measures||[]).map((measure,index)=>`### ${index+1}. ${['confirmed_input','confirmed_material'].includes(measure.kind)?'确认输入与核算说明':measure.kind==='professional'?'专业判断':'流程措施'}\n\n${measure.text}\n\n- 成立条件：${(measure.conditions||[]).map(valueText).join('；')||'未附加条件（仍需 SR 核实现场适用性）'}\n- 事实引用：${(measure.fact_refs||[]).join(' / ')||'无'}\n- 资料引用：${(measure.evidence_refs||[]).join(' / ')||'无'}\n- 计算引用：${(measure.calculation_refs||[]).join(' / ')||'无'}`).join('\n\n');
  const history=(p.history_decisions||[]).map(item=>`- ${item.reference_id} / ${({review_and_adjust:'结合本季变化审查与调整',reference_only:'仅作设计参考',adopt:'沿用',adjust:'调整',reject:'不采用',not_adopt:'不采用'})[item.decision]||item.decision}：${(item.reasons||[]).map(valueText).join('；')}`).join('\n');
  const dimensions={condition_fit:'条件适配',evidence_sufficiency:'依据充分',numeric_consistency:'数值一致',execution_readiness:'完整可执行',risk_preparedness:'风险与调整准备'};
  const historical=!!(run.formal_id||run.formal);
  const current=!historical&&!!run.draft&&review?.candidate_version===run.candidate_version&&!run.stale&&!hasDependencyChanges(run);
  const summary=review?`规则 ${review.rule_version} / 候选 C${review.candidate_version} / 内容 ${review.content_hash||'未记录'}\n\n${historical?'技术确认时的历史评价；当前依据适用性需重新核实。':!current?'当前评价不再适用，需刷新研究再评价；暂不可技术确认。':review.can_confirm?'本次程序检查允许进入 SR 技术确认；仍需专业判断。':'当前存在质量门槛，暂不可技术确认。'}\n\n${Object.entries(review.dimensions||{}).map(([key,value])=>'- '+(dimensions[key]||key)+'：'+value).join('\n')}\n\n${(review.issues||[]).map(issue=>`- ${issue.code} / ${issue.severity||'待处理'}：${issue.message}；关闭条件：${issue.close_condition||'需 SR 复核'}`).join('\n')}`:'未记录评价；旧版本不自动视为已通过当前规则。';
  return `## 结构化措施\n\n${measures||'旧版本未记录结构化措施。'}\n\n## 历史采用与调整\n\n${history||'未记录历史处理决策。'}\n\n## 待决事项\n\n${(p.pending_decisions||[]).map(item=>'- '+valueText(item)).join('\n')||'未记录待决事项。'}\n\n## ${historical?'技术确认时的评价':'当前质量评价摘要'}\n\n${summary}`;
}

function reviewPanel(run){
  const review=run.review;
  if(!review)return run.draft?notice(run.formal?'此技术版本按旧规则保存，未补写新评价。':'此草稿尚无当前规则评价，请重新审查。'):'';
  const dimensions={condition_fit:'条件适配',evidence_sufficiency:'依据充分',numeric_consistency:'数值一致',execution_readiness:'完整可执行',risk_preparedness:'风险与调整准备'};
  const historical=!!(run.formal_id||run.formal);
  const current=!historical&&!!run.draft&&review.candidate_version===run.candidate_version&&!run.stale&&!hasDependencyChanges(run);
  return `<section class="card review-panel"><div class="card-head"><h2>${historical?'技术确认时的评价':'草稿质量评价'}</h2><span class="badge ${historical?'':review.can_confirm&&current?'green':'amber'}">${historical?'历史评价快照':review.can_confirm&&current?'可技术确认':'暂不可技术确认'}</span></div><div class="card-pad">${historical?notice('此评价记录技术确认时的检查结果；当前资料是否仍适用，请查看引用区的当前状态。','info'):''}<div class="review-dimensions">${Object.entries(review.dimensions||{}).map(([key,value])=>`<div class="review-dimension"><span>${esc(dimensions[key]||key)}</span><strong class="${value==='符合'?'pass':value==='不符合'?'fail':''}">${esc(value)}</strong></div>`).join('')}</div><p class="muted small mt">评价绑定 C${esc(review.candidate_version)} · ${esc(review.rule_version)}${historical||current?'':hasDependencyChanges(run)||run.stale?' · 输入或来源已变化，需刷新研究再评价':' · 绑定版本已变化，请重新审查'}<br><span class="mono review-hash">内容 ${esc(review.content_hash)}</span></p>${(review.issues||[]).length?`<div class="review-issues mt">${review.issues.map(issue=>`<article class="review-issue"><div class="row gap8"><span class="badge ${['blocking','blocker','error'].includes(issue.severity)?'red':'amber'}">${['blocking','blocker','error'].includes(issue.severity)?'阻塞':'需处理'}</span><strong>${esc(issue.message)}</strong></div><p>关闭条件：${esc(issue.close_condition||'由负责 SR 复核并重新评价')}</p><small class="muted">${esc(issue.code)} · ${esc(issue.field||'当前草稿')}</small></article>`).join('')}</div>`:`<p class="small mt">${historical?'当时的程序检查未发现阻塞项；此记录不代表当前依据仍适用。':!current?'旧评价未发现阻塞项；当前输入或来源需重新核实，需刷新研究再评价。':'本次程序检查未发现阻塞项；技术确认仍需 SR 判断。'}</p>`}</div></section>`;
}

function structuredPlan(plan){
  if(!plan.measures)return '';
  const constraints=Array.isArray(plan.constraints)?plan.constraints:Object.entries(plan.constraints||{}).map(([key,value])=>`${({variety:'品种要求',budget_cny:'合作预算',budget_basis:'预算口径'})[key]||key}：${key==='budget_basis'&&value==='company_cooperation_amount'?'公司合作产品金额':valueText(value)}`);
  return `<div class="divider"></div><h3>结构化措施与证据</h3>${(plan.measures||[]).map(measure=>`<article class="measure"><div class="row gap8"><span class="badge">${['confirmed_input','confirmed_material'].includes(measure.kind)?'确认输入与核算说明':measure.kind==='professional'?'专业判断':'流程措施'}</span><strong>${esc(measure.text)}</strong></div><p>成立条件：${esc((measure.conditions||[]).map(valueText).join('；')||'未附加条件（仍需SR核实现场适用性）')}</p><p class="small muted">事实：${esc((measure.fact_refs||[]).join(' / ')||'无')}<br>资料：${esc((measure.evidence_refs||[]).join(' / ')||'无')}<br>计算：${esc((measure.calculation_refs||[]).join(' / ')||'无')}</p></article>`).join('')}<h3 class="mt">历史采用与调整</h3>${(plan.history_decisions||[]).map(item=>`<div class="measure"><strong>${esc(({review_and_adjust:'结合本季变化审查与调整',reference_only:'仅作设计参考',adopt:'沿用',adjust:'调整',reject:'不采用',not_adopt:'不采用'})[item.decision]||item.decision)}</strong><p>${esc((item.reasons||[]).map(valueText).join('；'))}</p><small class="muted">历史引用 ${esc(item.reference_id)}</small></div>`).join('')||'<p class="small muted mt">未记录历史采用决策。</p>'}${constraints.length?`<h3 class="mt">本季约束</h3><ul class="warning-list">${constraints.map(item=>`<li>${esc(valueText(item))}</li>`).join('')}</ul>`:''}${(plan.pending_decisions||[]).length?`<h3 class="mt">待决事项</h3><ul class="warning-list">${plan.pending_decisions.map(item=>`<li>${esc(valueText(item))}</li>`).join('')}</ul>`:''}`;
}

function documentList(){
  const docs=(state.documents||[]).filter(doc=>isSR()||doc.status==='published');
  return `<section class="card mt"><div class="card-head"><div><h2>资料与版本</h2><p class="muted small">共享或指定客户 · 审核发布后才可用作当前依据</p></div>${isSR()?button('新建资料','new-document','','primary'):''}</div>${docs.length?docs.map(doc=>`<details class="source-document"><summary><span>${esc(doc.title)}</span><span class="badge ${doc.status==='published'?'green':doc.status==='revoked'?'red':'amber'}">${esc(documentStatuses[doc.status]||doc.status)} · v${esc(doc.version_number||1)}</span></summary><div class="source-content"><div class="row gap8"><span class="badge">${doc.kind==='professional'?'专业资料':'流程资料'}</span><span class="badge">${esc(purposes[doc.purpose]||doc.purpose)}</span><span class="badge">${doc.demo?'演示资料':'非演示标识'}</span></div><p class="muted small mt">范围：${esc(doc.customer_id?(state.data.customers.find(c=>c.id===doc.customer_id)?.name||doc.customer_id):'共享资料')} · ${esc(doc.crop||'通用')} / ${esc(doc.region||'通用')}<br>来源：${esc(doc.source)} / ${esc(doc.locator)}<br>版本 ${esc(doc.version)} · 修订 ${esc(doc.revision)} · ${esc(doc.id)}<br>有效期：${doc.valid_from?esc(date(doc.valid_from)):'未限定开始'} 至 ${doc.valid_until?esc(date(doc.valid_until)):'未限定截止'}</p><pre class="document-body">${esc(doc.body)}</pre><h3>声明支持的主张</h3><ul class="warning-list">${(doc.supported_claims||[]).map(claim=>`<li>${esc(claim)}</li>`).join('')||'<li>未声明；不自动推定能够支持专业处方。</li>'}</ul>${doc.conditions?.length?`<p class="muted small mt">适用条件：${esc(doc.conditions.join('；'))}</p>`:''}${doc.publication_note?`<p class="small mt">审核 / 撤回说明：${esc(doc.publication_note)}</p>`:''}${canManageDocument(doc)?`<div class="row mt">${button('建立修订版本','revise-document',doc.id)}${doc.status==='draft'?button('审核发布','publish-document',doc.id,'primary'):''}${['published','superseded'].includes(doc.status)?button('撤回资料','revoke-document',doc.id,'danger'):''}</div>`:''}</div></details>`).join(''):empty('当前没有获准可查看的资料。')}</section>`;
}

function documentEditor(previous=null){
  if(!isSR()||(previous&&!canManageDocument(previous)))return;
  const doc=previous||{kind:'procedure',purpose:'plan_research',crop:'通用',region:'通用',demo:true,supported_claims:[]};
  const options=(values,current)=>Object.entries(values).map(([value,label])=>`<option value="${esc(value)}" ${current===value?'selected':''}>${esc(label)}</option>`).join('');
  const field=(name,label,max,required=true)=>`<div class="field"><label for="doc-${name}">${label}</label><input id="doc-${name}" name="${name}" value="${esc(doc[name])}" maxlength="${max}" ${required?'required':''}></div>`;
  const localDate=value=>value?new Date(new Date(value)-new Date(value).getTimezoneOffset()*60000).toISOString().slice(0,16):'';
  modalOpen(previous?'建立资料修订版本':'新建资料草稿',`<form id="document-form" data-document-id="${esc(previous?.id||'')}" data-document-revision="${esc(previous?.revision||0)}"><div class="form-grid one-mobile">${field('title','资料标题',200)}<div class="field"><label for="doc-file">导入本地文字 · 可选</label><input id="doc-file" type="file" accept=".txt,.md,text/plain,text/markdown"><small>UTF-8 文本 / Markdown，正文最多16000字；导入后可编辑。</small></div><div class="field full"><label for="doc-body">正文 · 纯文本或 Markdown</label><textarea id="doc-body" name="body" required maxlength="16000" rows="9">${esc(doc.body)}</textarea></div><div class="field"><label for="doc-kind">资料类型</label><select id="doc-kind" name="kind">${options({procedure:'流程资料',professional:'专业资料'},doc.kind)}</select></div><div class="field"><label for="doc-purpose">用途</label><select id="doc-purpose" name="purpose">${options(purposes,doc.purpose)}</select></div><div class="field"><label for="doc-crop">适用作物</label><select id="doc-crop" name="crop">${options({'通用':'通用','玉米':'玉米','水稻':'水稻','小麦':'小麦'},doc.crop)}</select></div>${field('region','适用地区',120)}${field('source','来源与身份',1000)}${field('locator','原文定位 / 章节',200)}<div class="field full"><label for="doc-scope">资料范围</label><select id="doc-scope" name="customer_id" ${previous?'disabled':''}><option value="">共享资料</option>${(state.data.customers||[]).map(c=>`<option value="${esc(c.id)}" ${doc.customer_id===c.id?'selected':''}>仅 ${esc(c.name)}</option>`).join('')}</select>${previous?`<input type="hidden" name="customer_id" value="${esc(doc.customer_id||'')}">`:''}<small>版本沿用原客户范围；共享资料仅由创建 SR 管理。</small></div><div class="field"><label for="doc-valid-from">有效开始 · 可选</label><input id="doc-valid-from" name="valid_from" type="datetime-local" value="${localDate(doc.valid_from)}"></div><div class="field"><label for="doc-valid-until">有效截止 · 可选</label><input id="doc-valid-until" name="valid_until" type="datetime-local" value="${localDate(doc.valid_until)}"></div><div class="field full"><label for="doc-conditions">适用条件 · 每行一条</label><textarea id="doc-conditions" name="conditions" rows="3" maxlength="20019">${esc((doc.conditions||[]).join('\n'))}</textarea><small>最多20条；请记录地区、时间或现场条件等限制。</small></div><div class="field full"><label for="doc-claims">声明支持的主张 · 每行一条</label><textarea id="doc-claims" name="supported_claims" rows="4" maxlength="40019">${esc((doc.supported_claims||[]).join('\n'))}</textarea><small>最多20条；声明主张不能替代专业审核。</small></div></div><label class="check"><input type="checkbox" name="demo" ${doc.demo?'checked':''}>此资料为演示内容</label>${notice('保存产生草稿；点击审核发布只记录 SR 确认，不构成外部权威证明。修订会保留原版本正文。')}<div class="form-actions"><button type="button" class="btn" data-action="close-modal">取消</button><button type="submit" class="btn primary">保存资料草稿</button></div></form>`);
}

function documentTransition(id,transition){
  const doc=state.documents.find(item=>item.id===id);
  if(!doc||!canManageDocument(doc))return;
  const publish=transition==='publish';
  modalOpen(publish?'审核并发布资料':'撤回当前资料',`<form id="document-transition-form" data-document-id="${esc(doc.id)}" data-document-revision="${esc(doc.revision)}" data-transition="${transition}"><h3>${esc(doc.title)} · v${esc(doc.version_number)}</h3><p class="muted small mt">${publish?'请核对正文、来源、用途和适用范围；发布会替代同文档旧的当前版本。':'撤回后不能再作为当前可用依据；历史正文与引用身份保留。'}</p><div class="field mt"><label for="document-transition-note">${publish?'审核确认说明':'撤回原因'}</label><textarea id="document-transition-note" name="note" required maxlength="4000" rows="4"></textarea></div><div class="form-actions"><button type="button" class="btn" data-action="close-modal">取消</button><button type="submit" class="btn ${publish?'primary':'danger'}">${publish?'确认发布':'确认撤回'}</button></div></form>`);
}

async function importDocumentFile(input){
  const file=input.files?.[0],form=input.form;
  if(!file||!form)return;
  try{
    if(!/\.(txt|md)$/i.test(file.name)||file.size>64000)throw new Error('请选择不超过16000字的 .txt 或 .md 文件');
    const body=await file.text();
    if(body.length>16000)throw new Error('导入正文超过16000字，请缩减后导入');
    if(!form.isConnected||!modal.open)return;
    form.elements.body.value=body;
    if(!form.elements.title.value)form.elements.title.value=file.name.replace(/\.(txt|md)$/i,'').slice(0,200);
  }catch(error){input.value='';toast(error.message,true);}
}

