"""protect_pdf: lock a PDF with a password (AES-256) into a NEW file."""
from langchain_core.tools import tool

from tools.common import guarded
from tools.documents.pdf import _common as c

TOOL = "protect_pdf"


@tool
@guarded(TOOL)
def protect_pdf(source: str, password: str, destination: str = "", overwrite: bool = False) -> str:
    """Lock a PDF with a password (AES-256) and save it as a NEW PDF that asks for the password when opened; the original is never changed. `source` is an absolute PDF path inside an approved read folder. `password` is 4 to 100 characters; it cannot be recovered if forgotten. PDFs that already have a password are refused (unlock them first). `destination` is optional: a folder or full .pdf file name inside an allowed write folder (default: next to the source as '<name>_protected.pdf'). A new file is created immediately; replacing an existing file needs overwrite=true and the user's approval. This tool is available in the PDF tools tab only, never ask the user to type a password in the chat."""
    import pymupdf

    pw = password or ""
    if not 4 <= len(pw) <= 100:
        raise c.ToolError("Choose a password between 4 and 100 characters.")

    def build(paths):
        doc = c.open_pdf(paths[0])  # refuses PDFs that already have a password
        return doc, "AES-256 password set"

    return c.make_pdf(TOOL, c.one(source), destination, overwrite, "_protected", build, "Protected",
                      {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": pw, "owner_pw": pw})