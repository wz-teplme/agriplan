"""Portable evaluation evidence; automatic checks and manual records stay separate."""
import copy
import hashlib
import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


DIMENSIONS = {'condition_fit':'条件适配','evidence_support':'依据充分',
              'numeric_consistency':'数值一致','execution_readiness':'完整可执行',
              'risk_preparedness':'风险与调整准备'}
GRADES = ('符合','部分符合','不符合','不可评价')
CORE_FIELDS = ('schema_version','suite_id','suite_hash','evaluation_id','created_at','mode',
               'provenance','cases','telemetry','manual_reviews','parent_report_hash','version_manifest')
CASE_FIELDS = ('id','title','run_id','status','snapshot','draft','review','actions','questions',
               'checks','elapsed_ms','error_category','context')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',',':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def redact(value):
    key=os.getenv('AGRIPLAN_MODEL_API_KEY','')
    if isinstance(value,str):
        return value.replace(key,'[REDACTED]') if key else value
    if isinstance(value,list):
        return [redact(item) for item in value]
    if isinstance(value,dict):
        return {name:redact(item) for name,item in value.items()}
    return value


def finalize_report(core):
    if not isinstance(core,dict) or core.get('schema_version')!=1 or core.get('mode') not in ('demo','live'):
        raise ValueError('不支持的评测报告版本或模式')
    report=redact({key:copy.deepcopy(core[key]) for key in CORE_FIELDS if key in core})
    cases=report.get('cases')
    if not isinstance(cases,list) or not cases:
        raise ValueError('评测报告必须包含案例')
    seen=set();passed=errors=0;normalized=[]
    for original in cases:
        if not isinstance(original,dict):
            raise ValueError('案例格式错误')
        case={key:original[key] for key in CASE_FIELDS if key in original}
        cid=case.get('id')
        if not isinstance(cid,str) or not cid or cid in seen:
            raise ValueError('案例标识必须非空且唯一')
        seen.add(cid)
        checks=case.get('checks',[])
        if not isinstance(checks,list) or any(not isinstance(c,dict) or type(c.get('passed')) is not bool for c in checks):
            raise ValueError('自动检查结果必须是布尔值')
        error=bool(case.get('error_category'))
        case['automatic_passed']=bool(checks) and all(c['passed'] for c in checks) and not error
        passed+=int(case['automatic_passed']);errors+=int(error)
        case['case_hash']=digest(case)
        normalized.append(case)
    report.update(cases=normalized,provenance='rule_generated' if report['mode']=='demo' else 'live_provider',
                  external_validation='not_performed',professional_validation='not_performed')
    report['summary']={'total_cases':len(cases),'passed_cases':passed,'failed_cases':len(cases)-passed,
                       'error_cases':errors,'automatic_pass_rate':passed/len(cases),
                       'overall_status':'passed' if passed==len(cases) else 'failed'}
    if report.get('manual_reviews') is not None and not report.get('parent_report_hash') and core.get('report_hash'):
        report['parent_report_hash']=core['report_hash']
    report['report_hash']=digest(report)
    return report


def verify_report(report):
    if not isinstance(report,dict) or canonical(report)!=canonical(finalize_report(report)):
        raise ValueError('报告内容或自动汇总与绑定哈希不一致')


def review_template(report):
    verify_report(report)
    return {'schema_version':1,'evaluation_id':report['evaluation_id'],'report_hash':report['report_hash'],
            'scope':'synthetic_local_cases','identity_status':'unverified',
            'instructions':'人工记录不验证身份或专业资质；五维填写四档评分及说明，不据此声称生产适用。',
            'reviews':[{'case_id':case['id'],'case_hash':case['case_hash'],'reviewer':None,
                        'reviewed_at':None,'dimensions':dict.fromkeys(DIMENSIONS),'notes':None}
                       for case in report['cases']]}


def validate_reviews(report, payload):
    verify_report(report)
    if (not isinstance(payload,dict) or payload.get('schema_version')!=1 or
            payload.get('evaluation_id')!=report['evaluation_id'] or payload.get('report_hash')!=report['report_hash'] or
            payload.get('scope')!='synthetic_local_cases'):
        raise ValueError('人工复核没有绑定当前评测报告')
    items=payload.get('reviews')
    if not isinstance(items,list) or len(items)>len(report['cases']):
        raise ValueError('人工复核案例列表错误')
    cases={case['id']:case for case in report['cases']};seen=set();records=[]
    for item in items:
        if not isinstance(item,dict):
            raise ValueError('人工复核条目错误')
        cid=item.get('case_id')
        if not isinstance(cid,str) or cid not in cases or cid in seen or item.get('case_hash')!=cases[cid]['case_hash']:
            raise ValueError('人工复核案例未知、重复或内容已变化')
        seen.add(cid)
        dimensions=item.get('dimensions')
        if not isinstance(dimensions,dict) or set(dimensions)!=set(DIMENSIONS):
            raise ValueError('人工复核需要完整五维评分字段')
        values=[item.get('reviewer'),item.get('reviewed_at'),item.get('notes'),*dimensions.values()]
        if all(value is None or value=='' for value in values):
            continue
        for name,maximum in (('reviewer',160),('notes',4000)):
            value=item.get(name)
            if not isinstance(value,str) or not value.strip() or len(value)>maximum:
                raise ValueError('已填复核必须记录复核人员和说明')
        if any(value not in GRADES for value in dimensions.values()):
            raise ValueError('人工评分只能使用符合、部分符合、不符合、不可评价')
        try:
            date=datetime.fromisoformat(item['reviewed_at'].replace('Z','+00:00'))
            if date.tzinfo is None or date>datetime.now(timezone.utc):
                raise ValueError()
        except (ValueError,TypeError,KeyError,AttributeError):
            raise ValueError('复核时间必须带时区且不能来自未来') from None
        records.append({'case_id':cid,'case_hash':cases[cid]['case_hash'],
                        'reviewer':item['reviewer'].strip(),'reviewed_at':date.isoformat(),
                        'dimensions':dict(dimensions),'notes':item['notes'].strip(),
                        'status':'manual_recorded_unverified_identity'})
    return {'report_hash':report['report_hash'],'recorded':len(records),
            'pending':len(cases)-len(records),'identity_status':'unverified','records':redact(records)}


def render_report(report):
    verify_report(report)
    esc=lambda value:html.escape(str(value),quote=True)
    pretty=lambda value:esc(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False))
    summary=report['summary'];telemetry=report.get('telemetry',{})
    manual=report.get('manual_reviews') or {'recorded':0,'pending':summary['total_cases'],'records':[]}
    records={item['case_id']:item for item in manual.get('records',[])}
    metric=lambda key:'未知' if telemetry.get(key) is None else esc(telemetry[key])
    parts=[]
    for case in report['cases']:
        rows=''.join('<tr><td>'+esc(c.get('name',''))+'</td><td>'+('通过' if c['passed'] else '失败')+
                     '</td><td>'+pretty(c.get('expected'))+'</td><td>'+pretty(c.get('actual'))+'</td></tr>'
                     for c in case.get('checks',[]))
        record=records.get(case['id'])
        human=('<p>人工已记录（身份和专业资质未核验） · '+esc(record['reviewer'])+'</p><pre>'+pretty(record)+'</pre>'
               if record else '<p class="pending">人工五维复核：待填写；未进行专业验证。</p>')
        details=''.join('<details><summary>'+title+'</summary><pre>'+pretty(case.get(key))+'</pre></details>'
                        for key,title in [('snapshot','固定输入与来源'),('draft','候选与确定性核算'),
                                          ('review','程序质量评价'),('context','历史、依据与模块结果'),
                                          ('actions','研究动作'),('questions','待确认项')])
        parts.append('<section><h2>'+esc(case.get('title',case['id']))+' <small>'+esc(case['id'])+'</small></h2>'+
                     '<p><b>'+('自动流程检查通过' if case['automatic_passed'] else '自动流程检查失败')+
                     '</b> · 研究状态 '+esc(case.get('status','未知'))+' · 耗时 '+esc(case.get('elapsed_ms','未知'))+' ms</p>'+
                     ('<p class="error">失败类别：'+esc(case['error_category'])+'</p>' if case.get('error_category') else '')+
                     '<div class="scroll"><table><thead><tr><th>检查</th><th>结果</th><th>预期</th><th>实际</th></tr></thead><tbody>'+rows+
                     '</tbody></table></div>'+human+details+'<p class="hash">案例绑定 '+esc(case['case_hash'])+'</p></section>')
    css='''body{margin:0;background:#f4f6f1;color:#203b31;font:15px/1.65 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1120px;margin:auto;padding:32px 20px}header,section{background:white;padding:26px;border:1px solid #dae2d9;border-radius:12px;margin-bottom:20px}h1{font-size:28px;margin:0 0 12px}h2{font-size:21px;margin:0 0 12px}small,.hash{font-size:12px;color:#60776a}.hash{overflow-wrap:anywhere}.metrics{display:flex;gap:12px;flex-wrap:wrap}.metrics span{background:#edf2e8;padding:10px 14px;border-radius:6px}.pending{color:#85651f}.error{color:#9c312a}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;border-bottom:1px solid #e2e7df;padding:10px;vertical-align:top;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f7f8f5;padding:14px;font-size:12px}summary{cursor:pointer;padding:10px 0}a{color:#2b6650}.scroll{overflow:auto}@media(max-width:600px){main{padding:12px}header,section{padding:16px}h1{font-size:23px}th,td{padding:6px}}'''
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'+
            '<title>AgriPlan · 本地评测证据</title><style>'+css+'</style><main><header><p>AGRIPLAN / LOCAL EVALUATION</p>'+
            '<h1>本地流程评测与人工复核</h1><p>固定合成数据 · '+('规则演示，无模型调用' if report['mode']=='demo' else '已选择真实调用模式，调用情况见记录')+
            ' · 未进行专业验证</p><p>自动通过表示符合案例预期；等待/受限可能是正确结果。不是农艺评分、生产适用证明或收益测量。</p>'+
            '<div class="metrics"><span>案例 '+str(summary['total_cases'])+'</span><span>自动通过 '+str(summary['passed_cases'])+
            '</span><span>自动失败 '+str(summary['failed_cases'])+'</span><span>人工已记录 '+str(manual.get('recorded',0))+
            '</span><span>待复核 '+str(manual.get('pending',summary['total_cases']))+'</span></div><p>模型请求尝试 '+metric('attempted_calls')+
            ' · 输入 token '+metric('input_tokens')+' · 输出 token '+metric('output_tokens')+' · 费用 '+metric('estimated_cost')+
            '</p><p><a href="report.json">完整JSON证据</a> · <a href="review-template.json">人工复核模板JSON</a></p>'+
            '<p class="hash">评测 '+esc(report['evaluation_id'])+' · '+esc(report['created_at'])+'<br>报告绑定 '+esc(report['report_hash'])+
            '</p><p>哈希用于版本绑定，并非数字签名。人工填写不验证身份或专业资质。</p></header>'+''.join(parts)+
            '<section><h2>调用证据与限制</h2><pre>'+pretty(telemetry)+'</pre><details><summary>实现与依赖版本</summary><pre>'+pretty(report.get('version_manifest'))+
            '</pre></details><p>真实专业样本、专家审核、真实模型语义质量和生产效果仍需另行验收。</p></section></main></html>')


def save_report(report, output_dir):
    verify_report(report)
    folder=Path(output_dir).resolve()/('eval-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:10])
    folder.mkdir(parents=True,exist_ok=False)
    documents={'json':('report.json',json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)),
               'html':('report.html',render_report(report)),
               'review':('review-template.json',json.dumps(review_template(report),ensure_ascii=False,indent=2,allow_nan=False))}
    paths={}
    for key,(name,content) in documents.items():
        path=folder/name
        with path.open('x',encoding='utf-8') as target:
            target.write(content)
        paths[key]=str(path)
    return paths
