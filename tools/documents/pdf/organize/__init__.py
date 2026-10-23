"""Organize PDF tools: merge, split, remove pages, extract pages, organize (reorder / rotate), scan to PDF."""
from tools.documents.pdf.organize.extract_pages import extract_pdf_pages
from tools.documents.pdf.organize.merge import merge_pdf
from tools.documents.pdf.organize.organize import organize_pdf
from tools.documents.pdf.organize.remove_pages import remove_pdf_pages
from tools.documents.pdf.organize.scan_to_pdf import scan_to_pdf
from tools.documents.pdf.organize.split import split_pdf

ORGANIZE_TOOLS = [merge_pdf, split_pdf, remove_pdf_pages, extract_pdf_pages, organize_pdf, scan_to_pdf]
