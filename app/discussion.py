"""Version-bound explanations and explicit, durable draft text proposals."""
import hashlib
import json

from fastapi import HTTPException

from .domain import configured_mode, dumps, timestamp, uid
from .knowledge import evidence_status
from .model import generate_discussion
from .review import inspect_candidate
from .services import audit, expect, persist_draft_edit, require_sr, rule_explanation, run_for, season_for


def proposals_for(db,run_id):
    result=[]
    for row in db.execute('SELECT * FROM discussion_proposals WHERE run_id=? ORDER BY rowid',(run_id,)):
        item=dict(row)
        for key in ('binding','content'):
            item[key]=json.loads(item[key])
        result.append(item)
    return result


def binding_for(run):
    return {'run_revision':run['revision'],'input_revision':run['snapshot']['revision'],
            'candidate_version':run['candidate_version'],'formal_id':run['formal_id'],
            'content_hash':hashlib.sha256(dumps(run['draft']).encode()).hexdigest()}


def editable(db,run,actor):
    require_sr(actor)
    if run['formal_id'] or not run['draft'] or run['status'] not in ('completed','limited'):
        raise HTTPException(409,'只有当前待确认草稿可提出或保存修改；正式版本请重新研制')
    expect(season_for(db,run['season_id'],actor)['revision'],run['snapshot']['revision'])


def discussion_context(db,run,actor,request):
    snapshot=run['snapshot']
    draft=run['draft'] or {}
    references=['facts:R'+str(snapshot['revision'])]
    if run['candidate_version']:
        references.append('draft:C'+str(run['candidate_version']))
    calc=draft.get('calculation') or run['state'].get('calculation')
    if calc:
        references.append(calc['id'])
    documents=[]
    for evidence_id in draft.get('evidence_refs',[]):
        document,error=evidence_status(db,evidence_id,snapshot['crop'],snapshot['region'],snapshot['customer_id'])
        if document:
            references.append(evidence_id)
            documents.append({'id':evidence_id,'title':document['title'],'body':document['body'],
                'source':document['source'],'locator':document['locator'],'version':document['version'],
                'demo':document['demo'],'current_error':error})
    history=run['state'].get('history',[])
    references.extend(item['reference_id'] for item in history)
    records=[dict(row) for row in db.execute('SELECT id,kind,observed_at,notes,plan_id FROM records WHERE season_id=? ORDER BY observed_at DESC LIMIT 30',(run['season_id'],))]
    references.extend(item['id'] for item in records)
    references.extend(item['report_id'] for item in snapshot.get('report_refs',[]))
    prior=[]
    for row in db.execute('''SELECT m.message,m.answer,c.versions,c.mode FROM messages m
            LEFT JOIN answer_contexts c ON c.message_id=m.id
            WHERE m.run_id=? ORDER BY m.rowid DESC LIMIT 6''',(run['id'],)):
        prior.append({'message':row['message'],'answer':row['answer'],
                      'versions':json.loads(row['versions']) if row['versions'] else None,'mode':row['mode']})
    review=inspect_candidate(db,run,run['draft']) if run['draft'] else None
    if review:
        references.extend(item['id'] for item in review['issues'])
    current_revision=season_for(db,run['season_id'],actor)['revision']
    context={'request_intent':request.intent,'message':request.message,'binding':binding_for(run),
        'object':{key:snapshot[key] for key in ('customer_id','customer_name','plot_id','plot_name','year','crop','region')},
        'facts':snapshot['facts'],'current_fact_revision':current_revision,'draft':run['draft'],
        'server_calculation':calc,'history':history,'actual_records':records,'documents':documents,
        'prior_discussion':list(reversed(prior)),
        'confirmed_report_sources':snapshot.get('report_refs',[]),
        'current_review':review,'allowed_refs':list(dict.fromkeys(references)),
        'limitations':['技术确认不等于商务批准或执行放行','历史计划不代表实际或效果','模型自由文字需要SR复核']}
    guard={'binding':binding_for(run),'current_fact_revision':current_revision,
           'evidence_fingerprint':review['evidence_fingerprint'] if review else None}
    return context,guard


def discuss_run(store,run_id,actor,request):
    # Read a scoped snapshot, then release the connection before any provider call.
    with store.read() as db:
        run=run_for(db,run_id,actor)
        if request.expected_revision is not None:
            expect(run['revision'],request.expected_revision)
        if request.intent=='change_request':
            editable(db,run,actor)
        context,guard=discussion_context(db,run,actor,request)
        rule=rule_explanation(db,run,request.message)
    edit=None
    if request.proposed_edit is not None:
        edit=request.proposed_edit.model_dump()
        response={'answer':'以下为手工修改预览，尚未保存。请核对差异和审查结果后，由SR确认保存新草稿。',
                  'category':'change_request','refs':['draft:C'+str(run['candidate_version'])],'mode':'manual_edit_preview'}
    elif configured_mode()=='llm':
        try:
            result=generate_discussion(context)
            if (any(ref not in context['allowed_refs'] for ref in result.refs)
                    or len(set(result.refs))!=len(result.refs)
                    or (request.intent=='question' and result.intent=='edit')):
                raise ValueError('引用或意图越界')
        except Exception:
            # Provider exceptions can contain URLs, keys or private response bodies.
            raise HTTPException(502,'模型讨论未通过协议检查或服务不可用；原事实和草稿未修改') from None
        edit=result.edit.model_dump() if result.edit else None
        answer=result.answer
        if result.pending_decisions:
            answer+='\n待确认：'+'；'.join(result.pending_decisions)+'。请由SR通过本季资料或补问入口确认。'
        response={'answer':answer,'category':'change_request' if edit else 'needs_facts' if result.intent=='needs_facts' else 'semantic',
                  'refs':result.refs,'mode':'model_edit_preview' if edit else 'semantic_model_explanation'}
    elif request.intent=='change_request':
        response={'answer':'当前为规则演示模式，不能从自然语言推导修改。请填写手工摘要/阶段预览，或配置模型后再提出建议；事实变化请在本季资料中确认并重新研制。',
                  'category':'needs_facts','refs':[],'mode':'rule_question_explanation'}
    else:
        response=rule|{'mode':'rule_question_explanation'}

    with store.write() as db:
        current=run_for(db,run_id,actor)
        if request.intent=='change_request':
            editable(db,current,actor)
        _,current_guard=discussion_context(db,current,actor,request)
        if current_guard!=guard:
            raise HTTPException(409,'讨论期间版本、事实或依据已变化，请刷新后重新讨论')
        proposal=None
        if edit is not None:
            before={key:current['draft'][key] for key in ('summary','stages')}
            if edit!=before:
                preview=inspect_candidate(db,current,current['draft']|edit)
                fields=[key for key in before if before[key]!=edit[key]]
                content={'before':before,'after':edit,'reason':request.reason or '未记录（系统不猜测理由）',
                    'reason_source':'sr_explicit' if request.reason else 'not_recorded',
                    'impact':['调整'+('摘要' if key=='summary' else '服务阶段文字与安排') for key in fields]+
                             ['确认事实、逐行核算、物资、约束和审核措施沿用原版本；保存时重新评价。','自由文字需SR专业复核，不等于新生产处方已验证。'],
                    'preview_review':preview}
                proposal={'id':uid(),'run_id':run_id,'actor_id':actor['id'],'binding':guard['binding'],
                    'content':content,'status':'pending','applied_version':None,'created_at':timestamp()}
            else:
                response['answer']+='\n文字与当前草稿一致，未创建修改建议。'
        mid=proposal['id'] if proposal else uid()
        versions={'input_revision':run['snapshot']['revision'],'candidate_version':run['candidate_version'],
                  'formal_id':run['formal_id'],'run_revision':run['revision']}
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',(mid,run_id,actor['id'],request.message,response['answer'],timestamp()))
        db.execute('INSERT INTO answer_contexts VALUES(?,?,?,?,?)',(mid,response['category'],dumps(response['refs']),dumps(versions),response['mode']))
        if proposal:
            db.execute('INSERT INTO discussion_proposals VALUES(?,?,?,?,?,?,?,?)',(
                mid,run_id,actor['id'],dumps(proposal['binding']),dumps(proposal['content']),'pending',None,proposal['created_at']))
        return response|{'id':mid,'versions':versions,'changed':False,'proposal':proposal}


def apply_proposal(store,run_id,proposal_id,actor,request):
    require_sr(actor)
    with store.write() as db:
        run=run_for(db,run_id,actor)
        editable(db,run,actor)
        row=db.execute('SELECT * FROM discussion_proposals WHERE id=? AND run_id=?',(proposal_id,run_id)).fetchone()
        if row is None:
            raise HTTPException(404,'修改建议不存在或不属于当前研究')
        if row['status']=='applied':
            if run['candidate_version']!=row['applied_version']:
                raise HTTPException(409,'该建议已保存，当前草稿又有变化，请查看当前版本')
            return {'id':run_id,'candidate_version':row['applied_version'],'duplicate':True}
        expect(run['revision'],request.expected_revision)
        if json.loads(row['binding'])!=binding_for(run):
            raise HTTPException(409,'建议对应的原版本已变化，请基于当前草稿重新提出')
        content=json.loads(row['content'])
        result=persist_draft_edit(db,run,actor,content['after']['summary'],content['after']['stages'],content['reason'])
        db.execute("UPDATE discussion_proposals SET status='applied',applied_version=? WHERE id=?",(result['candidate_version'],proposal_id))
        audit(db,run['season_id'],run_id,actor['id'],'discussion_proposal_applied',{
            'proposal_id':proposal_id,'message_id':proposal_id,'from_version':run['candidate_version'],
            'to_version':result['candidate_version'],'reason':content['reason'],'reason_source':content['reason_source']})
        return result|{'duplicate':False}
