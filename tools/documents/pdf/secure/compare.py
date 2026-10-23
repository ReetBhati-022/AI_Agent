"""compare_pdf: find the TEXT differences between two PDFs and write them into a NEW report PDF."""
import difflib
import html
import io
import re

from langchain_core.tools import tool

from tools.common import guarded
from tools.documents.pdf import _common as c

TOOL = "compare_pdf"
MAX_BLOCKS = 300        # change blocks shown in the report
MAX_LINES_SHOWN = 40    # lines shown per block side
MAX_LINES = 30000       # text lines per PDF (keeps the comparison fast)
CSS = """
body { font-family: sans-serif; font-size: 10pt; }
h1 { font-size: 18pt; color: #4b3070; margin-bottom: 4pt; }
h2 { font-size: 11pt; color: #4b3070; margin-top: 12pt; margin-bottom: 3pt; }
p { margin: 1pt 0; }
.meta { color: #555555; font-size: 9pt; }
.del { background-color: #fde2e4; color: #8f1d2c; }
.add { background-color: #dff3e7; color: #1d6b46; }
"""


def _lines(doc) -> list:
    """[(page number, normalised text line)] for every non-empty line of text."""
    out = []
    for number, page in enumerate(doc, 1):
        for raw in page.get_text("text").splitlines():
            text = re.sub(r"\s+", " ", raw).strip()
            if text:
                out.append((number, text))
    return out


def _page(lines: list, i: int) -> int:
    return lines[min(i, len(lines) - 1)][0] if lines else 1


def _block(lines: list, a: int, b: int, cls: str, sign: str) -> str:
    shown = lines[a:b][:MAX_LINES_SHOWN]
    out = "".join(f'<p class="{cls}">{sign} {html.escape(t)} <span class="meta">(p.{n})</span></p>' for n, t in shown)
    if b - a > MAX_LINES_SHOWN:
        out += f'<p class="meta">... {b - a - MAX_LINES_SHOWN} more line(s)</p>'
    return out


def _render(body: str):
    import pymupdf

    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    story = pymupdf.Story(html=body, user_css=CSS)
    page_box = pymupdf.paper_rect("a4")
    where = page_box + (48, 48, -48, -48)
    more = 1
    while more:
        dev = writer.begin_page(page_box)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return pymupdf.open("pdf", buf.getvalue())


@tool
@guarded(TOOL)
def compare_pdf(source_a: str, source_b: str, destination: str = "", overwrite: bool = False) -> str:
    """Compare the TEXT of two PDFs and save a NEW report PDF that lists what was removed (red, only in A) and added (green, only in B), with page numbers; the originals are never changed. `source_a` is the original / older PDF and `source_b` the new one, both absolute PDF paths inside an approved read folder. Images and layout are not compared; scanned PDFs without text need ocr_pdf first. Password-protected PDFs are refused. `destination` is optional: a folder or full .pdf file name inside an allowed write folder (default: next to source_a as '<name>_comparison.pdf'). A new file is created immediately; replacing an existing file needs overwrite=true and the user's approval."""
    import os

    limit = c.cfg("max_compare_pages", 300)

    def inputs():
        return [c.read_source(source_a), c.read_source(source_b)]

    def build(paths):
        docs = [c.open_pdf(p) for p in paths]
        try:
            for d, p in zip(docs, paths):
                if d.page_count > limit:
                    raise c.ToolError(f"{os.path.basename(p)} has {d.page_count} pages, over the limit of {limit} for comparing.")
            la, lb = _lines(docs[0]), _lines(docs[1])
            pages = (docs[0].page_count, docs[1].page_count)
        finally:
            for d in docs:
                d.close()
        if not la and not lb:
            raise c.ToolError("Neither PDF has selectable text (they may be scans). Run OCR PDF on them first.")
        if len(la) > MAX_LINES or len(lb) > MAX_LINES:
            raise c.ToolError("These PDFs have too much text to compare at once. Compare fewer pages (use Extract pages first).")

        sm = difflib.SequenceMatcher(None, [t for _, t in la], [t for _, t in lb], autojunk=False)
        ops = [o for o in sm.get_opcodes() if o[0] != "equal"]
        gone = sum(i2 - i1 for _, i1, i2, _, _ in ops)
        new = sum(j2 - j1 for _, _, _, j1, j2 in ops)

        name_a, name_b = (html.escape(os.path.basename(p)) for p in paths)
        body = [f"<h1>PDF comparison</h1>",
                f'<p>A (original): <b>{name_a}</b> <span class="meta">({pages[0]} page(s))</span></p>',
                f'<p>B (new): <b>{name_b}</b> <span class="meta">({pages[1]} page(s))</span></p>']
        if not ops:
            body.append("<h2>No text differences found</h2>")
            body.append('<p class="meta">Only text is compared: images, colors and layout are not checked.</p>')
        else:
            body.append(f"<h2>{len(ops)} change(s): {gone} line(s) removed, {new} line(s) added</h2>")
            body.append('<p class="meta">Red = only in A. Green = only in B. (p.N) is the page in that file.</p>')
            for n, (tag, i1, i2, j1, j2) in enumerate(ops[:MAX_BLOCKS], 1):
                kind = {"delete": "Removed", "insert": "Added", "replace": "Changed"}[tag]
                where = f"A page {_page(la, i1)}" if tag == "delete" else f"B page {_page(lb, j1)}" if tag == "insert" \
                    else f"A page {_page(la, i1)} / B page {_page(lb, j1)}"
                body.append(f"<h2>{n}. {kind} <span class=\"meta\">({where})</span></h2>")
                body.append(_block(la, i1, i2, "del", "-") + _block(lb, j1, j2, "add", "+"))
            if len(ops) > MAX_BLOCKS:
                body.append(f'<p class="meta">... {len(ops) - MAX_BLOCKS} more change(s) not shown.</p>')
        detail = "identical text" if not ops else f"{len(ops)} change(s), {gone} removed, {new} added"
        return _render("".join(body)), detail

    return c.make_pdf(TOOL, inputs, destination, overwrite, "_comparison", build, "Compared")