# notebooklm-study — Implementation Plan

## Overview

A CLI tool that takes a NotebookLM notebook name, runs 7 study prompts via `nlm`, builds a Markdown study guide, converts to PDF, emails to Readwise, and creates Anki flashcards.

**Usage:** `notebooklm-study "The Denial of Death"`

---

## Files to Create

```
/Users/home/projects/notebookLM-prompt/
├── notebooklm-study          # Shell wrapper (entry point, goes on PATH)
├── study.py                  # Main Python script (all logic)
├── requirements.txt          # Python dependencies
├── README.md                 # User documentation
├── CLAUDE.md                 # Agent instructions
└── PLAN.md                   # This file
```

---

## 1. Shell Wrapper: `notebooklm-study`

```bash
#!/usr/bin/env bash
# Thin wrapper — delegates everything to study.py
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/study.py" "$@"
```

**Installation:** `chmod +x notebooklm-study && ln -sf $(pwd)/notebooklm-study /usr/local/bin/`

---

## 2. Python Script: `study.py`

### Architecture

```
main()
├── resolve_notebook(name) → (id, title)       # nlm notebook list --json
├── run_study_prompts(id) → [7 responses]       # nlm notebook query (sequential)
├── generate_flashcards(id) → [(front, back)]   # nlm flashcards create + download
├── build_markdown(title, responses, cards) → md_path
├── convert_to_pdf(md_path) → pdf_path          # reuse md_to_pdf.py
├── email_to_readwise(pdf_path, title)           # gog gmail send
└── import_to_anki(title, cards)                 # AnkiConnect via requests
```

### Key Implementation Details

#### 2a. Notebook Resolution
```python
def resolve_notebook(name: str) -> tuple[str, str]:
    """Run `nlm notebook list --json`, fuzzy-match name, return (id, title)."""
    result = subprocess.run(
        ["nlm", "notebook", "list", "--json"],
        capture_output=True, text=True, timeout=30
    )
    notebooks = json.loads(result.stdout)
    # Case-insensitive exact match first, then substring match
    for nb in notebooks:
        if nb["title"].strip().lower() == name.strip().lower():
            return nb["id"], nb["title"].strip()
    for nb in notebooks:
        if name.strip().lower() in nb["title"].strip().lower():
            return nb["id"], nb["title"].strip()
    raise SystemExit(f"Notebook not found: '{name}'")
```

#### 2b. Study Prompts (Sequential, with retry)

The 7 fixed prompts:

| # | Title | Prompt |
|---|-------|--------|
| 1 | Big Picture First | "Summarize this book into the 10 most important concepts I must understand for an exam. Explain each in simple terms and include why it matters." |
| 2 | Chapter-by-Chapter Breakdown | "Give a structured summary of each chapter with key ideas, definitions, and 2-3 likely exam questions per chapter." |
| 3 | Key Terms & Definitions | "Extract all key terms, concepts, and definitions from this book and organize them into a clean study list." |
| 4 | Exam-Style Questions | "Generate 20 high-quality exam-style questions (mix of multiple choice, short answer, and essay) based only on this book." |
| 5 | Hard Concepts Made Simple | "Identify the most difficult or confusing concepts in this book and explain them in the simplest way possible." |
| 6 | Connections & Themes | "Explain how the main ideas in this book connect to each other. Show relationships, cause-effect, and big themes." |
| 7 | Testable Topics & Traps | "Based on this content, what are the most testable topics and common traps students might fall into?" |

```python
def run_prompt(notebook_id: str, prompt: str, timeout: int = 120) -> str:
    """Run a single nlm query. Retry once with 180s timeout on failure."""
    try:
        result = subprocess.run(
            ["nlm", "notebook", "query", notebook_id, prompt, "--timeout", str(timeout)],
            capture_output=True, text=True, timeout=timeout + 30
        )
        data = json.loads(result.stdout)
        return data["value"]["answer"]
    except Exception:
        # Retry with longer timeout
        result = subprocess.run(
            ["nlm", "notebook", "query", notebook_id, prompt, "--timeout", "180"],
            capture_output=True, text=True, timeout=210
        )
        data = json.loads(result.stdout)
        return data["value"]["answer"]
```

**Critical discovery:** `nlm notebook query` returns JSON by default:
```json
{
  "value": {
    "answer": "...markdown text with [1] citation refs...",
    "conversation_id": "...",
    "sources_used": [...],
    "citations": {...}
  }
}
```
Extract `.value.answer` — it contains markdown-formatted text.

#### 2c. Flashcard Generation & Download

**Discovery from testing:**
- `nlm flashcards create <id> --confirm` → starts async generation, returns artifact ID
- `nlm studio status <id>` → poll for completion (status: "in_progress" → "completed")
- `nlm download flashcards <id> -f json -o <path>` → downloads completed flashcards

**JSON structure:**
```json
{
  "title": "Becker Flashcards",
  "cards": [
    {"front": "Question text?", "back": "Answer text."},
    ...
  ]
}
```

```python
def generate_flashcards(notebook_id: str) -> list[dict]:
    """Create and download flashcards. Returns list of {front, back} dicts."""
    # Step 1: Trigger creation
    subprocess.run(
        ["nlm", "flashcards", "create", notebook_id, "--confirm"],
        capture_output=True, text=True, timeout=120
    )
    
    # Step 2: Poll for completion (max 5 min)
    for _ in range(30):  # 30 × 10s = 5 minutes
        time.sleep(10)
        result = subprocess.run(
            ["nlm", "studio", "status", notebook_id],
            capture_output=True, text=True, timeout=30
        )
        artifacts = json.loads(result.stdout)
        flashcard_artifacts = [a for a in artifacts if a["type"] == "flashcards"]
        if flashcard_artifacts and flashcard_artifacts[-1]["status"] == "completed":
            break
    
    # Step 3: Download
    tmp_path = tempfile.mktemp(suffix=".json")
    subprocess.run(
        ["nlm", "download", "flashcards", notebook_id, "-f", "json", "-o", tmp_path],
        capture_output=True, text=True, timeout=30
    )
    with open(tmp_path) as f:
        data = json.load(f)
    os.unlink(tmp_path)
    return data.get("cards", [])
```

#### 2d. Markdown Assembly

Output format:
```markdown
# The Denial of Death — Study Prompts

## Prompt 1: Big Picture First
[response from nlm]

## Prompt 2: Chapter-by-Chapter Breakdown
[response]

...all 7...

## Flashcards

| # | Front | Back |
|---|-------|------|
| 1 | Question? | Answer. |
| ... | ... | ... |
```

Saved to: `/tmp/notebooklm-study/<notebook-name>-study-prompts.md`

#### 2e. PDF Conversion

**Reuse `/Users/home/.openclaw/workspace/md_to_pdf.py`** — it exposes `md_to_pdf(input_path, output_path)` as a callable function.

```python
import sys
sys.path.insert(0, "/Users/home/.openclaw/workspace")
from md_to_pdf import md_to_pdf

pdf_path = f"/Users/home/Downloads/{sanitized_name}-study-prompts.pdf"
md_to_pdf(md_path, pdf_path)
```

**Fallback:** If import fails, call as subprocess:
```python
subprocess.run(["python3", "/Users/home/.openclaw/workspace/md_to_pdf.py", md_path, pdf_path])
```

#### 2f. Email to Readwise

```python
def email_to_readwise(attachment_path: str, notebook_title: str):
    subprocess.run([
        "gog", "gmail", "send",
        "--to", "amornj@library.readwise.io",
        "--subject", f"{notebook_title} Study Prompts",
        "--body", "Save to library",
        "--attach", attachment_path,
        "--force"  # skip confirmation
    ], check=True, timeout=60)
```

**Note:** `--force` / `-y` flag skips the confirmation prompt (discovered from `gog gmail send --help`).

#### 2g. Anki Flashcard Import

Uses `requests` to talk to AnkiConnect (localhost:8765).

```python
ANKI_URL = "http://localhost:8765"

def import_to_anki(notebook_title: str, cards: list[dict]):
    """Create subdeck and add flashcards."""
    deck_name = f"Amorn::{notebook_title}"
    
    # Create subdeck (AnkiConnect action: createDeck)
    requests.post(ANKI_URL, json={
        "action": "createDeck",
        "version": 6,
        "params": {"deck": deck_name}
    })
    
    # Add notes (AnkiConnect action: addNotes)
    notes = [{
        "deckName": deck_name,
        "modelName": "Basic",
        "fields": {"Front": card["front"], "Back": card["back"]},
        "options": {"allowDuplicate": False},
        "tags": ["notebooklm-study"]
    } for card in cards]
    
    response = requests.post(ANKI_URL, json={
        "action": "addNotes",
        "version": 6,
        "params": {"notes": notes}
    })
    result = response.json()
    added = sum(1 for r in result.get("result", []) if r is not None)
    print(f"  Added {added}/{len(cards)} flashcards to '{deck_name}'")
```

**Why direct AnkiConnect instead of OpenClaw tools?** The Python script runs standalone outside OpenClaw, so it uses the AnkiConnect HTTP API directly. This is more portable and works in any environment where AnkiConnect is running.

**Existing decks pattern:** `Amorn::Manufacturing Consent`, `Amorn::Myostatin inhibitors` — our naming (`Amorn::The Denial of Death`) is consistent.

---

## 3. Error Handling Strategy

| Failure | Behavior |
|---------|----------|
| Notebook not found | Print error, exit 1 |
| `nlm query` fails | Retry once with 180s timeout. If still fails, skip that prompt with "[Failed to generate]" |
| `nlm flashcards create` fails | Log warning, continue without flashcards |
| PDF generation fails | Email the `.md` file instead |
| Anki import fails | Log warning, don't fail the run |
| `gog gmail send` fails | Log warning, print path to PDF for manual handling |

---

## 4. CLI Arguments

```
notebooklm-study "Notebook Name"    # Full run
notebooklm-study "Name" --no-anki   # Skip Anki import
notebooklm-study "Name" --no-email  # Skip email
notebooklm-study "Name" --no-pdf    # Skip PDF, just build markdown
notebooklm-study "Name" --no-flashcards  # Skip NLM flashcard generation
```

Uses `argparse` with these optional flags.

---

## 5. Output Summary

At the end, the script prints:

```
✅ notebooklm-study complete for "The Denial of Death"
   📄 Markdown: /tmp/notebooklm-study/the-denial-of-death-study-prompts.md
   📕 PDF:      /Users/home/Downloads/the-denial-of-death-study-prompts.pdf
   📧 Emailed:  amornj@library.readwise.io
   🃏 Anki:     60 flashcards → Amorn::The Denial of Death
```

---

## 6. Dependencies

### Python (requirements.txt)
```
fpdf2>=2.7.0
requests>=2.28.0
```

### System
- `nlm` CLI (installed at `~/.local/bin/nlm`)
- `gog` CLI (installed globally)
- AnkiConnect running on localhost:8765
- Python 3.10+

---

## 7. Timing Estimates

| Step | Expected Duration |
|------|-------------------|
| Notebook resolution | 2-5s |
| Each nlm query (×7) | 30-90s each, ~5-8 min total |
| Flashcard creation | 30-90s creation + polling |
| Flashcard download | 2-5s |
| Markdown assembly | instant |
| PDF conversion | 1-3s |
| Email send | 2-5s |
| Anki import | 1-2s |
| **Total** | **~7-12 minutes** |

---

## 8. Testing Steps

### Unit Tests (manual)
1. **Notebook resolution:** `python3 -c "from study import resolve_notebook; print(resolve_notebook('Denial'))"` → should return ID + title
2. **Single prompt:** Run prompt 1 only, verify markdown output
3. **Flashcard download:** Verify JSON parsing from `nlm download flashcards`
4. **PDF generation:** Generate from sample markdown
5. **Anki import:** Import 3 test flashcards, verify in Anki

### Integration Test
```bash
# Dry run (no email, no anki)
notebooklm-study "The Denial of Death" --no-email --no-anki

# Full run
notebooklm-study "The Denial of Death"
```

### Edge Cases to Test
- Notebook name with special characters
- Notebook name that doesn't exist → should exit 1
- NLM auth expired → should fail gracefully
- AnkiConnect not running → should warn and continue
- Duplicate flashcards → should skip (allowDuplicate: false)

---

## 9. Future Enhancements (Not in v1)

- `--difficulty easy|medium|hard` flag for flashcard generation
- `--focus "specific topic"` flag for flashcard generation
- Progress bar / spinner during long nlm queries
- Cache responses to avoid re-running prompts
- Support multiple notebooks in one run
- `--output-dir` flag for custom output location
