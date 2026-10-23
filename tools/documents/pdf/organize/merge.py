"""merge_pdf: combine several PDFs into one."""
from langchain_core.tools import tool

from tools.common import guarded
from tools.documents.pdf import _common as c

TOOL = "merge_pdf"


@tool
@guarded(TOOL)
def merge_pdf(sources: str, destination: str = "", overwrite: bool = False) -> str:
    """Merge two or more PDF files into ONE new PDF, in the order listed; the originals are never changed. `sources` is a list of absolute PDF paths inside an approved read folder, one per line (or separated by ';'). `destination` is optional: a folder or a full .pdf file name inside an allowed write folder (default: next to the first file as '<name>_merged.pdf'). Links and bookmarks are kept. A new file is created immediately; replacing an existing file needs overwrite=true and the user's approval. Password-protected PDFs are refused."""
    def inputs():
        paths = c.split_paths(sources)
        if len(paths) < 2:
            raise c.ToolError("Give at least two PDF files to merge (one path per line).")
        return [c.read_source(p) for p in paths]

    def build(paths):
        import pymupdf

        out, toc = pymupdf.open(), []
        for path in paths:
            with c.open_pdf(path) as doc:
                toc += [[lvl, title, page + out.page_count] for lvl, title, page, *_ in doc.get_toc() if page > 0]
                out.insert_pdf(doc)  # keeps links; bookmarks are re-created below
        if toc:
            toc[0][0] = 1
            for prev, cur in zip(toc, toc[1:]):
                cur[0] = min(cur[0], prev[0] + 1)  # PDF outlines may not skip levels
            out.set_toc(toc)
        return out, f"{len(paths)} files, {out.page_count} pages"

    return c.make_pdf(TOOL, inputs, destination, overwrite, "_merged", build, "Merged")
