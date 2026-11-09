"""Immutable source files, extraction candidates, and explicitly confirmed pH."""
import hashlib
import json
import re
import subprocess
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import HTTPException

from .domain import Facts, dumps, timestamp, uid
from .services import audit, expect, require_sr, season_for

MAX_FILE_BYTES=8*1024*1024


def file_path(db,report_id):
    if not re.fullmatch(r'[a-f0-9]{24}',report_id):
        raise HTTPException(404,'报告不存在')
    database=db.execute('PRAGMA database_list').fetchone()['file']
    return Path(database).parent/'source-files'/(report_id+'.original')


def original_status(db,report):
    path=file_path(db,report['id'])
    try:
        if not path.is_file():
            return 'missing'
        if path.stat().st_size!=report['size_bytes'] or hashlib.sha256(path.read_bytes()).hexdigest()!=report['sha256']:
            return 'hash_mismatch'
    except OSError:
        return 'missing'
    return 'available'


def verified_bytes(db,report):
    path=file_path(db,report['id'])
    try:
        if path.stat().st_size>MAX_FILE_BYTES:
            raise ValueError('原件超限')
        blob=path.read_bytes()
        if len(blob)!=report['size_bytes'] or hashlib.sha256(blob).hexdigest()!=report['sha256']:
            raise ValueError('哈希不符')
    except (OSError,ValueError):
        raise HTTPException(409,'报告原件缺失或哈希不符，不能读取、提取或确认') from None
    return blob


def report_for(db,report_id,actor):
    row=db.execute('SELECT * FROM reports WHERE id=?',(report_id,)).fetchone()
    if row is None:
        raise HTTPException(404,'报告不存在或无访问权限')
    season=season_for(db,row['season_id'],actor)
    return dict(row),season


def candidate_issues(candidate,season):
    issues=[]
    def issue(code,message,severity='blocking'):
        issues.append({'code':code,'message':message,'severity':severity})
    if not candidate:
        issue('PH_REQUIRED','未识别唯一pH检测值，请核对原件并人工转录。')
        return issues
    try:
        value=Decimal(candidate['raw_value'])
        if not value.is_finite() or not 0<=value<=14 or value.as_tuple().exponent < -4:
            raise ValueError('pH范围')
    except (InvalidOperation,ValueError,TypeError):
        issue('PH_INVALID','pH必须为0至14的有限数值，最多4位小数。')
    if candidate['original_unit'].strip().casefold() not in ('ph','1','无量纲','unitless'):
        issue('UNIT_INVALID','pH原单位未知或不匹配；不能把mg/kg等浓度单位当作pH。')
    if candidate['plot_name'] not in (season['plot_name'],season['plot_id']):
        issue('PLOT_MISMATCH','报告地块名称或ID与当前本季不一致。')
    try:
        sampled=date.fromisoformat(candidate['sampled_on'])
        if sampled>date.today():
            raise ValueError('未来日期')
        if sampled.year!=season['year']:
            issue('SAMPLING_SEASON_REVIEW','采样年份与本季不同，SR需另核实当前适用性。','warning')
    except (ValueError,TypeError):
        issue('SAMPLING_DATE_REQUIRED','需要有效且不在未来的采样日期。')
    if not candidate.get('locator') or not candidate.get('excerpt'):
        issue('LOCATOR_REQUIRED','需要原件页码、表格/行定位及摘录。')
    issue('SR_REPORT_REVIEW','提取或转录不等于事实；请SR对照原件核对。','warning')
    return issues


def make_candidate(raw_value,original_unit,plot_name,sampled_on,page,locator,excerpt,manual=False):
    candidate={'field':'soil_ph','raw_value':raw_value.strip(),'value':None,
        'original_unit':original_unit.strip(),'normalized_unit':None,'plot_name':plot_name.strip(),
        'sampled_on':sampled_on.strip(),'page':page,'locator':locator,'excerpt':excerpt,
        'identity':'manual_candidate' if manual else 'extracted_candidate',
        'signal':'人工转录，需SR对照原件确认' if manual else '程序模式匹配，仅为提取信号，非专业可信度'}
    try:
        value=Decimal(candidate['raw_value'])
        if value.is_finite() and 0<=value<=14 and value.as_tuple().exponent>=-4:
            candidate['value']=format(value.normalize(),'f')
    except InvalidOperation:
        pass
    if candidate['original_unit'].casefold() in ('ph','1','无量纲','unitless'):
        candidate['normalized_unit']='1'
    return candidate


def parse_pages(pages):
    plots,sampled,values=[],[],[]
    unparsed_ph=False
    for page in pages:
        for line_number,line in enumerate(page['text'].splitlines(),1):
            plot=re.fullmatch(r'\s*(?:地块(?:名称|编号)?|plot)\s*[:：=]\s*(.*?)\s*',line,re.I)
            when=re.fullmatch(r'\s*(?:采样日期|采样时间|sampled|sampling date)\s*[:：=]\s*(.*?)\s*',line,re.I)
            ph=re.fullmatch(r'\s*(?:土壤\s*)?pH\s*[:：=]\s*([^\s]+)(?:\s+(.*?))?\s*',line,re.I)
            if plot: plots.append(plot[1])
            if when: sampled.append(when[1])
            if ph: values.append((ph[1],ph[2] or 'pH',page['page'],line_number,line))
            if not ph and re.search(r'pH[^\d\r\n]{0,40}[+-]?\d',line,re.I):
                unparsed_ph=True
    if len(values)!=1 or unparsed_ph:
        return None
    value,unit,page,line_number,excerpt=values[0]
    unique_plot=set(plots); unique_date=set(sampled)
    return make_candidate(value,unit,next(iter(unique_plot)) if len(unique_plot)==1 else '',
        next(iter(unique_date)) if len(unique_date)==1 else '',page,f'第{page}页 · 文本行{line_number}',excerpt)


def extraction_view(row):
    item=dict(row)
    for key in ('pages','candidate','issues'):
        item[key]=json.loads(item[key]) if item[key] is not None else None
    return item


def report_view(db,report,season):
    result=dict(report)
    extracts=[extraction_view(row) for row in db.execute('SELECT * FROM report_extractions WHERE report_id=? ORDER BY sequence',(report['id'],))]
    current=next((item for item in extracts if item['id']==report['current_extraction_id']),None)
    integrity=original_status(db,report)
    result.update(extractions=extracts,current_extraction=current,original_status=integrity,
        can_confirm=bool(current and current['candidate'] and report['status']!='confirmed' and integrity=='available'
                         and not any(i['severity']=='blocking' for i in candidate_issues(current['candidate'],season))))
    return result


def reports_for(db,season):
    return [report_view(db,dict(row),season) for row in db.execute('SELECT * FROM reports WHERE season_id=? ORDER BY rowid DESC',(season['id'],))]


def upload_report(store,season_id,actor,filename,mime,blob):
    require_sr(actor)
    if not filename or len(filename)>160 or any(ord(c)<32 or c in '/\\' for c in filename):
        raise HTTPException(422,'文件名无效，不能包含路径或控制字符')
    extension=Path(filename).suffix.casefold()
    types={'.txt':'text/plain','.md':'text/plain','.pdf':'application/pdf','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg'}
    if extension not in types or mime.split(';')[0].strip()!=types[extension]:
        raise HTTPException(415,'仅支持UTF-8文本、PDF、PNG或JPEG，扩展名与类型须一致')
    if not blob or len(blob)>MAX_FILE_BYTES:
        raise HTTPException(413,'原件不能为空且最多8MiB')
    signatures={'.pdf':b'%PDF-','.png':b'\x89PNG\r\n\x1a\n','.jpg':b'\xff\xd8\xff','.jpeg':b'\xff\xd8\xff'}
    if extension in signatures and not blob.startswith(signatures[extension]):
        raise HTTPException(415,'文件签名与声明类型不符')
    if extension in ('.txt','.md'):
        try:
            text=blob.decode('utf-8-sig')
            if '\x00' in text: raise ValueError('二进制文本')
        except (UnicodeDecodeError,ValueError):
            raise HTTPException(415,'文本原件需为UTF-8且不能含空字节') from None
    rid=uid()
    with store.read() as db:
        season_for(db,season_id,actor)
        path=file_path(db,rid)
    path.parent.mkdir(parents=True,exist_ok=True)
    created=False
    try:
        with path.open('xb') as target:
            created=True
            target.write(blob)
        with store.write() as db:
            season=season_for(db,season_id,actor)
            db.execute('''INSERT INTO reports(id,season_id,filename,mime_type,sha256,size_bytes,revision,status,uploaded_by,uploaded_at)
                VALUES(?,?,?,?,?,?,1,'uploaded',?,?)''',(rid,season_id,filename,types[extension],hashlib.sha256(blob).hexdigest(),len(blob),actor['id'],timestamp()))
            audit(db,season_id,None,actor['id'],'report_uploaded',{'report_id':rid,'sha256':hashlib.sha256(blob).hexdigest(),'filename':filename})
            report=dict(db.execute('SELECT * FROM reports WHERE id=?',(rid,)).fetchone())
            return report_view(db,report,season)
    except BaseException:
        if created:
            path.unlink(missing_ok=True)
        raise


def save_extraction(db,report,season,actor,method,pages,candidate,issues):
    xid=uid()
    sequence=db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM report_extractions WHERE report_id=?',(report['id'],)).fetchone()[0]
    issues=issues+candidate_issues(candidate,season)
    db.execute('INSERT INTO report_extractions VALUES(?,?,?,?,?,?,?,?,?)',(
        xid,report['id'],sequence,method,dumps(pages),dumps(candidate) if candidate else None,dumps(issues),actor['id'],timestamp()))
    status='manual_required' if any(i['severity']=='blocking' for i in issues) else 'extracted'
    db.execute('UPDATE reports SET revision=revision+1,status=?,current_extraction_id=? WHERE id=?',(status,xid,report['id']))
    audit(db,season['id'],None,actor['id'],'report_candidate_saved',{'report_id':report['id'],'extraction_id':xid,'method':method})
    return report_view(db,dict(db.execute('SELECT * FROM reports WHERE id=?',(report['id'],)).fetchone()),season)


def extract_report(store,report_id,actor,request):
    require_sr(actor)
    with store.read() as db:
        report,season=report_for(db,report_id,actor)
        expect(report['revision'],request.expected_revision)
        if report['status']=='confirmed': raise HTTPException(409,'已确认报告不可重新提取，请新上传修订原件')
        blob=verified_bytes(db,report)
        path=file_path(db,report_id)
    pages=[]; issues=[]; method='text_pattern'
    if report['mime_type']=='text/plain':
        text=blob.decode('utf-8-sig')
        if len(text)>100000:
            issues.append({'code':'TEXT_LIMIT','message':'文本超过100000字，请核对原件并人工转录。','severity':'blocking'})
        else: pages=[{'page':1,'text':text}]
    elif report['mime_type']=='application/pdf':
        method='pdf_text_pattern'
        try:
            process=subprocess.run([sys.executable,'-X','utf8','-m','app.report_extract',str(path)],
                cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,encoding='utf-8',timeout=15,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            result=json.loads(process.stdout)
            if process.returncode or result['error']: raise ValueError('PDF提取失败')
            pages=result['pages']
        except (subprocess.TimeoutExpired,ValueError,KeyError,OSError):
            issues.append({'code':'PDF_EXTRACTION_FAILED','message':'PDF解析失败、加密、超时或超出本地限额；原件已保留，请人工转录。','severity':'blocking'})
    else:
        issues.append({'code':'OCR_NOT_CONFIGURED','message':'当前未接入图像OCR；原件已保留，请对照原件人工转录。','severity':'blocking'})
    candidate=parse_pages(pages)
    with store.write() as db:
        current,season=report_for(db,report_id,actor)
        expect(current['revision'],report['revision'])
        verified_bytes(db,current)
        return save_extraction(db,current,season,actor,method,pages,candidate,issues)


def manual_candidate(store,report_id,actor,request):
    require_sr(actor)
    with store.write() as db:
        report,season=report_for(db,report_id,actor)
        expect(report['revision'],request.expected_revision)
        if report['status']=='confirmed': raise HTTPException(409,'已确认报告不可改写，请新上传修订原件')
        verified_bytes(db,report)
        previous=db.execute('SELECT * FROM report_extractions WHERE id=?',(report['current_extraction_id'],)).fetchone()
        pages=json.loads(previous['pages']) if previous else []
        if (report['mime_type'] in ('text/plain','image/png','image/jpeg') and request.page!=1) or (pages and request.page>len(pages)):
            raise HTTPException(422,'页码超出已知原件页数')
        candidate=make_candidate(request.raw_value,request.original_unit,request.plot_name,request.sampled_on,
            request.page,request.locator,request.excerpt,manual=True)
        return save_extraction(db,report,season,actor,'manual_transcription',pages,candidate,[])


def confirm_report(store,report_id,actor,request):
    require_sr(actor)
    with store.write() as db:
        report,season=report_for(db,report_id,actor)
        verified_bytes(db,report)
        if report['status']=='confirmed':
            if (report['current_extraction_id']!=request.extraction_id or report['confirmation_source']!=request.source
                    or season['revision']!=report['confirmed_input_revision']):
                raise HTTPException(409,'该报告已确认或当前事实已变化，请查看原确认记录')
            return report_view(db,report,season)|{'input_revision':report['confirmed_input_revision'],'duplicate':True}
        expect(report['revision'],request.expected_revision)
        expect(season['revision'],request.expected_input_revision)
        if report['current_extraction_id']!=request.extraction_id:
            raise HTTPException(409,'候选版本已变化，请查看当前候选')
        row=db.execute('SELECT * FROM report_extractions WHERE id=? AND report_id=?',(request.extraction_id,report_id)).fetchone()
        extraction=extraction_view(row) if row else None
        candidate=extraction['candidate'] if extraction else None
        issues=candidate_issues(candidate,season)
        if not extraction or any(i['severity']=='blocking' for i in extraction['issues']+issues):
            raise HTTPException(409,'候选有缺项、单位/地块/日期错误或提取失败，请先人工核对修正')
        facts=Facts.model_validate(season['facts']|{'soil_ph':candidate['value']}).model_dump(mode='json')
        revision=season['revision']+1
        db.execute('UPDATE seasons SET facts=?,revision=? WHERE id=?',(dumps(facts),revision,season['id']))
        source=f"SR确认报告 {report_id} / 提取 {request.extraction_id} / {candidate['locator']}；{request.source}"
        db.execute('INSERT INTO fact_versions VALUES(?,?,?,?,?,?)',(season['id'],revision,dumps(facts),actor['id'],source,timestamp()))
        inherit_sources(db,season['id'],season['revision'],revision,season['facts'],facts)
        db.execute('INSERT OR REPLACE INTO fact_report_sources VALUES(?,?,?,?,?)',(season['id'],revision,'soil_ph',report_id,request.extraction_id))
        db.execute("UPDATE reports SET status='confirmed',revision=revision+1,confirmed_input_revision=?,confirmation_source=?,confirmed_by=?,confirmed_at=? WHERE id=?",(revision,request.source,actor['id'],timestamp(),report_id))
        audit(db,season['id'],None,actor['id'],'report_fact_confirmed',{'report_id':report_id,'extraction_id':request.extraction_id,
            'input_revision':revision,'field':'soil_ph','value':candidate['value'],'source':request.source,'sha256':report['sha256']})
        return report_view(db,dict(db.execute('SELECT * FROM reports WHERE id=?',(report_id,)).fetchone()),season)|{'input_revision':revision,'duplicate':False}


def inherit_sources(db,season_id,from_revision,to_revision,before,after):
    for row in db.execute('SELECT * FROM fact_report_sources WHERE season_id=? AND revision=?',(season_id,from_revision)):
        field=row['field']
        equal=before.get(field)==after.get(field)
        if field=='soil_ph' and before.get(field) is not None and after.get(field) is not None:
            equal=Decimal(str(before[field]))==Decimal(str(after[field]))
        if equal:
            db.execute('INSERT INTO fact_report_sources VALUES(?,?,?,?,?)',(season_id,to_revision,field,row['report_id'],row['extraction_id']))


def confirmed_sources(db,season_id,revision):
    result=[]
    rows=db.execute('''SELECT s.*,r.filename,r.sha256,e.candidate,e.method FROM fact_report_sources s
        JOIN reports r ON r.id=s.report_id JOIN report_extractions e ON e.id=s.extraction_id
        WHERE s.season_id=? AND s.revision=?''',(season_id,revision))
    for row in rows:
        candidate=json.loads(row['candidate'])
        result.append({'report_id':row['report_id'],'extraction_id':row['extraction_id'],'field':row['field'],
            'input_revision':revision,'filename':row['filename'],'sha256':row['sha256'],'method':row['method'],
            **{key:candidate[key] for key in ('value','original_unit','normalized_unit','sampled_on','page','locator','excerpt')}})
    return result


def check_sources(db,snapshot):
    result=[]
    expected=confirmed_sources(db,snapshot['id'],snapshot['revision'])
    if snapshot.get('report_refs',[])!=expected:
        result.append({'report_id':'binding','error':'报告来源列表与该事实版本不一致','sha256':None})
    for reference in snapshot.get('report_refs',[]):
        report=db.execute('SELECT * FROM reports WHERE id=?',(reference.get('report_id'),)).fetchone()
        error=None
        try:
            if (reference not in expected or report is None or report['season_id']!=snapshot['id']
                    or report['status']!='confirmed' or report['sha256']!=reference.get('sha256')
                    or Decimal(str(snapshot['facts'].get('soil_ph')))!=Decimal(reference['value'])):
                error='报告来源与当前快照不一致'
            elif original_status(db,report)!='available':
                error='原件缺失或哈希不符'
        except (InvalidOperation,KeyError,TypeError,OSError):
            error='报告来源无法验证'
        result.append({'report_id':reference.get('report_id'),'error':error,'sha256':reference.get('sha256')})
    return result
