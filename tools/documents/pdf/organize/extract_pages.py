"""extract_pdf_pages: copy chosen pages of a PDF into a new PDF."""
from langchain_core.tools import tool

from tools.common import guarded
from tools.documents.pdf import _common as c

TOOL = "extract_pdf_pages"


@tool
@guarded(TOOL)
def extract_pdf_pages(source: str, pages: str, destination: str = "", overwrite: bool = False) -> str:
    """Copy chosen pages of a PDF into ONE new PDF; the original is never changed. `source` is an absolute PDF path inside an approved read folder. `pages` are the pages to KEEP in the new file, 1-based, in the order you want them, like '2', '1-3,7' or '9-' (to the end). `destination` is optional: a folder or full .pdf file name inside an allowed write folder (default: next to the source as '<name>_extract.pdf'). A new file is created immediately; replacing an existing file needs overwrite=true and the user's approval."""
    def build(paths):
        doc = c.open_pdf(paths[0])
        total = doc.page_count
        doc.select(c.parse_pages(pages, total))
        return doc, f"{doc.page_count} of {total} pages"

    return c.make_pdf(TOOL, c.one(source), destination, overwrite, "_extract", build, "Extracted pages from")
