"""ocr_pdf: make a scanned PDF searchable (invisible text layer from Tesseract)."""
import re

from langchain_core.tools import tool

from security.settings import get_settings
from tools.common import guarded
from tools.documents.pdf import _common as c

TOOL = "ocr_pdf"


@tool
@guarded(TOOL)
def ocr_pdf(source: str, language: str = "", destination: str = "", overwrite: bool = False) -> str:
    """Run OCR (Tesseract) on a scanned PDF and save a NEW PDF whose text can be searched and copied; the original is never changed. Pages that already contain text are copied untouched. `source` is an absolute PDF path inside an approved read folder. `language` is optional, a Tesseract code such as 'eng' or 'eng+hin' (default: ocr.language in config.yaml). Needs Tesseract installed. OCR is slow: at most pdf_tools.max_ocr_pages pages (default 50). `destination` is optional: a folder or full .pdf file name inside an allowed write folder (default: next to the source as '<name>_ocr.pdf'). A new file is created immediately; replacing an existing file needs overwrite=true and the user's approval."""
    ocr = get_settings().get("ocr", {})
    lang = language.strip() or ocr.get("language", "eng")
    if not re.fullmatch(r"[A-Za-z_]+(\+[A-Za-z_]+)*", lang):
        raise c.ToolError("language must look like 'eng' or 'eng+hin'.")

    def build(paths):
        import pymupdf

        src = c.open_pdf(paths[0])
        if src.page_count > c.cfg("max_ocr_pages", 50):
            raise c.ToolError(f"{src.page_count} pages is over the pdf_tools.max_ocr_pages limit. Split the PDF first.")
        out, done = pymupdf.open(), 0
        for page in src:
            if page.get_text().strip():  # already has text: keep the original page
                out.insert_pdf(src, from_page=page.number, to_page=page.number)
                continue
            try:
                data = page.get_pixmap(dpi=ocr.get("dpi", 200)).pdfocr_tobytes(language=lang)
            except Exception as exc:  # noqa: BLE001  Tesseract or its language data is missing
                raise c.ToolError(f"OCR failed: {str(exc).splitlines()[0][:150]}. Install Tesseract and the '{lang}' language data.") from exc
            with pymupdf.open("pdf", data) as scanned:
                out.insert_pdf(scanned)
            done += 1
        src.close()
        return out, f"OCR on {done} of {out.page_count} page(s), language {lang}"

    return c.make_pdf(TOOL, c.one(source), destination, overwrite, "_ocr", build, "OCR'd")
