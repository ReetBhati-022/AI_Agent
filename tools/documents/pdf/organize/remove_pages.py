"""remove_pdf_pages: save a copy of a PDF without the pages you pick."""
from langchain_core.tools import tool

from tools.common import guarded
from tools.documents.pdf import _common as c

TOOL = "remove_pdf_pages"


@tool
@guarded(TOOL)
def remove_pdf_pages(source: str, pages: str, destination: str = "", overwrite: bool = False) -> str:
    """Delete chosen pages from a PDF and save the rest as a NEW PDF; the original is never changed. `source` is an absolute PDF path inside an approved read folder. `pages` are the pages to REMOVE, 1-based, like '2', '1-3,7' or '9-' (to the end); you cannot remove every page. `destination` is optional: a folder or full .pdf file name inside an allowed write folder (default: next to the source as '<name>_removed.pdf'). A new file is created immediately; replacing an existing file needs overwrite=true and the user's approval."""
    def build(paths):
        doc = c.open_pdf(paths[0])
        drop = set(c.parse_pages(pages, doc.page_count))
        if len(drop) >= doc.page_count:
            raise c.ToolError("That would remove every page. Keep at least one page.")
        total = doc.page_count
        doc.select([i for i in range(total) if i not in drop])
        return doc, f"removed {len(drop)} page(s), {doc.page_count} left"

    return c.make_pdf(TOOL, c.one(source), destination, overwrite, "_removed", build, "Removed pages from")
