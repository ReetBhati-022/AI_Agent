"""scan_to_pdf: turn scanned pages / photos (JPG, PNG, TIF...) into one PDF, one image per page."""
import io
import os
import re
from typing import Literal

from langchain_core.tools import tool

from security import permissions
from tools.common import guarded
from tools.documents.pdf import _common as c

TOOL = "scan_to_pdf"
PAPER = {"a4": (595.28, 841.89), "letter": (612.0, 792.0)}  # points
MARGIN = 18.0


@tool
@guarded(TOOL)
def scan_to_pdf(sources: str, page_size: Literal["fit", "a4", "letter"] = "fit", destination: str = "",
                overwrite: bool = False) -> str:
    """Combine scanned pages or photos into ONE new PDF, one image per page, in the order listed; the images are never changed. `sources` is a list of absolute image paths (jpg, png, tif, bmp, gif, webp) inside an approved read folder, one per line (or separated by ';'); a line may also be a FOLDER, then all its images are used, sorted by name (page2 before page10). Multi-page TIFFs give one page per frame; photos are turned upright using their EXIF data. `page_size`: 'fit' = each page matches its image (default), 'a4' or 'letter' = centered on that paper with a margin. `destination` is optional: a folder or full .pdf file name inside an allowed write folder (default: next to the first image as '<name>_scan.pdf'). The text in the images is NOT recognized (use ocr_pdf on the result for that). A new file is created immediately; replacing an existing file needs overwrite=true and the user's approval."""
    def inputs():
        files = []
        for raw in c.split_paths(sources):
            if os.path.isdir(raw):
                folder = permissions.check_read_dir(raw)
                files += [os.path.join(folder, n) for n in sorted(os.listdir(folder), key=lambda n: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", n)])
                          if os.path.splitext(n)[1].lower() in c.IMAGE_EXTS]
            else:
                files.append(raw)
        if not files:
            raise c.ToolError("Give at least one image file or a folder with images (one per line).")
        if len(files) > c.cfg("max_scan_images", 300):
            raise c.ToolError("Too many images (pdf_tools.max_scan_images).")
        return [c.read_source(f, c.IMAGE_EXTS, "an image") for f in files]

    def build(paths):
        import pymupdf
        from PIL import Image, ImageOps, ImageSequence

        out = pymupdf.open()
        for path in paths:
            with Image.open(path) as im:
                dpi = min(max(float((im.info.get("dpi") or (150,))[0] or 150), 72.0), 600.0)
                for frame in ImageSequence.Iterator(im):
                    img = ImageOps.exif_transpose(frame.copy())
                    if "A" in img.getbands() or img.mode == "P":  # transparent -> white background
                        img = img.convert("RGBA")
                        white = Image.new("RGB", img.size, "white")
                        white.paste(img, mask=img.getchannel("A"))
                        img = white
                    buf = io.BytesIO()
                    img.convert("RGB").save(buf, "JPEG", quality=92)
                    w, h = img.size
                    if page_size == "fit":
                        pw, ph = w * 72 / dpi, h * 72 / dpi
                        rect = pymupdf.Rect(0, 0, pw, ph)
                    else:
                        pw, ph = PAPER[page_size] if h >= w else PAPER[page_size][::-1]
                        s = min((pw - 2 * MARGIN) / w, (ph - 2 * MARGIN) / h)
                        rect = pymupdf.Rect(0, 0, w * s, h * s) + ((pw - w * s) / 2, (ph - h * s) / 2, (pw - w * s) / 2, (ph - h * s) / 2)
                    out.new_page(width=pw, height=ph).insert_image(rect, stream=buf.getvalue())
        return out, f"{len(paths)} image(s) -> {out.page_count} page(s)"

    return c.make_pdf(TOOL, inputs, destination, overwrite, "_scan", build, "Combined images into")
