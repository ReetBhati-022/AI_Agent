"""repair_pdf: rebuild a damaged PDF into a clean new file."""
from langchain_core.tools import tool

from tools.common import guarded
from tools.documents.pdf import _common as c

TOOL = "repair_pdf"


@tool
@guarded(TOOL)
def repair_pdf(source: str, destination: str = "", overwrite: bool = False) -> str:
    """Try to repair a damaged or broken PDF (bad cross-reference table, truncated file, 'cannot open') by rebuilding its structure into a NEW PDF; the original is never changed. `source` is an absolute PDF path inside an approved read folder. Pages that cannot be recovered are lost. Password-protected PDFs are refused. `destination` is optional: a folder or full .pdf file name inside an allowed write folder (default: next to the source as '<name>_repaired.pdf'). A new file is created immediately; replacing an existing file needs overwrite=true and the user's approval."""
    def build(paths):
        doc = c.open_pdf(paths[0])  # MuPDF rebuilds a broken structure while opening
        return doc, f"{doc.page_count} page(s) recovered" + (", damage was found and fixed" if doc.is_repaired else ", no damage found")

    return c.make_pdf(TOOL, c.one(source), destination, overwrite, "_repaired", build, "Repaired", {"garbage": 4, "clean": True})
