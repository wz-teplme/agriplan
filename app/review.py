"""Deterministic candidate checks; no model or network calls."""
import hashlib
import json

from .domain import calculate, decimal, dumps, timestamp, uid
from .knowledge import evidence_status
from .materials import additional_calculation, material_text, material_manifest
from .questions import missing_questions

RULE_VERSION = 'candidate-review-v4'
DIMENSIONS = ('condition_fit', 'evidence_sufficiency', 'numeric_consistency',
              'execution_readiness', 'risk_preparedness')
REVIEW_FIELDS = {'review', 'dimensions', 'issues', 'can_confirm', 'content_hash',
                 'evidence_fingerprint', 'rule_version'}


def _hash(value):
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()


def _references(value):
    return value if isinstance(value, list) and all(isinstance(x, str) and x for x in value) else []


def inspect_candidate(db, run, draft):
    """Re-read current versions and evaluate the supplied immutable candidate content."""
    snapshot = run['snapshot']
    facts, product = snapshot['facts'], snapshot['product']
    draft = draft if isinstance(draft, dict) else {}
    issues, dimensions = [], dict.fromkeys(DIMENSIONS, '符合')

    def issue(code, field, message, close_condition, dimension, severity='blocking'):
        issues.append({'id':_hash([code, field])[:20], 'code':code, 'severity':severity,
                       'field':field, 'message':message, 'close_condition':close_condition,
                       'responsible_role':'SR', 'references':[field]})
        if severity == 'blocking':
            dimensions[dimension] = '不符合'
        elif dimensions[dimension] == '符合':
            dimensions[dimension] = '部分符合'

    report_sources=[]
    if 'report_refs' in snapshot:
        from .reports import check_sources
        report_sources=check_sources(db,snapshot)
        for source in report_sources:
            if source['error']:
                issue('REPORT_SOURCE_UNAVAILABLE','report_refs.'+str(source['report_id']),
                      source['error'],'恢复原件完整性并核对当前确认来源后重新评价。','evidence_sufficiency')

    measures = draft.get('measures')
    if draft.get('schema_version') != 2 or not isinstance(measures, list) or not measures:
        issue('STRUCTURED_DRAFT_REQUIRED', 'measures', '旧规则或空草稿缺少可审核的结构化措施。',
              '基于当前输入重新生成包含措施、条件和引用的 v2 草稿。', 'execution_readiness')
        measures = []
    stages = draft.get('stages')
    if not isinstance(stages, list) or not stages or any(
            not isinstance(stage, dict) or any(not str(stage.get(k) or '').strip() for k in ('name','timing','action'))
            for stage in stages):
        issue('EXECUTION_STAGES_REQUIRED', 'stages', '缺少完整的阶段、时间或行动说明。',
              '补齐阶段安排并重新评价。', 'execution_readiness')

    current_season = db.execute('SELECT revision FROM seasons WHERE id=?', (run['season_id'],)).fetchone()
    if (current_season is None or current_season['revision'] != snapshot.get('revision')
            or draft.get('input_revision') != snapshot.get('revision')):
        issue('INPUT_REVISION_STALE', 'input_revision', '草稿与当前本季确认输入版本不一致。',
              '使用当前输入版本重新研制和核算。', 'condition_fit')
    current_product = db.execute('SELECT * FROM products WHERE id=?', (product.get('id'),)).fetchone()
    additional_current=[]
    for item in facts.get('additional_materials',[]):
        saved=snapshot.get('additional_products',{}).get(item['id'])
        current=db.execute('SELECT * FROM products WHERE id=?',(item['product_id'],)).fetchone()
        additional_current.append(dict(current) if current else None)
        if saved is None or current is None or dict(current)!=saved or not current['active'] or current['unit']!='CNY/kg':
            issue('PRODUCT_VERSION_CHANGED','additional_materials.'+item['id'],'追加产品目录或价格版本已变化。',
                  '按当前有效目录重新研制核算。','numeric_consistency')
    product_keys = ('id','price','unit','revision','active')
    draft_product = draft.get('product') if isinstance(draft.get('product'), dict) else {}
    if (current_product is None or not current_product['active'] or current_product['unit'] != 'CNY/kg'
            or any(current_product[key] != product.get(key) for key in product_keys)
            or any(draft_product.get(key) != product.get(key) for key in product_keys)
            or facts.get('product_id') != product.get('id')):
        issue('PRODUCT_VERSION_CHANGED', 'product', '产品或价格版本、有效状态及计价单位已变化或与输入不一致。',
              '核实当前有效产品和价格，更新输入后重新核算。', 'numeric_consistency')
    if draft.get('variety') != facts.get('variety'):
        issue('CONSTRAINT_VIOLATION', 'variety', '草稿更改了本季已确认的品种约束。',
              '保留已确认品种，或由 SR 更新本季事实后重新研制。', 'condition_fit')
    if draft.get('constraints') != {key:facts.get(key) for key in ('variety','budget_cny','budget_basis')}:
        issue('CONSTRAINT_VIOLATION', 'constraints', '草稿约束与本季已确认的品种、预算及预算口径不一致。',
              '完整保留当前确认约束（包括未知空值），或由 SR 更新事实后重新研制。', 'condition_fit')

    rate = facts.get('rate_kg_per_mu')
    if rate is None:
        issue('RATE_CONFIRMATION_REQUIRED', 'facts.rate_kg_per_mu', '本季专业用量尚未由 SR 确认。',
              '由 SR 确认本季单位面积用量及来源后重新核算。', 'numeric_consistency')
    if not str(facts.get('rate_source') or '').strip():
        issue('RATE_SOURCE_REQUIRED', 'facts.rate_source', '确认用量缺少来源，不能用历史参数或模型解释替代。',
              '由 SR 补齐本季确认参数来源后重新核算。', 'evidence_sufficiency')

    calculation = draft.get('calculation') if isinstance(draft.get('calculation'), dict) else {}
    additional_expected=None
    if (facts.get('additional_materials') or snapshot.get('workflow_version')=='mvp-v3'
            or any(key in calculation for key in ('additional_lines','cooperation_total_cny'))):
        try:
            additional_expected=additional_calculation(snapshot)
            if any(calculation.get(key)!=value for key,value in additional_expected.items()):
                raise ValueError('追加行或合计不一致')
        except (ValueError,KeyError,TypeError):
            issue('CALCULATION_MISMATCH','calculation.additional_lines','追加行数量、金额、合计或血缘无法与工具核算一致。',
                  '按当前输入与全部适用目录重新核算，不能删除或篡改追加行。','numeric_consistency')
    for question in missing_questions(snapshot):
        issue('REQUIRED_FACT_REQUIRED','facts.'+question['field'],question['prompt'],
              '由SR通过当前问题确认事实并提供来源后恢复。','condition_fit')
    manifest_required=('materials' in draft or snapshot.get('workflow_version')=='mvp-v3' or bool(facts.get('additional_materials')))
    if manifest_required:
        try:
            if draft.get('materials')!=material_manifest(snapshot,calculation):
                raise ValueError('物资表不一致')
        except (ValueError,KeyError,TypeError):
            issue('MATERIALS_MISMATCH','materials','物资表与服务器逐行核算或计算引用不一致。',
                  '恢复全部公司产品的数量、金额、目录版本及计算引用。','numeric_consistency')
        if sum(isinstance(m,dict) and m.get('kind')=='confirmed_input' for m in measures)!=1:
            issue('CONFIRMED_INPUT_MISMATCH','measures.confirmed_input','主产品确认说明缺失或重复。',
                  '保留一条服务器生成的主产品确认说明。','execution_readiness')
    expected = None
    try:
        expected = calculate(facts['area_mu'], rate, product['price'])
        numeric_ok = all(decimal(calculation.get(key)) == decimal(expected[key]) for key in ('total_kg','cost_cny'))
        numeric_ok = numeric_ok and all(calculation.get(key) == expected[key] for key in ('quantity_unit','money_unit','rule_version'))
        inputs = calculation.get('inputs')
        numeric_ok = numeric_ok and isinstance(inputs, dict) and all(
            decimal(inputs.get(key)) == decimal(value) for key, value in {
                'area_mu':facts['area_mu'], 'rate_kg_per_mu':rate, 'price_cny_per_kg':product['price']}.items())
        refs = calculation.get('input_refs')
        numeric_ok = numeric_ok and isinstance(refs, dict) and all(refs.get(key) == value for key, value in {
            'season_id':run['season_id'], 'fact_revision':snapshot['revision'], 'product_id':product['id'],
            'product_revision':product['revision'], 'rate_source':facts.get('rate_source')}.items())
        if not numeric_ok or not calculation.get('id'):
            raise ValueError('核算结果或血缘不一致')
    except (ValueError, KeyError, TypeError):
        issue('CALCULATION_MISMATCH', 'calculation', '数量、金额、单位或输入引用无法与确定性工具核算一致。',
              '使用当前面积、SR 确认用量和产品价格重新运行核算工具，保留计算引用。', 'numeric_consistency')
    if expected is not None and facts.get('budget_cny') is not None:
        try:
            total=additional_expected['cooperation_total_cny'] if additional_expected else expected['cost_cny']
            if decimal(total) > decimal(facts['budget_cny']):
                issue('BUDGET_EXCEEDED', 'calculation.cost_cny', '按确认输入重新核算的产品小计超过公司合作预算。',
                      '由 SR 调整已确认方案或预算后重新核算，不能只删除超预算提示。', 'numeric_consistency')
        except ValueError:
            issue('BUDGET_INVALID', 'facts.budget_cny', '预算值不能用于核验。',
                  '由 SR 确认有效预算。', 'numeric_consistency')

    evidence_ids = _references(draft.get('evidence_refs'))
    if not evidence_ids:
        issue('EVIDENCE_REQUIRED', 'evidence_refs', '草稿缺少可追溯依据。',
              '引用适用且已发布的资料版本后重新评价。', 'evidence_sufficiency')
    all_evidence = set(evidence_ids)
    for measure in measures:
        if isinstance(measure, dict):
            all_evidence.update(_references(measure.get('evidence_refs')))
    documents, evidence_versions = {}, []
    evidence_messages = {
        'EVIDENCE_NOT_FOUND':'引用资料不存在。', 'EVIDENCE_SCOPE':'引用资料不在当前客户可用范围。',
        'EVIDENCE_NOT_PUBLISHED':'引用资料未发布或已被新版本替代。', 'EVIDENCE_REVOKED':'引用资料已撤回。',
        'EVIDENCE_EXPIRED':'引用资料不在当前有效期。', 'EVIDENCE_NOT_APPLICABLE':'引用资料的作物或地区不适用。'}
    for evidence_id in sorted(all_evidence):
        document, error = evidence_status(db, evidence_id, snapshot['crop'], snapshot['region'], snapshot['customer_id'])
        evidence_versions.append({'id':evidence_id, 'document':document, 'error':error})
        field = 'evidence_refs.'+evidence_id
        if error:
            issue(error, field, evidence_messages.get(error, '引用资料当前不可用。'),
                  '改用当前客户范围、作物和地区适用且有效发布的资料后重新评价。', 'evidence_sufficiency')
            continue
        if not str(document.get('source') or '').strip() or not str(document.get('locator') or '').strip():
            issue('EVIDENCE_SOURCE_REQUIRED', field, '引用资料缺少明确来源或定位。',
                  '补齐来源和定位，并经人工审核发布新版本。', 'evidence_sufficiency')
            continue
        if document.get('purpose') not in ('plan_research','knowledge'):
            issue('EVIDENCE_PURPOSE_MISMATCH', field, '该资料用途不能支持主方案研究。',
                  '引用用途为 plan_research 或 knowledge 的适用资料。', 'evidence_sufficiency')
            continue
        documents[evidence_id] = document
        if document.get('demo'):
            issue('DEMO_EVIDENCE', field, '引用资料带有演示身份，不能据此声称真实生产效果已验证。',
                  '实际使用时替换为适用且完成审核的真实资料。', 'risk_preparedness', 'warning')
    measure_ids = set()
    for index, measure in enumerate(measures):
        field = 'measures.'+str(index)
        if not isinstance(measure, dict) or measure.get('kind') not in ('procedure','professional','confirmed_input','confirmed_material'):
            issue('MEASURE_KIND_INVALID', field, '措施类型无法按现有规则评价。',
                  '明确措施属于流程、当前确认参数说明或专业措施。', 'execution_readiness')
            continue
        measure_id = measure.get('id')
        if not isinstance(measure_id, str) or not measure_id.strip() or measure_id in measure_ids:
            issue('MEASURE_ID_INVALID', field+'.id', '措施缺少唯一标识，无法稳定关联评价问题。',
                  '为每条措施提供非空且唯一的标识后重新评价。', 'execution_readiness')
        else:
            measure_ids.add(measure_id)
        if not str(measure.get('text') or '').strip():
            issue('MEASURE_TEXT_REQUIRED', field+'.text', '措施内容为空。',
                  '补齐可审核的措施正文。', 'execution_readiness')
        refs = _references(measure.get('evidence_refs'))
        fact_refs = _references(measure.get('fact_refs'))
        calculation_refs = _references(measure.get('calculation_refs'))
        if fact_refs != measure.get('fact_refs') or any(ref not in facts or facts[ref] is None for ref in fact_refs):
            issue('FACT_REFERENCE_INVALID', field+'.fact_refs', '措施引用了未确认或不存在的本季事实。',
                  '改用当前已确认且存在的本季事实字段。', 'evidence_sufficiency')
        if (calculation_refs != measure.get('calculation_refs') or
                any(ref != calculation.get('id') for ref in calculation_refs)):
            issue('CALCULATION_REFERENCE_INVALID', field+'.calculation_refs', '措施引用了不存在的核算记录。',
                  '关联本草稿的确定性核算记录 ID。', 'numeric_consistency')
        if any(reference not in evidence_ids for reference in refs):
            issue('EVIDENCE_REFERENCE_INVALID', field+'.evidence_refs', '措施引用未列入草稿依据清单。',
                  '使措施引用与草稿依据清单保持一致后重新评价。', 'evidence_sufficiency')
        if measure['kind'] in ('procedure','professional'):
            if not refs:
                issue('EVIDENCE_REQUIRED', field+'.evidence_refs', '措施缺少对应的资料引用。',
                      '为该措施关联可用且能够支持该主张的资料版本。', 'evidence_sufficiency')
            supported = [documents[ref] for ref in refs if ref in documents
                         and documents[ref].get('kind') == measure['kind']
                         and measure.get('text') in documents[ref].get('supported_claims', [])]
            if not supported:
                code = 'PROFESSIONAL_SUPPORT_REQUIRED' if measure['kind']=='professional' else 'PROCEDURE_SUPPORT_REQUIRED'
                issue(code, field, '措施正文没有得到相同类型资料中人工审核主张的精确支持。',
                      '引用同类型已发布资料中审核通过的原文主张；专业措施不能借用流程资料自证。', 'evidence_sufficiency')
            if any(document.get('conditions') for document in supported):
                issue('CONDITION_REVIEW_REQUIRED', field+'.evidence_conditions', '支持资料有适用条件，尚无可靠的条件核验结论。',
                      '补齐适用条件的确认事实及专业复核，不能删除措施条件绕过来源限制。', 'condition_fit')
        elif measure['kind']=='confirmed_material':
            line_id=measure.get('material_line_id')
            line=next((item for item in (additional_expected or {}).get('additional_lines',[]) if item['id']==line_id),None)
            if (line is None or measure.get('text')!=material_text(snapshot,line)
                    or set(fact_refs)!={'area_mu','additional_materials'}
                    or calculation_refs!=[calculation.get('id')]):
                issue('CONFIRMED_INPUT_MISMATCH',field,'追加产品说明或引用与服务器核算不符。',
                      '恢复服务器生成的追加产品确认说明与引用。','numeric_consistency')
        else:
            confirmed_text = (f"SR 本季确认用量 {rate} kg/亩，来源：{facts.get('rate_source')}；"
                              f"按确认面积 {facts.get('area_mu')} 亩核算产品总量 {calculation.get('total_kg')} kg、"
                              f"金额 {calculation.get('cost_cny')} CNY。")
            if measure.get('text') != confirmed_text:
                issue('CONFIRMED_INPUT_MISMATCH', field+'.text', '确认参数说明包含不能由当前事实和工具核算直接验证的表述。',
                      '仅使用服务器生成的本季确认参数、来源和核算说明；额外专业主张应单列并关联专业资料。', 'evidence_sufficiency')
            if not {'area_mu','rate_kg_per_mu','rate_source','product_id'}.issubset(fact_refs) or any(ref not in facts for ref in fact_refs):
                issue('FACT_REFERENCE_INVALID', field+'.fact_refs', '确认参数说明没有完整引用当前本季确认事实。',
                      '引用当前面积、单位面积用量、来源和产品事实字段。', 'evidence_sufficiency')
            if _references(measure.get('calculation_refs')) != [calculation.get('id')] or not calculation.get('id'):
                issue('CALCULATION_REFERENCE_INVALID', field+'.calculation_refs', '确认参数说明未引用当前工具核算记录。',
                      '关联本草稿的确定性核算记录 ID。', 'numeric_consistency')
        if measure.get('conditions'):
            issue('CONDITION_REVIEW_REQUIRED', field+'.conditions', '措施成立条件尚无可可靠核验的适用性结论。',
                  '补齐条件对应的确认事实和专业复核；不能用模型自报通过关闭问题。', 'condition_fit')
    if draft.get('pending_decisions'):
        issue('PENDING_DECISION', 'pending_decisions', '草稿仍包含待决事项。',
              '按事项补齐当前确认事实或可核验依据，更新草稿后重新评价。', 'execution_readiness')
    if facts.get('additional_materials'):
        actual=[m.get('material_line_id') for m in measures if isinstance(m,dict) and m.get('kind')=='confirmed_material']
        expected_ids={item['id'] for item in facts['additional_materials']}
        if set(actual)!=expected_ids or len(actual)!=len(expected_ids):
            issue('CONFIRMED_INPUT_MISMATCH','measures','追加产品确认说明缺失或重复。',
                  '保留每一条追加产品的服务器确认说明。','execution_readiness')
    issue('SR_FREE_TEXT_REVIEW', 'summary/stages', '程序只核验结构、引用和核算；摘要与阶段正文仍需 SR 审查，未自动证明其中所有专业结论。',
          '由 SR 审查摘要和阶段正文的适用性，专业措施应列入结构化措施并关联审核依据。', 'risk_preparedness', 'warning')
    if facts.get('soil_ph') is None:
        issue('SOIL_PH_UNKNOWN', 'facts.soil_ph', '非必填测土 pH 未知，不能支持依赖它的专业判断。',
              '需要相关专业判断时补充确认的检测资料。', 'condition_fit', 'warning')
        if dimensions['condition_fit'] != '不符合':
            dimensions['condition_fit'] = '无法评价'
    if facts.get('budget_cny') is None:
        issue('BUDGET_UNKNOWN', 'facts.budget_cny', '公司合作预算未知，仅能核算已知产品小计。',
              '需要预算适配判断时由 SR 确认预算。', 'risk_preparedness', 'warning')
        if dimensions['risk_preparedness'] != '不符合':
            dimensions['risk_preparedness'] = '无法评价'
    if not all_evidence:
        dimensions['evidence_sufficiency'] = '无法评价'

    fingerprint = _hash({'evidence':evidence_versions,'report_sources':report_sources,'additional_products':additional_current,
                         'product':dict(current_product) if current_product else None})
    return {'rule_version':RULE_VERSION, 'content_hash':_hash({k:v for k,v in draft.items() if k not in REVIEW_FIELDS}),
            'input_revision':snapshot.get('revision'), 'dimensions':dimensions, 'issues':issues,
            'can_confirm':not any(item['severity']=='blocking' for item in issues), 'evidence_fingerprint':fingerprint}


def save_review(db, run, draft, candidate_version):
    review = inspect_candidate(db, run, draft) | {'id':uid(), 'run_id':run['id'],
             'candidate_version':candidate_version, 'created_at':timestamp()}
    db.execute('''INSERT INTO candidate_reviews(id,run_id,candidate_version,content_hash,input_revision,
               evidence_fingerprint,rule_version,dimensions,issues,can_confirm,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
               (review['id'], review['run_id'], candidate_version, review['content_hash'], review['input_revision'],
                review['evidence_fingerprint'], review['rule_version'], dumps(review['dimensions']),
                dumps(review['issues']), int(review['can_confirm']), review['created_at']))
    return review


def latest_review(db, run_id):
    row = db.execute('SELECT * FROM candidate_reviews WHERE run_id=? ORDER BY rowid DESC LIMIT 1', (run_id,)).fetchone()
    if row is None:
        return None
    review = dict(row)
    for field in ('dimensions','issues'):
        review[field] = json.loads(review[field])
    review['can_confirm'] = bool(review['can_confirm'])
    return review
