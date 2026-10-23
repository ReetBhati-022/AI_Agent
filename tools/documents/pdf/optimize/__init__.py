"""Optimize PDF tools: compress, repair, OCR."""
from tools.documents.pdf.optimize.compress import compress_pdf
from tools.documents.pdf.optimize.ocr import ocr_pdf
from tools.documents.pdf.optimize.repair import repair_pdf

OPTIMIZE_TOOLS = [compress_pdf, repair_pdf, ocr_pdf]
