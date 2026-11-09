"""The six fixed research dependencies and immutable tool receipts."""
import hashlib
import json
from datetime import datetime, timezone

from fastapi import HTTPException

from .domain import dumps, timestamp, uid
from .knowledge import evidence_status

MODULES = ('history', 'evidence', 'quantity', 'budget', 'market', 'candidate')
MAX_GENERATIONS = 4
MAX_ACTIONS = 12


def fingerprint(value):
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()


def capture_snapshot(db, season, previous=None):
    previous = previous or {}
    snapshot = {key:season[key] for key in ('id','customer_id','plot_id','customer_name','plot_name','year','crop','region','revision','facts')}
    product = db.execute('SELECT * FROM products WHERE id=?', (season['facts']['product_id'],)).fetchone()
    if product is None or not product['active'] or product['unit'] != 'CNY/kg':
        raise HTTPException(422, '适用产品目录不可用')
    extra = {}
    for item in season['facts'].get('additional_materials', []):
        row = db.execute('SELECT * FROM products WHERE id=?', (item['product_id'],)).fetchone()
        if row is None or not row['active'] or row['unit'] != 'CNY/kg':
            raise HTTPException(422, '追加产品目录不可用')
        extra[item['id']] = dict(row)
    from .reports import confirmed_sources
    snapshot.update(product=dict(product), additional_products=extra,
                    report_refs=confirmed_sources(db, season['id'], season['revision']),
                    goal=previous.get('goal',''), required_fields=previous.get('required_fields',[]),
                    captured_at=timestamp(), source_identity='SR 已确认输入 / 演示产品目录',
                    workflow_version='mvp-v3')
    return snapshot


def dependency_context(db, snapshot, state):
    facts = snapshot['facts']
    identity = {key:snapshot[key] for key in ('id','customer_id','plot_id','year','crop','region')}
    history = [dict(row) for row in db.execute('''SELECT * FROM history WHERE year<? AND crop=?
        AND (plot_id=? OR (published=1 AND region=?)) ORDER BY id''',
        (snapshot['year'],snapshot['crop'],snapshot['plot_id'],snapshot['region']))]
    evidence = []
    for row in db.execute('''SELECT * FROM knowledge WHERE (customer_id IS NULL OR customer_id=?)
        AND crop IN (?, '通用') AND region IN (?, '通用') AND purpose IN ('plan_research','knowledge') ORDER BY id''',
        (snapshot['customer_id'],snapshot['crop'],snapshot['region'])):
        document, error = evidence_status(db,row['id'],snapshot['crop'],snapshot['region'],snapshot['customer_id'])
        # Only currently usable documents affect retrieval; revoke/publish/expiry changes this set.
        if not error:
            evidence.append(document)
    from .reports import check_sources
    reports = {'refs':snapshot.get('report_refs',[]), 'integrity':check_sources(db,snapshot)}
    quantity = {key:facts.get(key) for key in ('area_mu','rate_kg_per_mu','rate_source','product_id','additional_materials')}
    quantity['additional_materials'] = facts.get('additional_materials') or []
    values = {
        'history': {'identity':identity, 'catalog':history},
        'evidence': {'identity':identity,'query':snapshot.get('goal'),
                     'repair_query':snapshot['crop']+' 本季方案 核对 流程',
                     'retrieval_policy':'scoped-lexical-one-broader-v1','purpose':'plan_research','catalog':evidence},
        'quantity': {'identity':identity,'facts':quantity},
        'budget': {'identity':identity,'quantity':quantity,'product':snapshot['product'],
                   'additional_products':snapshot.get('additional_products',{}),
                   'budget_cny':facts.get('budget_cny'),'budget_basis':facts.get('budget_basis','company_cooperation_amount')},
        'market': {'identity':identity,'storage':facts.get('storage'),'requested':bool(state.get('include_market'))},
    }
    values['candidate'] = {'dependencies':values.copy(),'facts':facts,'reports':reports,
                           'required_fields':snapshot.get('required_fields',[]),'input_revision':snapshot['revision']}
    return {name:fingerprint(values[name]) for name in MODULES}


def market_expired(state):
    value = state.get('market',{}).get('data_as_of')
    if not value:
        return False
    try:
        date = datetime.fromisoformat(value)
        return date.tzinfo is None or not 0 <= (datetime.now(timezone.utc)-date).total_seconds() <= 7*86400
    except (TypeError, ValueError):
        return True


def valid_receipt(db, run_id, name, module, expected_hash):
    if not isinstance(module,dict) or not module.get('result_ref'):
        return False
    row = db.execute('SELECT * FROM research_results WHERE id=? AND run_id=? AND module=?',
                     (module['result_ref'],run_id,name)).fetchone()
    return row is not None and row['input_hash'] == expected_hash == module.get('input_hash')


def affected_modules(db, run, context):
    modules = run['state'].get('modules')
    old_context = run['state'].get('_context')
    if not isinstance(modules,dict) or not old_context:
        return list(MODULES)
    affected = [name for name in MODULES if old_context.get(name) != context[name]]
    for name, module in modules.items():
        if name in MODULES and not valid_receipt(db,run['id'],name,module,context[name]) and name not in affected:
            affected.append(name)
    if market_expired(run['state']) and run['state'].get('include_market'):
        affected.append('market')
    if any(name != 'candidate' for name in affected):
        affected.append('candidate')
    return [name for name in MODULES if name in affected]


def remember_result(db, run, state, name, result, generation):
    context = state['_context']
    reference = uid()
    refs = {'season_id':run['season_id'],'input_revision':run['snapshot']['revision'],'context_hash':context[name]}
    if name=='evidence':
        refs.update(query=state.get('_evidence_query',run['snapshot'].get('goal')),
                    purpose='plan_research',customer_id=run['snapshot']['customer_id'],
                    crop=run['snapshot']['crop'],region=run['snapshot']['region'])
    db.execute('''INSERT INTO research_results(id,run_id,module,input_hash,input_refs,result,generation,input_revision,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)''', (reference,run['id'],name,context[name],
        dumps(refs),
        dumps(result),generation,run['snapshot']['revision'],timestamp()))
    state.setdefault('modules',{})[name] = {'result_ref':reference,'input_hash':context[name],
        'input_revision':run['snapshot']['revision'],'reused':False,'reason':'本代工具结果'}


def reuse_state(db, run, snapshot, context, affected):
    old = run['state']
    state = {'include_market':bool(old.get('include_market')), 'modules':{}, '_context':context}
    if old.get('_execution_actor_id'):
        state['_execution_actor_id'] = old['_execution_actor_id']
    fields = {'history':'history','evidence':'evidence','quantity':'quantity','budget':'calculation','market':'market'}
    for name, field in fields.items():
        module = old.get('modules',{}).get(name)
        if name in affected or not valid_receipt(db,run['id'],name,module,context[name]):
            continue
        receipt = db.execute('SELECT result FROM research_results WHERE id=?',(module['result_ref'],)).fetchone()
        state[field] = json.loads(receipt['result'])
        state['modules'][name] = module | {'reused':True,'reason':'依赖指纹一致，复用已验证结果'}
    if 'history' in state:
        from .research import history_differences
        state['history'] = [item | {'differences':history_differences(item,snapshot['facts'])}
                            for item in state['history']]
    if 'calculation' in state:
        calc = state['calculation']
        old_id = calc['id']
        calc.update(id=uid(),reuse_of=old_id)
        calc['input_refs'].update(fact_revision=snapshot['revision'],product_revision=snapshot['product']['revision'])
        for line in calc.get('additional_lines',[]):
            line['input_refs'].update(fact_revision=snapshot['revision'],
                                      product_revision=snapshot['additional_products'][line['id']]['revision'])
    return state
