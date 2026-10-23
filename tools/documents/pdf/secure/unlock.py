"""unlock_pdf: remove the password from a PDF into a NEW file (the password must be known)."""
from langchain_core.tools import tool

from tools.common import guarded
from tools.documents.pdf import _common as c

TOOL = "unlock_pdf"


@tool
@guarded(TOOL)
def unlock_pdf(source: str, password: str = "", destination: str = "", overwrite: bool = False) -> str:
    """Remove the password from a protected PDF and save the result as a NEW PDF that opens without one; the original is never changed. `source` is an absolute PDF path inside an approved read folder. `password` is the password the PDF asks for when it is opened (leave it empty for a PDF that only has editing/printing restrictions). A wrong password is refused. `destination` is optional: a folder or full .pdf file name inside an allowed write folder (default: next to the source as '<name>_unlocked.pdf'). A new file is created immediately; replacing an existing file needs overwrite=true and the user's approval. Only for the user's own PDFs. This tool is available in the PDF tools tab only, never ask the user to type a password in the chat."""
    import pymupdf

    def build(paths):
        try:
            doc = pymupdf.open(paths[0])
        except Exception as exc:  # noqa: BLE001
            raise c.ToolError(f"Could not open the PDF ({exc}).") from exc
        if doc.needs_pass:
            if not password:
                doc.close()
                raise c.ToolError("This PDF is locked. Enter its password.")
            if not doc.authenticate(password):
                doc.close()
                raise c.ToolError("Wrong password.")
            detail = "password removed"
        elif doc.is_encrypted:
            detail = "restrictions removed"
        else:
            doc.close()
            raise c.ToolError("This PDF is not password-protected.")
        if doc.page_count == 0:
            doc.close()
            raise c.ToolError("This PDF has no pages.")
        return doc, detail

    return c.make_pdf(TOOL, c.one(source), destination, overwrite, "_unlocked", build, "Unlocked",
                      {"encryption": pymupdf.PDF_ENCRYPT_NONE})