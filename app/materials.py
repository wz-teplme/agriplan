"""Server-owned additional product arithmetic and fixed factual explanations."""
from decimal import Decimal, ROUND_HALF_UP

from .domain import calculate, decimal, uid


def quantity_calculation(snapshot):
    facts = snapshot['facts']
    area = decimal(facts['area_mu'],positive=True)
    return {'total_kg':str(area*decimal(facts['rate_kg_per_mu'])), 'quantity_unit':'kg',
            'additional_lines':[{'id':item['id'],'product_id':item['product_id'],
                                 'total_kg':str(area*decimal(item['rate_kg_per_mu']))}
                                for item in facts.get('additional_materials',[])]}


def budget_calculation(snapshot, quantity, quantity_ref=None):
    facts, product = snapshot['facts'], snapshot['product']
    if not product['active'] or product['unit']!='CNY/kg' or not facts.get('rate_source'):
        raise ValueError('产品、价格单位或专业参数依据无效')
    cost = (decimal(quantity['total_kg'])*decimal(product['price'])).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    total, lines = cost, []
    quantities = {line['id']:line for line in quantity['additional_lines']}
    for item in facts.get('additional_materials',[]):
        extra = snapshot['additional_products'][item['id']]
        if extra['id']!=item['product_id'] or not extra['active'] or extra['unit']!='CNY/kg' or not item.get('rate_source'):
            raise ValueError('追加产品目录或确认来源无效')
        amount = (decimal(quantities[item['id']]['total_kg'])*decimal(extra['price'])).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        total += amount
        lines.append({'id':item['id'],'product_id':extra['id'],'product_name':extra['name'],
                      'rate_kg_per_mu':item['rate_kg_per_mu'],'rate_source':item['rate_source'],
                      'price_cny_per_kg':extra['price'],'total_kg':quantities[item['id']]['total_kg'],'cost_cny':str(amount),
                      'input_refs':{'season_id':snapshot['id'],'fact_revision':snapshot['revision'],
                                    'product_revision':extra['revision'],'product_id':extra['id'],'rate_source':item['rate_source']}})
    return {'id':uid(),'total_kg':quantity['total_kg'],'cost_cny':str(cost),'quantity_unit':'kg','money_unit':'CNY',
            'formula':'面积(亩) × SR确认用量(kg/亩) × 单价(CNY/kg)','rule_version':'quantity-cost-v1',
            'rounding':'ROUND_HALF_UP / 0.01 CNY','quantity_ref':quantity_ref,
            'input_refs':{'season_id':snapshot['id'],'fact_revision':snapshot['revision'],
                          'product_id':product['id'],'product_revision':product['revision'],'rate_source':facts['rate_source']},
            'inputs':{'area_mu':facts['area_mu'],'rate_kg_per_mu':facts['rate_kg_per_mu'],'price_cny_per_kg':product['price']},
            'additional_lines':lines,'cooperation_total_cny':str(total.quantize(Decimal('.01')))}


def additional_calculation(snapshot):
    facts=snapshot['facts']
    total=Decimal(calculate(facts['area_mu'],facts['rate_kg_per_mu'],snapshot['product']['price'])['cost_cny'])
    lines=[]
    for item in facts.get('additional_materials',[]):
        product=snapshot['additional_products'][item['id']]
        if product['id']!=item['product_id'] or not product['active'] or product['unit']!='CNY/kg' or not item.get('rate_source'):
            raise ValueError('追加产品目录或确认来源无效')
        result=calculate(facts['area_mu'],item['rate_kg_per_mu'],product['price'])
        total+=decimal(result['cost_cny'])
        lines.append({'id':item['id'],'product_id':product['id'],'product_name':product['name'],
                      'rate_kg_per_mu':item['rate_kg_per_mu'],'rate_source':item['rate_source'],
                      'price_cny_per_kg':product['price'],'total_kg':result['total_kg'],'cost_cny':result['cost_cny'],
                      'input_refs':{'season_id':snapshot['id'],'fact_revision':snapshot['revision'],
                                    'product_revision':product['revision'],'product_id':product['id'],
                                    'rate_source':item['rate_source']}})
    return {'additional_lines':lines,'cooperation_total_cny':str(total.quantize(Decimal('.01')))}


def material_text(snapshot,line):
    return (f"SR 本季确认追加产品 {line['product_name']} 用量 {line['rate_kg_per_mu']} kg/亩，来源：{line['rate_source']}；"
            f"按确认面积 {snapshot['facts']['area_mu']} 亩核算总量 {line['total_kg']} kg、金额 {line['cost_cny']} CNY。")


def material_manifest(snapshot,calculation):
    facts,product=snapshot['facts'],snapshot['product']
    primary=calculate(facts['area_mu'],facts['rate_kg_per_mu'],product['price'])
    result=[{'product_id':product['id'],'product_revision':product['revision'],
             'quantity_kg':primary['total_kg'],'cost_cny':primary['cost_cny'],'calculation_ref':calculation.get('id')}]
    for line in additional_calculation(snapshot)['additional_lines']:
        result.append({'id':line['id'],'product_id':line['product_id'],'product_revision':line['input_refs']['product_revision'],
                       'quantity_kg':line['total_kg'],'cost_cny':line['cost_cny'],'calculation_ref':calculation.get('id')})
    return result
