"""A small durable research runtime: bounded tools, checkpoints and lease fencing."""
import json
import os
import time
from datetime import datetime, timedelta, timezone

from .domain import compare_quotes, decimal, dumps, timestamp, uid
from .model import Action, choose_action, generate_candidate
from .knowledge import search_documents
from .review import save_review, inspect_candidate
from .materials import budget_calculation, quantity_calculation, material_text, material_manifest
from .questions import missing_questions
from .dependencies import dependency_context, capture_snapshot, remember_result, market_expired

LEASE_SECONDS = 120
MAX_ACTIONS = 12


def execution_error(db, run, *, candidate=None):
    """Every provider/tool boundary checks the same live authority and input binding."""
    state = json.loads(run['state']) if isinstance(run['state'],str) else run['state']
    execution_actor = state.get('_execution_actor_id',run['actor_id'])
    authority = db.execute('''SELECT 1 FROM users u JOIN assignments a ON a.user_id=u.id
        JOIN seasons s ON s.customer_id=a.customer_id WHERE u.id=? AND u.role='SR' AND s.id=?''',
        (execution_actor,run['season_id'])).fetchone()
    if authority is None:
        return '当前 SR 职责已撤销，研究已停止；请由当前负责人新建研究'
    season = db.execute('''SELECT s.*,c.name customer_name,c.region,p.name plot_name FROM seasons s
        JOIN customers c ON c.id=s.customer_id JOIN plots p ON p.id=s.plot_id WHERE s.id=?''',(run['season_id'],)).fetchone()
    snapshot = json.loads(run['snapshot']) if isinstance(run['snapshot'],str) else run['snapshot']
    if season is None or season['revision'] != snapshot['revision']:
        return '本季资料已变化，请基于新版本明确刷新或新建研究'
    current = dict(season) | {'facts':json.loads(season['facts'])}
    try:
        captured = capture_snapshot(db,current,snapshot)
        context = dependency_context(db,captured,state)
    except Exception:
        return '当前适用目录或报告来源不可用，请核对后明确刷新'
    if state.get('_context'):
        changed = {name for name in context if state['_context'].get(name) != context[name]}
        # Preserve the existing final-candidate live review: a source revoked during
        # a provider call is retained as a blocked historical claim, never confirmable.
        if changed:
            blocked_candidate = (candidate is not None and changed <= {'evidence','candidate'}
                and not inspect_candidate(db,dict(run)|{'snapshot':snapshot},candidate)['can_confirm'])
            if not blocked_candidate:
                return '研究所依赖的目录或报告来源已变化，请查看影响并明确刷新'
    if state.get('include_market') and market_expired(state):
        return '市场演示已超过七日时效，请明确刷新后重新比较'
    return None


def fail_execution(db, run_id, job_id, message):
    db.execute("UPDATE runs SET status='failed',error=?,revision=revision+1 WHERE id=?",(message,run_id))
    db.execute("UPDATE jobs SET status='done',token=NULL WHERE id=?",(job_id,))


def history_differences(content, facts):
    return [{'field':field,'label':label,'historical':content.get(field),'current':facts.get(field)}
            for field,label in [('area_mu','面积'),('budget_cny','公司合作预算'),('rate_kg_per_mu','SR确认用量'),
                                ('variety','品种要求'),('additional_materials','追加产品明细')]
            if content.get(field) != facts.get(field)]


def history_for(db, season):
    rows = db.execute('''SELECT * FROM history WHERE year<? AND crop=? AND
                      (plot_id=? OR (published=1 AND region=?))
                      ORDER BY CASE WHEN plot_id=? THEN 0 ELSE 1 END, year DESC''',
                      (season['year'], season['crop'], season['plot_id'], season['region'], season['plot_id'])).fetchall()
    facts = season['facts'] if isinstance(season['facts'], dict) else json.loads(season['facts'])
    result = []
    for row in rows:
        item = dict(row)
        content = json.loads(item.pop('content'))
        differences = history_differences(content,facts)
        result.append(item | content | {'relation':'同地块历史' if row['plot_id']==season['plot_id'] else '已发布流程参考（适配未验证）',
                                       'differences':differences, 'reference_id':row['id']})
    return result


def search_knowledge(db, query, crop='通用', region='通用', customer_id=None):
    return search_documents(db, query, crop, region, customer_id)


def market_demo(crop, region, storage):
    now = datetime.now(timezone.utc)
    def quote(qid, buyer, price, freight, deduction, age=0):
        return dict(id=qid, buyer=buyer, price=price, freight=freight, deduction=deduction,
                    crop='玉米', region='吉林', unit='CNY/kg', moisture='14',
                    as_of=(now-timedelta(days=age)).isoformat(), source='合成演示报价 · 非实时行情')
    quotes = [quote('demo-a','示例收购点 A','2.50','0.08','0.02'),
              quote('demo-b','示例收购点 B','2.56','0.16','0.03'),
              quote('demo-c','示例收购点 C','2.65',None,'0.02'),
              quote('demo-old','过期演示报价','2.80','0.10','0.01',10)]
    return compare_quotes(quotes, crop=crop, region=region, storage=storage, now=now) | {'demo':True}


def claim_job(store, now=None):
    now = time.time() if now is None else now
    with store.write() as db:
        row = db.execute('''SELECT j.* FROM jobs j JOIN runs r ON r.id=j.run_id
                          WHERE (j.status='queued' OR (j.status='running' AND j.lease_until<?))
                          AND r.status IN ('queued','running') ORDER BY j.rowid LIMIT 1''', (now,)).fetchone()
        if row is None:
            return None
        run = db.execute('SELECT * FROM runs WHERE id=?',(row['run_id'],)).fetchone()
        error = execution_error(db,run)
        if error:
            fail_execution(db,run['id'],row['id'],error)
            return None
        if row['attempts'] >= 3:
            db.execute("UPDATE jobs SET status='failed' WHERE id=?", (row['id'],))
            db.execute("UPDATE runs SET status='failed',error=?,revision=revision+1 WHERE id=?", ('作业已达三次领取上限，请人工检查后新建研究',row['run_id']))
            return None
        token = uid()
        db.execute("UPDATE jobs SET status='running',token=?,lease_until=?,attempts=attempts+1 WHERE id=?", (token,now+LEASE_SECONDS,row['id']))
        db.execute("UPDATE runs SET status='running',revision=revision+1 WHERE id=?", (row['run_id'],))
        return dict(row) | {'token':token, 'lease_until':now+LEASE_SECONDS}


def commit_step(store, job, updates, *, now=None, action=None):
    now = time.time() if now is None else now
    with store.write() as db:
        lease = db.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone()
        if lease is None or lease['status']!='running' or lease['token']!=job['token'] or lease['lease_until']<now:
            return False
        run = db.execute('SELECT * FROM runs WHERE id=?', (job['run_id'],)).fetchone()
        if run['status'] != 'running':
            return False
        snapshot = json.loads(run['snapshot'])
        error = execution_error(db,run,candidate=updates.get('draft'))
        if error:
            fail_execution(db,run['id'],job['id'],error)
            return False
        allowed = {'state','status','draft','error'}
        if set(updates)-allowed:
            raise ValueError('非法检查点字段')
        status = updates.get('status', 'running')
        values = dict(updates)
        state = updates.get('state')
        if state is not None:
            parsed = dict(run) | {'snapshot':snapshot}
            outputs = {'query_history':('history','history'),'query_evidence':('evidence','evidence'),
                       'calculate_quantity':('quantity','quantity'),'calculate':('budget','calculation'),
                       'delegate_market':('market','market')}
            if action and action['type'] in outputs:
                module, field = outputs[action['type']]
                remember_result(db,parsed,state,module,state[field],job['generation'])
            if 'draft' in updates:
                remember_result(db,parsed,state,'candidate',updates['draft'],job['generation'])
        if 'draft' in updates:
            parsed = dict(run) | {'snapshot':snapshot}
            review = save_review(db, parsed, updates['draft'], run['candidate_version']+1)
            status = 'completed' if review['can_confirm'] else 'limited'
            values['status'] = status
        for field in ('state','draft'):
            if field in values:
                values[field] = dumps(values[field])
        if values:
            db.execute('UPDATE runs SET '+','.join(k+'=?' for k in values)+',revision=revision+1 WHERE id=?', (*values.values(),run['id']))
        if action:
            sequence = db.execute('SELECT COUNT(*) FROM actions WHERE run_id=?',(run['id'],)).fetchone()[0]+1
            db.execute('''INSERT INTO actions(id,run_id,sequence,type,purpose,output,created_at,generation)
                VALUES(?,?,?,?,?,?,?,?)''',(uid(),run['id'],sequence,action['type'],action['purpose'],dumps(action['output']),timestamp(),job['generation']))
        if status == 'waiting_input':
            existing = db.execute("SELECT id FROM questions WHERE run_id=? AND status='open'",(run['id'],)).fetchone()
            if not existing:
                for question in missing_questions(snapshot):
                    qid=uid()
                    revision=db.execute('SELECT COALESCE(MAX(revision),0)+1 FROM questions WHERE run_id=? AND field=?',
                                        (run['id'],question['field'])).fetchone()[0]
                    db.execute('INSERT INTO questions VALUES(?,?,?,?,?,?,NULL,NULL)',
                               (qid,run['id'],revision,question['field'],question['prompt'],'open'))
                    db.execute('INSERT INTO question_contexts VALUES(?,?,?,?,?,?)',
                               (qid,question['answer_type'],question['purpose'],question['impact'],dumps(question['options']),snapshot['revision']))
        if 'draft' in updates:
            version = run['candidate_version']+1
            db.execute('UPDATE runs SET candidate_version=? WHERE id=?',(version,run['id']))
            db.execute('INSERT INTO candidate_versions VALUES(?,?,?,?,?,?)',(run['id'],version,dumps(updates['draft']),None,'研究生成草稿',timestamp()))
        db.execute('UPDATE jobs SET status=?,lease_until=? WHERE id=?', ('running' if status=='running' else 'done', now+LEASE_SECONDS,job['id']))
        return True


def available_actions(snapshot, state):
    if 'history' not in state:
        return ['query_history']
    actions = []
    if 'evidence' not in state:
        actions.append('query_evidence')
    if state.get('include_market') and 'market' not in state:
        actions.append('delegate_market')
    if missing_questions(snapshot):
        actions.append('ask_human')
    elif 'quantity' not in state:
        actions.append('calculate_quantity')
    elif 'calculation' not in state:
        actions.append('calculate')
    return actions or ['repair_draft' if state.get('_repair_draft') else 'submit_result']


def build_draft(snapshot, state):
    facts = snapshot['facts']
    calc = state['calculation']
    warnings = ['演示资料及用量仅用于验证系统流程，实际方案需专业依据与 SR 复核。',
                '金额仅为本次示例产品合作小计；种子、农机、租金等未知，不代表完整成本或净收益。',
                '技术确认后仍需商务批准、适用付款条件和服务交接，MVP 不提供执行放行。']
    if facts.get('soil_ph') is None:
        warnings.append('测土 pH 未知；本草稿不推导依赖检测的专业处方。')
    if not state['history']:
        warnings.append('没有可用历史方案，依据本季确认资料与流程模板起草。')
    budget = facts.get('budget_cny')
    over_budget = budget is not None and decimal(calc.get('cooperation_total_cny',calc['cost_cny'])) > decimal(budget)
    if budget is None:
        warnings.append('公司合作预算未知，只展示已知产品小计。')
    if over_budget:
        warnings.append('已知产品小计超过确认预算，需要 SR 调整专业方案或确认新的预算后重研。')
    measures = [{'id':'confirmed-input', 'kind':'confirmed_input',
                 'text':f"SR 本季确认用量 {facts['rate_kg_per_mu']} kg/亩，来源：{facts['rate_source']}；按确认面积 {facts['area_mu']} 亩核算产品总量 {calc['total_kg']} kg、金额 {calc['cost_cny']} CNY。",
                 'fact_refs':['area_mu','rate_kg_per_mu','rate_source','product_id'],
                 'calculation_refs':[calc['id']], 'evidence_refs':[], 'conditions':[],
                 'history_refs':[], 'professional_judgment':False}]
    for line in calc.get('additional_lines',[]):
        measures.append({'id':'confirmed-material-'+line['id'],'kind':'confirmed_material','material_line_id':line['id'],
                         'text':material_text(snapshot,line),'fact_refs':['area_mu','additional_materials'],
                         'calculation_refs':[calc['id']],'evidence_refs':[],'conditions':[],
                         'history_refs':[],'professional_judgment':False})
    for doc in state['evidence']:
        if doc['kind'] == 'procedure' and doc['purpose'] == 'plan_research':
            for index, claim in enumerate(doc['supported_claims']):
                measures.append({'id':doc['id']+'-'+str(index), 'kind':'procedure', 'text':claim,
                                 'evidence_refs':[doc['id']], 'fact_refs':[], 'calculation_refs':[],
                                 'conditions':doc.get('conditions',[]), 'history_refs':[], 'professional_judgment':False})
    decisions = [{'reference_id':h['reference_id'], 'decision':'review_and_adjust' if h['differences'] else 'reference_only',
                  'reasons':[d['label']+'与本季不同，沿用前须重新确认' for d in h['differences']] + ['历史实际效果未验证，不自动沿用专业用量。']}
                 for h in state['history']]
    return {'schema_version':2, 'generation':{'mode':'rule_generated','contract_version':'candidate-v2'},
            'summary':f"围绕“{snapshot.get('goal','本季技术方案')}”，使用本季 R{snapshot['revision']} 确认输入核算，逐项审查 {len(decisions)} 条历史参考；流程措施见对应资料引用。专业用量仅解释 SR 确认值。",
            'variety':facts.get('variety'),
            'constraints':{k:facts.get(k) for k in ('variety','budget_cny','budget_basis')},
            'measures':measures, 'history_decisions':decisions, 'pending_decisions':[],
            'materials':material_manifest(snapshot,calc),
            'calculation_refs':[calc['id']], 'limitations':warnings,
            'product':snapshot['product'], 'calculation':calc, 'over_budget':over_budget,
            'stages':[{'name':'播前核对','timing':'播种前，由 SR 确认日期','action':'核对种子、地块、本季专业依据及适用供应条件。'},
                      {'name':'阶段服务','timing':'按本季生育进度由 SR 安排','action':'仅参考已确认参数；具体施用阶段与操作标准由 SR 专业复核后确定。'},
                      {'name':'田间巡查','timing':'按现场情况记录','action':'SR/FR 共用巡田与异常记录，计划和实际分开保存。'},
                      {'name':'季末复盘','timing':'季末','action':'补齐实际作业与收获资料，留存调整理由供下一季参考。'}],
            'history_refs':[h['reference_id'] for h in state['history']],
            'evidence_refs':[e['id'] for e in state['evidence']], 'warnings':warnings,
            'input_revision':snapshot['revision'], 'market':state.get('market')}


def model_draft(snapshot,state):
    draft=build_draft(snapshot,state)
    fixed=[measure for measure in draft['measures'] if measure['kind'] in ('confirmed_input','confirmed_material')]
    catalog={measure['id']:measure for measure in draft['measures'] if measure['kind'] not in ('confirmed_input','confirmed_material')}
    proposal=generate_candidate({'goal':snapshot.get('goal'),'snapshot':snapshot,
                                 'available_measures':list(catalog.values()),'history':state['history'],
                                 'evidence':state['evidence'],'calculation':state['calculation'],
                                 'protocol_feedback':state.get('_protocol_feedback'),
                                 'review_feedback':state.get('_review_feedback',[]),
                                 'previous_candidate':state.get('partial_candidate')})
    selected=proposal.selected_measure_ids
    history_ids=[item.reference_id for item in proposal.history_decisions]
    if (len(set(selected))!=len(selected) or any(mid not in catalog for mid in selected)
            or len(set(history_ids))!=len(history_ids)
            or set(history_ids)!={h['reference_id'] for h in state['history']}):
        raise ValueError('模型候选包含未知/重复措施或不完整历史引用')
    measures=fixed+[catalog[mid] for mid in selected]
    evidence_refs=list(dict.fromkeys(ref for measure in measures for ref in measure['evidence_refs']))
    return draft | {'summary':proposal.summary,'stages':[stage.model_dump() for stage in proposal.stages],
                    'measures':measures,'evidence_refs':evidence_refs,
                    'history_decisions':[item.model_dump() for item in proposal.history_decisions],
                    'pending_decisions':proposal.pending_decisions,
                    'generation':{'mode':'model_generated','model':os.getenv('AGRIPLAN_MODEL'),
                                  'contract_version':'model-candidate-v1'}}


def work_once(store):
    job = claim_job(store)
    if not job:
        return False
    try:
        for _ in range(MAX_ACTIONS+1):
            with store.read() as db:
                row = db.execute('SELECT * FROM runs WHERE id=?',(job['run_id'],)).fetchone()
                if row['status'] != 'running':
                    return True
                snapshot, state = json.loads(row['snapshot']), json.loads(row['state'])
                count = db.execute('SELECT COUNT(*) FROM actions WHERE run_id=? AND generation=?',(row['id'],job['generation'])).fetchone()[0]
                if not state.get('_context'):
                    state['_context'] = dependency_context(db,snapshot,state)
                    state['modules'] = {}
            if count >= MAX_ACTIONS:
                commit_step(store, job, {'status':'limited','error':'已达到动作预算，需人工复核后新建研究'})
                return True
            allowed = available_actions(snapshot, state)
            allowed.append('stop')
            if not commit_step(store,job,{}):
                return True
            if row['mode'] == 'llm':
                try:
                    action = choose_action({'goal':row['goal'], 'snapshot':snapshot, 'state':state,
                                            'protocol_feedback':state.get('_protocol_feedback')}, allowed)
                except (ValueError,KeyError,TypeError):
                    if state.get('_protocol_repairs'):
                        raise ValueError('协议修复机会已用尽') from None
                    state.update(_protocol_repairs=1,_protocol_feedback='上次响应不符合当前协议；只返回约定 JSON 字段并选择 allowed 中的动作。')
                    if not commit_step(store,job,{'state':state},action={'type':'repair_protocol',
                        'purpose':'一次受控协议修复','output':{'error_category':'invalid_protocol','feedback':state['_protocol_feedback']}}):
                        return True
                    continue
            else:
                reasons = {'query_history':'优先检索同地块往季方案，并区分本季差异', 'query_evidence':'检索获准资料并保留来源定位',
                           'ask_human':'关键专业用量缺失，等待 SR 明确确认', 'calculate':'使用本季确认参数及产品价格快照进行确定性核算',
                           'calculate_quantity':'按本季确认面积与逐行专业用量核算数量',
                           'delegate_market':'按需委派限定的演示市场报价比较', 'submit_result':'依据与核算齐备，提交供 SR 编辑复核的主建议',
                           'repair_draft':'使用有限补查后的当前依据重建草稿'}
                action = Action(type=allowed[0], purpose=reasons[allowed[0]])
            updates, output = {}, {}
            # A provider may have changed responsibility or facts while the planner was running.
            if not commit_step(store,job,{}):
                return True
            candidate = None
            if action.type in ('submit_result','repair_draft') and row['mode']=='llm':
                try:
                    candidate = model_draft(snapshot,state)
                except (ValueError,KeyError,TypeError):
                    if state.get('_protocol_repairs'):
                        raise ValueError('协议修复机会已用尽') from None
                    state.update(_protocol_repairs=1,_protocol_feedback='上次候选不符合约定字段或引用白名单；仅选择服务器提供的措施并完整关联历史。')
                    if not commit_step(store,job,{'state':state},action={'type':'repair_protocol',
                        'purpose':'一次受控候选协议修复','output':{'error_category':'invalid_candidate_protocol','feedback':state['_protocol_feedback']}}):
                        return True
                    continue
            with store.read() as db:
                if action.type == 'query_history':
                    state['history'] = history_for(db, snapshot)
                    output = {'reference_ids':[h['reference_id'] for h in state['history']], 'count':len(state['history'])}
                elif action.type == 'query_evidence':
                    query = snapshot['crop']+' 本季方案 核对 流程' if state.get('_quality_repairs') else row['goal']
                    state['_evidence_query'] = query
                    state['evidence'] = [doc for doc in search_knowledge(db,query,snapshot['crop'],snapshot['region'],snapshot['customer_id'])
                                         if doc['purpose'] in ('plan_research','knowledge')]
                    output = {'evidence_ids':[e['id'] for e in state['evidence']],
                              'query':query,'repair':bool(state.get('_quality_repairs')),'purpose':'plan_research'}
                elif action.type == 'ask_human':
                    updates['status'] = 'waiting_input'
                    output = {'field':'rate_kg_per_mu', 'reason':'SR 确认参数及来源后才能核算'}
                elif action.type == 'calculate_quantity':
                    state['quantity'] = quantity_calculation(snapshot)
                    output = state['quantity']
                elif action.type == 'calculate':
                    state['calculation'] = budget_calculation(snapshot,state['quantity'],
                        state.get('modules',{}).get('quantity',{}).get('result_ref'))
                    output = state['calculation']
                elif action.type == 'delegate_market':
                    state['market'] = market_demo(snapshot['crop'], snapshot['region'], snapshot['facts']['storage'])
                    output = {'best_quote_id':state['market']['best_quote_id'], 'demo':True}
                elif action.type in ('submit_result','repair_draft'):
                    candidate = candidate if candidate is not None else build_draft(snapshot,state)
                    review = inspect_candidate(db,dict(row)|{'snapshot':snapshot},candidate)
                    repairable = {'EVIDENCE_REQUIRED','EVIDENCE_NOT_FOUND','EVIDENCE_NOT_PUBLISHED',
                        'EVIDENCE_REVOKED','EVIDENCE_EXPIRED','EVIDENCE_NOT_APPLICABLE',
                        'PROCEDURE_SUPPORT_REQUIRED','PROFESSIONAL_SUPPORT_REQUIRED'}
                    # A live revocation remains a blocked candidate, preserving the original
                    # candidate-review contract. The next explicit refresh uses current sources.
                    live_context = dependency_context(db,snapshot,state)
                    source_changed = live_context != state['_context']
                    if (not source_changed and not state.get('_quality_repairs') and
                            any(issue['code'] in repairable for issue in review['issues'])):
                        state.update(_quality_repairs=1,_repair_draft=True)
                        state['partial_candidate'] = candidate
                        state['_review_feedback'] = [{'code':issue['code'],'field':issue['field'],
                            'close_condition':issue['close_condition']} for issue in review['issues'] if issue['severity']=='blocking']
                        state.pop('evidence',None)
                        state.get('modules',{}).pop('evidence',None)
                        output = {'result':'依据不足，登记一次资料补查和草稿重建','issue_codes':sorted({i['code'] for i in review['issues'] if i['code'] in repairable})}
                    else:
                        updates.update(status='completed',draft=candidate)
                        state.pop('_repair_draft',None)
                        output = {'result':'供 SR 复核的草稿，不是执行许可','generation':candidate['generation']}
                elif action.type == 'stop':
                    updates.update(status='limited',error='研究已受控停止，待 SR 处理缺口后明确恢复或新建研究')
                    state['_stopped'] = True
                    output = {'result':'受控停止，不形成可确认方案'}
            updates['state'] = state
            if not commit_step(store,job,updates,action={'type':action.type,'purpose':action.purpose,'output':output}):
                return True
            if updates.get('status') in ('waiting_input','completed','limited'):
                return True
        commit_step(store,job,{'status':'limited','error':'主动执行动作上限已到达'})
    except Exception as exc:
        # Never persist provider responses, credentials or arbitrary exception text.
        message = '模型/工具执行失败（'+type(exc).__name__+'）。请检查服务端配置或输入后重试；已有资料与轨迹已保留。'
        commit_step(store,job,{'status':'failed','error':message})
    return True
