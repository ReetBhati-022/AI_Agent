"""Security / compare PDF tools: unlock, protect, compare."""
from tools.documents.pdf.secure.compare import compare_pdf
from tools.documents.pdf.secure.protect import protect_pdf
from tools.documents.pdf.secure.unlock import unlock_pdf

SECURE_TOOLS = [unlock_pdf, protect_pdf, compare_pdf]