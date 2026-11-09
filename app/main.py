"""Same-origin API and responsive UI."""
import hashlib
import html
import json
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import domain, services, reports as report_service, knowledge as document_service
from .research import market_demo, search_knowledge
from .storage import Store

ROOT = Path(__file__).resolve().parents[1]


def create_app(db_path=None):
    store = Store(db_path or os.getenv('AGRIPLAN_DB', str(ROOT/'data'/'agriplan.db')))

    @asynccontextmanager
    async def lifespan(app):
        yield

    app = FastAPI(title='AgriPlan MVP', version='0.1.0', lifespan=lifespan,docs_url=None,redoc_url=None)
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1','localhost','[::1]','testserver'])

    @app.middleware('http')
    async def browser_boundary(request: Request, call_next):
        report_upload=request.method=='POST' and re.fullmatch(r'/api/seasons/[^/]+/reports',request.url.path)
        if request.method not in ('GET','HEAD','OPTIONS'):
            origin = request.headers.get('origin')
            if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
                return JSONResponse({'detail':'拒绝跨来源写入'},status_code=403)
            content_type = request.headers.get('content-type','')
            if not report_upload and request.url.path not in ('/api/logout',) and not content_type.startswith('application/json'):
                return JSONResponse({'detail':'写请求必须使用 application/json'},status_code=415)
        try:
            size = int(request.headers.get('content-length','0') or 0)
        except ValueError:
            return JSONResponse({'detail':'无效请求长度'},status_code=400)
        if size>(report_service.MAX_FILE_BYTES if report_upload else 65536):
            return JSONResponse({'detail':'原件超过8MiB' if report_upload else '请求体超过 64KB'},status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def actor(request: Request):
        token = request.cookies.get('agriplan_session','')
        digest = hashlib.sha256(token.encode()).hexdigest()
        with store.read() as db:
            row = db.execute('''SELECT u.id,u.username,u.name,u.role FROM users u JOIN sessions s ON s.user_id=u.id
                                WHERE s.token_hash=? AND s.expires_at>?''',(digest,time.time())).fetchone()
        if row is None:
            raise HTTPException(401,'请先登录')
        return dict(row)

    @app.get('/api/health')
    def health():
        return {'status':'ok','mode':domain.configured_mode(),'demo_data':True}

    @app.post('/api/login')
    def login(body: domain.Login, response: Response):
        with store.write() as db:
            row = db.execute('SELECT * FROM users WHERE username=?',(body.username,)).fetchone()
            if row is None or not domain.verify_password(body.password,row['password_hash']):
                raise HTTPException(401,'账号或密码错误')
            token = secrets.token_urlsafe(32)
            db.execute('DELETE FROM sessions WHERE expires_at<?',(time.time(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),row['id'],time.time()+8*3600))
        response.set_cookie('agriplan_session',token,httponly=True,samesite='strict',max_age=8*3600,path='/',secure=os.getenv('AGRIPLAN_COOKIE_SECURE')=='1')
        return {'id':row['id'],'name':row['name'],'role':row['role']}

    @app.post('/api/logout')
    def logout(request: Request, response: Response):
        token = request.cookies.get('agriplan_session','')
        with store.write() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
        response.delete_cookie('agriplan_session')
        return {'logged_out':True}

    @app.get('/api/bootstrap')
    def bootstrap(user=Depends(actor)):
        return services.bootstrap(store,user)

    @app.post('/api/customers',status_code=201)
    def customer_create(body: domain.CustomerCreate,user=Depends(actor)):
        return services.create_customer(store,user,body)

    @app.get('/api/seasons/{season_id}')
    def season_get(season_id:str,user=Depends(actor)):
        return services.season_detail(store,season_id,user)

    @app.put('/api/seasons/{season_id}')
    def season_put(season_id:str,body:domain.SeasonEdit,user=Depends(actor)):
        return services.edit_season(store,season_id,user,body)

    @app.post('/api/seasons/{season_id}/reports',status_code=201)
    async def report_upload(season_id:str,request:Request,filename:str=Query(min_length=1,max_length=160),user=Depends(actor)):
        services.require_sr(user)
        with store.read() as db:
            services.season_for(db,season_id,user)
        content=bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content)>report_service.MAX_FILE_BYTES:
                raise HTTPException(413,'原件超过8MiB')
        return report_service.upload_report(store,season_id,user,filename,request.headers.get('content-type',''),bytes(content))

    @app.get('/api/reports/{report_id}/original')
    def report_original(report_id:str,user=Depends(actor)):
        with store.read() as db:
            report,_=report_service.report_for(db,report_id,user)
            blob=report_service.verified_bytes(db,report)
        return Response(blob,media_type=report['mime_type'],headers={
            'Content-Disposition':"attachment; filename*=UTF-8''"+quote(report['filename'],safe='')})

    @app.post('/api/reports/{report_id}/extract')
    def report_extract(report_id:str,body:domain.Revision,user=Depends(actor)):
        return report_service.extract_report(store,report_id,user,body)

    @app.post('/api/reports/{report_id}/candidates')
    def report_candidate(report_id:str,body:domain.ReportCandidate,user=Depends(actor)):
        return report_service.manual_candidate(store,report_id,user,body)

    @app.post('/api/reports/{report_id}/confirm')
    def report_confirm(report_id:str,body:domain.ReportConfirm,user=Depends(actor)):
        return report_service.confirm_report(store,report_id,user,body)

    @app.post('/api/seasons/{season_id}/next',status_code=201)
    def next_season(season_id:str,body:domain.NewSeason,user=Depends(actor)):
        return services.create_next_season(store,season_id,user,body)

    @app.post('/api/research-runs',status_code=202)
    def research_create(body:domain.ResearchCreate,idempotency_key:str|None=Header(default=None),user=Depends(actor)):
        return services.create_research(store,user,body,idempotency_key)

    @app.get('/api/runs/{run_id}')
    def run_get(run_id:str,user=Depends(actor)):
        return services.run_detail(store,run_id,user)

    @app.post('/api/runs/{run_id}/answers')
    def answer(run_id:str,body:domain.Answer,user=Depends(actor)):
        return services.answer_question(store,run_id,user,body)

    @app.post('/api/runs/{run_id}/refresh',status_code=202)
    def research_refresh(run_id:str,body:domain.ResearchRefresh,user=Depends(actor)):
        from .runtime import refresh_run
        return refresh_run(store,run_id,user,body)

    @app.put('/api/runs/{run_id}/draft')
    def draft_put(run_id:str,body:domain.DraftEdit,user=Depends(actor)):
        return services.edit_draft(store,run_id,user,body)

    @app.post('/api/runs/{run_id}/confirm')
    def confirm(run_id:str,body:domain.Revision,user=Depends(actor)):
        return services.confirm_plan(store,run_id,user,body)

    @app.post('/api/runs/{run_id}/discuss')
    def discussion(run_id:str,body:domain.Discuss,user=Depends(actor)):
        return services.discuss(store,run_id,user,body)

    @app.post('/api/runs/{run_id}/proposals/{proposal_id}/apply')
    def proposal_apply(run_id:str,proposal_id:str,body:domain.Revision,user=Depends(actor)):
        from .discussion import apply_proposal
        return apply_proposal(store,run_id,proposal_id,user,body)

    @app.post('/api/runs/{run_id}/cancel')
    def cancel(run_id:str,body:domain.Revision,user=Depends(actor)):
        services.require_sr(user)
        with store.write() as db:
            run = services.run_for(db,run_id,user)
            services.expect(run['revision'],body.expected_revision)
            if run['status'] not in ('queued','running','waiting_input'):
                raise HTTPException(409,'任务已结束，不能取消')
            db.execute("UPDATE runs SET status='cancelled',revision=revision+1 WHERE id=?",(run_id,))
            db.execute("UPDATE jobs SET status='cancelled',token=NULL WHERE run_id=? AND status IN ('queued','running')",(run_id,))
            services.audit(db,run['season_id'],run_id,user['id'],'research_cancelled',{})
        return {'id':run_id,'status':'cancelled'}

    @app.post('/api/field-records',status_code=201)
    def record_create(body:domain.FieldRecord,user=Depends(actor)):
        return services.create_record(store,user,body)

    @app.get('/api/field-records')
    def records(user=Depends(actor)):
        return services.list_records(store,user)

    @app.post('/api/knowledge')
    def knowledge(body:domain.Discuss,user=Depends(actor)):
        with store.read() as db:
            evidence = search_knowledge(db,body.message)
        return {'answer':'\n\n'.join(e['body'] for e in evidence) if evidence else '本地资料未检索到可用依据，请联系专业负责人核查；系统不猜测答案。', 'evidence':evidence,
                'mode':'lexical_retrieval','limitation':'本地获准演示资料的关键词检索，无客户私有上下文；不生成生产处方。'}

    @app.get('/api/documents')
    def documents(user=Depends(actor)):
        return document_service.list_documents(store,user)

    @app.post('/api/documents',status_code=201)
    def document_create(body:domain.DocumentCreate,user=Depends(actor)):
        return document_service.create_document(store,user,body.model_dump(mode='json'))

    @app.get('/api/documents/{document_id}')
    def document_get(document_id:str,user=Depends(actor)):
        return document_service.get_document(store,user,document_id)

    @app.post('/api/documents/{document_id}/versions',status_code=201)
    def document_version(document_id:str,body:domain.DocumentVersion,user=Depends(actor)):
        return document_service.create_document(store,user,body.model_dump(mode='json'),previous_id=document_id)

    @app.post('/api/documents/{document_id}/publish')
    def document_publish(document_id:str,body:domain.DocumentTransition,user=Depends(actor)):
        return document_service.publish_document(store,user,document_id,body.expected_revision,body.note)

    @app.post('/api/documents/{document_id}/revoke')
    def document_revoke(document_id:str,body:domain.DocumentTransition,user=Depends(actor)):
        return document_service.revoke_document(store,user,document_id,body.expected_revision,body.note)

    @app.get('/api/runs/{run_id}/review')
    def candidate_review(run_id:str,user=Depends(actor)):
        return services.review_run(store,run_id,user)

    @app.post('/api/runs/{run_id}/review')
    def candidate_review_refresh(run_id:str,body:domain.Revision,user=Depends(actor)):
        return services.review_run(store,run_id,user,body.expected_revision)

    @app.get('/api/seasons/{season_id}/market')
    def market(season_id:str,user=Depends(actor)):
        with store.read() as db:
            season = services.season_for(db,season_id,user)
        return market_demo(season['crop'],season['region'],season['facts']['storage'])

    web = ROOT/'web'
    if web.exists():
        app.mount('/static',StaticFiles(directory=str(web)),name='static')

        @app.get('/',include_in_schema=False)
        def index():
            return FileResponse(web/'index.html')

        @app.get('/docs',response_class=HTMLResponse,include_in_schema=False)
        def api_docs():
            rows=[]
            for path, methods in app.openapi()['paths'].items():
                for method, spec in methods.items():
                    rows.append('<tr><td>'+html.escape(method.upper())+'</td><td class="mono">'+html.escape(path)+'</td><td>'+html.escape(spec.get('summary',''))+'</td></tr>')
            return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<title>AgriPlan API</title><link rel="stylesheet" href="/static/style.css"><main class="page"><h1>AgriPlan API · MVP</h1>'
                    '<p class="muted mt">同源 JSON API；业务请求需要登录后的会话。写操作按角色、对象及 revision 校验。</p>'
                    '<p class="row mt mb"><a class="btn" href="/">返回工作台</a><a class="btn" href="/openapi.json">完整 OpenAPI Schema</a></p>'
                    '<section class="card table-wrap"><table><thead><tr><th>Method</th><th>Path</th><th>Operation</th></tr></thead><tbody>'
                    +''.join(rows)+'</tbody></table></section></main></html>')

    return app
