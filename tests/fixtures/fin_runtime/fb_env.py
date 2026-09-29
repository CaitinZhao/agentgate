"""FinanceBench 真实文档环境：368 份美股财报 PDF 的检索工具与题源适配。"""
import json
import os
import re
from pathlib import Path

# Paths are env-overridable so containers can mount the corpus elsewhere (deploy_eval sets
# FB_DATA_DIR/FB_PDF_DIR for the jiuwen agent image).
def _repo_root() -> Path:
    """Workspace root located from this file's parents (the fixture package may sit at any
    depth: agentgate/tests/fixtures/fin_runtime); AGENTGATE_REPO_ROOT overrides."""
    env = os.environ.get("AGENTGATE_REPO_ROOT", "")
    if env and Path(env).is_dir():
        return Path(env)
    for cand in Path(__file__).resolve().parents:
        if (cand / "reference" / "refs" / "code" / "financebench").is_dir():
            return cand
    parents = Path(__file__).resolve().parents
    return parents[min(4, len(parents) - 1)]


# `or` short-circuits: _repo_root() only runs when the env var is unset (it needs the
# workspace layout; the container sets FB_DATA_DIR and has no workspace around it).
FB_DATA_DIR = Path(os.environ.get("FB_DATA_DIR")
                   or _repo_root() / "reference" / "refs" / "code" / "financebench" / "data")
FB_PDF_DIR = Path(os.environ.get("FB_PDF_DIR", FB_DATA_DIR.parent / "pdfs"))
FB_CACHE_DIR = Path(os.environ.get(
    "FB_CACHE_DIR", Path(__file__).resolve().parents[1] / "data" / "fb_cache"))

_docs: dict = {}
_page_cache: dict = {}


def _doc_info() -> dict:
    global _docs
    if not _docs:
        f = FB_DATA_DIR / "financebench_document_information.jsonl"
        if f.exists():
            for line in f.open(encoding="utf-8"):
                if line.strip():
                    d = json.loads(line)
                    _docs[d["doc_name"]] = d
    return _docs


def _doc_text_pages(doc_name: str) -> list:
    """按页提取 PDF 文本（带缓存），返回 [(page_no, text)]。"""
    if doc_name in _page_cache:
        return _page_cache[doc_name]
    cache_file = FB_CACHE_DIR / (doc_name + ".json")
    if cache_file.exists():
        pages = json.loads(cache_file.read_text(encoding="utf-8"))
        _page_cache[doc_name] = pages
        return pages
    pdf = FB_PDF_DIR / (doc_name + ".pdf")
    if not pdf.exists():
        _page_cache[doc_name] = []
        return []
    from pypdf import PdfReader
    reader = PdfReader(str(pdf))
    pages = []
    for i, page in enumerate(reader.pages, 1):
        try:
            text = (page.extract_text() or "")[:6000]
        except Exception:
            text = ""
        if text.strip():
            pages.append({"page": i, "text": text})
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    _page_cache[doc_name] = pages
    return pages


def list_filings(company=None):
    """列出文档库中的财报（可按公司过滤）。"""
    docs = _doc_info()
    out = []
    for name, d in sorted(docs.items()):
        if company and company.lower() not in d.get("company", "").lower():
            continue
        out.append({"doc_name": name, "company": d.get("company"),
                    "doc_type": d.get("doc_type"), "period": d.get("doc_period")})
    return out


def search_filing(doc_name, keyword, max_results=4):
    """在指定财报里按关键词检索，返回命中页摘要（页码=证据锚点）。"""
    pages = _doc_text_pages(doc_name)
    if not pages:
        return {"doc_name": doc_name, "error": "doc not found or no text",
                "hint": "先用 list_filings 确认文档名"}
    kws = [k.strip().lower() for k in re.split(r"[,;，；]", keyword) if k.strip()]
    scored = []
    for p in pages:
        low = p["text"].lower()
        score = sum(low.count(k) for k in kws)
        if score > 0:
            idx = min((low.find(k) for k in kws if k in low), default=0)
            excerpt = p["text"][max(0, idx - 300): idx + 900]
            scored.append({"page": p["page"], "score": score, "excerpt": excerpt})
    scored.sort(key=lambda x: -x["score"])
    return {"doc_name": doc_name, "hits": len(scored),
            "results": scored[:max_results],
            "note": "引用时注明 doc_name 与页码"}
