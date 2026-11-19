"""Sanity checks of delivered files. They say that a file is what its name claims at the container level and not that it is
any good: whether a workbook is right is a person's judgement, and this tool makes none."""
import io
import re
import zipfile
from pathlib import Path

MAIN_PART = {".xlsx": "xl/workbook.xml", ".docx": "word/document.xml", ".pptx": "ppt/presentation.xml"}
# "/Type /Page" but not "/Type /Pages": the page tree's own node is not a page
PAGE = re.compile(rb"/Type\s*/Page(?![A-Za-z])")
# files whose page objects sit in compressed object streams show only the tree's count
COUNT = re.compile(rb"/Count\s+[1-9]")


def check(name: str, data: bytes) -> dict:
    """{"ok": True/False/None, "detail": text}; None means that this kind of file has no check."""
    ext = Path(name).suffix.lower()
    if ext in MAIN_PART:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                bad = z.testzip()
                if bad:
                    return {"ok": False, "detail": f"zip member {bad} is corrupt"}
                if MAIN_PART[ext] not in z.namelist():
                    return {"ok": False, "detail": f"zip without {MAIN_PART[ext]}"}
                if not z.read(MAIN_PART[ext]).strip():
                    return {"ok": False, "detail": f"{MAIN_PART[ext]} is empty"}
        except zipfile.BadZipFile as e:
            return {"ok": False, "detail": f"not a readable zip: {e}"}
        return {"ok": True, "detail": f"zip with {MAIN_PART[ext]}"}
    if ext == ".pdf":
        if not data.startswith(b"%PDF"):
            return {"ok": False, "detail": "does not start with %PDF"}
        if PAGE.search(data):
            return {"ok": True, "detail": "%PDF header and a page object"}
        if COUNT.search(data):
            return {"ok": True, "detail": "%PDF header and a page count (page objects are compressed)"}
        return {"ok": False, "detail": "no page marker"}
    if ext == ".md":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            return {"ok": False, "detail": f"not UTF-8: {e}"}
        if not text.strip():
            return {"ok": False, "detail": "empty"}
        return {"ok": True, "detail": f"{len(text)} characters of UTF-8"}
    return {"ok": None, "detail": f"no check for {ext or 'a file without an extension'}"}
