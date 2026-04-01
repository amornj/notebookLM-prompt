# CLAUDE.md — notebooklm-study

## Project Overview

CLI tool that generates study guides + Anki flashcards from Google NotebookLM notebooks.
Single Python script (`study.py`) + shell wrapper (`notebooklm-study`).

**Entry point:** `notebooklm-study "Notebook Name"` → runs study.py

---

## Architecture

```
notebooklm-study (shell wrapper)
    └── study.py (Python, all logic)
            ├── resolve_notebook()      → nlm notebook list --json
            ├── run_study_prompts()     → nlm query notebook ×7
            ├── wait_for_flashcards()   → nlm flashcards create + poll
            ├── download_flashcards()   → nlm download flashcards -f json
            ├── parse_markdown_flashcards()  → fallback if JSON fails
            ├── build_markdown()        → assemble .md file
            ├── convert_to_pdf()        → md_to_pdf.py import or subprocess
            ├── email_pdf()             → gog gmail send
            └── import_to_anki()        → AnkiConnect HTTP API (localhost:8765)
```

---

## Key Files

| File | Purpose |
|------|---------|
| `study.py` | Main implementation — all logic |
| `notebooklm-study` | Shell wrapper (execs study.py) |
| `requirements.txt` | Python deps: fpdf2, requests |
| `README.md` | User docs |
| `CLAUDE.md` | This file |
| `PLAN.md` | Original planning notes (can be deleted) |

---

## NLM CLI Commands Used

```bash
# List notebooks → resolve name to ID
nlm notebook list --json

# Query a notebook with a prompt → returns JSON {value: {answer: "..."}}
nlm query notebook <id> <question> --timeout 120

# Create flashcards (async, starts generation)
nlm flashcards create <notebook_id> --confirm

# Poll flashcard status
nlm list artifacts <notebook_id>
# → [{id, type: "flashcards", status: "in_progress"|"completed"|"failed"}]

# Download flashcards as JSON
nlm download flashcards <notebook_id> -f json -o /tmp/cards.json
# → {title, cards: [{front, back}, ...]}

# Fallback: download as markdown
nlm download flashcards <notebook_id> -f markdown -o /tmp/cards.md
```

**JSON format for flashcards** (preferred):
```json
{
  "title": "Bookname Flashcards",
  "cards": [
    {"front": "Question text?", "back": "Answer text."},
    ...
  ]
}
```

**Markdown format** (fallback parse with regex):
```
## Card 1
**Front:** Question?
**Back:** Answer.
```

---

## AnkiConnect Integration

- URL: `http://localhost:8765`
- API version: 6
- Import flow:
  1. `createDeck` → `Amorn` (parent, ignore if exists)
  2. `createDeck` → `Amorn::NotebookName` (subdeck)
  3. `modelNames` → find "Basic" model
  4. `modelFieldNames` → get field names for the model
  5. `addNote` × N cards (Basic model, Front/Back fields, tag: `notebooklm-study`)

**Deck naming:** `Amorn::<NotebookTitle>` (spaces OK, special chars stripped by slugify)

---

## PDF Generation

Uses `/Users/home/.openclaw/workspace/md_to_pdf.py`:
- Tries `importlib.util` import first (if it exposes `md_to_pdf(src, dst)`)
- Falls back to `subprocess.run([python3, md_to_pdf_path, src, dst])`
- Output goes to `~/Downloads/<slug>-study-prompts.pdf`

---

## Error Handling Policy

| Failure | Behavior |
|---------|----------|
| Notebook not found | `print` + `sys.exit(1)` — hard fail |
| NLM query fails | Retry once at 180s. If still fails → insert `[Failed to generate]` |
| Flashcard creation fails | Warn, continue without flashcards |
| PDF generation fails | Fall back to emailing `.md` file |
| Anki import fails | Warn, do NOT fail the run |
| `gog gmail send` fails | Warn, print path to PDF for manual handling |

---

## Development

```bash
# Test with a quick prompt (no email, no anki)
python3 study.py "Manufacturing Consent" --no-email --no-anki --no-flashcards

# Full test
python3 study.py "Manufacturing Consent"
```

---

## Adding New Prompts

Edit the `PROMPTS` list in `study.py`:

```python
PROMPTS = [
    ("Prompt Title", "Full prompt text..."),
    # ...
]
```

---

## Flashcard Parsing Notes

- **JSON is preferred** — clean `{front, back}` structure
- **Markdown fallback** is needed when `nlm download flashcards` returns HTML or when JSON parse fails
- Markdown regex: `\*\*Front:\*\*` and `\*\*Back:\*\*` markers per card
- Cards are separated by `---` or `## Card N` headers

---

## Environment Assumptions

- NLM auth is already configured (`nlm login` done)
- Gmail auth via `gog` is already configured
- AnkiConnect running on localhost:8765
- `~/Downloads/` is writable
- `~/.openclaw/workspace/md_to_pdf.py` exists
