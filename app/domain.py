"""Deterministic boundaries: quantities, money, quote comparability and requests."""
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def uid():
    return secrets.token_hex(12)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def decimal(value, *, positive=False):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or (positive and number == 0) or number > Decimal('1000000000'):
            raise ValueError('数值必须有限、非负且在允许范围内')
        return number
    except (InvalidOperation, TypeError) as exc:
        raise ValueError('无效数值') from exc


def calculate(area, rate, price):
    quantity = decimal(area, positive=True) * decimal(rate)
    cost = (quantity * decimal(price)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    return {'total_kg': str(quantity), 'cost_cny': str(cost), 'quantity_unit': 'kg',
            'money_unit': 'CNY', 'formula': '面积(亩) × SR确认用量(kg/亩) × 单价(CNY/kg)',
            'rule_version': 'quantity-cost-v1', 'rounding': 'ROUND_HALF_UP / 0.01 CNY'}


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ':' + digest


def verify_password(password, encoded):
    return hmac.compare_digest(password_hash(password, encoded.split(':')[0]), encoded)


def compare_quotes(quotes, *, crop, region, storage, now=None):
    now = now or datetime.now(timezone.utc)
    eligible, excluded = [], []
    for quote in quotes:
        try:
            as_of = datetime.fromisoformat(quote['as_of'])
            if as_of.tzinfo is None or not 0 <= (now-as_of).total_seconds() <= 7*86400:
                raise ValueError('报价时点过期、缺时区或来自未来')
            if (quote['crop'], quote['region'], quote['unit'], quote['moisture']) != (crop, region, 'CNY/kg', '14'):
                raise ValueError('作物、地区、单位或含水率口径不一致')
            if quote.get('freight') is None or quote.get('deduction') is None:
                raise ValueError('运输或扣价未知，不能算净到手价')
            net = decimal(quote['price']) - decimal(quote['freight']) - decimal(quote['deduction'])
            eligible.append(quote | {'net_cny_per_kg': str(net.quantize(Decimal('.0001'), rounding=ROUND_HALF_UP))})
        except (ValueError, KeyError) as exc:
            excluded.append({'id': quote.get('id'), 'buyer': quote.get('buyer'), 'reason': str(exc)})
    eligible.sort(key=lambda x: Decimal(x['net_cny_per_kg']), reverse=True)
    strategies = ['compare_current_buyers'] if eligible else []
    if storage == 'yes' and eligible:
        strategies.append('store')
    return {'eligible': eligible, 'excluded': excluded, 'best_quote_id': eligible[0]['id'] if eligible else None,
            'strategies': strategies, 'storage': storage, 'data_as_of': now.isoformat(),
            'limitation': '演示报价，只比较已知口径；不预测价格、不代客交易。仓储可用不代表储粮一定获利。'}


Number = Annotated[Decimal, Field(ge=0, le=1000000000, max_digits=16, decimal_places=4, allow_inf_nan=False)]
Area = Annotated[Decimal, Field(gt=0, le=1000000, max_digits=14, decimal_places=4, allow_inf_nan=False)]
Text = Annotated[str, Field(min_length=1, max_length=4000)]


class Request(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Login(Request):
    username: Annotated[str, Field(min_length=1, max_length=80)]
    password: Annotated[str, Field(min_length=1, max_length=200)]


Rate = Annotated[Decimal, Field(ge=0, le=1000, decimal_places=4, allow_inf_nan=False)]


class MaterialInput(Request):
    id: Annotated[str, Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')]
    product_id: Literal['fert-1','fert-2']
    rate_kg_per_mu: Rate | None = None
    rate_source: Annotated[str, Field(max_length=1000)] = ''

    @model_validator(mode='after')
    def require_source(self):
        if self.rate_kg_per_mu is not None and not self.rate_source:
            raise ValueError('追加产品用量需要 SR 本季确认来源')
        return self


class Facts(Request):
    area_mu: Area
    budget_cny: Number | None = None
    budget_basis: Literal['company_cooperation_amount'] = 'company_cooperation_amount'
    variety: Annotated[str, Field(max_length=160)] = ''
    soil_ph: Annotated[Decimal, Field(ge=0, le=14, allow_inf_nan=False)] | None = None
    product_id: Literal['fert-1', 'fert-2'] = 'fert-1'
    rate_kg_per_mu: Annotated[Decimal, Field(ge=0, le=1000, decimal_places=4, allow_inf_nan=False)] | None = None
    rate_source: Annotated[str, Field(max_length=1000)] = ''
    storage: Literal['unknown', 'yes', 'no'] = 'unknown'
    note: Annotated[str, Field(max_length=4000)] = ''
    additional_materials: Annotated[list[MaterialInput], Field(max_length=19)] = []

    @model_validator(mode='after')
    def require_rate_source(self):
        if self.rate_kg_per_mu is not None and not self.rate_source:
            raise ValueError('专业用量需要 SR 确认的来源，不从历史或模型自动推导')
        products=[self.product_id]+[item.product_id for item in self.additional_materials]
        ids=[item.id for item in self.additional_materials]
        if len(set(products))!=len(products) or len(set(ids))!=len(ids):
            raise ValueError('产品和明细标识不能重复，避免重复计费')
        return self


class SeasonEdit(Request):
    expected_revision: int = Field(ge=1)
    facts: Facts
    source: Text


class CustomerCreate(Request):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    plot_name: Annotated[str, Field(min_length=1, max_length=120)]
    region: Annotated[str, Field(min_length=1, max_length=120)]
    crop: Literal['玉米', '水稻', '小麦'] = '玉米'
    year: int = Field(ge=2000, le=2100)
    area_mu: Area


class NewSeason(Request):
    year: int = Field(ge=2000, le=2100)
    crop: Literal['玉米', '水稻', '小麦']
    area_mu: Area


class ResearchCreate(Request):
    season_id: Text
    goal: Text
    expected_revision: int = Field(ge=1)
    include_market: bool = False
    required_fields: Annotated[list[Literal['budget_cny','variety','storage','soil_ph']],Field(max_length=4)] = []

    @model_validator(mode='after')
    def unique_required(self):
        if len(set(self.required_fields))!=len(self.required_fields):
            raise ValueError('本次必需事实不能重复')
        return self


class Answer(Request):
    question_id: Text
    question_revision: int = Field(ge=1)
    value: str | Decimal
    source: Text
    expected_input_revision: int | None = Field(default=None,ge=1)


class Stage(Request):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    timing: Annotated[str, Field(min_length=1, max_length=200)]
    action: Annotated[str, Field(min_length=1, max_length=1000)]


class DraftText(Request):
    summary: Text
    stages: Annotated[list[Stage], Field(min_length=1, max_length=12)]


class DraftEdit(DraftText):
    expected_revision: int = Field(ge=1)
    reason: Annotated[str, Field(max_length=2000)] = ''


class Revision(Request):
    expected_revision: int = Field(ge=1)


class ResearchRefresh(Request):
    expected_revision: int = Field(ge=1)
    expected_input_revision: int = Field(ge=1)
    expected_context_hash: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    intent: Literal['refresh','repair'] = 'refresh'


class Discuss(Request):
    message: Text
    intent: Literal['question','change_request'] = 'question'
    expected_revision: int | None = Field(default=None,ge=1)
    reason: Annotated[str, Field(max_length=2000)] = ''
    proposed_edit: DraftText | None = None

    @model_validator(mode='after')
    def explicit_change(self):
        if self.intent=='change_request' and self.expected_revision is None:
            raise ValueError('修改建议必须绑定原研究版本')
        if self.intent=='question' and self.proposed_edit is not None:
            raise ValueError('询问不能夹带修改')
        return self


class ReportCandidate(Request):
    expected_revision: int = Field(ge=1)
    raw_value: Annotated[str, Field(min_length=1,max_length=100)]
    original_unit: Annotated[str, Field(max_length=40)]
    plot_name: Annotated[str, Field(max_length=160)]
    sampled_on: Annotated[str, Field(max_length=40)]
    page: int = Field(ge=1,le=1000)
    locator: Annotated[str, Field(min_length=1,max_length=500)]
    excerpt: Annotated[str, Field(min_length=1,max_length=2000)]


class ReportConfirm(Request):
    expected_revision: int = Field(ge=1)
    expected_input_revision: int = Field(ge=1)
    extraction_id: Text
    source: Annotated[str, Field(min_length=1,max_length=1000)]


class DocumentCreate(Request):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    body: Annotated[str, Field(min_length=1, max_length=16000)]
    kind: Literal['procedure', 'professional'] = 'procedure'
    purpose: Literal['plan_research', 'knowledge', 'market'] = 'plan_research'
    crop: Literal['通用', '玉米', '水稻', '小麦'] = '通用'
    region: Annotated[str, Field(min_length=1, max_length=120)] = '通用'
    source: Annotated[str, Field(min_length=1, max_length=1000)]
    locator: Annotated[str, Field(min_length=1, max_length=200)]
    customer_id: Annotated[str, Field(max_length=100)] | None = None
    demo: bool = True
    supported_claims: Annotated[list[Annotated[str, Field(min_length=1, max_length=2000)]], Field(max_length=20)] = []
    conditions: Annotated[list[Annotated[str, Field(min_length=1, max_length=1000)]], Field(max_length=20)] = []
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    @model_validator(mode='after')
    def validate_times(self):
        for value in (self.valid_from,self.valid_until):
            if value is not None and value.tzinfo is None:
                raise ValueError('资料有效时间必须有时区')
        if self.valid_from and self.valid_until and self.valid_from >= self.valid_until:
            raise ValueError('资料有效截止须晚于开始时间')
        return self


class DocumentVersion(DocumentCreate):
    expected_revision: int = Field(ge=1)


class DocumentTransition(Request):
    expected_revision: int = Field(ge=1)
    note: Text


class FieldRecord(Request):
    season_id: Text
    kind: Literal['inspection', 'operation', 'incident', 'review']
    observed_at: datetime
    notes: Text
    plan_id: Text | None = None

    @model_validator(mode='after')
    def require_timezone(self):
        if self.observed_at.tzinfo is None:
            raise ValueError('实际时间必须有时区')
        if self.observed_at > datetime.now(timezone.utc):
            raise ValueError('实际记录不能填未来时间')
        return self


def configured_mode():
    mode = os.getenv('AGRIPLAN_MODE', 'demo')
    if mode not in ('demo', 'llm'):
        raise ValueError('AGRIPLAN_MODE 必须是 demo 或 llm')
    return mode
