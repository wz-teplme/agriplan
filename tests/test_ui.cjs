// Execute real frontend route logic with deliberately out-of-order responses.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const root = {innerHTML:'',textContent:'',className:'',open:false,dataset:{}};
root.close=()=>{root.open=false;};
root.showModal=()=>{root.open=true;};
root.querySelector=()=>root;
const handlers={};
class Form {constructor(id,values,dataset){this.id=id;this.values=values;this.dataset=dataset;}querySelector(){return {disabled:false};}}
const context = vm.createContext({
  document:{querySelector:()=>root,addEventListener:(name,fn)=>{handlers[name]=fn;},activeElement:{tagName:'BODY'}},
  location:{hash:'#todos'},window:{addEventListener:()=>{},scrollTo:()=>{}},
  setInterval:()=>{},setTimeout:()=>{},clearTimeout:()=>{},
  fetch:()=>new Promise(()=>{}),console,crypto:require('node:crypto').webcrypto,
  HTMLFormElement:Form,FormData:class {constructor(form){this.form=form;}*[Symbol.iterator](){yield* Object.entries(this.form.values);}getAll(key){return [].concat(this.form.values[key]??[]);}},handlers,
});
vm.runInContext(fs.readFileSync('web/app.js','utf8'),context);
(async()=>{
  await vm.runInContext(`(async()=>{
    state.data={user:{name:'测试',role:'SR'},runs:[],customers:[],seasons:[]};
    const pending={}; api=path=>new Promise(resolve=>pending[path]=resolve);
    const renders=[]; render=()=>renders.push(state.season?.id);
    location.hash='#season/A'; const first=loadRoute();
    location.hash='#season/B'; const second=loadRoute();
    pending['/seasons/B']({id:'B'}); await second;
    pending['/seasons/A']({id:'A'}); await first;
    if(state.season.id!=='B')throw new Error('迟到的 A 请求覆盖了当前客户 B');
    if(renders.at(-1)!=='B')throw new Error('当前路由渲染了其他客户');
  })()`,context);
  console.log('PASS: out-of-order season responses preserve the current customer');
  await vm.runInContext(`(async()=>{
    let calls=0;const keys=[];
    api=async(path,opts)=>{keys.push(opts.headers['Idempotency-Key']);if(++calls===1)throw new Error('lost response');return {id:'persisted'};};
    const body={season_id:'A',goal:'同一研究',expected_revision:1,include_market:false};
    try{await createRun(body);}catch(e){}
    await createRun(body);
    if(keys[0]!==keys[1])throw new Error('网络重试更换幂等键');
    await createRun(body);
    if(keys[1]===keys[2])throw new Error('成功后的新研究未创建新的幂等键');
  })()`,context);
  console.log('PASS: a lost create response reuses its idempotency key');
  context.form=new Form('draft-form',{summary:'A edited summary',stage_name_0:'A阶段',stage_timing_0:'A时点',stage_action_0:'A措施',reason:'A理由'},{runId:'A',runRevision:'10',stageCount:'1'});
  await vm.runInContext(`(async()=>{
    state.run={id:'B',revision:20,draft:{stages:[{}]}};
    let sent;api=async(path,options)=>{sent={path,body:options.body};return {};};refresh=async()=>{};
    await handlers.submit({target:form,preventDefault(){}});
    if(sent.path!=='/runs/A/draft'||sent.body.expected_revision!==10)throw new Error('旧A弹窗将内容提交给当前B对象');
    if(sent.body.stages.length!==1)throw new Error('旧弹窗没有保留原阶段数量');
  })()`,context);
  console.log('PASS: a stale modal submits only its bound object and revision');
  await vm.runInContext(`(async()=>{
    state.route='run';state.id='A';state.run={id:'A'};modal.open=true;
    location.hash='#run/B';api=async()=>({id:'B'});await loadRoute();
    if(modal.open)throw new Error('切换对象没有关闭旧弹窗');
  })()`,context);
  console.log('PASS: navigation closes the old object modal');
  await vm.runInContext(`(async()=>{
    const pending={};api=path=>new Promise(resolve=>pending[path]=resolve);
    state.documents=[];location.hash='#knowledge';const first=loadRoute();
    if(!pending['/documents'])throw new Error('资料页没有读取资料列表');
    location.hash='#season/C';const second=loadRoute();pending['/seasons/C']({id:'C'});await second;
    pending['/documents']([{id:'late'}]);await first;
    if(state.documents.length)throw new Error('迟到的资料列表覆盖了已切换的页面');
  })()`,context);
  console.log('PASS: document loading obeys route generation');
  context.form=new Form('document-transition-form',{note:'撤回 A'},{documentId:'A',documentRevision:'4',transition:'revoke'});
  await vm.runInContext(`(async()=>{
    state.documents=[{id:'B',revision:8}];let sent;api=async(path,options)=>{sent={path,body:options.body};return {};};
    await handlers.submit({target:form,preventDefault(){}});
    if(sent?.path!=='/documents/A/revoke'||sent.body.expected_revision!==4)throw new Error('资料弹窗未绑定原对象与版本');
  })()`,context);
  console.log('PASS: document lifecycle forms bind their document and revision');
  await vm.runInContext(`(()=>{
    state.tab='audit';auditTab=()=>'';
    state.run={id:'limited',status:'limited',revision:2,candidate_version:1,draft:{},snapshot:{revision:1,facts:{},customer_name:'客户',plot_name:'田块',year:2026,crop:'玉米'},questions:[],review:{can_confirm:false,dimensions:{依据充分:'不符合'},issues:[{code:'EVIDENCE_REQUIRED',severity:'blocking',message:'缺少可用依据',close_condition:'发布获准资料后重新审查'}]}};
    const html=runPage();
    if(!html.includes('data-action="edit-draft"'))throw new Error('受限草稿不能编辑');
    if(html.includes('data-action="confirm"'))throw new Error('阻塞评价仍显示可确认入口');
    if(!html.includes('缺少可用依据')||!html.includes('发布获准资料后重新审查'))throw new Error('没有展示评价问题及关闭条件');
    state.run.review.can_confirm=true;state.run.stale=true;
    if(runPage().includes('data-action="confirm"'))throw new Error('过期输入仍允许确认');
    state.run.stale=false;state.run.review.candidate_version=1;
    if(!runPage().includes('data-action="confirm"'))throw new Error('当前已通过评价没有技术确认入口');
    state.run.review.candidate_version=0;
    if(runPage().includes('data-action="confirm"'))throw new Error('旧候选评价仍允许确认');
  })()`,context);
  console.log('PASS: limited drafts show review issues and gate confirmation');
  await vm.runInContext(`(()=>{
    state.data.user.id='u1';state.data.user.role='SR';
    state.documents=[{id:'unsafe',title:'<script>bad</script>',body:'<img src=x onerror=bad()>',status:'draft',created_by:'u2',supported_claims:[],conditions:[]}];
    const html=documentList();
    if(html.includes('<script>')||html.includes('<img src=x'))throw new Error('资料正文未转义');
    if(html.includes('data-action="publish-document"'))throw new Error('非创建者获得共享资料管理入口');
    state.data.user.role='FR';
    if(documentList().includes('unsafe'))throw new Error('FR 页面暴露未发布资料');
    state.data.user.role='SR';
  })()`,context);
  console.log('PASS: source documents escape text and respect management visibility');
  context.form=new Form('document-form',{title:'修订标题',body:'修订正文',kind:'procedure',purpose:'plan_research',crop:'玉米',region:'吉林',source:'SR来源',locator:'第二节',customer_id:'c1',demo:'on',supported_claims:'主张一\n\n主张二',conditions:'仅供流程参考',valid_from:'',valid_until:''},{documentId:'original',documentRevision:'7'});
  await vm.runInContext(`(async()=>{
    let sent;api=async(path,options)=>{sent={path,body:options.body};return {};};
    await handlers.submit({target:form,preventDefault(){}});
    if(sent?.path!=='/documents/original/versions'||sent.body.expected_revision!==7)throw new Error('修订没有绑定原资料版本');
    if(sent.body.demo!==true||sent.body.supported_claims.length!==2||sent.body.customer_id!=='c1')throw new Error('资料字段未正确转换');
    if(sent.body.conditions?.[0]!=='仅供流程参考')throw new Error('修订资料丢失适用条件');
  })()`,context);
  console.log('PASS: source revisions keep scope and normalize claims');
  await vm.runInContext(`(async()=>{
    const form={isConnected:true,elements:{body:{value:'原正文'},title:{value:''}}};modal.open=true;
    await importDocumentFile({form,files:[{name:'流程.md',size:20,text:async()=>'<script>只作为正文</script>'}]});
    if(form.elements.body.value!=='<script>只作为正文</script>'||form.elements.title.value!=='流程')throw new Error('文本导入失败');
    const previous=form.elements.body.value;
    await importDocumentFile({form,files:[{name:'oversized.txt',size:20000,text:async()=>'a'.repeat(16001)}]});
    if(form.elements.body.value!==previous)throw new Error('超限文件覆盖已有正文');
    let finish;const pending=importDocumentFile({form,files:[{name:'late.txt',size:10,text:()=>new Promise(resolve=>finish=resolve)}]});
    form.isConnected=false;finish('迟到正文');await pending;
    if(form.elements.body.value!==previous)throw new Error('迟到导入写入已关闭弹窗');
  })()`,context);
  console.log('PASS: local text imports enforce size and ignore closed forms');
  await vm.runInContext(`(()=>{
    state.data.user.role='FR';
    const original={id:'k1',title:'当时依据',body:'保留当时引用的正文',source:'原始来源',locator:'第一节',version:'v1'};
    const run={formal_id:'P1',formal:{version:1},candidate_version:1,snapshot:{facts:{}},
      draft:{summary:'正式技术方案',calculation:{},product:{},stages:[],history_refs:[],evidence_refs:['k1'],warnings:[]},
      state:{evidence:[original]},evidence_statuses:[{id:'k1',error:'EVIDENCE_REVOKED',status:'revoked',document:{...original,body:'不应覆盖当时引用正文',revoked_by:'u1',revoked_at:'2026-10-17T08:30:00+00:00',publication_note:'适用条件待核实，暂停使用'}}],
      review:{can_confirm:true,candidate_version:1,dimensions:{evidence_sufficiency:'符合'},issues:[],rule_version:'candidate-review-v2',content_hash:'saved-hash'}};
    const before=JSON.stringify(run);
    const plan=planTab(run),review=reviewPanel(run);
    if(!plan.includes('已撤回')||!plan.includes('适用条件待核实，暂停使用')||!plan.includes('2026-10-17T08:30:00+00:00'))throw new Error('FR正式方案没有显示当前依据撤回身份、时间与原因');
    if(!plan.includes('保留当时引用的正文')||plan.includes('不应覆盖当时引用正文'))throw new Error('当前资料覆盖了当时引用正文');
    if(!review.includes('技术确认时的评价')||review.includes('可技术确认'))throw new Error('正式评价仍被显示为当前确认许可');
    if(JSON.stringify(run)!==before)throw new Error('显示当前依据状态修改了历史快照');
    state.data.user.role='SR';
  })()`,context);
  console.log('PASS: formal source revocation is visible to FR without rewriting history');
  vm.runInContext(`(()=>{
    const html=structuredPlan({measures:[{kind:'confirmed_input',text:'已确认输入的核算',conditions:[]}],constraints:{budget_basis:'company_cooperation_amount'},history_decisions:[]});
    if(!html.includes('确认输入与核算说明')||!html.includes('公司合作产品金额')||html.includes('company_cooperation_amount'))throw new Error('结构化措施仍显示错误分类或预算枚举');
    if(!html.includes('未附加条件（仍需SR核实现场适用性）'))throw new Error('空条件仍被显示为遗漏');
  })()`,context);
  console.log('PASS: confirmed inputs and budget basis use readable labels');
  vm.runInContext(`(()=>{
    const run={mode:'llm',candidate_version:2,snapshot:{year:2026,customer_name:'客户',plot_name:'地块',crop:'玉米',revision:1,facts:{}},
      draft:{summary:'模型候选',generation:{mode:'model_generated',contract_version:'candidate-v2',model:'model-example'},calculation:{},product:{},stages:[],history_refs:[],evidence_refs:['used'],warnings:[],
        measures:[{kind:'procedure',text:'依据现场资料核实排水',conditions:[],evidence_refs:['used']}],history_decisions:[{reference_id:'h1',decision:'reference_only',reasons:['本季条件不同']}],pending_decisions:['缺少本季现场排水记录']},
      state:{history:[],evidence:[{id:'used',title:'实际采用资料',body:'采用正文'},{id:'unused',title:'检索到但未引用',body:'未采用正文'}]},
      evidence_statuses:[{id:'used',status:'published',error:null},{id:'unused',status:'revoked',error:'EVIDENCE_REVOKED',document:{publication_note:'未采用资料的撤回原因'}}],
      review:{can_confirm:false,candidate_version:2,dimensions:{evidence_sufficiency:'部分符合'},issues:[{code:'PENDING_DECISION',message:'需核实现场',close_condition:'补全记录重新评价'}],rule_version:'candidate-review-v2'}};
    const html=planTab(run);
    if(!html.includes('模型生成')||!html.includes('model-example')||!html.includes('candidate-v2'))throw new Error('草稿没有显示已记录的模型生成身份');
    if(!html.includes('缺少本季现场排水记录'))throw new Error('待决缺口未展示');
    if(!html.includes('实际采用资料')||html.includes('检索到但未引用')||html.includes('未采用资料的撤回原因'))throw new Error('引用区仍混入未采用资料');
    const text=draftMarkdown(run);
    if(!text.startsWith('# AgriPlan 2026 本季技术方案'+String.fromCharCode(10,10)))throw new Error('导出没有保留 Markdown 换行');
    for(const expected of ['模型生成','candidate-v2','依据现场资料核实排水','本季条件不同','缺少本季现场排水记录','candidate-review-v2','PENDING_DECISION'])if(!text.includes(expected))throw new Error('导出遗漏 '+expected);
    if(text.includes('检索到但未引用'))throw new Error('导出包含未引用资料');
    run.draft.history_decisions[0].decision='not_adopt';
    if(!planTab(run).includes('不采用')||draftMarkdown(run).includes('not_adopt'))throw new Error('模型不采用历史的决策未映射中文');
    delete run.draft.generation;delete run.draft.evidence_refs;
    const legacy=planTab(run);
    if(!legacy.includes('旧版本：生成方式未记录')||!legacy.includes('检索到但未引用'))throw new Error('旧草稿身份或引用兼容失败');
  })()`,context);
  console.log('PASS: model provenance, cited evidence, pending gaps and structured export stay explicit');
  vm.runInContext(`(()=>{
    const r={candidate_version:1,snapshot:{year:2026,revision:1,facts:{area_mu:'300',rate_kg_per_mu:'20',rate_source:'主依据'}},state:{history:[],evidence:[]},
      draft:{summary:'两产品',product:{name:'主产品',price:'3.20'},calculation:{total_kg:'6000',cost_cny:'19200.00',cooperation_total_cny:'25350.00',additional_lines:[{id:'a-1',product_id:'fert-2',product_name:'追加产品',rate_kg_per_mu:'5',rate_source:'追加依据',price_cny_per_kg:'4.10',total_kg:'1500',cost_cny:'6150.00'}]},stages:[],warnings:[],history_refs:[],evidence_refs:[],measures:[{kind:'confirmed_material',text:'追加确认输入'}]}};
    const html=planTab(r),md=draftMarkdown(r);
    for(const text of ['追加产品','6,150','25,350','主产品数量','合作合计','确认输入与核算说明'])if(!html.includes(text))throw new Error('多行页面遗漏 '+text);
    for(const text of ['追加产品','6150.00','25350.00','主产品小计','合作合计','追加依据'])if(!md.includes(text))throw new Error('多行导出遗漏 '+text);
    const history=historyCards([{differences:[{label:'追加材料',historical:[],current:[{id:'a-1',product_id:'fert-2'}]}]}]);
    if(history.includes('[object Object]')||!history.includes('fert-2'))throw new Error('历史追加明细没有可读展示');
  })()`,context);
  console.log('PASS: all materials, totals and confirmed explanations appear in page and export');
  vm.runInContext(`(()=>{
    const run={id:'A',snapshot:{revision:8},questions:[{id:'text',revision:2,status:'open',field:'variety',answer_type:'text',prompt:'品种?',purpose:'保留硬要求',impact:'影响方案'},{id:'storage',revision:3,status:'open',answer_type:'select',prompt:'仓储?',options:[{value:'no',label:'无仓储'}]},{id:'legacy',revision:1,status:'open',prompt:'旧用量?'}],messages:[]};
    const html=discussionTab(run);
    for(const text of ['data-run-id="A"','data-input-revision="8"','保留硬要求','影响方案','type="text"','<select','value="no"','type="number"','3 个'])if(!html.includes(text))throw new Error('问题元数据遗漏 '+text);
    if(new Set([...html.matchAll(/id="(answer-[^"]+)"/g)].map(m=>m[1])).size!==9)throw new Error('多问题表单标识重复');
  })()`,context);
  console.log('PASS: question metadata renders all input types with distinct controls and bindings');
  context.form=new Form('answer-form',{value:'客户指定品种',source:'访谈'},{runId:'A',question:'q-text',revision:'3',inputRevision:'8'});
  await vm.runInContext(`(async()=>{
    state.run={id:'B',snapshot:{revision:99}};state.tab='discussion';let sent;
    api=async(path,options)=>{sent={path,body:options.body};return {status:'waiting_input'};};
    await handlers.submit({target:form,preventDefault(){}});
    if(sent.path!=='/runs/A/answers'||sent.body.question_id!=='q-text'||sent.body.question_revision!==3||sent.body.expected_input_revision!==8)throw new Error('问题回答未绑定原研究/问题/事实版本');
    if(state.tab!=='discussion')throw new Error('部分回答离开剩余问题页');
  })()`,context);
  console.log('PASS: partial answers preserve the discussion and submit their bound revisions');
  context.form=new Form('facts-form',{area_mu:'300',product_id:'fert-1',rate_kg_per_mu:'20',rate_source:'主依据',budget_cny:'',soil_ph:'',source:'本次确认','material.extra-1.product_id':'fert-2','material.extra-1.rate_kg_per_mu':'','material.extra-1.rate_source':''},{seasonId:'A',seasonRevision:'5'});
  await vm.runInContext(`(async()=>{
    state.season={id:'B',revision:90};let sent;api=async(path,options)=>{sent={path,body:options.body};return {};};
    await handlers.submit({target:form,preventDefault(){}});
    if(sent.path!=='/seasons/A'||sent.body.expected_revision!==5)throw new Error('资料提交没有绑定原种植季');
    const line=sent.body.facts.additional_materials[0];
    if(line.id!=='extra-1'||line.product_id!=='fert-2'||line.rate_kg_per_mu!==null)throw new Error('追加行解析错误');
    if(Object.keys(sent.body.facts).some(k=>k.startsWith('material.')))throw new Error('追加控件字段泄漏到Facts');
  })()`,context);
  console.log('PASS: facts submissions parse additional rows and bind the original season');
  context.form=new Form('start-form',{goal:'目标',required_field:['budget_cny','storage']},{seasonId:'A',seasonRevision:'6'});
  await vm.runInContext(`(async()=>{
    let sent;createRun=async body=>{sent=body;return {id:'new'};};
    await handlers.submit({target:form,preventDefault(){}});
    if(sent.season_id!=='A'||sent.expected_revision!==6||sent.required_fields.join(',')!=='budget_cny,storage')throw new Error('研究没有携带所选必需事实或绑定版本');
  })()`,context);
  console.log('PASS: research starts with explicitly selected ordinary facts');
  vm.runInContext(`(()=>{
    state.data.products=[{id:'fert-1',name:'主产品',price:'3.20'},{id:'fert-2',name:'追加产品',price:'4.10'}];
    let inserted='';const list={querySelectorAll:()=>[],insertAdjacentHTML:(position,html)=>{inserted=html;}};
    const editForm={elements:{product_id:{value:'fert-1'},note:{value:'未保存备注'}},querySelector:()=>list,querySelectorAll:()=>[]};
    addMaterialRow(editForm);
    if(!inserted.includes('fert-2')||!inserted.includes('material.')||editForm.elements.note.value!=='未保存备注')throw new Error('追加行没有沿用当前表单或丢失未保存字段');
    let removed=false;removeMaterialRow({remove(){removed=true;}});
    if(!removed||editForm.elements.note.value!=='未保存备注')throw new Error('删除行失败或影响原输入');
  })()`,context);
  console.log('PASS: add/remove material operations preserve unsaved form values');
  vm.runInContext(`(()=>{
    for(const data of [
      {product_id:'fert-1','material.extra.product_id':'fert-1','material.extra.rate_kg_per_mu':'','material.extra.rate_source':''},
      {product_id:'fert-1','material.extra.product_id':'fert-2','material.extra.rate_kg_per_mu':'5','material.extra.rate_source':'  '}
    ]){let rejected=false;try{parseMaterials(data);}catch(e){rejected=true;}if(!rejected)throw new Error('重复产品或无依据用量没有阻止提交');}
  })()`,context);
  console.log('PASS: duplicate products and rates without evidence are rejected before save');
  context.form=new Form('discuss-form',{message:'为什么这样安排？'},{runId:'original',runRevision:'12',routeGeneration:'0'});
  await vm.runInContext(`(async()=>{
    state.route='run';state.id='other';state.run={id:'other',revision:99};
    let sent;api=async(path,options)=>{sent={path,body:options.body};return {changed:false,proposal:null};};
    await handlers.submit({target:form,preventDefault(){}});
    if(sent.path!=='/runs/original/discuss'||sent.body.expected_revision!==12||sent.body.intent!=='question')throw new Error('询问未绑定原研究版本及只读意图');
  })()`,context);
  console.log('PASS: questions submit their original run, revision and read-only intent');
  vm.runInContext(`(()=>{
    state.data.mode='demo';state.data.user.role='SR';
    const run={id:'original',revision:12,status:'completed',candidate_version:3,snapshot:{revision:8},questions:[],messages:[{message:'旧消息',answer:'旧解释'},{message:'新问题',answer:'解释',mode:'semantic_model_explanation',category:'semantic'}],draft:{summary:'原摘要',calculation:{},stages:[]},proposals:[{id:'proposal',run_id:'original',status:'pending',binding:{run_revision:12,input_revision:8,candidate_version:3,formal_id:null,content_hash:'<hash>'},content:{before:{summary:'<script>原</script>',stages:[{name:'播前',timing:'原时间',action:'原措施'}]},after:{summary:'<img src=x onerror=bad()>',stages:[{name:'播前',timing:'新时间',action:'新措施'}]},reason:'未记录',impact:['<影响>'],preview_review:{can_confirm:false,issues:[{message:'缺少依据',close_condition:'补齐资料'}]}}}]};
    let html=discussionTab(run);
    for(const value of ['data-run-revision="12"','data-action="propose-edit"','data-proposal-id="proposal"','C3','R8','原时间','新时间','缺少依据','补齐资料','旧解释','模型语义解释'])if(!html.includes(value))throw new Error('讨论或预览遗漏 '+value);
    if(html.includes('<script>原')||html.includes('<img src=x')||html.includes('<影响>')||html.includes('<hash>'))throw new Error('建议内容或身份未转义');
    if(!html.includes('id="apply-proposal-form-proposal"'))throw new Error('当前SR建议没有保存表单');
    for(const patch of [{revision:13},{candidate_version:4},{snapshot:{revision:9}},{stale:true},{formal_id:'P1'},{status:'running'}]){
      if(discussionTab({...run,...patch}).includes('id="apply-proposal-form-proposal"'))throw new Error('旧绑定/正式/非就绪建议仍允许应用');
    }
    run.proposals[0].status='applied';run.proposals[0].applied_version=4;
    html=discussionTab(run);if(!html.includes('C4')||html.includes('id="apply-proposal-form-proposal"'))throw new Error('已保存建议仍可再次保存');
    state.data.user.role='FR';run.proposals[0].status='pending';
    html=discussionTab(run);if(html.includes('data-action="propose-edit"')||html.includes('id="apply-proposal-form-proposal"'))throw new Error('FR获得修改入口');
    state.data.user.role='SR';
  })()`,context);
  console.log('PASS: escaped proposal comparisons show review gates and enforce role/version eligibility');
  context.form=new Form('proposal-form',{message:'请改成明确核实',reason:'',edit_mode:'manual',summary:'修改摘要',stage_name_0:'播前',stage_timing_0:'播前一周',stage_action_0:'核实条件'},{runId:'A',runRevision:'10',stageCount:'1',routeGeneration:'-1'});
  await vm.runInContext(`(async()=>{
    state.run={id:'B',revision:20};let sent;api=async(path,options)=>{sent={path,body:options.body};return {changed:false,proposal:{id:'p1'}};};
    await handlers.submit({target:form,preventDefault(){}});
    if(sent?.path!=='/runs/A/discuss'||sent.body.expected_revision!==10||sent.body.intent!=='change_request')throw new Error('手工预览提交错误对象或意图');
    if(sent.body.reason!==''||sent.body.proposed_edit.summary!=='修改摘要'||sent.body.proposed_edit.stages[0].action!=='核实条件')throw new Error('手工文字或空理由被改写');
    form.values.edit_mode='model';await handlers.submit({target:form,preventDefault(){}});
    if('proposed_edit' in sent.body||sent.body.reason!=='')throw new Error('模型预览混入手工字段或猜测理由');
  })()`,context);
  console.log('PASS: manual/model previews preserve original bindings and optional SR reasons');
  context.form=new Form('apply-proposal-form-p1',{}, {runId:'A',proposalId:'p1',runRevision:'10',routeGeneration:'-1'});
  await vm.runInContext(`(async()=>{
    state.route='run';state.id='B';state.run={id:'B',revision:20};state.tab='facts';modal.open=true;
    let finish,sent;api=(path,options)=>{sent={path,body:options?.body};return new Promise(resolve=>finish=resolve);};
    const pending=handlers.submit({target:form,preventDefault(){}});finish({id:'A',candidate_version:4,duplicate:false});await pending;
    if(sent.path!=='/runs/A/proposals/p1/apply'||sent.body.expected_revision!==10)throw new Error('建议保存没有绑定原研究及预览版本');
    if(state.run.id!=='B'||state.tab!=='facts'||!modal.open)throw new Error('迟到的建议保存改变了当前对象、页签或弹窗');
  })()`,context);
  console.log('PASS: late proposal saves leave the newly selected object and modal untouched');
  await vm.runInContext(`(async()=>{
    state.data.mode='demo';state.data.user.role='SR';state.route='run';state.id='A';
    state.run={id:'A',revision:10,status:'completed',candidate_version:3,snapshot:{revision:8},draft:{summary:'<原摘要>',stages:[{name:'播前',timing:'原时点',action:'原措施'}]}};
    proposeEdit();
    if(!modal.open||modal.dataset.runId!=='A'||modal.dataset.runRevision!=='10'||modal.dataset.stageCount!=='1')throw new Error('修改弹窗没有绑定原对象和阶段');
    let html=modal.innerHTML;
    if(!html.includes('&lt;原摘要&gt;')||!html.includes('原措施')||html.includes('data-manual-edit hidden disabled'))throw new Error('demo手工文字没有预填或不可填写');
    if(/name="reason"[^>]*required/.test(html))throw new Error('SR理由被强制填写');
    state.data.mode='llm';proposeEdit();html=modal.innerHTML;
    if(!html.includes('value="model"')||!html.includes('data-manual-edit hidden disabled'))throw new Error('模型模式未提供模型起草或隔离手工校验');
    const fieldset={hidden:true,disabled:true};
    handlers.change({target:{id:'proposal-edit-mode',value:'manual',form:{querySelector:()=>fieldset}}});
    if(fieldset.hidden||fieldset.disabled)throw new Error('切换手工方式未启用文字编辑');
    handlers.change({target:{id:'proposal-edit-mode',value:'model',form:{querySelector:()=>fieldset}}});
    if(!fieldset.hidden||!fieldset.disabled)throw new Error('切回模型仍提交手工文字');
  })()`,context);
  console.log('PASS: bound preview dialogs prefill demo edits and switch model/manual input validation');
  context.form=new Form('apply-proposal-form-current',{}, {runId:'A',proposalId:'current',runRevision:'10'});
  await vm.runInContext(`(async()=>{
    form.dataset.routeGeneration=String(routeGeneration);location.hash='#run/A';state.route='run';state.id='A';state.run={id:'A',revision:10};state.tab='plan';
    const paths=[];api=async(path,options)=>{paths.push(path);if(path.endsWith('/apply'))return {id:'A',candidate_version:4,duplicate:false};if(path==='/runs/A')return {id:'A',revision:11,candidate_version:4};throw new Error('意外请求 '+path);};
    await handlers.submit({target:form,preventDefault(){}});
    if(paths.join('|')!=='/runs/A/proposals/current/apply|/runs/A'||state.run.candidate_version!==4||state.tab!=='discussion')throw new Error('确认保存没有更新新C并保留讨论页');
  })()`,context);
  console.log('PASS: applying the current preview reloads the new candidate and keeps discussion selected');
  vm.runInContext(`(()=>{
    state.data.user.role='SR';state.data.runs=[];
    const extraction={id:'x1',sequence:1,method:'text_pattern',pages:[{page:1,text:'<script>原页</script>'}],candidate:{field:'soil_ph',raw_value:'6.5',value:'6.5',original_unit:'pH',normalized_unit:'1',plot_name:'东田',sampled_on:'2026-10-10',page:1,locator:'第3行',excerpt:'pH:6.5',identity:'extracted_candidate'},issues:[]};
    const report={id:'report1',season_id:'A',filename:'<土壤>.txt',mime_type:'text/plain',sha256:'abc123',size_bytes:50,revision:2,status:'extracted',original_status:'available',can_confirm:true,current_extraction:extraction,extractions:[extraction]};
    state.season={id:'A',revision:8,plot_name:'东田',year:2026,facts:{},plans:[],history:[],reports:[report],report_sources:[]};
    const html=seasonPage();
    for(const value of ['报告原件与检测候选','abc123','/api/reports/report1/original','自动文字候选','第3行','report-confirm','report-manual'])if(!html.includes(value))throw new Error('报告页面遗漏 '+value);
    if(html.includes('<script>原页')||html.includes('<土壤>')||html.includes('<details open'))throw new Error('报告内容没有转义或默认展开全文');
    report.can_confirm=false;report.current_extraction.issues=[{message:'单位无法确认',severity:'blocking'}];
    if(seasonPage().includes('data-action="report-confirm"')||!seasonPage().includes('单位无法确认'))throw new Error('阻塞候选仍可确认或没有显示原因');
    state.data.user.role='FR';const readOnly=seasonPage();
    for(const action of ['report-confirm','report-manual','report-extract'])if(readOnly.includes('data-action="'+action+'"'))throw new Error('FR获得报告写入口');
    if(readOnly.includes('id="report-upload-form"'))throw new Error('FR获得报告上传入口');
    if(!readOnly.includes('/api/reports/report1/original'))throw new Error('FR没有原件读取入口');
    state.data.user.role='SR';report.can_confirm=true;report.current_extraction.issues=[];
    report.status='confirmed';
    if(!seasonPage().includes('原候选已由 SR 确认'))throw new Error('报告确认后仍显示待核对候选身份');
  })()`,context);
  console.log('PASS: report originals, escaped candidates, issues and role gates render in the season');
  context.form=new Form('report-upload-form',{}, {seasonId:'A',seasonRevision:'8',routeGeneration:'-1'});
  await vm.runInContext(`(async()=>{
    const file={name:'测土.md',size:50,type:'text/markdown'};form.elements={report_file:{files:[file]}};
    state.route='season';state.id='B';state.season={id:'B',reports:[]};modal.open=true;
    let sent;fetch=async(path,options)=>{sent={path,options};return {ok:true,json:async()=>({id:'uploaded'})};};
    await handlers.submit({target:form,preventDefault(){}});
    if(sent?.path!=='/api/seasons/A/reports?filename='+encodeURIComponent('测土.md')||sent.options.body!==file||sent.options.headers['Content-Type']!=='text/plain'||sent.options.credentials!=='same-origin')throw new Error('上传未使用原始文件、规范MIME或原本季');
    if(state.season.id!=='B'||!modal.open)throw new Error('迟到上传污染当前对象或弹窗');
    sent=null;form.elements.report_file.files=[{name:'huge.txt',size:8*1024*1024+1}];await handlers.submit({target:form,preventDefault(){}});
    if(sent)throw new Error('超限文件仍被上传');
    form.elements.report_file.files=[{name:'bad.exe',size:1}];await handlers.submit({target:form,preventDefault(){}});
    if(sent)throw new Error('不支持格式仍被上传');
  })()`,context);
  console.log('PASS: raw report uploads enforce size/type and ignore stale route completion');
  context.form=new Form('report-confirm-form',{source:'SR逐项核对原件'}, {reportId:'report1',reportRevision:'2',seasonId:'A',seasonRevision:'8',extractionId:'x1',routeGeneration:'-1'});
  await vm.runInContext(`(async()=>{
    state.route='season';state.id='B';state.season={id:'B'};modal.open=true;
    let sent;api=async(path,options)=>{sent={path,body:options?.body};return {input_revision:9};};
    await handlers.submit({target:form,preventDefault(){}});
    if(sent?.path!=='/reports/report1/confirm'||sent.body.expected_revision!==2||sent.body.expected_input_revision!==8||sent.body.extraction_id!=='x1'||sent.body.source!=='SR逐项核对原件')throw new Error('报告确认未固定原报告、候选和本季R');
    if(state.season.id!=='B'||!modal.open)throw new Error('迟到确认关闭新对象弹窗');
    form.id='report-manual-form';form.values={raw_value:'6.2',original_unit:'pH',plot_name:'东田',sampled_on:'',page:'2',locator:'检测表第2行',excerpt:'pH 6.2'};
    await handlers.submit({target:form,preventDefault(){}});
    if(sent.path!=='/reports/report1/candidates'||sent.body.expected_revision!==2||sent.body.page!==2||sent.body.sampled_on!==''||sent.body.raw_value!=='6.2')throw new Error('人工转录没有保留原单位/日期/定位与版本');
  })()`,context);
  console.log('PASS: report confirm and manual submissions keep explicit candidate/season bindings');
  vm.runInContext(`(()=>{
    const source={report_id:'r-fixed',extraction_id:'x-fixed',filename:'固定检测.txt',sha256:'fixed-hash',value:'6.5',original_unit:'pH',normalized_unit:'1',sampled_on:'2026-10-10',page:1,locator:'<第3行>',excerpt:'<pH:6.5>',method:'text_pattern',input_revision:8,field:'soil_ph'};
    state.tab='facts';state.run={id:'A',revision:1,status:'completed',questions:[],snapshot:{revision:8,facts:{soil_ph:'6.5'},product:{name:'主产品',revision:1},report_refs:[source]}};
    state.season={id:'other',report_sources:[{...source,filename:'新对象报告'}]};
    const html=runPage();
    for(const value of ['SR 已确认检测来源','固定检测.txt','fixed-hash','/api/reports/r-fixed/original','x-fixed'])if(!html.includes(value))throw new Error('固定报告来源遗漏 '+value);
    if(html.includes('新对象报告')||html.includes('<第3行>')||html.includes('<pH:6.5>'))throw new Error('报告来源读取了当前本季或没有转义');
  })()`,context);
  console.log('PASS: research facts show their fixed confirmed report sources');
  vm.runInContext(`(()=>{
    state.data.user.role='SR';
    const run={id:'recovery',revision:12,status:'limited',candidate_version:3,snapshot:{revision:8},runtime:{modules:[
      {name:'history',status:'ready',result_ref:'result-history',input_revision:8,reused:true,reason:'对象未变化'},
      {name:'evidence',status:'stale',result_ref:'<old-source>',input_revision:8,reused:false,reason:'<资料撤回>'},
      {name:'quantity',status:'pending',result_ref:null,input_revision:9,reused:false,reason:'面积变化'},
      {name:'market',status:'not_requested',result_ref:null,input_revision:8,reused:false,reason:'未请求市场'}],
      changed_fields:['area_mu','<source>'],affected_modules:['quantity','budget','candidate'],reusable_modules:['history'],context_hash:'<context>',current_input_revision:9,generation:2,remaining_refreshes:2,actions_used:7,max_actions:12,max_generations:4,can_refresh:true,can_repair:true,limitation:'仅能有限补查'}};
    const html=runtimePanel(run);
    for(const text of ['研究模块与恢复','历史检索','获准资料','逐行数量','就绪','失效','待处理','未请求','result-history','已复用','对象未变化','R8','R9','7 / 12','2 / 4','剩余恢复 2 次','面积','&lt;source&gt;','&lt;资料撤回&gt;','&lt;old-source&gt;','data-action="refresh-run"','data-action="repair-run"'])if(!html.includes(text))throw new Error('恢复面板遗漏 '+text);
    if(html.includes('<source>')||html.includes('<context>')||html.includes('<资料撤回>'))throw new Error('恢复面板未转义来源或绑定');
    if(runtimePanel({...run,status:'completed'}).includes('data-action="repair-run"'))throw new Error('已完成研究仍可有限补查');
    const active=runtimePanel({...run,status:'running',candidate_version:0,runtime:{...run.runtime,changed_fields:[],affected_modules:['history','evidence','quantity','budget','candidate']}});
    if(active.includes('事实或适用来源已变化')||active.includes('data-action="refresh-run"')||active.includes('data-action="repair-run"'))throw new Error('初始执行被误判为来源过期或出现恢复入口');
    for(const patch of [{formal_id:'P1'},{formal:{version:1}},{runtime:{...run.runtime,can_refresh:false,can_repair:false,remaining_refreshes:0}},{runtime:{...run.runtime,remaining_refreshes:0}},{runtime:{...run.runtime,generation:4}}]){
      const blocked=runtimePanel({...run,...patch});
      if(blocked.includes('data-action="refresh-run"')||blocked.includes('data-action="repair-run"'))throw new Error('正式或预算耗尽研究获得恢复入口');
    }
    if(!runtimePanel({...run,runtime:{...run.runtime,remaining_refreshes:0}}).includes('预算已用尽'))throw new Error('预算耗尽未说明需要新研究');
    state.data.user.role='FR';const readOnly=runtimePanel(run);
    if(readOnly.includes('data-action="refresh-run"')||readOnly.includes('data-action="repair-run"')||!readOnly.includes('result-history'))throw new Error('FR恢复权限或只读模块展示错误');
    state.data.user.role='SR';
  })()`,context);
  console.log('PASS: runtime receipts, drift, budgets and recovery role/status gates render safely');
  vm.runInContext(`(()=>{
    state.tab='audit';state.run={id:'source-drift',revision:12,status:'limited',candidate_version:3,draft:{},snapshot:{revision:8},questions:[],review:{can_confirm:true,candidate_version:3,dimensions:{},issues:[]},runtime:{modules:[],changed_fields:['evidence_catalog'],affected_modules:['evidence','candidate'],reusable_modules:[],context_hash:'ctx',current_input_revision:8,generation:1,remaining_refreshes:3,actions_used:5,max_actions:12,max_generations:4,can_refresh:true,can_repair:true}};
    const html=runPage();
    for(const action of ['confirm','edit-draft','review'])if(html.includes('data-action="'+action+'"'))throw new Error('来源变化仍允许旧草稿写操作 '+action);
    if(canPropose(state.run)||!html.includes('明确刷新'))throw new Error('来源变化未要求明确刷新或仍能提出修改');
    state.run.status='queued';state.run.draft=null;state.run.runtime.affected_modules=[];
    if(!runPage().includes('旧草稿 C3'))throw new Error('重建中未说明旧C保留');
  })()`,context);
  console.log('PASS: source drift requires explicit refresh and rebuilding preserves old candidate identity');
  vm.runInContext(`(()=>{
    state.route='run';state.id='A';state.run={id:'A',revision:12,status:'limited',candidate_version:3,snapshot:{revision:8},runtime:{modules:[],changed_fields:['area_mu'],affected_modules:['quantity','budget','candidate'],reusable_modules:['history','evidence'],context_hash:'ctx<&>',current_input_revision:9,generation:2,remaining_refreshes:2,actions_used:7,max_actions:12,max_generations:4,can_refresh:true,can_repair:true}};
    recoveryDialog('refresh');
    if(!modal.open||modal.dataset.runId!=='A'||modal.dataset.runRevision!=='12'||modal.dataset.inputRevision!=='9'||modal.dataset.contextHash!=='ctx<&>'||modal.dataset.intent!=='refresh'||modal.dataset.routeGeneration!==String(routeGeneration))throw new Error('恢复弹窗未绑定原研究、版本、输入或上下文');
    for(const text of ['旧草稿 C3','历史修订','逐行数量','历史检索','7 / 12','R9'])if(!modal.innerHTML.includes(text))throw new Error('恢复确认遗漏 '+text);
    recoveryDialog('repair');if(modal.dataset.intent!=='repair')throw new Error('补查意图没有单独绑定');
    modal.close();state.data.user.role='FR';recoveryDialog('refresh');if(modal.open)throw new Error('FR可直接打开恢复弹窗');
    state.data.user.role='SR';state.run.formal_id='P1';recoveryDialog('refresh');if(modal.open)throw new Error('正式P可直接打开恢复弹窗');
  })()`,context);
  console.log('PASS: recovery confirmation binds its complete preview and preserves historical candidate context');
  context.form=new Form('recovery-form',{}, {runId:'A',runRevision:'12',inputRevision:'9',contextHash:'ctx-A',intent:'refresh',routeGeneration:'-1'});
  await vm.runInContext(`(async()=>{
    state.route='run';state.id='B';state.run={id:'B',revision:99};state.tab='facts';modal.open=true;
    let finish,sent;api=(path,options)=>{sent={path,body:options.body};return new Promise(resolve=>finish=resolve);};
    const pending=handlers.submit({target:form,preventDefault(){}});finish({id:'A',status:'queued',generation:3,duplicate:false});await pending;
    if(sent.path!=='/runs/A/refresh'||sent.body.expected_revision!==12||sent.body.expected_input_revision!==9||sent.body.expected_context_hash!=='ctx-A'||sent.body.intent!=='refresh')throw new Error('恢复未提交绑定的原预览');
    if(state.run.id!=='B'||state.tab!=='facts'||!modal.open)throw new Error('迟到恢复污染新对象或弹窗');
  })()`,context);
  console.log('PASS: late recovery submits only its original preview and leaves the new route untouched');
  await vm.runInContext(`(async()=>{
    form.dataset.routeGeneration=String(routeGeneration);form.dataset.intent='repair';state.route='run';state.id='A';state.run={id:'A',revision:12};state.tab='discussion';
    const paths=[];api=async(path,options)=>{paths.push(path);if(path.endsWith('/refresh')){if(options.body.intent!=='repair')throw new Error('补查意图被改写');return {id:'A',status:'queued',generation:3,duplicate:true};}if(path==='/runs/A')return {id:'A',revision:13,status:'queued',candidate_version:3};throw new Error('意外读取 '+path);};
    await handlers.submit({target:form,preventDefault(){}});
    if(paths.join('|')!=='/runs/A/refresh|/runs/A'||state.run.revision!==13||state.tab!=='plan')throw new Error('当前恢复未更新研究并显示重建进度');
    const before=state.run;modal.open=true;state.tab='facts';api=async()=>{throw new Error('恢复预览已过期，请重新读取');};
    await handlers.submit({target:form,preventDefault(){}});
    if(state.run!==before||!modal.open||state.tab!=='facts')throw new Error('冲突恢复错误地关闭预览或切换页签');
  })()`,context);
  console.log('PASS: current repair reloads once, and a rejected stale preview leaves state intact');
  await vm.runInContext(`(async()=>{
    form.dataset.routeGeneration=String(routeGeneration);state.route='run';state.id='A';state.run={id:'A',revision:12};state.tab='discussion';
    const oldSummary={id:'A',status:'failed',candidate_version:3};state.data.runs=[oldSummary];
    let finishRead;api=async(path)=>path.endsWith('/refresh')?{id:'A',status:'queued',generation:3}:new Promise(resolve=>finishRead=resolve);
    const pending=handlers.submit({target:form,preventDefault(){}});await Promise.resolve();await Promise.resolve();
    if(!finishRead)throw new Error('恢复成功后没有读取研究');
    state.route='run';state.id='B';state.run={id:'B',revision:99};state.tab='facts';modal.open=true;
    finishRead({id:'A',revision:13,status:'queued'});await pending;
    if(state.run.id!=='B'||state.tab!=='facts'||!modal.open)throw new Error('迟到的恢复详情覆盖另一个研究');
    if(state.data.runs[0]!==oldSummary||oldSummary.status!=='failed'||oldSummary.candidate_version!==3)throw new Error('切换对象后迟到恢复仍改写bootstrap摘要');
  })()`,context);
  console.log('PASS: navigation during the recovery reload preserves the newly selected research');
  await vm.runInContext(`(async()=>{
    form.dataset.routeGeneration=String(routeGeneration);state.route='run';state.id='A';state.run={id:'A',revision:12};
    const other={id:'B',status:'failed',candidate_version:2,customer_name:'另一客户'};
    state.data.runs=[{id:'A',season_id:'season-A',status:'failed',revision:12,candidate_version:3,customer_name:'原客户',plot_name:'原地块',year:2026,crop:'玉米',goal:'当前目标'},other];
    state.customer='customer-A';
    api=async(path)=>path.endsWith('/refresh')?{id:'A',status:'queued',generation:3}:{id:'A',status:'completed',revision:14,candidate_version:4,formal_id:null,error:null};
    await handlers.submit({target:form,preventDefault(){}});
    const summary=state.data.runs[0];
    if(summary.status!=='completed'||summary.candidate_version!==4||summary.revision!==14)throw new Error('Worker已完成恢复但列表仍显示旧failed/C3');
    if(summary.customer_name!=='原客户'||summary.season_id!=='season-A'||summary.plot_name!=='原地块'||state.data.runs[1]!==other||state.customer!=='customer-A')throw new Error('恢复同步丢失客户筛选、摘要元数据或改写其他研究');
    state.customer='';
  })()`,context);
  console.log('PASS: an already completed recovery synchronizes list summaries while preserving scope and metadata');
  vm.runInContext(`(()=>{
    const run={id:'partial',status:'limited',draft:null,state:{partial_candidate:{summary:'<候选摘要>'},_review_feedback:[{code:'EVIDENCE_REQUIRED',field:'measures',close_condition:'<补齐获准资料>'}]},actions:[]};
    const html=progress(run);
    for(const text of ['暂存候选','不可技术确认','EVIDENCE_REQUIRED','&lt;补齐获准资料&gt;'])if(!html.includes(text))throw new Error('受控停止暂存候选缺少处理提示 '+text);
    if(html.includes('<补齐获准资料>')||html.includes('data-action="confirm"'))throw new Error('暂存候选可确认或反馈未转义');
  })()`,context);
  console.log('PASS: stopped partial candidates retain visible review gaps without becoming confirmable drafts');
  vm.runInContext(`(()=>{
    const run={status:'completed',candidate_version:4,stale:false,draft:{},review:{can_confirm:true,candidate_version:4,rule_version:'r1',content_hash:'hash',dimensions:{},issues:[]},runtime:{affected_modules:['evidence','candidate']}};
    for(const html of [reviewPanel(run),structuredExport(run)]){
      if(html.includes('本次程序检查允许进入 SR 技术确认')||html.includes('badge green')||!html.includes('需刷新研究再评价'))throw new Error('来源漂移后的页面或导出仍表示质量评价可确认');
    }
    run.formal_id='P1';
    if(!reviewPanel(run).includes('历史评价快照')||!structuredExport(run).includes('技术确认时的历史评价'))throw new Error('正式历史评价被当前来源漂移覆盖');
  })()`,context);
  console.log('PASS: dependency drift invalidates current review and export claims while keeping formal review history');
})().catch(e=>{console.error(e);process.exitCode=1;});
