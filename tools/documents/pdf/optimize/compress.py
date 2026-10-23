"""compress_pdf: make a PDF smaller (downscale big images + squeeze streams)."""
from typing import Literal

from langchain_core.tools import tool

from tools.common import guarded
from tools.documents.pdf import _common as c

TOOL = "compress_pdf"
LEVELS = {"low": (200, 85), "medium": (150, 70), "high": (96, 50)}  # (image dpi, JPEG quality)


@tool
@guarded(TOOL)
def compress_pdf(source: str, level: Literal["low", "medium", "high"] = "medium", destination: str = "",
                 overwrite: bool = False) -> str:
    """Reduce the file size of a PDF and save it as a NEW PDF; the original is never changed. `source` is an absolute PDF path inside an approved read folder. `level`: 'low' = best quality, small saving; 'medium' = balanced (default); 'high' = smallest file, images get blurrier. Large images are scaled down and recompressed, fonts and data streams are squeezed. `destination` is optional: a folder or full .pdf file name inside an allowed write folder (default: next to the source as '<name>_compressed.pdf'). A new file is created immediately; replacing an existing file needs overwrite=true and the user's approval."""
    dpi, quality = LEVELS[level]

    def build(paths):
        doc = c.open_pdf(paths[0])
        doc.rewrite_images(dpi_threshold=dpi + 50, dpi_target=dpi, quality=quality)
        return doc, f"images {dpi} dpi, quality {quality}"

    save = {"garbage": 4, "clean": True, "deflate_images": True, "deflate_fonts": True}
    return c.make_pdf(TOOL, c.one(source), destination, overwrite, "_compressed", build, "Compressed", save, check_smaller=True)
