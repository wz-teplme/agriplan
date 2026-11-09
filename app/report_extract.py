"""Bounded PDF text extraction in a disposable child process; never OCR."""
import json
import sys


def pdf_pages(path):
    from pypdf import PdfReader
    import pypdf.filters
    # Bound each decoded stream as well as the parent process timeout/page count.
    pypdf.filters.ZLIB_MAX_OUTPUT_LENGTH=2_000_000
    reader=PdfReader(path,strict=True)
    if reader.is_encrypted:
        raise ValueError('加密PDF需转人工核对')
    if len(reader.pages)>30:
        raise ValueError('PDF超过30页')
    pages=[]
    total=0
    for index,page in enumerate(reader.pages):
        text=page.extract_text() or ''
        total+=len(text)
        if total>100000:
            raise ValueError('提取文本超过100000字')
        pages.append({'page':index+1,'text':text})
    return pages


if __name__=='__main__':
    try:
        result={'pages':pdf_pages(sys.argv[1]),'error':None}
    except Exception:
        result={'pages':[],'error':'PDF解析失败、加密或超出本地限额，请核对原件并人工转录'}
    print(json.dumps(result,ensure_ascii=False))
