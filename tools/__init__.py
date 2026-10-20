"""Registered tools, grouped by folder."""
from tools.audit.history import file_operation_history, get_approved_result
from tools.system.clipboard import read_clipboard
from tools.system.launch import open_file, open_file_location
from tools.system.shell import run_shell_command
from tools.system.info import get_current_directory, get_disk_usage, get_system_info
from tools.documents.others.convert import convert_document
from tools.documents.others.extract import extract_text
from tools.documents.others.notes import create_note
from tools.documents.others.report import generate_report
from tools.documents.others.research import research_report
from tools.documents.files.file_edit import append_file, create_file, replace_text, write_file
from tools.documents.files.file_ops import (copy_file, create_directory, delete_directory, delete_file, move_file,
                                   rename_file)
from tools.documents.files.content_search import search_by_content
from tools.documents.files.file_reader import read_file
from tools.documents.files.discovery import list_directory, search_by_extension, search_files
from tools.documents.files.metadata import find_duplicates, get_file_info
from tools.web.search import web_search

GROUPS = {
    "traversal": [list_directory, search_files, search_by_extension, get_file_info, find_duplicates],
    "reading": [search_by_content, read_file],
    "modify": [create_file, write_file, append_file, replace_text, rename_file, copy_file, move_file,
               create_directory, delete_file, delete_directory],
    "web": [web_search],
    "computer": [get_system_info, get_disk_usage, get_current_directory, run_shell_command, open_file_location,
                 open_file, read_clipboard],
    "documents": [create_note, extract_text, convert_document, generate_report, research_report],
    "audit": [file_operation_history, get_approved_result],
}
TOOLS = [t for group in GROUPS.values() for t in group]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}