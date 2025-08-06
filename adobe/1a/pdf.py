import fitz
import json
from pathlib import Path
import re
import sys

def extract_title(doc):
    meta = doc.metadata
    if meta.get('title') and len(meta['title']) > 5:
        return meta['title']
    page = doc[0]
    blocks = page.get_text("dict")["blocks"]
    candidates = []
    for block in blocks:
        if "lines" in block:
            for line in block["lines"]:
                text = "".join(span["text"] for span in line["spans"]).strip()
                size = max(span["size"] for span in line["spans"])
                if len(text) > 10 and size > 12:
                    candidates.append((text, size, line["spans"][0]["bbox"][1]))
    if candidates:
        candidates.sort(key=lambda x: (-x[1], x[2], -len(x[0])))
        return candidates[0][0]
    return "Document Title"

def extract_outline(doc):
    import re
    outline = []
    seen = set()
    page_pattern = re.compile(r"^(page\s*\d+\s*of\s*\d+|\d+\s*of\s*\d+)$", re.IGNORECASE)
    for page_num in range(len(doc)):
        page = doc[page_num]
        blocks = page.get_text("dict")["blocks"]
        lines = []
        for block in blocks:
            if "lines" in block:
                for line in block["lines"]:
                    text = "".join(span["text"] for span in line["spans"]).strip()
                    # Remove non-alphanumeric (keep spaces for multi-word headings)
                    clean_text = re.sub(r"[^\w\s]", "", text)
                    clean_text = clean_text.strip()
                    if not clean_text or not any(c.isalnum() for c in clean_text):
                        continue
                    # Skip page marker headings like 'Page 11 of 12', '3 of 4', etc.
                    if page_pattern.match(clean_text.lower().replace("  ", " ")):
                        continue
                    sizes = [span["size"] for span in line["spans"]]
                    fonts = [span["font"] for span in line["spans"]]
                    is_bold = any("bold" in f.lower() for f in fonts)
                    lines.append({"text": clean_text, "size": max(sizes), "is_bold": is_bold})
        if not lines:
            continue
        max_size = max(l["size"] for l in lines)
        for l in lines:
            # Use lowercased text for deduplication
            key = (l["text"].lower(), l["size"], l["is_bold"])
            if key in seen:
                continue
            if l["size"] == max_size and l["is_bold"]:
                level = "H1"
            elif l["size"] >= max_size - 2 and l["is_bold"]:
                level = "H2"
            elif l["size"] >= max_size - 4:
                level = "H3"
            else:
                continue
            if len(l["text"]) < 80:
                outline.append({"level": level, "text": l["text"], "page": page_num + 1})
                seen.add(key)
    return outline

def process_pdf(pdf_path, out_path):
    doc = fitz.open(pdf_path)
    title = extract_title(doc)
    outline = extract_outline(doc)
    result = {"title": title, "outline": outline}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

def main():
    # Use current directory for processing
    input_dir = Path.cwd()
    output_dir = Path.cwd()
    
    print(f"Processing PDFs in: {input_dir}")
    print(f"Found PDF files: {list(input_dir.glob('*.pdf'))}")
    
    for pdf_file in input_dir.glob("*.pdf"):
        out_file = output_dir / (pdf_file.stem + ".json")
        print(f"Processing: {pdf_file} -> {out_file}")
        try:
            process_pdf(str(pdf_file), str(out_file))
            print(f"Successfully created: {out_file}")
        except Exception as e:
            print(f"Error processing {pdf_file}: {str(e)}", file=sys.stderr)

if __name__ == "__main__":
    main()