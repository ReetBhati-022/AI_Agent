"""JSON / Markdown / plain text -> formatted PDF.  Libraries do the heavy lifting:

    json2html      JSON (any shape, nested) -> HTML tables / lists
    markdown-it-py Markdown -> HTML (headings, nested lists, tables, quotes, code, links)
    pymupdf        HTML + CSS -> paginated A4 PDF (Story), clickable links, "Page X of Y" footer

Used by convert_document (.json / .md / .txt -> .pdf) and research_report.
"""
import html as html_lib
import re

MARGIN_LEFT, MARGIN_RIGHT, MARGIN_TOP, MARGIN_BOTTOM = 56, 56, 60, 66

CSS = """
body { font-family: sans-serif; font-size: 10.5pt; color: #222222; line-height: 1.35; }
h1 { font-size: 22pt; color: #12355b; margin-top: 0pt; margin-bottom: 4pt; }
h2 { font-size: 15pt; color: #12355b; margin-top: 16pt; margin-bottom: 5pt;
     border-bottom: 1px solid #b8c4d2; padding-bottom: 2pt; }
h3 { font-size: 12.5pt; color: #1f4e79; margin-top: 12pt; margin-bottom: 3pt; }
h4, h5, h6 { font-size: 11pt; color: #333333; margin-top: 9pt; margin-bottom: 2pt; }
p { margin-top: 0pt; margin-bottom: 6pt; }
ul, ol { margin-top: 2pt; margin-bottom: 6pt; }
li { margin-bottom: 3pt; }
a { color: #0b5cad; text-decoration: underline; }
code { font-family: monospace; font-size: 9.5pt; background-color: #eef1f5; }
pre { font-family: monospace; font-size: 9pt; background-color: #f3f5f8; padding: 6pt;
      margin-top: 4pt; margin-bottom: 8pt; border: 1px solid #d9dee5; }
blockquote { margin-left: 8pt; margin-top: 4pt; margin-bottom: 8pt; padding-left: 9pt;
             border-left: 3px solid #9fb3c8; color: #444444; }
table { width: 100%; margin-top: 4pt; margin-bottom: 10pt; }
th { background-color: #12355b; color: #ffffff; font-weight: bold; padding: 4pt; text-align: left;
     border: 1px solid #12355b; font-size: 9.5pt; }
td { padding: 4pt; border: 1px solid #c9d2dd; font-size: 9.5pt; vertical-align: top; }
hr { margin-top: 8pt; margin-bottom: 8pt; }
"""

# characters the built-in PDF fonts may miss -> safe equivalents
_FIX = str.maketrans({"‑": "-", "‐": "-", "‒": "-", " ": " ", "​": None, "﻿": None,
                      "⁠": None, "­": None})


def _build_pdf(body: str, footer_title: str, out_path: str) -> list:
    import pymupdf  # imported lazily so the app starts without it

    media = pymupdf.paper_rect("a4")
    where = pymupdf.Rect(media.x0 + MARGIN_LEFT, media.y0 + MARGIN_TOP, media.x1 - MARGIN_RIGHT, media.y1 - MARGIN_BOTTOM)
    pdf = pymupdf.Story(html=body.translate(_FIX), user_css=CSS).write_with_links(lambda n, filled: (media, where, pymupdf.Identity))
    total, grey = pdf.page_count, (0.45, 0.5, 0.56)
    short = footer_title if len(footer_title) <= 70 else footer_title[:67] + "..."
    for number, page in enumerate(pdf, start=1):
        y = media.y1 - MARGIN_BOTTOM + 22
        page.draw_line((media.x0 + MARGIN_LEFT, y - 10), (media.x1 - MARGIN_RIGHT, y - 10), color=(0.8, 0.84, 0.88), width=0.5)
        page.insert_text((media.x0 + MARGIN_LEFT, y + 2), short, fontname="helv", fontsize=8, color=grey)
        label = f"Page {number} of {total}"
        width = pymupdf.get_text_length(label, fontname="helv", fontsize=8)
        page.insert_text((media.x1 - MARGIN_RIGHT - width, y + 2), label, fontname="helv", fontsize=8, color=grey)
    pdf.set_metadata({"title": footer_title, "creator": "Personal AI Agent"})
    pdf.save(out_path, garbage=3, deflate=True)
    pdf.close()
    return [f"{total} page(s), formatted PDF (headings, lists, tables, links, page numbers)."]


def render_json_pdf(data, out_path: str, title: str = "Document") -> list:
    """Any JSON value -> PDF: objects become two-column tables, lists of objects become real tables, nesting is kept."""
    from json2html import json2html

    if not isinstance(data, (dict, list)):
        data = {"value": data}
    body = f"<h1>{html_lib.escape(title)}</h1>" + json2html.convert(json=data)  # json2html escapes the values
    return _build_pdf(body, title, out_path)


def render_text_pdf(text: str, out_path: str, markdown: bool = True, fallback_title: str = "Document") -> list:
    """Markdown (or plain text) -> PDF. Raw HTML inside the text is shown as text, never executed."""
    if markdown:
        from markdown_it import MarkdownIt

        body = MarkdownIt("commonmark", {"html": False, "linkify": True}).enable(["table", "linkify"]).render(text)
        m = re.search(r"^\s*#\s+(.+?)\s*$", text, flags=re.MULTILINE)
        title = re.sub(r"[*`_]", "", m.group(1)).strip() if m else fallback_title
    else:
        paras = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
        body = "".join("<p>" + html_lib.escape(p.strip("\n")).replace("\n", "<br>") + "</p>" for p in paras if p.strip())
        title = fallback_title
    return _build_pdf(body, title, out_path)


if __name__ == "__main__":  # python -m tools.documents.others.json_render input.json|.md|.txt output.pdf
    import json
    import os
    import sys

    src, dst = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8-sig") as fh:
        content = fh.read()
    name = os.path.basename(src)
    if src.lower().endswith(".json"):
        print(render_json_pdf(json.loads(content), dst, name))
    else:
        print(render_text_pdf(content, dst, markdown=src.lower().endswith(".md"), fallback_title=name))
