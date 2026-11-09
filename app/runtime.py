"""Read-only dependency previews and atomic, version-bound SR recovery."""
from fastapi import HTTPException

from .domain import dumps, timestamp, uid
from .dependencies import (MODULES, MAX_ACTIONS, MAX_GENERATIONS, affected_modules,
                           capture_snapshot, dependency_context, fingerprint, market_expired, reuse_state)


def generation_for(db, run_id):
    return db.execute('SELECT COALESCE(MAX(generation),1) FROM jobs WHERE run_id=?',(run_id,)).fetchone()[0]


def preview(db, run, season, actor):
    error = None
    try:
        snapshot = capture_snapshot(db,season,run['snapshot'])
        context = dependency_context(db,snapshot,run['state'])
    except HTTPException as exc:
        snapshot, context, error = None, {}, exc.detail
    affected = affected_modules(db,run,context) if snapshot else list(MODULES)
    changed = [name for name in sorted(set(season['facts']) | set(run['snapshot'].get('facts',{})))
               if season['facts'].get(name) != run['snapshot'].get('facts',{}).get(name)]
    if snapshot:
        if snapshot['product'] != run['snapshot'].get('product') or snapshot['additional_products'] != run['snapshot'].get('additional_products',{}):
            changed.append('product_catalog')
        old = run['state'].get('_context',{})
        if old.get('evidence') and old['evidence'] != context['evidence']:
            changed.append('evidence_catalog')
        if old.get('history') and old['history'] != context['history']:
            changed.append('history_catalog')
        if market_expired(run['state']):
            changed.append('market_expired')
    generation = generation_for(db,run['id'])
    count = db.execute('SELECT COUNT(*) FROM actions WHERE run_id=? AND generation=?',(run['id'],generation)).fetchone()[0]
    limitation = (error or ('已技术确认，请新建研究' if run['formal_id'] else
                  '已达到四代预算，请新建研究' if generation >= MAX_GENERATIONS else
                  '已取消研究，请新建研究' if run['status']=='cancelled' else ''))
    can = actor['role']=='SR' and not limitation
    modules = []
    for name in MODULES:
        receipt = run['state'].get('modules',{}).get(name,{})
        status = ('not_requested' if name=='market' and not run['state'].get('include_market') else
                  'stale' if name in affected else 'ready' if receipt else 'pending')
        modules.append({'name':name,'status':status,'result_ref':receipt.get('result_ref'),
                        'input_revision':receipt.get('input_revision'),'reused':bool(receipt.get('reused')),
                        'reason':('当前依赖已变化，需重研' if status=='stale' else receipt.get('reason','尚未产生工具结果'))})
    return {'modules':modules,'changed_fields':changed,'affected_modules':affected,
            'reusable_modules':[item['name'] for item in modules if item['status']=='ready'],
            'context_hash':fingerprint({'revision':season['revision'],'context':context,'error':error,
                                        'market_expired':market_expired(run['state'])}),
            'current_input_revision':season['revision'],'generation':generation,
            'remaining_refreshes':max(0,MAX_GENERATIONS-generation),'actions_used':count,
            'max_actions':MAX_ACTIONS,'max_generations':MAX_GENERATIONS,
            'can_refresh':can and bool(affected),'can_repair':can and run['status'] in ('failed','limited'),'limitation':limitation}


def refresh_run(store, run_id, actor, request):
    from .services import audit, expect, require_sr, run_for, season_for
    require_sr(actor)
    with store.write() as db:
        run = run_for(db,run_id,actor)
        season = season_for(db,run['season_id'],actor)
        digest = fingerprint(request.model_dump(mode='json'))
        old = db.execute('SELECT * FROM research_refreshes WHERE run_id=? AND actor_id=? AND request_hash=?',
                         (run_id,actor['id'],digest)).fetchone()
        # Authorization and formal/cancel guards still apply to a lost-response retry.
        if run['formal_id'] or run['status']=='cancelled':
            raise HTTPException(409,'正式或已取消的研究只能新建研究')
        if old:
            expect(season['revision'],request.expected_input_revision)
            snapshot = capture_snapshot(db,season,run['snapshot'])
            expect(fingerprint(dependency_context(db,snapshot,run['state'])),old['context_hash'])
            return {'id':run_id,'status':run['status'],'generation':old['generation'],'duplicate':True}
        current = preview(db,run,season,actor)
        expect(run['revision'],request.expected_revision)
        expect(season['revision'],request.expected_input_revision)
        expect(current['context_hash'],request.expected_context_hash)
        if not current['can_'+request.intent]:
            raise HTTPException(409,current['limitation'] or '没有需要刷新的变化；可明确选择有限补查')
        snapshot = capture_snapshot(db,season,run['snapshot'])
        context = dependency_context(db,snapshot,run['state'])
        affected = list(current['affected_modules'])
        if request.intent=='repair':
            affected = [name for name in MODULES if name in affected or name in ('evidence','candidate')]
        state = reuse_state(db,run,snapshot,context,affected)
        state['_execution_actor_id'] = actor['id']
        generation = current['generation']+1
        db.execute("UPDATE jobs SET status='cancelled',token=NULL WHERE run_id=? AND status IN ('queued','running')",(run_id,))
        db.execute("UPDATE questions SET status='superseded' WHERE run_id=? AND status='open'",(run_id,))
        db.execute("UPDATE runs SET snapshot=?,state=?,draft=NULL,status='queued',error=NULL,revision=revision+1 WHERE id=?",
                   (dumps(snapshot),dumps(state),run_id))
        db.execute("INSERT INTO jobs VALUES(?,?,?,'queued',NULL,NULL,0)",(uid(),run_id,generation))
        db.execute('INSERT INTO research_refreshes VALUES(?,?,?,?,?,?,?,?)',
                   (uid(),run_id,actor['id'],digest,fingerprint(context),request.intent,generation,timestamp()))
        audit(db,run['season_id'],run_id,actor['id'],'research_refreshed',
              {'generation':generation,'intent':request.intent,'affected_modules':affected,'input_revision':snapshot['revision']})
        return {'id':run_id,'status':'queued','generation':generation,'duplicate':False}
