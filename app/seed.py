"""Synthetic fixtures; no price, yield or agronomic efficacy claim."""
import os

from .domain import dumps, password_hash, timestamp


def seed(store):
    with store.write() as db:
        if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            return
        encoded = password_hash(os.getenv('AGRIPLAN_DEMO_PASSWORD', 'agriplan-demo'))
        db.executemany('INSERT INTO users VALUES(?,?,?,?,?)', [
            ('u1','sr_chen','陈晓 · 技术经理','SR',encoded),
            ('u2','sr_liu','刘宁 · 技术经理','SR',encoded),
            ('u3','fr_zhou','周禾 · 一线农艺师','FR',encoded),
        ])
        db.executemany('INSERT INTO customers VALUES(?,?,?)', [('c1','青禾家庭农场','吉林'),('c2','丰穗种植合作社','吉林'),('c3','远山农业','黑龙江')])
        db.executemany('INSERT INTO assignments VALUES(?,?)', [('u1','c1'),('u1','c2'),('u2','c3'),('u3','c1')])
        db.executemany('INSERT INTO plots VALUES(?,?,?)', [('p1','c1','东河一号田'),('p2','c2','南坡试验田'),('p3','c3','北山地块')])
        for sid, pid, cid, area, budget, rate in [('s1','p1','c1','300','26000','20'),('s2','p2','c2','120','15000',None),('s3','p3','c3','80','10000','20')]:
            facts = {'area_mu':area,'budget_cny':budget,'budget_basis':'company_cooperation_amount','variety':'客户自有种子，保持原品种',
                     'soil_ph':None,'product_id':'fert-1','rate_kg_per_mu':rate,'rate_source':'SR 确认的演示参数（非生产处方）' if rate is not None else '',
                     'storage':'no','note':'本记录与历史均为合成演示资料。非必填测土数据缺失，保留未知。'}
            db.execute('INSERT INTO seasons VALUES(?,?,?,?,?,?,?,NULL)', (sid,pid,cid,2026,'玉米',1,dumps(facts)))
            db.execute('INSERT INTO fact_versions VALUES(?,?,?,?,?,?)',(sid,1,dumps(facts),'u1' if cid!='c3' else 'u2','初始化演示数据',timestamp()))
        for year, area, rate, budget in [(2025,'300','25','32000'),(2024,'280','22','30000')]:
            content = {'version':'P1','summary':'往季玉米合作技术方案（演示）','variety':'客户自有种子',
                       'area_mu':area,'rate_kg_per_mu':rate,'budget_cny':budget,'product_id':'fert-1',
                       'measures':['播前核对种子与地块条件','阶段巡田并记录实际'],
                       'adjustment_reason':'往季客户采购条件','actual_outcome':None,
                       'limitations':['缺少完整收获与执行记录，不能据此证明方案效果','历史用量不自动成为本季确认参数']}
            db.execute('INSERT INTO history VALUES(?,?,?,?,?,?,?)',('h'+str(year),'p1',year,'玉米','吉林',0,dumps(content)))
        db.execute('INSERT INTO history VALUES(?,?,?,?,?,?,?)', ('case1',None,2025,'玉米','吉林',1,dumps({
            'version':'案例-v1','summary':'吉林玉米脱敏流程示例（已发布的演示案例）','variety':'未记载','area_mu':'100',
            'rate_kg_per_mu':None,'budget_cny':None,'measures':['先核实本季约束，再由 SR 确认专业用量'],
            'actual_outcome':None,'limitations':['仅地区与作物匹配，土壤/积温/降雨未验证，不构成农艺近似证明']})))
        db.executemany('INSERT INTO products VALUES(?,?,?,?,?,?)', [('fert-1','示例复合肥 A','3.20','CNY/kg',1,1),('fert-2','示例复合肥 B','4.10','CNY/kg',1,1)])
        db.executemany('INSERT INTO knowledge(id,title,body,crop,source,locator,version) VALUES(?,?,?,?,?,?,?)', [
            ('k1','历年方案如何参考','同一地块先比较本季面积、预算、品种和已确认用量。缺少实际收获记录时只能作为设计参考，不能证明效果。历史参数需要 SR 本季重新确认。','通用','项目修订手册 · 本地演示摘编','业务流程 §6.7 / §11.8','demo-v1'),
            ('k2','用量与预算核算','用量必须来自 SR 明确确认的参数。总量=面积×单位面积用量，金额=总量×适用单价。未知租金、农机费和自有物资成本保持未知，不能据此宣称完整净收益。','通用','项目修订手册 · 本地演示摘编','技术手册 §11.3—11.5','demo-v1'),
            ('k3','测土资料与加急边界','报告提取不等于确认事实。非必填资料缺失不阻止草稿，但依赖检测的专业判断需要适用依据和 SR 复核。本系统示例资料不用于制定生产处方。','通用','项目修订手册 · 本地演示摘编','业务流程 §4.2—4.7','demo-v1'),
            ('k4','含水率与粮食报价','只有作物、地区、单位、含水率、运输及扣价口径可比较的报价，才能核算净到手价。费用未知不按零处理。无仓储不能默认储粮；本演示不预测价格。','通用','项目修订手册 · 本地演示摘编','业务流程 §18—19','demo-v1'),
        ])
