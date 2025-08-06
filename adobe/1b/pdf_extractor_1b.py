import fitz  # PyMuPDF
import os
import re
import json
from datetime import datetime

INPUT_JSON = "challenge1b_input.json"
OUTPUT_JSON = "challenge1b_output.json"
MAX_SECTIONS_PER_DOC = 5

STOP_WORDS = set([
    "the","is","in","on","at","to","for","a","an","and",
    "of","with","this","that","these","those","are","be",
    "as","by","from","or","it","its","can","will","has","have"
])
WORD_RE = re.compile(r"\b\w+\b")
APPENDIX_RE = re.compile(r'^Appendix [A-Z]:', re.IGNORECASE)

def tokenize(text):
    return [w for w in WORD_RE.findall(text.lower()) if w not in STOP_WORDS]

def extract_headers(doc):
    headers = []
    for pno in range(len(doc)):
        page = doc.load_page(pno)
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if "lines" not in block: continue
            for line in block["lines"]:
                spans = line["spans"]
                text = "".join(s.get("text","") for s in spans).strip()
                if not (5 <= len(text) <= 100): continue
                max_sz = max(s.get("size",0) for s in spans)
                bold = any((s.get("flags",0)&16)!=0 or 'bold' in s.get("font","").lower() for s in spans)

                if bold and max_sz>=12 or APPENDIX_RE.match(text):
                    headers.append((pno+1, re.sub(r"\s+"," ", text), max_sz))
    return headers

def score_header(text, keywords):
    return sum(1 for w in tokenize(text) if w in keywords)

def get_page_text(doc, page_num):
    txt = doc.load_page(page_num-1).get_text().strip()
    return txt if len(txt)<=1000 else txt[:1000] + '...'

def process_documents():
    spec = json.load(open(INPUT_JSON,'r',encoding='utf-8'))
    docs_info = spec.get('documents', [])
    persona = spec.get('persona',{}).get('role','')
    job = spec.get('job_to_be_done',{}).get('task','')
    keywords = set(tokenize(job))

    metadata = {
        "input_documents": [d['filename'] for d in docs_info],
        "persona": persona,
        "job_to_be_done": job,
        "processing_timestamp": datetime.now().isoformat()
    }
    extracted_secs = []
    sub_analysis = []

    for info in docs_info:
        fname = info['filename']
        if not os.path.exists(fname):
            print(f"Skipping missing: {fname}")
            continue
        doc = fitz.open(fname)
        headers = extract_headers(doc)

        scored = [(score_header(txt,keywords), pg, txt) for pg,txt,_ in headers]
        scored = [s for s in scored if s[0]>0]
        scored.sort(reverse=True, key=lambda x:x[0])

        seen = set(); rank=1
        for sc,pg,txt in scored:
            if rank>MAX_SECTIONS_PER_DOC or txt in seen: break
            seen.add(txt)
            extracted_secs.append({"document":fname,"section_title":txt,
                                   "importance_rank":rank,"page_number":pg})
            sub_analysis.append({"document":fname,"page_number":pg,
                                 "refined_text":get_page_text(doc,pg)})
            rank+=1
        doc.close()

    out = {"metadata":metadata,
           "extracted_sections":extracted_secs,
           "subsection_analysis":sub_analysis}
    json.dump(out, open(OUTPUT_JSON,'w',encoding='utf-8'), indent=2, ensure_ascii=False)
    print(f"Output: {OUTPUT_JSON}")

if __name__=='__main__':
    process_documents()
