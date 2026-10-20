"""Markdown -> nicely formatted PDF (PyMuPDF Story). Used by convert_document for .md / .txt -> .pdf.

Supports: title block, headings, paragraphs, bold / italic / `code`, clickable links (also bare URLs),
nested bullet and numbered lists (numbers keep counting), tables, block quotes, code blocks,
horizontal rules, and a footer with the document title and "Page X of Y" on every page.
Needs only pymupdf (already in requirements.txt).
"""
import html as html_lib
import os
import re

# ------------------------------------------------------------------ layout
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
.subtitle { color: #667788; font-size: 9.5pt; margin-bottom: 12pt; }
"""

# ------------------------------------------------------------------ inline markdown
_CODE = re.compile(r"(`[^`\n]+`)")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITAL = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])|(?<![\w])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w])")
_URL = re.compile(r"(?<![\"'>=\w/])(https?://[^\s<>\"']*[^\s<>\"'.,;:!?)\]])")
_TOKEN = re.compile(r"\x00(\d+)\x00")

# characters the built-in PDF fonts may miss -> safe equivalents
_CHAR_FIX = {"\u2011": "-", "\u2010": "-", "\u2012": "-", "\u202f": " ", "\u200b": "", "\ufeff": "",
             "\u2060": "", "\u00ad": ""}


def _fix_chars(text: str) -> str:
    return text.translate({ord(k): v for k, v in _CHAR_FIX.items()})


def inline(text: str) -> str:
    """Escape one line of Markdown text and turn the inline syntax into HTML."""
    stash = []

    def keep(markup: str) -> str:
        stash.append(markup)
        return f"\x00{len(stash) - 1}\x00"

    out = []
    for part in _CODE.split(_fix_chars(text)):
        if part.startswith("`") and part.endswith("`") and len(part) > 2:
            out.append(keep(f"<code>{html_lib.escape(part[1:-1], quote=False)}</code>"))
            continue
        part = html_lib.escape(part, quote=False)
        part = _LINK.sub(lambda m: keep(f'<a href="{m.group(2)}">{m.group(1)}</a>'), part)
        part = _URL.sub(lambda m: keep(f'<a href="{m.group(1)}">{m.group(1)}</a>'), part)
        part = _BOLD.sub(lambda m: f"<b>{m.group(2)}</b>", part)
        part = _ITAL.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", part)
        out.append(part)
    result = "".join(out)
    while _TOKEN.search(result):  # restore (stash items may be nested once, e.g. a link text)
        result = _TOKEN.sub(lambda m: stash[int(m.group(1))], result)
    return result


# ------------------------------------------------------------------ block markdown
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _indent(raw: str) -> int:
    return len(raw.expandtabs(4)) - len(raw.expandtabs(4).lstrip(" "))


def _split_row(line: str) -> list:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]


def _render_table(rows: list, aligns: list) -> str:
    def cell(tag, text, i):
        align = aligns[i] if i < len(aligns) else ""
        style = f' align="{align}"' if align else ""
        return f"<{tag}{style}>{inline(text)}</{tag}>"

    head, body = rows[0], rows[1:]
    cols = len(head)
    out = ['<table border="1" cellpadding="4" cellspacing="0" width="100%">', "<tr>"]
    out += [cell("th", c, i) for i, c in enumerate(head)]
    out.append("</tr>")
    for r in body:
        r = (r + [""] * cols)[:cols]
        out.append("<tr>" + "".join(cell("td", c, i) for i, c in enumerate(r)) + "</tr>")
    out.append("</table>")
    return "".join(out)


def _render_list(items: list) -> str:
    """items: [{'ordered': bool, 'number': int, 'indent': int, 'text': str}] in document order."""
    html, stack = [], []  # stack of (indent, tag); every <li> stays open until the next sibling / parent closes it

    def close_to(indent):
        while stack and stack[-1][0] > indent:
            html.append("</li></" + stack[-1][1] + ">")
            stack.pop()

    for it in items:
        tag = "ol" if it["ordered"] else "ul"
        close_to(it["indent"])
        if stack and stack[-1][0] == it["indent"]:
            if stack[-1][1] == tag:
                html.append("</li>")
            else:  # list type changed at the same level
                html.append("</li></" + stack[-1][1] + ">")
                stack.pop()
        if not stack or stack[-1][0] < it["indent"] or stack[-1][1] != tag:
            start = f' start="{it["number"]}"' if it["ordered"] and it["number"] != 1 else ""
            html.append(f"<{tag}{start}>")
            stack.append((it["indent"], tag))
        html.append("<li>" + inline(it["text"]))
    while stack:
        html.append("</li></" + stack[-1][1] + ">")
        stack.pop()
    return "".join(html)


def markdown_to_html(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, para, i, n = [], [], 0, len(lines)
    seen_h1 = False

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()

    while i < n:
        raw = lines[i]
        stripped = raw.strip()

        if _FENCE.match(raw):  # fenced code block
            flush()
            i += 1
            code = []
            while i < n and not _FENCE.match(lines[i]):
                code.append(lines[i])
                i += 1
            i += 1
            out.append("<pre>" + html_lib.escape(_fix_chars("\n".join(code)), quote=False) + "</pre>")
            continue

        if not stripped:
            flush()
            i += 1
            continue

        m = _HEADING.match(stripped)
        if m:
            flush()
            level = len(m.group(1))
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            if level == 1 and not seen_h1:  # an italic line right under the title becomes the subtitle
                seen_h1 = True
                j = i + 1
                while j < n and not lines[j].strip():
                    j += 1
                if j < n and re.match(r"^[_*][^_*].*[_*]$", lines[j].strip()):
                    out.append(f'<p class="subtitle">{inline(lines[j].strip())}</p>')
                    i = j
            i += 1
            continue

        if _HR.match(stripped):
            flush()
            out.append("<hr>")
            i += 1
            continue

        if stripped.startswith("|") and i + 1 < n and _TABLE_SEP.match(lines[i + 1]) and "|" in lines[i + 1]:
            flush()
            header = _split_row(stripped)
            aligns = []
            for c in _split_row(lines[i + 1]):
                aligns.append("center" if c.startswith(":") and c.endswith(":") else "right" if c.endswith(":") else "")
            rows = [header]
            i += 2
            while i < n and lines[i].strip().startswith("|"):
                rows.append(_split_row(lines[i]))
                i += 1
            out.append(_render_table(rows, aligns))
            continue

        if stripped.startswith(">"):
            flush()
            quote = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>" + markdown_to_html("\n".join(quote)) + "</blockquote>")
            continue

        m = _LIST_ITEM.match(raw)
        if m:
            flush()
            items = []
            while i < n:
                m = _LIST_ITEM.match(lines[i])
                if m:
                    marker = m.group(2)
                    items.append({"ordered": marker[0].isdigit(), "indent": _indent(lines[i]),
                                  "number": int(marker[:-1]) if marker[0].isdigit() else 1, "text": m.group(3)})
                    i += 1
                elif lines[i].strip() and _indent(lines[i]) > 0 and items and not _HEADING.match(lines[i].strip()):
                    items[-1]["text"] += " " + lines[i].strip()  # wrapped continuation of the item above
                    i += 1
                elif not lines[i].strip() and i + 1 < n and _LIST_ITEM.match(lines[i + 1]):
                    i += 1  # blank line inside a list
                else:
                    break
            # normalise indents to nesting levels (2 levels of whitespace never matter, only order)
            out.append(_render_list(items))
            continue

        para.append(stripped)
        i += 1

    flush()
    return "".join(out)


def plain_to_html(text: str) -> str:
    """.txt input: keep the author's line breaks, paragraphs separated by blank lines."""
    paras = re.split(r"\n\s*\n", _fix_chars(text).replace("\r\n", "\n"))
    return "".join("<p>" + "<br>".join(html_lib.escape(l, quote=False) for l in p.split("\n")) + "</p>"
                   for p in paras if p.strip())


# ------------------------------------------------------------------ PDF
def render_pdf(text: str, out_path: str, markdown: bool = True, fallback_title: str = "Document") -> list:
    import pymupdf  # imported lazily so the app starts without it

    body = markdown_to_html(text) if markdown else plain_to_html(text)
    m = re.search(r"^\s*#\s+(.+?)\s*$", text, flags=re.MULTILINE) if markdown else None
    title = re.sub(r"[*`]", "", m.group(1)).strip() if m else fallback_title

    media = pymupdf.paper_rect("a4")
    where = pymupdf.Rect(media.x0 + MARGIN_LEFT, media.y0 + MARGIN_TOP, media.x1 - MARGIN_RIGHT, media.y1 - MARGIN_BOTTOM)

    def rectfn(rect_num, filled):
        return media, where, pymupdf.Identity

    story = pymupdf.Story(html=body, user_css=CSS)
    doc = story.write_with_links(rectfn)  # in-memory PDF; links in the HTML stay clickable
    total = doc.page_count
    grey = (0.45, 0.5, 0.56)
    for number, page in enumerate(doc, start=1):
        y = media.y1 - MARGIN_BOTTOM + 22
        page.draw_line((media.x0 + MARGIN_LEFT, y - 10), (media.x1 - MARGIN_RIGHT, y - 10), color=(0.8, 0.84, 0.88), width=0.5)
        short = title if len(title) <= 70 else title[:67] + "..."
        page.insert_text((media.x0 + MARGIN_LEFT, y + 2), short, fontname="helv", fontsize=8, color=grey)
        label = f"Page {number} of {total}"
        width = pymupdf.get_text_length(label, fontname="helv", fontsize=8)
        page.insert_text((media.x1 - MARGIN_RIGHT - width, y + 2), label, fontname="helv", fontsize=8, color=grey)
    doc.set_metadata({"title": title, "creator": "Personal AI Agent"})
    doc.save(out_path, garbage=3, deflate=True)
    doc.close()
    return [f"{total} page(s), formatted PDF (title, headings, lists, tables, links, page numbers)."]


if __name__ == "__main__":  # python -m tools.documents.pdf.pdf_render input.md output.pdf
    import sys

    src, dst = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8") as fh:
        print(render_pdf(fh.read(), dst, markdown=src.lower().endswith(".md"), fallback_title=os.path.basename(src)))