"""/api/v1 content management (W3): Excel round-trip + autoadapt intake for a bank.

- GET  /benchmarks/{name}/cases/template?blank=1   download the Excel template (blank or bank)
- POST /benchmarks/{name}/cases/bulk               upload a filled xlsx: valid rows upserted;
       problem rows return a red-marked file download + the error table
- POST /benchmarks/{name}/autoadapt                external jsonl/csv -> bucketed into the bank
- GET  /tmp/{fname}                                red-marked/临时 file download (whitelisted names)

Marked files land in data/tmp/ and are swept by the worker after a day.
"""
import datetime
import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ...case.store import load_cases_db, upsert_cases
from ...case.xlsx_io import export_cases, export_template, import_xlsx, mark_errors
from .. import auth, banks, db
from .benchmark_routes import _get_visible_bank, _require_manage, _owner_name

router = APIRouter(prefix="/api/v1", tags=["content"])

_TMP_OK = re.compile(r"^bulk-[0-9a-z-]+\.(xlsx|jsonl|csv)$")


def _tmp_dir() -> Path:
    p = db.data_root() / "tmp"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _save_upload(file: UploadFile, suffix: str) -> Path:
    ts = time.strftime("%Y%m%d%H%M%S")
    path = _tmp_dir() / ("bulk-%s-%s%s" % (ts, re.sub(r"[^a-z0-9]", "", db.new_run_id()[-6:]), suffix))
    path.write_bytes(file.file.read())
    return path


def _bank_db_path_or_404(name: str, user: dict) -> tuple:
    bank = _get_visible_bank(name, user)
    _require_manage(bank, user)
    return bank, banks.bank_db_path(bank, _owner_name(bank))


@router.get("/benchmarks/{name}/cases/template")
def case_template(name: str, blank: bool = False,
                  user: dict = Depends(auth.require_min("member"))):
    """Excel template: blank with dropdowns, or prefilled from the bank for batch editing."""
    bank = _get_visible_bank(name, user)
    out = _tmp_dir() / ("bulk-%s-tpl.xlsx" % db.new_run_id()[-6:])
    if blank:
        export_template(str(out))
        fname = "case-template.xlsx"
    else:
        path = banks.bank_db_path(bank, _owner_name(bank))
        export_cases(load_cases_db(str(path), status=None), str(out))
        fname = "%s-cases.xlsx" % name
    return FileResponse(out, filename=fname)


@router.post("/benchmarks/{name}/cases/bulk")
async def bulk_cases(name: str, file: UploadFile,
                     user: dict = Depends(auth.current_user)):
    """Excel batch import: auto-complete what can be completed; problem rows are written back
    as a red-marked xlsx (download link in the response) until a clean pass imports them."""
    bank, path = _bank_db_path_or_404(name, user)
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "upload an .xlsx file (export the template first)")
    src = _save_upload(file, ".xlsx")
    today = datetime.date.today().isoformat()
    valid, problems, fixes = import_xlsx(str(src), suite_fallback=bank["name"],
                                         import_date=today)
    if problems:
        marked = mark_errors(str(src), problems, fixes,
                             out_path=str(_tmp_dir() / (src.stem + "-marked.xlsx")))
        return {"status": "problems", "imported": 0, "problems": problems,
                "fixes": fixes, "marked_download": "/api/v1/tmp/" + Path(marked).name}
    upsert_cases(str(path), valid)
    return {"status": "ok", "imported": len(valid), "problems": [], "fixes": fixes}


@router.post("/benchmarks/{name}/autoadapt")
async def autoadapt(name: str, file: UploadFile = None, source_url: str = "",
                    id_field: str = "id", question_field: str = "question",
                    answer_field: str = "answer", doc_field: str = "",
                    profile: str = "", level: str = "L1",
                    llm_hint: bool = False,
                    user: dict = Depends(auth.current_user)):
    """External dataset (jsonl/csv: uploaded file OR a URL the server fetches) -> deterministic
    bucketing -> cases upserted into this bank + a human-review checklist written beside it."""
    bank, path = _bank_db_path_or_404(name, user)
    if file and (file.filename or "").strip():
        suffix = ".csv" if (file.filename or "").lower().endswith(".csv") else ".jsonl"
        src = _save_upload(file, suffix)
    elif source_url.strip():
        url = source_url.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            raise HTTPException(400, "source_url must be http(s)")
        import httpx
        suffix = ".csv" if url.lower().endswith(".csv") else ".jsonl"
        src = _tmp_dir() / ("bulk-%s-ds%s" % (db.new_run_id()[-6:], suffix))
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                resp = await client.get(url)
            resp.raise_for_status()
            src.write_bytes(resp.content)
        except Exception as e:
            raise HTTPException(400, "fetch source_url failed: %r" % e)
    else:
        raise HTTPException(400, "provide a dataset file or source_url")
    from ...control.autoadapt import auto_adapt
    summary = auto_adapt(source=str(src), id_field=id_field,
                         question_field=question_field, answer_field=answer_field,
                         suite=bank["name"], out=str(Path(path).parent / "cases.jsonl"),
                         review=str(Path(path).parent / "adapt-review.md"),
                         doc_field=doc_field, profile=profile, level=level,
                         llm_hint=llm_hint, db=str(path))
    return summary


@router.get("/tmp/{fname}")
def tmp_download(fname: str, user: dict = Depends(auth.require_min("member"))):
    """Download a generated working file (red-marked xlsx). Names are whitelisted."""
    if "/" in fname or "\\" in fname or ".." in fname or not _TMP_OK.match(fname):
        raise HTTPException(400, "invalid file name")
    p = _tmp_dir() / fname
    if not p.is_file():
        raise HTTPException(404, "file not found (maybe swept): %s" % fname)
    return FileResponse(p, filename=fname)


_GUIDES = ("bank-edit", "bank-create", "run-eval")


@router.get("/guides/{guide}")
def get_guide(guide: str, lang: str = "zh",
              user: dict = Depends(auth.current_user)):
    """Serve the how-to guides (case editing / benchmark creation / run launch) as markdown
    text, so the UI can link them without exposing the whole docs tree."""
    if guide not in _GUIDES:
        raise HTTPException(404, "unknown guide: %s" % guide)
    p = _guide_path(guide, lang)
    if not p:
        raise HTTPException(404, "guide file not shipped with this install: %s" % guide)
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(p.read_text(encoding="utf-8"), media_type="text/markdown")


@router.get("/guides/{guide}/view")
def view_guide(guide: str, lang: str = "zh",
               user: dict = Depends(auth.current_user)):
    """Rendered HTML page for opening a guide in a new browser tab (cookie auth applies)."""
    if guide not in _GUIDES:
        raise HTTPException(404, "unknown guide: %s" % guide)
    p = _guide_path(guide, lang)
    if not p:
        raise HTTPException(404, "guide file not shipped with this install: %s" % guide)
    import markdown as _md  # optional; fall back to a <pre> when unavailable
    body = None
    try:
        body = _md.markdown(p.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"])
    except Exception:
        body = ""
    if not body:
        import html as _html
        body = "<pre>" + _html.escape(p.read_text(encoding="utf-8")) + "</pre>"
    html = ("<!doctype html><html lang=\"%s\"><head><meta charset=\"utf-8\">"
            "<title>%s · AgentGate</title><style>"
            "body{font-family:'Segoe UI','PingFang SC',sans-serif;max-width:880px;"
            "margin:24px auto;padding:0 16px;line-height:1.7;color:#1f2933}"
            "h1{border-bottom:2px solid #e2e8f0;padding-bottom:8px}"
            "h2{color:#16324f;margin-top:28px}"
            "code{background:#f1f5f9;border-radius:4px;padding:1px 5px;font-size:.9em}"
            "pre code{display:block;padding:12px;overflow-x:auto}"
            "table{border-collapse:collapse;width:100%%}"
            "th,td{border:1px solid #e2e8f0;padding:6px 10px;font-size:.95em}"
            "th{background:#f8fafc}"
            "blockquote{border-left:3px solid #cbd5e0;margin:8px 0;padding:2px 12px;color:#486581}"
            "</style></head><body>%s</body></html>" % (lang, guide, body))
    from fastapi.responses import HTMLResponse
    return HTMLResponse(html)


def _docs_image_path(fname: str):
    """Screenshots referenced by the docs (../images/*.png resolve here when a guide is
    rendered under /guides/<guide>/view)."""
    import re as _re
    if not _re.fullmatch(r"[A-Za-z0-9._-]+\.(?:png|jpg|jpeg|gif|webp)", fname or ""):
        return None
    bases = [Path(__file__).resolve().parents[4] / "docs" / "images",     # repo (src/) layout
             Path("/app/agentgate/docs/images")]                          # platform image bundle
    return next((b / fname for b in bases if (b / fname).is_file()), None)


def _guide_path(guide: str, lang: str):
    sub = "en" if lang == "en" else "zh"
    fname = guide + ".md"
    bases = [Path(__file__).resolve().parents[4] / "docs" / sub,     # repo (src/) layout
             Path("/app/agentgate/docs") / sub]                       # platform image bundle
    return next((b / fname for b in bases if (b / fname).is_file()), None)

@router.get("/guides/images/{fname}")
def guide_image(fname: str, user: dict = Depends(auth.current_user)):
    """Static doc screenshots (cookie/bearer auth applies; extension-whitelisted)."""
    p = _docs_image_path(fname)
    if not p:
        raise HTTPException(404, "image not found: %s" % fname)
    return FileResponse(p)
