"""PDF tools, grouped like the menu: organize/, optimize/ and secure/. Add a section = a new folder + list it here."""
from tools.documents.pdf.optimize import OPTIMIZE_TOOLS
from tools.documents.pdf.organize import ORGANIZE_TOOLS
from tools.documents.pdf.secure import SECURE_TOOLS

PDF_TOOLS = [*ORGANIZE_TOOLS, *OPTIMIZE_TOOLS, *SECURE_TOOLS]
