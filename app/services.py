"""Business use cases and revision-checked transactions."""
import hashlib
import json
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException

from .domain import Facts, configured_mode, dumps, timestamp, uid
from .research import history_for, market_demo, search_knowledge
from .review import inspect_candidate, save_review, latest_review
from .knowledge import evidence_status
from .questions import normalize_answer


def require_sr(actor):
    if actor['role'] != 'SR':
        raise HTTPException(403, '需要 SR 技术经理权限')


def season_for(db, season_id, actor):
    row = db.execute('''SELECT s.*,c.name customer_name,c.region,p.name plot_name
                       FROM seasons s JOIN customers c ON c.id=s.customer_id
                       JOIN plots p ON p.id=s.plot_id
                       JOIN assignments a ON a.customer_id=c.id
                       WHERE s.id=? AND a.user_id=?''',(season_id,actor['id'])).fetchone()
    if row is None:
        raise HTTPException(404, '对象不存在或不在您的责任范围')
    return dict(row) | {'facts':json.loads(row['facts'])}


def run_for(db, run_id, actor):
    row = db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '研究不存在或无访问权限')
    season_for(db,row['season_id'],actor)
    run = dict(row)
    for key in ('snapshot','state','draft'):
        run[key] = json.loads(run[key]) if run[key] else None
    return run


def expect(actual, expected):
    if actual != expected:
        raise HTTPException(409, '内容版本已变化，请刷新后查看差异再提交')


def audit(db, season_id, run_id, actor_id, event, payload):
    db.execute('INSERT INTO audit VALUES(?,?,?,?,?,?,?)',(uid(),season_id,run_id,actor_id,event,dumps(payload),timestamp()))


def plan_view(row):
    if row is None:
        return None
    result = dict(row)
    result['content'] = json.loads(result['content'])
    return result | {'technical_status':'confirmed','business_status':'not_connected','execution_status':'blocked'}


def season_detail(store, season_id, actor):
    with store.read() as db:
        season = season_for(db,season_id,actor)
        from .reports import reports_for, confirmed_sources
        season['reports']=reports_for(db,season)
        season['report_sources']=confirmed_sources(db,season_id,season['revision'])
        season['history'] = history_for(db,season)
        season['plans'] = [plan_view(r) for r in db.execute('SELECT * FROM plans WHERE season_id=? ORDER BY version DESC',(season_id,))]
        season['fact_versions'] = [dict(r) | {'facts':json.loads(r['facts'])} for r in db.execute('SELECT * FROM fact_versions WHERE season_id=? ORDER BY revision DESC',(season_id,))]
        return season


def bootstrap(store, actor):
    with store.read() as db:
        customers = [dict(r) for r in db.execute('SELECT c.* FROM customers c JOIN assignments a ON a.customer_id=c.id WHERE a.user_id=? ORDER BY c.name',(actor['id'],))]
        seasons = [dict(r) for r in db.execute('''SELECT s.*,c.name customer_name,c.region,p.name plot_name FROM seasons s
                       JOIN customers c ON c.id=s.customer_id JOIN plots p ON p.id=s.plot_id
                       JOIN assignments a ON a.customer_id=s.customer_id WHERE a.user_id=? ORDER BY c.name,s.year DESC''',(actor['id'],))]
        for s in seasons:
            s['facts'] = json.loads(s['facts'])
        runs = [dict(r) for r in db.execute('''SELECT r.id,r.season_id,r.goal,r.status,r.revision,r.candidate_version,r.formal_id,r.mode,r.error,r.created_at,
                      c.name customer_name,p.name plot_name,s.year,s.crop FROM runs r JOIN seasons s ON r.season_id=s.id
                      JOIN customers c ON c.id=s.customer_id JOIN plots p ON p.id=s.plot_id
                      JOIN assignments a ON a.customer_id=s.customer_id WHERE a.user_id=? ORDER BY r.rowid DESC''',(actor['id'],))]
        products = [dict(r) for r in db.execute('SELECT * FROM products WHERE active=1')]
        plans = [dict(r) for r in db.execute('''SELECT p.id,p.season_id,p.version,p.created_at FROM plans p
                      JOIN seasons s ON s.id=p.season_id JOIN assignments a ON a.customer_id=s.customer_id
                      WHERE a.user_id=? ORDER BY p.created_at DESC''',(actor['id'],))]
        return {'user':actor,'customers':customers,'seasons':seasons,'runs':runs,'products':products,
                'plans':plans,'mode':configured_mode(),'demo_data':True,'database_version':6}


def create_customer(store, actor, request):
    require_sr(actor)
    cid,pid,sid = uid(),uid(),uid()
    facts = Facts(area_mu=request.area_mu).model_dump(mode='json')
    with store.write() as db:
        db.execute('INSERT INTO customers VALUES(?,?,?)',(cid,request.name,request.region))
        db.execute('INSERT INTO assignments VALUES(?,?)',(actor['id'],cid))
        db.execute('INSERT INTO plots VALUES(?,?,?)',(pid,cid,request.plot_name))
        db.execute('INSERT INTO seasons VALUES(?,?,?,?,?,?,?,NULL)',(sid,pid,cid,request.year,request.crop,1,dumps(facts)))
        db.execute('INSERT INTO fact_versions VALUES(?,?,?,?,?,?)',(sid,1,dumps(facts),actor['id'],'SR 新建地块季节',timestamp()))
        audit(db,sid,None,actor['id'],'customer_created',{'customer_id':cid,'plot_id':pid})
    return {'customer_id':cid,'plot_id':pid,'season_id':sid}


def create_next_season(store, season_id, actor, request):
    require_sr(actor)
    with store.write() as db:
        previous = season_for(db,season_id,actor)
        if request.year <= previous['year']:
            raise HTTPException(422,'下一种植季年份须晚于当前年份')
        if db.execute('SELECT 1 FROM seasons WHERE plot_id=? AND year=?',(previous['plot_id'],request.year)).fetchone():
            raise HTTPException(409,'该地块该年份已有种植季，请打开已有资料')
        sid = uid()
        facts = Facts(area_mu=request.area_mu).model_dump(mode='json')
        db.execute('INSERT INTO seasons VALUES(?,?,?,?,?,?,?,NULL)',(sid,previous['plot_id'],previous['customer_id'],request.year,request.crop,1,dumps(facts)))
        db.execute('INSERT INTO fact_versions VALUES(?,?,?,?,?,?)',(sid,1,dumps(facts),actor['id'],'SR 新建种植季并明确确认面积',timestamp()))
        audit(db,sid,None,actor['id'],'season_created',{'previous_season_id':season_id})
        return {'season_id':sid}


def edit_season(store, season_id, actor, request):
    require_sr(actor)
    facts = request.facts.model_dump(mode='json')
    with store.write() as db:
        season = season_for(db,season_id,actor)
        expect(season['revision'],request.expected_revision)
        revision = season['revision']+1
        db.execute('UPDATE seasons SET facts=?,revision=? WHERE id=?',(dumps(facts),revision,season_id))
        db.execute('INSERT INTO fact_versions VALUES(?,?,?,?,?,?)',(season_id,revision,dumps(facts),actor['id'],request.source,timestamp()))
        from .reports import inherit_sources
        inherit_sources(db,season_id,season['revision'],revision,season['facts'],facts)
        audit(db,season_id,None,actor['id'],'facts_updated',{'from_revision':season['revision'],'to_revision':revision,'source':request.source})
    return {'id':season_id,'revision':revision}


def create_research(store, actor, request, key):
    require_sr(actor)
    if not key or len(key)>160:
        raise HTTPException(400,'请提供 1—160 字符的 Idempotency-Key')
    payload=request.model_dump(mode='json')
    if not payload.get('required_fields'):
        payload.pop('required_fields',None)  # Preserve pre-v3 lost-response retries.
    digest = hashlib.sha256(dumps(payload).encode()).hexdigest()
    with store.write() as db:
        old = db.execute('SELECT * FROM idempotency WHERE actor_id=? AND key=?',(actor['id'],key)).fetchone()
        if old:
            if old['payload_hash'] != digest:
                raise HTTPException(409,'相同幂等键已用于不同请求')
            response = json.loads(old['response'])
            run_for(db,response['id'],actor)
            return response
        season = season_for(db,request.season_id,actor)
        expect(season['revision'],request.expected_revision)
        from .dependencies import capture_snapshot, dependency_context
        snapshot = capture_snapshot(db,season,{'goal':request.goal,'required_fields':request.required_fields})
        state = {'include_market':request.include_market,'modules':{},'_execution_actor_id':actor['id']}
        state['_context'] = dependency_context(db,snapshot,state)
        rid = uid()
        db.execute('''INSERT INTO runs(id,season_id,actor_id,goal,status,revision,snapshot,state,mode,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''',(rid,request.season_id,actor['id'],request.goal,'queued',1,dumps(snapshot),dumps(state),configured_mode(),timestamp()))
        db.execute('INSERT INTO jobs VALUES(?,?,?,? ,NULL,NULL,0)',(uid(),rid,1,'queued'))
        response = {'id':rid,'status':'queued','revision':1}
        db.execute('INSERT INTO idempotency VALUES(?,?,?,?)',(actor['id'],key,digest,dumps(response)))
        audit(db,request.season_id,rid,actor['id'],'research_started',{'input_revision':season['revision'],'include_market':request.include_market})
        return response


def run_detail(store, run_id, actor):
    with store.read() as db:
        run = run_for(db,run_id,actor)
        current = season_for(db,run['season_id'],actor)
        run.update(stale=current['revision']!=run['snapshot']['revision'],current_fact_revision=current['revision'])
        run['questions'] = [dict(r) | {'answer':json.loads(r['answer']) if r['answer'] else None} for r in db.execute('SELECT * FROM questions WHERE run_id=? ORDER BY rowid',(run_id,))]
        for question in run['questions']:
            context=db.execute('SELECT * FROM question_contexts WHERE question_id=?',(question['id'],)).fetchone()
            if context:
                question.update(dict(context))
                question['options']=json.loads(context['options'])
            else:
                question.update(answer_type='number',purpose='旧版问题：确认本季产品用量以完成核算。',
                                impact='缺少确认用量时不能恢复核算；旧版未记录问题初始事实版本。',
                                options=[],input_revision=None,metadata_mode='legacy_compatibility')
        run['actions'] = [dict(r) | {'output':json.loads(r['output'])} for r in db.execute('SELECT * FROM actions WHERE run_id=? ORDER BY sequence',(run_id,))]
        run['versions'] = [dict(r) | {'content':json.loads(r['content'])} for r in db.execute('SELECT * FROM candidate_versions WHERE run_id=? ORDER BY version DESC',(run_id,))]
        run['audit'] = [dict(r) | {'payload':json.loads(r['payload'])} for r in db.execute('SELECT * FROM audit WHERE run_id=? ORDER BY rowid DESC',(run_id,))]
        run['messages'] = [dict(r) for r in db.execute('SELECT * FROM messages WHERE run_id=? ORDER BY rowid',(run_id,))]
        for message in run['messages']:
            context = db.execute('SELECT * FROM answer_contexts WHERE message_id=?',(message['id'],)).fetchone()
            if context:
                message.update(category=context['category'],refs=json.loads(context['refs']),versions=json.loads(context['versions']),mode=context['mode'])
        from .discussion import proposals_for
        run['proposals'] = proposals_for(db,run_id)
        run['review'] = latest_review(db,run_id)
        if run['draft'] and not run['formal_id']:
            fresh = inspect_candidate(db,run,run['draft'])
            run['saved_review'] = run['review']
            run['review'] = fresh | {'candidate_version':run['candidate_version'],'created_at':timestamp(),'mode':'live_recheck'}
        run['formal'] = plan_view(db.execute('SELECT * FROM plans WHERE id=?',(run['formal_id'],)).fetchone()) if run['formal_id'] else None
        run['evidence_statuses'] = []
        # Preserve the historical draft/plan and its review; current source status is separate.
        for evidence_id in (run['draft'] or {}).get('evidence_refs',[]):
            doc,error=evidence_status(db,evidence_id,run['snapshot']['crop'],run['snapshot']['region'],run['snapshot']['customer_id'])
            run['evidence_statuses'].append({'id':evidence_id,'error':error,'status':doc['status'] if doc else 'unavailable','document':doc})
        from .runtime import preview
        run['runtime'] = preview(db,run,current,actor)
        return run


def answer_question(store, run_id, actor, request):
    require_sr(actor)
    with store.write() as db:
        run = run_for(db,run_id,actor)
        question = db.execute('SELECT * FROM questions WHERE id=? AND run_id=?',(request.question_id,run_id)).fetchone()
        if not question:
            raise HTTPException(404,'问题不属于本任务')
        if run['status']=='cancelled' or question['status']=='superseded':
            raise HTTPException(409,'研究已取消，不能再提交回答')
        season = season_for(db,run['season_id'],actor)
        expect(season['revision'],run['snapshot']['revision'])
        try:
            value=normalize_answer(question['field'],request.value)
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from None
        saved={'value':value,'source':request.source}
        expect(question['revision'],request.question_revision)
        if question['status']=='answered':
            if json.loads(question['answer']) != saved:
                raise HTTPException(409,'该问题已保存不同回答，请通过资料修改发起新研究')
            remaining=db.execute("SELECT count(*) FROM questions WHERE run_id=? AND status='open'",(run_id,)).fetchone()[0]
            return {'saved':True,'run_id':run_id,'status':run['status'],'remaining_questions':remaining,'message':'已有回答已保存，无需重复填写'}
        if run['status'] != 'waiting_input':
            raise HTTPException(409,'研究当前不接受回答')
        from .runtime import generation_for
        from .dependencies import MAX_GENERATIONS
        remaining_before=db.execute("SELECT count(*) FROM questions WHERE run_id=? AND status='open'",(run_id,)).fetchone()[0]
        if remaining_before==1 and generation_for(db,run_id)>=MAX_GENERATIONS:
            raise HTTPException(409,'已达到四代预算，请新建研究；回答不会消耗或重置研究预算')
        if request.expected_input_revision is not None:
            expect(season['revision'],request.expected_input_revision)
        elif question['field']!='rate_kg_per_mu':
            raise HTTPException(422,'回答该问题需要提供当前事实版本')
        facts=json.loads(dumps(season['facts']))
        field=question['field']
        if field.startswith('additional_materials.'):
            line_id=field.split('.')[1]
            line=next((item for item in facts.get('additional_materials',[]) if item['id']==line_id),None)
            if line is None:
                raise HTTPException(409,'追加明细已变化，请重新研制')
            line.update(rate_kg_per_mu=value,rate_source=request.source)
        else:
            facts[field]=value
            if field=='rate_kg_per_mu':
                facts['rate_source']=request.source
        try:
            facts = Facts.model_validate(facts).model_dump(mode='json')
        except ValueError:
            raise HTTPException(422,'回答不能形成有效的本季确认事实') from None
        revision = season['revision']+1
        db.execute('UPDATE seasons SET facts=?,revision=? WHERE id=?',(dumps(facts),revision,season['id']))
        db.execute('INSERT INTO fact_versions VALUES(?,?,?,?,?,?)',(season['id'],revision,dumps(facts),actor['id'],request.source,timestamp()))
        from .reports import inherit_sources
        inherit_sources(db,season['id'],season['revision'],revision,season['facts'],facts)
        db.execute("UPDATE questions SET status='answered',answer=?,actor_id=? WHERE id=?",(dumps(saved),actor['id'],question['id']))
        from .dependencies import capture_snapshot, dependency_context, affected_modules, reuse_state
        updated_season = season | {'facts':facts,'revision':revision}
        snapshot = capture_snapshot(db,updated_season,run['snapshot'])
        context = dependency_context(db,snapshot,run['state'])
        state = reuse_state(db,run,snapshot,context,affected_modules(db,run,context))
        remaining=db.execute("SELECT count(*) FROM questions WHERE run_id=? AND status='open'",(run_id,)).fetchone()[0]
        if not remaining:
            state['_execution_actor_id'] = actor['id']
        db.execute("UPDATE runs SET snapshot=?,state=?,status=?,error=NULL,revision=revision+1 WHERE id=?",(dumps(snapshot),dumps(state),'waiting_input' if remaining else 'queued',run_id))
        if not remaining:
            generation = db.execute('SELECT MAX(generation) FROM jobs WHERE run_id=?',(run_id,)).fetchone()[0]+1
            db.execute('INSERT INTO jobs VALUES(?,?,?,? ,NULL,NULL,0)',(uid(),run_id,generation,'queued'))
        audit(db,run['season_id'],run_id,actor['id'],'answer_saved',{'question_id':question['id'],'source':request.source,'fact_revision':revision})
    return {'saved':True,'run_id':run_id,'status':'waiting_input' if remaining else 'queued','remaining_questions':remaining,'message':'回答已保存，仍有待确认项' if remaining else '回答已保存，恢复已登记'}


def edit_draft(store, run_id, actor, request):
    require_sr(actor)
    with store.write() as db:
        run = run_for(db,run_id,actor)
        expect(run['revision'],request.expected_revision)
        if run['formal_id'] or run['status'] not in ('completed','limited') or run['draft'] is None:
            raise HTTPException(409,'只有待确认草稿可以编辑；正式版本修改需新建研究')
        season = season_for(db,run['season_id'],actor)
        expect(season['revision'],run['snapshot']['revision'])
        return persist_draft_edit(db,run,actor,request.summary,[s.model_dump() for s in request.stages],request.reason)


def persist_draft_edit(db,run,actor,summary,stages,reason):
    run_id=run['id']
    draft = run['draft'] | {'summary':summary,'stages':stages}
    version = run['candidate_version']+1
    review = save_review(db,run,draft,version)
    db.execute('UPDATE runs SET draft=?,candidate_version=?,status=?,revision=revision+1 WHERE id=?',(dumps(draft),version,'completed' if review['can_confirm'] else 'limited',run_id))
    if run['state'].get('_context') and isinstance(run['state'].get('modules'),dict):
        from .dependencies import remember_result
        from .runtime import generation_for
        remember_result(db,run,run['state'],'candidate',draft,generation_for(db,run_id))
        db.execute('UPDATE runs SET state=? WHERE id=?',(dumps(run['state']),run_id))
    reason = reason or '未记录（系统不猜测理由）'
    db.execute('INSERT INTO candidate_versions VALUES(?,?,?,?,?,?)',(run_id,version,dumps(draft),actor['id'],reason,timestamp()))
    audit(db,run['season_id'],run_id,actor['id'],'draft_edited',{'from_version':run['candidate_version'],'to_version':version,'reason':reason})
    return {'id':run_id,'candidate_version':version}


def confirm_plan(store, run_id, actor, request):
    require_sr(actor)
    rejection = None
    result = None
    with store.write() as db:
        run = run_for(db,run_id,actor)
        if run['formal_id']:
            return plan_view(db.execute('SELECT * FROM plans WHERE id=?',(run['formal_id'],)).fetchone())
        expect(run['revision'],request.expected_revision)
        season = season_for(db,run['season_id'],actor)
        expect(season['revision'],run['snapshot']['revision'])
        if run['status'] not in ('completed','limited') or not run['draft']:
            raise HTTPException(409,'研究尚未形成可复核草稿')
        review = save_review(db,run,run['draft'],run['candidate_version'])
        if not review['can_confirm']:
            rejection = '质量审查未通过：'+'；'.join(i['message'] for i in review['issues'] if i['severity']=='blocking')
            db.execute("UPDATE runs SET status='limited',revision=revision+1 WHERE id=?",(run_id,))
        if db.execute("SELECT 1 FROM questions WHERE run_id=? AND status='open'",(run_id,)).fetchone():
            rejection = '仍有关键待回答问题'
        if not rejection and run['state'].get('_context'):
            from .runtime import preview
            if preview(db,run,season,actor)['affected_modules']:
                rejection = '研究依赖的事实、适用目录或来源已变化，请明确刷新后重新复核确认'
        if not rejection:
            result = _save_confirmed_plan(db,run,season,actor)
    if rejection:
        raise HTTPException(409,rejection)
    return result


def _save_confirmed_plan(db,run,season,actor):
        run_id = run['id']
        pid = uid()
        version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM plans WHERE season_id=?',(run['season_id'],)).fetchone()[0]
        db.execute('INSERT INTO plans VALUES(?,?,?,?,?,?,?,?)',(pid,run['season_id'],run_id,version,dumps(run['draft']),season['revision'],actor['id'],timestamp()))
        db.execute('UPDATE runs SET formal_id=?,revision=revision+1 WHERE id=?',(pid,run_id))
        db.execute('UPDATE seasons SET current_plan_id=? WHERE id=?',(pid,run['season_id']))
        facts = run['snapshot']['facts']
        content = {'version':'P'+str(version),'summary':run['draft']['summary'],'variety':facts['variety'],
                   'area_mu':facts['area_mu'],'rate_kg_per_mu':facts['rate_kg_per_mu'],'budget_cny':facts['budget_cny'],
                   'measures':[s['action'] for s in run['draft']['stages']], 'plan_id':pid,'actual_outcome':None,
                   'adjustment_reason':db.execute('SELECT reason FROM candidate_versions WHERE run_id=? ORDER BY version DESC LIMIT 1',(run_id,)).fetchone()[0],
                   'limitations':['技术确认方案，不代表已执行或效果已验证；实际记录需要另查']}
        content['additional_materials']=facts.get('additional_materials',[])
        db.execute('INSERT INTO history VALUES(?,?,?,?,?,?,?)',(pid,season['plot_id'],season['year'],season['crop'],season['region'],0,dumps(content)))
        audit(db,run['season_id'],run_id,actor['id'],'technical_confirmed',{'plan_id':pid,'version':version,'business_status':'not_connected','execution_status':'blocked'})
        return plan_view(db.execute('SELECT * FROM plans WHERE id=?',(pid,)).fetchone())


def review_run(store,run_id,actor,expected_revision=None):
    if expected_revision is None:
        with store.read() as db:
            run=run_for(db,run_id,actor)
            if not run['draft']:
                raise HTTPException(409,'尚无候选草稿可审查')
            return inspect_candidate(db,run,run['draft']) | {'candidate_version':run['candidate_version']}
    require_sr(actor)
    with store.write() as db:
        run = run_for(db,run_id,actor)
        if expected_revision is not None:
            expect(run['revision'],expected_revision)
        if not run['draft']:
            raise HTTPException(409,'尚无候选草稿可审查')
        review = save_review(db,run,run['draft'],run['candidate_version'])
        if not run['formal_id']:
            db.execute('UPDATE runs SET status=?,revision=revision+1 WHERE id=?',('completed' if review['can_confirm'] else 'limited',run_id))
        return review


def discuss(store,run_id,actor,request):
    from .discussion import discuss_run
    return discuss_run(store,run_id,actor,request)


def rule_explanation(db,run,query):
    snapshot=run['snapshot']
    facts=snapshot['facts']
    refs=[]
    category='scope'
    if any(word in query for word in ('合同','锁价','付款','商务','执行放行')):
        category='business'
        answer='合同、锁价、付款及商务审批系统尚未接入。技术确认只保存方案版本，当前执行未放行；没有已签约或已锁价的事实可供证明。'
    elif any(word in query for word in ('成本','预算','金额','单价','怎么算','多少钱','合作合计','小计','总价','总额')):
        category='cost'
        calc=run['state'].get('calculation')
        if calc:
            lines=[f"主产品 {snapshot['product']['name']}：{facts['area_mu']} 亩 × {facts['rate_kg_per_mu']} kg/亩 × {snapshot['product']['price']} CNY/kg = {calc['cost_cny']} 元。"]
            lines += [f"追加 {line['product_name']}：{facts['area_mu']} 亩 × {line['rate_kg_per_mu']} kg/亩 × {line['price_cny_per_kg']} CNY/kg = {line['cost_cny']} 元。" for line in calc.get('additional_lines',[])]
            budget=facts.get('budget_cny')
            answer=(f"输入 R{snapshot['revision']}。\n"+'\n'.join(lines)+f"\n公司产品合作合计 {calc.get('cooperation_total_cny',calc['cost_cny'])} 元；合作预算 {budget if budget is not None else '未知'} 元。完整成本与净收益仍未知。")
            refs=[calc['id']]
        else:
            answer='当前还没有完成核算；需确认面积、专业用量及来源后才能生成产品小计。'
    elif any(word in query for word in ('历史','往季','沿用','变化','差异')):
        category='history'
        lines=[]
        for history in run['state'].get('history',[]):
            refs.append(history['reference_id'])
            differences='；'.join(f"{d['label']}：往季 {d['historical']} → 本季 {d['current']}" for d in history['differences']) or '已比较的条件未发现差异'
            lines.append(f"{history['year']} {history['relation']}：{differences}")
        answer=('\n'.join(lines) or '当前没有适用历史参考。')+'\n历史实际效果尚未验证；参数不能自动沿用，本季专业用量以 SR 确认为准。'
    elif any(word in query for word in ('用量','剂量','参数','依据来源')):
        category='rate'
        answer=f"本季确认用量：{facts.get('rate_kg_per_mu') if facts.get('rate_kg_per_mu') is not None else '待确认'} kg/亩；来源：{facts.get('rate_source') or '待 SR 提供'}。系统只解释该确认输入，不从历史或模型推导专业用量。"
        for item in facts.get('additional_materials',[]):
            answer+=f"\n追加产品 {item['product_id']}：{item.get('rate_kg_per_mu') if item.get('rate_kg_per_mu') is not None else '待确认'} kg/亩；来源：{item.get('rate_source') or '待SR提供'}。"
        refs=['facts:R'+str(snapshot['revision'])]
    elif any(word in query for word in ('风险','测土','问题','缺少','限制','为什么不能')):
        category='risk'
        if run['draft']:
            review=inspect_candidate(db,run,run['draft'])
            answer='\n'.join(i['message']+' 关闭条件：'+i['close_condition'] for i in review['issues']) or '当前规则审查未发现阻塞项；仍须 SR 复核现场适用性。'
            refs=[i['id'] for i in review['issues']]
        else:
            answer='尚无草稿可审查。请先处理待回答问题，补齐当前研究所需事实。'
    elif any(word in query for word in ('版本','草稿','确认','修改')):
        category='version'
        answer=f"当前输入 R{snapshot['revision']}、候选 C{run['candidate_version']}。"+('已保存技术确认版本。' if run['formal_id'] else '尚未技术确认。')+'讨论只追加解释；修改草稿或本季输入需通过对应表单保存新版本。'
    elif any(word in query for word in ('资料','来源','引用','证据','依据')):
        category='evidence'
        rows=run['state'].get('evidence',[])
        refs=[row['id'] for row in rows]
        answer='\n'.join(f"{e['title']} / {e['source']} / {e['locator']} / {e['version']}；{'演示' if e['demo'] else '非演示'}资料" for e in rows) or '当前没有可用的资料引用，不能通过依据质量门槛。'
        answer+='\n确认方案前会重新检查来源状态、适用范围与时效。'
    else:
        answer='当前规则解释支持：产品成本与预算、历史差异、确认用量、资料引用、风险缺口、版本及商务状态。这个问题超出已实现的分类，请查看对应资料或明确上述主题；系统不会猜测专业结论。'
    return {'answer':answer,'category':category,'refs':refs}


def create_record(store, actor, request):
    with store.write() as db:
        season = season_for(db,request.season_id,actor)
        if request.plan_id:
            plan = db.execute('SELECT * FROM plans WHERE id=? AND season_id=?',(request.plan_id,request.season_id)).fetchone()
            if plan is None:
                raise HTTPException(404,'方案不属于本季或无访问权限')
            if datetime.fromisoformat(plan['created_at']) > request.observed_at:
                raise HTTPException(422,'不能把实际记录关联到发生时尚未确认的技术方案')
        rid = uid()
        db.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?)',(rid,request.season_id,actor['id'],request.kind,request.observed_at.isoformat(),request.notes,request.plan_id,timestamp()))
        audit(db,request.season_id,None,actor['id'],'field_record_saved',{'record_id':rid,'kind':request.kind})
        return {'id':rid,'season_id':request.season_id}


def list_records(store, actor):
    with store.read() as db:
        return [dict(r) for r in db.execute('''SELECT r.*,u.name actor_name,u.role actor_role,c.name customer_name,p.name plot_name,s.year
                    FROM records r JOIN users u ON u.id=r.actor_id JOIN seasons s ON s.id=r.season_id
                    JOIN plots p ON p.id=s.plot_id JOIN customers c ON c.id=s.customer_id
                    JOIN assignments a ON a.customer_id=s.customer_id WHERE a.user_id=? ORDER BY r.observed_at DESC''',(actor['id'],))]
