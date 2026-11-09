"""A finite field registry. Questions can never grant business permissions."""
from decimal import Decimal, InvalidOperation


FIELDS={
    'budget_cny':('number','公司合作预算','用于评价多产品合作金额是否符合本次预算。','不回答则不能完成本次要求的预算适配判断。'),
    'variety':('text','本季品种要求','用于保留客户本季明确品种约束。','不回答则不能确认本次要求的品种约束。'),
    'soil_ph':('number','本季测土 pH','本次研究明确要求该检测事实。','检测缺失时不推导依赖pH的专业结论。'),
    'storage':('select','本季仓储条件','用于核实本次市场策略所需仓储条件。','仓储未知不按可以储粮处理。')}


def missing_questions(snapshot):
    facts=snapshot['facts']
    result=[]
    rates=[('rate_kg_per_mu',facts,'主产品')]+[
        ('additional_materials.'+item['id']+'.rate_kg_per_mu',item,'追加产品 '+item['product_id'])
        for item in facts.get('additional_materials',[])]
    for field,item,label in rates:
        if item.get('rate_kg_per_mu') is None:
            result.append({'field':field,'answer_type':'number','prompt':'请 SR 确认'+label+'单位面积用量（kg/亩）及依据。',
                           'purpose':'核算该产品数量与合作小计，不能从历史或预算倒推。','impact':'缺少确认用量时不能完成产品核算。','options':[]})
    for field in snapshot.get('required_fields',[]):
        kind,label,purpose,impact=FIELDS[field]
        value=facts.get(field)
        if value is None or value=='' or (field=='storage' and value=='unknown'):
            result.append({'field':field,'answer_type':kind,'prompt':'请 SR 确认'+label+'及来源。','purpose':purpose,'impact':impact,
                           'options':[{'value':'yes','label':'有可用仓储'},{'value':'no','label':'无可用仓储'}] if field=='storage' else []})
    return result


def normalize_answer(field,value):
    if field=='storage':
        if value not in ('yes','no'):
            raise ValueError('仓储回答必须为yes或no')
        return value
    if field=='variety':
        if not isinstance(value,str) or not value.strip() or len(value.strip())>160:
            raise ValueError('品种回答需1至160字')
        return value.strip()
    if field not in ('budget_cny','soil_ph','rate_kg_per_mu') and not (
            field.startswith('additional_materials.') and field.endswith('.rate_kg_per_mu')):
        raise ValueError('不允许回答该字段')
    try:
        number=Decimal(str(value))
        limit=Decimal('1000000000' if field=='budget_cny' else '14' if field=='soil_ph' else '1000')
        if not number.is_finite() or number<0 or number>limit or number.as_tuple().exponent < -4:
            raise ValueError('回答数字超出范围或精度')
        return format(number.normalize(),'f')
    except (InvalidOperation,TypeError):
        raise ValueError('回答必须是有效数字') from None
