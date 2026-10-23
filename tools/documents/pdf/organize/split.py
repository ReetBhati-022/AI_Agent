"""split_pdf: cut one PDF into several PDFs (every page, every N pages, or the page ranges you list)."""
import os
from typing import Literal

from langchain_core.tools import tool

from security.guard import row
from tools.common import guarded, human_size, ok
from tools.documents.pdf import _common as c

TOOL = "split_pdf"


@tool
@guarded(TOOL)
def split_pdf(source: str, mode: Literal["ranges", "every", "each"] = "ranges", ranges: str = "", every: int = 1,
              destination: str = "", overwrite: bool = False) -> str:
    """Split ONE PDF into several new PDFs; the original is never changed. `source` is an absolute PDF path inside an approved read folder. `mode`: 'ranges' = one file per group in `ranges`, groups separated by ';' (e.g. '1-3;4-6;7-' gives 3 files; commas inside a group pick separate pages: '1,5;2-4'); 'every' = a new file every `every` pages; 'each' = one file per page. `destination` is optional: a FOLDER inside an allowed write folder (default: a new folder '<name>_split' next to the source). Files are named '<name>_part01.pdf', '<name>_part02.pdf'... New files are created immediately; replacing existing ones needs overwrite=true and the user's approval."""
    c.check_enabled()

    def plan():
        real = c.read_source(source)
        base = os.path.splitext(os.path.basename(real))[0]
        folder = destination.strip().strip("\"'") or os.path.join(os.path.dirname(real), base + "_split")
        with c.open_pdf(real) as doc:
            total = doc.page_count
        if mode == "each":
            groups = [[i] for i in range(total)]
        elif mode == "every":
            n = max(1, every)
            groups = [list(range(s, min(s + n, total))) for s in range(0, total, n)]
        else:
            groups = [c.parse_pages(g, total) for g in ranges.split(";") if g.strip()]
            if not groups:
                raise c.ToolError("For mode 'ranges' say which pages go into each file, separated by ';' - e.g. 1-3;4-6;7-")
        if len(groups) > c.cfg("max_split_files", 200):
            raise c.ToolError(f"That would create {len(groups)} files (limit: pdf_tools.max_split_files). Use bigger groups.")
        width = max(2, len(str(len(groups))))
        outs = [c.check_output(os.path.join(folder, f"{base}_part{n:0{width}d}.pdf"), overwrite) + (g,)
                for n, g in enumerate(groups, 1)]
        return real, outs

    def run():
        import pymupdf

        real, outs = plan()
        rows = []
        with c.open_pdf(real) as src:
            for final, was, pages in outs:
                with pymupdf.open() as part:
                    for i in pages:
                        part.insert_pdf(src, from_page=i, to_page=i)
                    size = c.save_pdf(part, final)
                rows.append(row("overwritten" if was else "created", final, f"{len(pages)} page(s), {human_size(size)}"))
        return ok(TOOL, rows, [f"Split into {len(rows)} file(s).", "The original file was not changed."])

    real, outs = plan()
    folder = os.path.dirname(outs[0][0])
    replacing = any(was for _, was, _ in outs)
    desc = f"Split {real} into {len(outs)} PDFs in {folder}" + (" (REPLACING existing files)" if replacing else "")
    return c.submit(TOOL, desc, replacing, run, folder)
