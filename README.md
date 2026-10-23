# Personal AI Agent (LangGraph + Flask / Streamlit)

Windows file-system assistant: search, read, edit with approval, documents, notes, reports and web research.
A LangGraph agent (model: `gpt-oss:120b-cloud` via your Ollama endpoint) picks from **32 tools**:

| Group | Tools |
|---|---|
| Find files (5) | `list_directory`, `search_files`, `search_by_extension`, `get_file_info`, `find_duplicates` |
| Read (2) | `read_file`, `search_by_content` |
| Change files (10) | `create_file`, `write_file`, `append_file`, `replace_text`, `rename_file`, `copy_file`, `move_file`, `create_directory`, `delete_file`, `delete_directory` |
| Computer (7) | `get_system_info`, `get_disk_usage`, `get_current_directory`, `open_file_location`, `open_file`, `run_shell_command`, `read_clipboard` |
| PDF tools (12) | Organize: `merge_pdf`, `split_pdf`, `remove_pdf_pages`, `extract_pdf_pages`, `organize_pdf`, `scan_to_pdf`. Optimize: `compress_pdf`, `repair_pdf`, `ocr_pdf`. Security: `unlock_pdf`, `protect_pdf` (form only), `compare_pdf` |
| Documents (5) | `create_note`, `extract_text`, `convert_document`, `generate_report`, `research_report` |
| Web (1) | `web_search` (Tavily) |
| Audit (2) | `file_operation_history`, `get_approved_result` |

Risky changes (overwrite, append, replace, rename, move, delete) only happen after **you click Approve** in the app; deleted items go to the Recycle Bin.

## Quick start (PowerShell)

Run every command from the project folder (the one that contains `agent/`, `tools/`, `ui/`).

```powershell
conda activate ai-agent
cd <your project folder>
pip install -r requirements.txt
copy .env.example .env      # then edit .env: TAVILY_API_KEY, APPROVED_READ_DIRS, ...
python -m ui.flask_app      # Flask UI      -> http://127.0.0.1:5000
# or
streamlit run ui/streamlit_app.py
```

If `conda activate` fails in PowerShell, run `conda init powershell` once and reopen the terminal.
Make sure Ollama is running (`ollama list` should show `gpt-oss:120b-cloud`; cloud models need `ollama signin`).
Use **Check model connection** in the sidebar to verify the endpoint.

## The two UIs

Both UIs use the same agent and the same tools; Approve / Reject cards work exactly the same.

- **Flask** (`python -m ui.flask_app`): purple / blue / pink sidebar with every feature (Find files, Read & search text, Documents, Web search, Edit files, Computer, History & audit), recent chats, drive filter and status; chat workspace on the right. Change address with `FLASK_HOST` / `FLASK_PORT`.
- **Streamlit** (`streamlit run ui/streamlit_app.py`): chat plus a sidebar with manual tools.

Files: `ui/flask_app.py`, `ui/templates/index.html`, `ui/static/style.css`, `ui/static/app.js`, `ui/streamlit_app.py`.

## Chat memory

Chats are kept per conversation so a restart or browser refresh resumes the same chat.

| `MEMORY_BACKEND` (in `.env`) | What happens |
|---|---|
| `memory` | Chats live in RAM only and are lost when the app stops. No setup needed. |
| `postgres` (needs Docker) | Chats are saved in Postgres and kept `MEMORY_RETENTION_DAYS` days (default 7); older chats are removed at start. |

To use Postgres, set `POSTGRES_PASSWORD` (and optionally `POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_PORT`) in `.env`, then run from the project folder:

```powershell
docker compose --env-file .env -f config/docker-compose.yml up -d
```

If Postgres cannot be reached, the app falls back to in-memory chats and shows the reason in the status area.

## Web search and research reports

Both need `TAVILY_API_KEY` in `.env` (get one at https://tavily.com).

| Tool | What it does | Approve needed? |
|---|---|---|
| `web_search` | Tavily search in "advanced" depth. Returns result rows (title, url, best-matching snippet), a short synthesized answer and the full text of each page (capped). `topic` is `general` or `news`; `detailed=false` for a quick fact | no |
| `research_report` | ONE call = several different web searches + a detailed report of about `pages` pages (default 4, never fewer than 3, max 8), written section by section (overview, key facts table, analysis sections, conclusion, sources with links). The real PDF page count is checked and extra sections are added until the minimum is reached. Saves a formatted PDF (or `.md` with `output="md"`) plus a `.md` copy in the notes folder; never overwrites | no |

Limits are under `web:` and `research:` in `config/config.yaml` (results per search, text kept per page, searches per report, sources used, min/max pages).
Use `research_report` when you want details, a full comparison or a report/PDF; use `web_search` for quick questions. Web text is treated as untrusted data and instructions inside it are ignored.

## PDF reading and OCR (optional)

`read_file` reads digital PDFs with PyMuPDF. Scanned PDF pages (images, no text) are read with OCR **only if Tesseract is installed**:
install it from https://github.com/UB-Mannheim/tesseract/wiki (tick the languages you need) and add it to `PATH`, then reopen PowerShell.
Settings are under `ocr:` in `config/config.yaml` (language, dpi, max OCR pages per call, or `enabled: false`). Without Tesseract, scanned pages
simply return no text with a clear note. PyMuPDF is AGPL-licensed (fine for personal use).

## Changing files (create / write / delete ...)

| Setting (`config/config.yaml`, `modify:`) | Meaning |
|---|---|
| `write_dirs` | Changes are allowed **only** inside these folders (default: Documents, Desktop, Downloads of CT_USER) |
| `allow_all_writes` | `true` = anywhere on C:/D: (also `ALLOW_ALL_WRITES=true` in `.env`). Excluded folders, the app's own folder, links, blocked names and program/script types stay protected |
| `confirm_changes` | `true` (default) = overwrite/append/replace/rename/move/delete wait for your **Approve** button. `false` = run immediately |
| `delete_mode` | `trash` (Recycle Bin, default) or `permanent` |

Creating a new file/folder or copying to a new name runs immediately. Only text files can be written (see `content.supported_extensions`).
Drive roots, the user profile, Desktop/Documents/Downloads themselves and the write folders themselves can never be deleted or renamed.
The model cannot approve anything: only the buttons in the app can.

## Computer tools

| Tool | What it does | Approve needed? |
|---|---|---|
| `get_system_info` | Windows, Python and conda/venv details | no |
| `get_disk_usage` | Total / used / free for C: and D: | no |
| `get_current_directory` | Working folder, app folder, home folder | no |
| `open_file_location` | Shows a file/folder in File Explorer (does not open the file) | no |
| `open_file` | Opens a file in its default app; only types in `computer.open_allowed_extensions` (documents, text, images, media; never programs/scripts) | **yes** |
| `run_shell_command` | Runs ONE PowerShell command, returns stdout / stderr / exit code | **yes** |
| `read_clipboard` | Reads clipboard **text** (PowerShell `Get-Clipboard`) | **yes, always** |

Settings are under `computer:` in `config/config.yaml`. `confirm_actions` is separate from `modify.confirm_changes`, so turning the modify gate off never makes commands run by themselves.
`run_shell_command` runs with a minimal environment (your `.env` keys are not passed on), a timeout, an output cap, and never inside the app's own folder. A deny-list (`blocked_command_patterns`) refuses deletes, file writes/redirects, format/registry/shutdown, downloads, nested shells and similar before a command can even be approved.
A deny-list can never catch everything, so **read the command on the Approve card before you click**.

## Documents, notes, reports and audit log

| Tool | What it does | Approve needed? |
|---|---|---|
| `create_note` | New `.md` / `.txt` note in `notes.dir` (never overwrites; a number is added if the name exists) | no |
| `extract_text` | Text from pdf (OCR for scans), docx, pptx, xlsx, csv, json, html, text, images (OCR). `engine: whisperer` = cloud engine, see below | local: no. whisperer: **always** |
| `convert_document` | New file in another format, original untouched. Replacing an existing file needs `overwrite=true` + approval | only to overwrite |
| `generate_report` | Saves a `.md`/`.html` report of a file search (`kind: search`) or of the agent's actions (`kind: operations`) in `reports.dir` | no |
| `research_report` | Detailed web research report as PDF + `.md`, see "Web search and research reports" | no |
| `merge_pdf` | Several PDFs -> one new PDF (order as listed, bookmarks kept) | only to overwrite |
| `split_pdf` | One PDF -> several PDFs: by ranges (`1-3;4-6;7-`), every N pages, or one per page. Goes into a new `<name>_split` folder | only to overwrite |
| `remove_pdf_pages` | Copy of a PDF without the pages you list | only to overwrite |
| `extract_pdf_pages` | New PDF with only the pages you list | only to overwrite |
| `organize_pdf` | Reorder / repeat / reverse / drop pages and rotate pages into a new PDF | only to overwrite |
| `scan_to_pdf` | Images (or a folder of images) -> one PDF, one image per page (`fit`, `a4` or `letter`). No OCR | only to overwrite |
| `compress_pdf` | Smaller PDF (`low` / `medium` / `high`): big images are scaled down, streams squeezed | only to overwrite |
| `repair_pdf` | Rebuilds a damaged PDF into a clean new one | only to overwrite |
| `ocr_pdf` | Scanned PDF -> searchable PDF (Tesseract; pages that already have text are kept) | only to overwrite |
| `unlock_pdf` | Removes the password from a PDF into a new file (you must know it). Form only, not available to the chat agent | only to overwrite |
| `protect_pdf` | Locks a PDF with a password (AES-256) into a new file. Form only, not available to the chat agent | only to overwrite |
| `compare_pdf` | Compares the text of two PDFs and writes a report PDF (removed = red, added = green) | only to overwrite |
| `file_operation_history` | Shows the audit log: done, failed, pending, approved, rejected, expired, external_upload | no |
| `get_approved_result` | Lets the assistant see the result of an approved `read_clipboard` / whisperer request (only those tools, `approved_results.tools`) | no |

`notes.dir`, `reports.dir` and `convert.output_dir` must be inside `modify.write_dirs`. If a source folder is not writable, `convert_document` writes into `convert.output_dir`.

**Conversions** (offline): docx -> txt, md | pdf -> txt, md | txt, md -> docx, pdf (md -> txt) | html -> txt | csv <-> json | csv, json <-> xlsx (xlsx -> csv, json) | png, jpg, webp, bmp, gif, tif -> any other image or pdf.
Not supported: docx -> pdf, pptx, old `.doc` (they need Word/LibreOffice). Values in csv/json/xlsx keep their exact text (`convert.infer_types: false`); cells starting with `= + - @` get a leading `'` so Excel cannot run them as formulas.

**Audit log**: every action goes to `logs/audit.jsonl` (`audit:` in `config/config.yaml`): time, tool, status, description, path. Clipboard text and document contents are never written to it.

### LLMWhisperer (optional cloud OCR)

`extract_text(engine="whisperer")` sends the file to [LLMWhisperer](https://unstract.com/llmwhisperer/) (Unstract) for layout-preserving text from scans, forms and tables.
**The file leaves your computer.** It is off by default. To use it: `pip install llmwhisperer-client`, set `LLMWHISPERER_API_KEY` in `.env`, set `whisperer.enabled: true` in `config/config.yaml` (EU users: set `whisperer.base_url`). Every upload needs your Approve click (even if `confirm_actions` is false), only the types in `whisperer.allowed_extensions` are accepted, and each upload is written to the audit log as `external_upload`. Check the current free-tier limits and prices on Unstract's site.

## Allow reading everywhere (temporary)

`allow_all_reads: true` in `config/config.yaml` (or `ALLOW_ALL_READS=true` in `.env`) lets `read_file`, `search_by_content` and `find_duplicates` work anywhere on C: and D:.
Windows system folders, excluded folders and sensitive file names (`.env*`, `*.pem`, `*secret*`, ...) stay blocked, and nothing is ever written or deleted.
Set it to `false` to go back to `approved_read_dirs` only.

## Configuration

Settings come from two places; `.env` values override `config/config.yaml`.

**`.env`** (copy `.env.example`, never commit it):

| Variable | Meaning |
|---|---|
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | Model endpoint and name |
| `APPROVED_READ_DIRS` | Folders whose file contents may be read (separate with `;`) |
| `TAVILY_API_KEY` | Web search and `research_report` |
| `LLMWHISPERER_API_KEY` | Optional cloud OCR |
| `MEMORY_BACKEND`, `MEMORY_RETENTION_DAYS`, `POSTGRES_*` | Chat memory, see "Chat memory" |
| `CONFIRM_CHANGES`, `ALLOW_ALL_WRITES`, `ALLOW_ALL_READS` | Safety switches (see the sections above) |

**`config/config.yaml`** sections: `llm`, `agent`, `filesystem`, `limits`, `content`, `ocr`, `modify`, `computer`, `audit`, `clipboard`, `notes`, `web`, `research`, `reports`, `convert`, `extract`, `whisperer`, `approved_results`.

**`config/docker-compose.yml`**: Postgres for chat memory.

**Important:** set `approved_read_dirs` to the folders whose contents may be read. Default is `C:\Users\CT_USER\Documents`.

## Structure

```
.env.example, requirements.txt   Template for .env (your real .env stays local) and dependencies
config/config.yaml               All settings (model, limits, folders, approval rules)
config/docker-compose.yml        Postgres for chat memory
agent/                           state.py, graph.py (model <-> tools loop), llm.py, memory.py
security/permissions.py          All permission checks
security/settings.py             config/config.yaml + .env loader (ROOT = project folder)
security/guard.py                Approval gate (stages risky actions until you click Approve) + audit log
ui/flask_app.py, ui/templates/, ui/static/   Flask UI        (python -m ui.flask_app)
ui/streamlit_app.py              Streamlit UI                (streamlit run ui/streamlit_app.py)
tools/__init__.py, tools/common.py   Registers every tool; shared result format and helpers
tools/system/                    shell.py, clipboard.py, launch.py, info.py (system info, disk usage)
tools/web/search.py              web_search (Tavily)
tools/audit/history.py           file_operation_history, get_approved_result
tools/documents/files/           walker.py, discovery.py, metadata.py (find/info), file_reader.py, content_search.py (read),
                                 file_edit.py, file_ops.py (create/write/append/rename/copy/move/delete)
tools/documents/pdf/             _common.py (shared flow), organize/ and optimize/ (the PDF tools, one small file each)
tools/documents/others/          json_render.py (JSON / Markdown / text -> formatted PDF), convert.py, notes.py, extract.py, whisperer.py, report.py, research.py
logs/audit.jsonl                 Audit log (created at run time, not in Git)
```

To add a new tool, put the module in the matching folder and list it in `tools/__init__.py`.

## Security model

| Rule | How |
|---|---|
| Names/metadata on C: and D: | `check_search_path` (allowed drives, exclusions) |
| Contents only in approved folders | `check_read_dir` + per-file `check_readable_file` |
| Path traversal | absolute paths only; `realpath` collapses `..` before any check |
| Symlink/junction escapes | walks never follow links/junctions; files re-resolved before opening |
| Sensitive files | name patterns like `.env*`, `*.pem`, `*secret*` are never read |
| Limits | max results, per-file size caps, search timeouts (`config/config.yaml`) |
| Changes only in write folders | `check_write_path`: parent resolved, links never followed, app folder protected, blocked names/extensions |
| Risky changes need you | `security/guard.py` stages them (and writes the audit log); only the UI Approve button runs them |
| LLM can't bypass | the model only calls tools; every check runs in backend code |
| Untrusted content | snippets and web pages are flagged as data; system prompt forbids following them |

`find_duplicates` and `read_file` read file bytes, so they only work inside approved folders. `read_file` also refuses blocked names (`.env*`, `*.pem`, ...), files over `max_read_file_size_mb`, and caps output at `max_read_chars`.

## Git / GitHub

`.gitignore` already keeps `.env`, `logs/`, `*.zip`, `__pycache__/` and `.venv/` out of Git. Only `.env.example` (no secrets) is committed. If a key was ever committed by mistake, create a new key; Git history keeps old files.

```powershell
git add .
git commit -m "Describe your change"
git push
```

## Manual verification (Streamlit sidebar → Manual tools, or the matching feature in the Flask sidebar)

1. **list_directory**: path `C:\Users\CT_USER\Desktop`. Compare with File Explorer. Then try `C:\Windows`: expect an "excluded" error.
2. **search_files**: query `report`, drive All. Hits should contain "report" in any letter case. Try `inv*` for wildcards. Unreadable folders are only counted in the notes.
3. **search_by_extension**: extension `.pdf` with a directory like `C:\Users\CT_USER\Documents`. Try `.txt,.csv` and `name_contains`.
4. **search_by_content**: create a small `.txt` inside an approved folder containing a unique word, then search for it in different letter case. Then try a directory outside approved folders: expect "Content access denied".
5. **read_file**: full path of a `.pdf`, `.docx`, `.csv` or `.json` inside an approved folder (PDF: try pages `1-2`, and a scanned PDF if Tesseract is installed; CSV shows a table). A `.exe`, or a file outside approved folders, gives a clear error.
6. **get_file_info**: full path of any file. Compare size and modified date with Properties. Pass a folder: expect a clear error.
7. **find_duplicates**: copy any file inside an approved folder, run on that folder: expect a group with both paths. Confirm both files still exist afterwards.
8. **create_file / write_file / append_file / replace_text**: in a write folder, create `t.txt` with some text. Overwrite it (Approve card appears, file unchanged until you click), append a line, replace a word; open the file to confirm each change. Try a path outside the write folders: expect "Changes are not allowed here".
9. **rename_file / copy_file / move_file**: rename `t.txt` to `t2.txt`, copy it into a new folder, move the copy back. Rename needs Approve; copy to a new name does not. Try renaming to `x.exe`: expect a refusal.
10. **create_directory / delete_file / delete_directory**: create a folder, put a file in it, delete the file (Approve, then check the Recycle Bin), delete the folder (non-empty needs `recursive`). Try deleting `C:\Users\CT_USER\Documents` itself: expect "protected folder".
11. **web_search / research_report**: set `TAVILY_API_KEY`, then run `web_search` with a short query, and `research_report` with a topic and `pages` 3. Open the saved PDF in the notes folder and check the page count and the sources list.

Chat examples: "read C:\Users\CT_USER\Documents\report.pdf and summarize it", "list C:\Users\CT_USER\Desktop", "find files named invoice", "find all .pdf files on D:",
"search my Documents for 'budget'", "find duplicates in my Documents", "research the best budget laptops and give me a PDF report".

Use **Clear chat history** to reset the conversation.

## PDF tools (Organize PDF)

Code lives in `tools/documents/pdf/organize/` and `optimize/` (one small file per tool, shared rules in `pdf/_common.py`; the UI forms are built from each tool's signature, so a new tool needs no UI code). In the Flask UI open **PDF tools** in the sidebar, or just ask the agent ("merge a.pdf and b.pdf", "remove pages 2-4 from report.pdf").
Originals are never changed. Results are new files next to the source (or at `destination`) and must be inside `modify.write_dirs`. Limits are in the `pdf_tools:` section of `config/config.yaml`. Password-protected PDFs are refused.

## JSON -> PDF

`convert_document` turns a `.json` file into a formatted PDF (`target_format: pdf`): objects become two-column tables, lists of objects become real tables, nesting is kept. Markdown and text files are converted the same way (`.md` / `.txt` -> `.pdf`).
Code: `tools/documents/others/json_render.py` (about 100 lines; the libraries `json2html`, `markdown-it-py` and `pymupdf` do the work). Look and feel is the `CSS` block at the top of that file.
Try it from a terminal: `python -m tools.documents.others.json_render input.json output.pdf`
Install the new libraries once: `python -m pip install -r requirements.txt`