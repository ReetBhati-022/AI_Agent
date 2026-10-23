"""organize_pdf: reorder, repeat, reverse, drop and rotate the pages of a PDF."""
from typing import Literal

from langchain_core.tools import tool

from tools.common import guarded
from tools.documents.pdf import _common as c

TOOL = "organize_pdf"


@tool
@guarded(TOOL)
def organize_pdf(source: str, page_order: str = "", reverse: bool = False, rotate_pages: str = "",
                 rotation: Literal["90", "180", "270"] = "90", destination: str = "", overwrite: bool = False) -> str:
    """Rearrange the pages of a PDF and save the result as a NEW PDF; the original is never changed. `source` is an absolute PDF path inside an approved read folder. `page_order` (optional) is the new page order, 1-based, like '3,1,2,5-7'; pages you leave out are dropped, a page written twice is repeated, '9-5' counts down; blank keeps every page in its current order. `reverse` flips the final order. `rotate_pages` (optional) lists pages of the RESULT to turn clockwise by `rotation` degrees (90, 180 or 270), e.g. '2,4-6' or 'all'. Give at least one of page_order, reverse, rotate_pages. `destination` is optional: a folder or full .pdf file name inside an allowed write folder (default: next to the source as '<name>_organized.pdf'). A new file is created immediately; replacing an existing file needs overwrite=true and the user's approval."""
    if not (page_order.strip() or reverse or rotate_pages.strip()):
        raise c.ToolError("Nothing to do: give a page_order, tick reverse, or list pages to rotate.")

    def build(paths):
        doc = c.open_pdf(paths[0])
        total = doc.page_count
        order = c.parse_pages(page_order, total, repeat=True) if page_order.strip() else list(range(total))
        doc.select(order[::-1] if reverse else order)
        if rotate_pages.strip():
            for i in c.parse_pages(rotate_pages, doc.page_count):
                doc[i].set_rotation((doc[i].rotation + int(rotation)) % 360)
        return doc, f"{total} -> {doc.page_count} page(s)"

    return c.make_pdf(TOOL, c.one(source), destination, overwrite, "_organized", build, "Organized")
