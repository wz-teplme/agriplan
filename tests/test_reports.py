"""Original reports, unconfirmed measurements and confirmed fact lineage."""
import hashlib
import io
import json
import unittest
from pathlib import Path

import test_mvp as baseline


class ReportTests(unittest.TestCase):
    setUp=baseline.WorkflowTests.setUp
    tearDown=baseline.WorkflowTests.tearDown
    login=baseline.WorkflowTests.login
    start=baseline.WorkflowTests.start
    drain=baseline.WorkflowTests.drain
    detail=baseline.WorkflowTests.detail

    def text(self,value='6.5 pH',plot='东河一号田',sampled='2026-10-10'):
        return f'合成测土报告\n地块: {plot}\n采样日期: {sampled}\npH: {value}\n仅用于系统验收'.encode('utf-8')

    def upload(self,content=None,filename='soil.txt',mime='text/plain',season='s1'):
        response=self.client.post('/api/seasons/'+season+'/reports',params={'filename':filename},
            content=content if content is not None else self.text(),headers={'Content-Type':mime})
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def extract(self,report):
        response=self.client.post('/api/reports/'+report['id']+'/extract',json={'expected_revision':report['revision']})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def confirm(self,report,revision=1,**values):
        return self.client.post('/api/reports/'+report['id']+'/confirm',json={
            'expected_revision':report['revision'],'expected_input_revision':revision,
            'extraction_id':report['current_extraction']['id'],'source':'SR核对原件、地块、日期、单位和提取值',**values})

    def test_original_and_candidate_stay_separate_until_one_confirmed_revision(self):
        original=self.text()
        report=self.upload(original)
        self.assertEqual(report['sha256'],hashlib.sha256(original).hexdigest())
        raw=self.client.get('/api/reports/'+report['id']+'/original')
        self.assertEqual(raw.content,original)
        self.assertIn('attachment',raw.headers['content-disposition'])
        extracted=self.extract(report)
        self.assertEqual(extracted['current_extraction']['candidate']['value'],'6.5')
        self.assertEqual(extracted['current_extraction']['candidate']['page'],1)
        season=self.client.get('/api/seasons/s1').json()
        self.assertEqual(season['revision'],1)
        self.assertIsNone(season['facts']['soil_ph'])
        confirmed=self.confirm(extracted)
        self.assertEqual(confirmed.status_code,200,confirmed.text)
        self.assertEqual(confirmed.json()['input_revision'],2)
        self.assertTrue(self.confirm(extracted).json()['duplicate'])
        season=self.client.get('/api/seasons/s1').json()
        self.assertEqual(season['facts']['soil_ph'],'6.5')
        self.assertEqual(season['revision'],2)
        self.assertEqual(season['report_sources'][0]['report_id'],report['id'])
        self.assertEqual(self.client.get('/api/reports/'+report['id']+'/original').content,original)

    def test_units_plot_date_and_ambiguous_values_block_confirmation(self):
        cases=[self.text(value='6.5 mg/kg'),self.text(plot='另一个地块'),self.text(sampled=''),
               self.text(sampled='2099-01-01'),self.text()+b'\npH: 7.1 pH',
               self.text()+'\n土壤pH值: 8.2 pH'.encode(),self.text()+b'\npH (KCl): 5.2 pH']
        for data in cases:
            with self.subTest(data=data):
                report=self.extract(self.upload(data))
                self.assertFalse(report['can_confirm'])
                self.assertTrue(any(i['severity']=='blocking' for i in report['current_extraction']['issues']))
                if report['current_extraction']['candidate']:
                    self.assertEqual(self.confirm(report).status_code,409)
        self.assertEqual(self.client.get('/api/seasons/s1').json()['revision'],1)

    def test_manual_transcription_preserves_failed_extraction_and_identity(self):
        report=self.extract(self.upload(self.text()+b'\npH: 8.2 pH'))
        response=self.client.post('/api/reports/'+report['id']+'/candidates',json={
            'expected_revision':report['revision'],'raw_value':'6.5','original_unit':'pH','plot_name':'东河一号田',
            'sampled_on':'2026-10-10','page':1,'locator':'检测表第一行','excerpt':'pH: 6.5 pH'})
        self.assertEqual(response.status_code,200,response.text)
        manual=response.json()
        self.assertEqual(len(manual['extractions']),2)
        self.assertEqual(manual['current_extraction']['method'],'manual_transcription')
        self.assertEqual(manual['current_extraction']['candidate']['identity'],'manual_candidate')
        self.assertEqual(self.confirm(manual).status_code,200)

    def test_scope_roles_and_filename_signature_boundaries(self):
        report=self.upload()
        self.login('fr_zhou')
        self.assertEqual(self.client.get('/api/reports/'+report['id']+'/original').status_code,200)
        self.assertEqual(self.client.post('/api/seasons/s1/reports?filename=f.txt',content=self.text(),headers={'Content-Type':'text/plain'}).status_code,403)
        self.assertEqual(self.client.post('/api/reports/'+report['id']+'/extract',json={'expected_revision':1}).status_code,403)
        self.login('sr_liu')
        self.assertEqual(self.client.get('/api/reports/'+report['id']+'/original').status_code,404)
        self.login('sr_chen')
        for filename,mime,content in [('bad.html','text/html',b'<script>bad</script>'),('../bad.txt','text/plain',b'text'),('bad.pdf','application/pdf',b'not pdf')]:
            response=self.client.post('/api/seasons/s1/reports',params={'filename':filename},content=content,headers={'Content-Type':mime})
            self.assertIn(response.status_code,(415,422),response.text)
        oversized=self.client.post('/api/seasons/s1/reports?filename=big.txt',content=b'a'*(8*1024*1024+1),headers={'Content-Type':'text/plain'})
        self.assertEqual(oversized.status_code,413)

    def test_stale_report_or_fact_revision_cannot_confirm_and_hash_change_blocks(self):
        report=self.extract(self.upload())
        self.assertEqual(self.confirm(report,expected_revision=1).status_code,409)
        self.assertEqual(self.confirm(report,revision=2).status_code,409)
        source=Path(self.app.state.store.path).parent/'source-files'/(report['id']+'.original')
        source.write_bytes(b'tampered')
        self.assertEqual(self.confirm(report).status_code,409)
        self.assertEqual(self.client.get('/api/reports/'+report['id']+'/original').status_code,409)
        self.assertEqual(self.client.get('/api/seasons/s1').json()['revision'],1)

    def test_confirmed_lineage_survives_unchanged_ph_and_clears_after_manual_change(self):
        report=self.extract(self.upload())
        self.assertEqual(self.confirm(report).status_code,200)
        season=self.client.get('/api/seasons/s1').json()
        changed=self.client.put('/api/seasons/s1',json={'expected_revision':2,'facts':season['facts']|{'area_mu':'301'},'source':'SR更新面积'})
        self.assertEqual(changed.status_code,200,changed.text)
        season=self.client.get('/api/seasons/s1').json()
        self.assertEqual(season['report_sources'][0]['report_id'],report['id'])
        started=self.client.post('/api/research-runs',headers={'Idempotency-Key':'report-r3'},
            json={'season_id':'s1','goal':'依据当前确认资料核对本季方案','expected_revision':3})
        self.assertEqual(started.status_code,202,started.text)
        self.drain()
        with self.app.state.store.read() as db:
            run_id=db.execute('SELECT id FROM runs').fetchone()[0]
        run=self.detail(run_id)
        self.assertEqual(run['snapshot']['report_refs'][0]['report_id'],report['id'])
        source=Path(self.app.state.store.path).parent/'source-files'/(report['id']+'.original')
        source.write_bytes(b'broken after confirmation')
        review=self.detail(run_id)['review']
        self.assertFalse(review['can_confirm'])
        self.assertIn('REPORT_SOURCE_UNAVAILABLE',[i['code'] for i in review['issues']])
        response=self.client.put('/api/seasons/s1',json={'expected_revision':3,'facts':season['facts']|{'soil_ph':'7.0'},'source':'SR另行测量'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.get('/api/seasons/s1').json()['report_sources'],[])

    def test_late_report_does_not_rewrite_formal_plan(self):
        rid=self.start()['id']; self.drain(); run=self.detail(rid)
        saved=self.client.post('/api/runs/'+rid+'/confirm',json={'expected_revision':run['revision']})
        self.assertEqual(saved.status_code,200,saved.text)
        original=saved.json()['content']
        report=self.extract(self.upload())
        self.assertEqual(self.confirm(report).status_code,200)
        old=self.detail(rid)
        self.assertTrue(old['stale'])
        self.assertEqual(old['formal']['content'],original)

    def pdf(self,blank=False):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
        writer=PdfWriter(); writer.add_blank_page(width=300,height=300)
        if not blank:
            page=writer.add_blank_page(width=300,height=300)
            font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
            page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
            stream=DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 20 260 Td (Plot: p1) Tj 0 -20 Td (Sampled: 2026-10-10) Tj 0 -20 Td (pH: 6.8 pH) Tj ET')
            page[NameObject('/Contents')]=writer._add_object(stream)
        output=io.BytesIO(); writer.write(output); return output.getvalue()

    def test_pdf_text_page_locator_and_scan_manual_fallback(self):
        report=self.extract(self.upload(self.pdf(),filename='test.pdf',mime='application/pdf'))
        candidate=report['current_extraction']['candidate']
        self.assertEqual(candidate['page'],2)
        self.assertEqual(candidate['value'],'6.8')
        self.assertTrue(report['can_confirm'])
        empty=self.extract(self.upload(self.pdf(blank=True),filename='scan.pdf',mime='application/pdf'))
        self.assertEqual(empty['status'],'manual_required')
        self.assertFalse(empty['can_confirm'])
        self.assertEqual(self.client.get('/api/reports/'+empty['id']+'/original').status_code,200)

    def test_equal_numeric_ph_keeps_report_lineage(self):
        report=self.extract(self.upload())
        self.assertEqual(self.confirm(report).status_code,200)
        season=self.client.get('/api/seasons/s1').json()
        response=self.client.put('/api/seasons/s1',json={'expected_revision':2,
            'facts':season['facts']|{'soil_ph':'6.50'},'source':'SR仅调整显示精度'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(self.client.get('/api/seasons/s1').json()['report_sources']),1)

    def test_unreadable_original_fails_closed_without_breaking_season_page(self):
        from unittest.mock import patch
        report=self.upload()
        with patch('app.reports.Path.read_bytes',side_effect=PermissionError('test read denied')):
            response=self.client.get('/api/seasons/s1')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['reports'][0]['original_status'],'missing')

    def test_streaming_upload_without_length_still_enforces_limit(self):
        def chunks():
            for _ in range(9): yield b'a'*(1024*1024)
        response=self.client.post('/api/seasons/s1/reports?filename=stream.txt',content=chunks(),
            headers={'Content-Type':'text/plain'})
        self.assertEqual(response.status_code,413,response.text)

    def test_partial_upload_write_failure_removes_only_its_new_file(self):
        from unittest.mock import patch
        from app.reports import upload_report
        real_open=Path.open
        class PartialWrite:
            def __init__(self,file): self.file=file
            def __enter__(self): return self
            def __exit__(self,*args): self.file.close()
            def write(self,data):
                self.file.write(data[:4]); self.file.flush()
                raise OSError('simulated disk full')
        def failing_open(path,mode='r',*args,**kwargs):
            file=real_open(path,mode,*args,**kwargs)
            return PartialWrite(file) if mode=='xb' else file
        with patch('app.reports.Path.open',failing_open):
            with self.assertRaises(OSError):
                upload_report(self.app.state.store,'s1',{'id':'u1','role':'SR'},'test.txt','text/plain',self.text())
        self.assertEqual(list((Path(self.app.state.store.path).parent/'source-files').glob('*.original')),[])
        with self.app.state.store.read() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM reports').fetchone()[0],0)
