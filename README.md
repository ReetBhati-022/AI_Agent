# Personal AI Agent (LangGraph + Streamlit)

Windows file-system assistant (search, read, edit with approval, documents, notes, reports). A LangGraph agent (model: `gpt-oss:120b-cloud` via your Ollama endpoint)
picks from its tools: search/read tools, 10 file-changing tools (create, write, append, replace, rename, copy, move, mkdir, delete), 7 computer tools (system info, disk usage, current folder, PowerShell command, open in Explorer, open file, clipboard), 4 document tools (notes, text extraction, conversion, reports), an audit log and optional Tavily `web_search`.
Risky changes (overwrite, append, replace, rename, move, delete) only happen after **you click Approve** in the app; deleted items go to the Recycle Bin.

## Setup (PowerShell)

```powershell
conda activate ai-agent
cd   C:\Users\CT_USER\Desktop\starting_code\personal_ai_agent
pip install -r requirements.txt
copy .env.example .env      # then edit: set TAVILY_API_KEY, APPROVED_READ_DIRS, ...
streamlit run ui/streamlit_app.py
```

If `conda activate` fails in PowerShell, run `conda init powershell` once and reopen the terminal.
Make sure Ollama is running (`ollama list` should show `gpt-oss:120b-cloud`; cloud models need `ollama signin`).
Use **Check model connection** in the sidebar to verify the endpoint.


## Flask UI (new)

`ui/flask_app.py` is a second front end for the same agent and tools: purple / blue / pink sidebar on the left with every feature
(Find files, Read & search text, Documents, Web search, Edit files, Computer, History & audit), recent chats, drive filter and
status; chat workspace on the right. Approve / Reject cards work exactly as in Streamlit.

```powershell
pip install -r requirements.txt
python -m ui.flask_app       # http://127.0.0.1:5000   (FLASK_PORT / FLASK_HOST to change)
```

Files: `ui/flask_app.py`, `ui/templates/index.html`, `ui/static/style.css`, `ui/static/app.js`. The Streamlit `ui/streamlit_app.py` still works unchanged.

## PDF reading and OCR (optional)

`read_file` reads digital PDFs with PyMuPDF. Scanned PDF pages (images, no text) are read with OCR **only if Tesseract is installed**:
install it from https://github.com/UB-Mannheim/tesseract/wiki (tick the languages you need) and add it to `PATH`, then reopen PowerShell.
Settings are under `ocr:` in `config.yaml` (language, dpi, max OCR pages per call, or `enabled: false`). Without Tesseract, scanned pages
simply return no text with a clear note. PyMuPDF is AGPL-licensed (fine for personal use).

## Changing files (create / write / delete ...)

| Setting (config.yaml `modify:`) | Meaning |
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

Settings are under `computer:` in `config.yaml`. `confirm_actions` is separate from `modify.confirm_changes`, so turning the modify gate off never makes commands run by themselves.
`run_shell_command` runs with a minimal environment (your `.env` keys are not passed on), a timeout, an output cap, and never inside the app's own folder. A deny-list (`blocked_command_patterns`) refuses deletes, file writes/redirects, format/registry/shutdown, downloads, nested shells and similar before a command can even be approved.
A deny-list can never catch everything, so **read the command on the Approve card before you click**.

## Documents, notes, reports and audit log

| Tool | What it does | Approve needed? |
|---|---|---|
| `create_note` | New `.md` / `.txt` note in `notes.dir` (never overwrites; a number is added if the name exists) | no |
| `extract_text` | Text from pdf (OCR for scans), docx, pptx, xlsx, csv, json, html, text, images (OCR). `engine: whisperer` = cloud engine, see below | local: no. whisperer: **always** |
| `convert_document` | New file in another format, original untouched. Replacing an existing file needs `overwrite=true` + approval | only to overwrite |
| `generate_report` | Saves a `.md`/`.html` report of a file search (`kind: search`) or of the agent's actions (`kind: operations`) in `reports.dir` | no |
| `file_operation_history` | Shows the audit log: done, failed, pending, approved, rejected, expired, external_upload | no |
| `read_clipboard` | Reads clipboard **text** (PowerShell `Get-Clipboard`) | **yes, always** |
| `get_approved_result` | Lets the assistant see the result of an approved `read_clipboard` / whisperer request (only those tools, `approved_results.tools`) | no |

`notes.dir`, `reports.dir` and `convert.output_dir` must be inside `modify.write_dirs`. If a source folder is not writable, `convert_document` writes into `convert.output_dir`.

**Conversions** (offline): docx -> txt, md | pdf -> txt, md | txt, md -> docx, pdf (md -> txt) | html -> txt | csv <-> json | csv, json <-> xlsx (xlsx -> csv, json) | png, jpg, webp, bmp, gif, tif -> any other image or pdf.
Not supported: docx -> pdf, pptx, old `.doc` (they need Word/LibreOffice). Values in csv/json/xlsx keep their exact text (`convert.infer_types: false`); cells starting with `= + - @` get a leading `'` so Excel cannot run them as formulas.

**Audit log**: every action goes to `logs/audit.jsonl` (`audit:` in `config.yaml`): time, tool, status, description, path. Clipboard text and document contents are never written to it.

### LLMWhisperer (optional cloud OCR)

`extract_text(engine="whisperer")` sends the file to [LLMWhisperer](https://unstract.com/llmwhisperer/) (Unstract) for layout-preserving text from scans, forms and tables.
**The file leaves your computer.** It is off by default. To use it: `pip install llmwhisperer-client`, set `LLMWHISPERER_API_KEY` in `.env`, set `whisperer.enabled: true` in `config.yaml` (EU users: set `whisperer.base_url`). Every upload needs your Approve click (even if `confirm_actions` is false), only the types in `whisperer.allowed_extensions` are accepted, and each upload is written to the audit log as `external_upload`. Check the current free-tier limits and prices on Unstract's site.

## Allow reading everywhere (temporary)

`allow_all_reads: true` in `config.yaml` (or `ALLOW_ALL_READS=true` in `.env`) lets `read_file`, `search_by_content` and `find_duplicates` work anywhere on C: and D:.
Windows system folders, excluded folders and sensitive file names (`.env*`, `*.pem`, `*secret*`, ...) stay blocked, and nothing is ever written or deleted.
Set it to `false` to go back to `approved_read_dirs` only.

## Configuration

- `config/config.yaml`: model, endpoint, limits, exclusions, approved read folders.
- `config/docker-compose.yml`: Postgres for chat memory. Run from the project folder: `docker compose --env-file .env -f config/docker-compose.yml up -d`.
- `.env` (optional): `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `APPROVED_READ_DIRS` (separate folders with `;`).
- **Important:** set `approved_read_dirs` to the folders whose contents may be read. Default is `C:\Users\CT_USER\Documents`.

## Structure

```
.env, requirements.txt           Secrets and dependencies (project root)
config/config.yaml               All settings (model, limits, folders, approval rules)
config/docker-compose.yml        Postgres for chat memory
agent/                           state.py, graph.py (model <-> tools loop), llm.py, memory.py
security/permissions.py          All permission checks
security/settings.py             config.yaml + .env loader (ROOT = project folder)
security/guard.py               Approval gate (stages risky actions until you click Approve) + audit log
ui/flask_app.py, ui/templates/, ui/static/   Flask UI        (python -m ui.flask_app)
ui/streamlit_app.py              Streamlit UI                (streamlit run ui/streamlit_app.py)
tools/__init__.py, tools/common.py   Registers every tool; shared result format and helpers
tools/system/                    shell.py, clipboard.py, launch.py, info.py (system info, disk usage)
tools/web/search.py              web_search (Tavily)
tools/audit/history.py           file_operation_history, get_approved_result
tools/documents/files/           walker.py, discovery.py, metadata.py (find/info), file_reader.py, content_search.py (read),
                                 file_edit.py, file_ops.py (create/write/append/rename/copy/move/delete)
tools/documents/pdf/             pdf_render.py (Markdown -> formatted PDF)
tools/documents/others/          convert.py, notes.py, extract.py, whisperer.py, report.py, research.py
logs/audit.jsonl                 Audit log
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
| Limits | max results, per-file size caps, search timeouts (config.yaml) |
| Changes only in write folders | `check_write_path`: parent resolved, links never followed, app folder protected, blocked names/extensions |
| Risky changes need you | `security/guard.py` stages them (and writes the audit log); only the UI Approve button runs them |
| LLM can't bypass | the model only calls tools; every check runs in backend code |
| Untrusted content | snippets are flagged as data; system prompt forbids following them |

`find_duplicates` and `read_file` read file bytes, so they only work inside approved folders. `read_file` also refuses blocked names (`.env*`, `*.pem`, ...), files over `max_read_file_size_mb`, and caps output at `max_read_chars`.

## Manual verification (Streamlit sidebar → Manual tools)

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

Web search: set `TAVILY_API_KEY`, then use the `web_search` tool or ask "search the web for ...".

Chat examples: "read C:\Users\CT_USER\Documents\report.pdf and summarize it", "list C:\Users\CT_USER\Desktop", "find files named invoice", "find all .pdf files on D:",
"search my Documents for 'budget'", "find duplicates in my Documents".

Use **Clear chat history** to reset the conversation.