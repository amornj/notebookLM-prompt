# notebooklm-study

Generate a full study guide (Markdown + PDF), Audio Overview, 20-MCQ Quiz, and Anki flashcards from a Google NotebookLM notebook — in one command.

```
$ notebooklm-study "The Denial of Death"
```

---

## What Gets Generated

| Output | Destination | Content |
|--------|-------------|---------|
| 📄 Obsidian Markdown | `~/projects/obsidian/journal/` | **All 7 prompts** including MCQ + artifact links |
| 📕 PDF | `~/Downloads/` | **6 prompts** (no MCQ/flashcard table), hint at end |
| 🎧 Audio Overview | `~/Downloads/` | Podcast-style summary from NotebookLM |
| 📝 Quiz (20 MCQ) | `~/projects/obsidian/journal/` | Full quiz as markdown |
| 🃏 Flashcards | Anki `Amorn::<Notebook>` | All cards imported |

---

## The 7 Study Prompts

| # | Title | Obsidian | PDF |
|---|-------|----------|-----|
| 1 | Big Picture First | ✅ | ✅ |
| 2 | Chapter-by-Chapter Breakdown | ✅ | ✅ |
| 3 | Key Terms & Definitions | ✅ | ✅ |
| 4 | Exam-Style Questions (20 MCQ) | ✅ | ❌ *(in NotebookLM)* |
| 5 | Hard Concepts Simplified | ✅ | ✅ |
| 6 | Connections & Themes | ✅ | ✅ |
| 7 | Testable Topics & Common Traps | ✅ | ✅ |

---

## Installation

```bash
# 1. Link the CLI wrapper
chmod +x notebooklm-study
ln -sf ~/projects/notebookLM-prompt/notebooklm-study /usr/local/bin/

# 2. Install Python dependencies
pip install -fpdf2 requests

# 3. Make sure these are on your PATH
nlm        # NotebookLM CLI
gog        # Gmail CLI

# 4. AnkiConnect addon installed + Anki running
```

---

## Usage

```bash
# Full run (audio + quiz + flashcards + PDF + email + Anki)
notebooklm-study "The Denial of Death"

# Skip Anki (faster — still gets audio + quiz + PDF)
notebooklm-study "Manufacturing Consent" --no-anki

# Skip email
notebooklm-study "Manufacturing Consent" --no-email

# Skip all studio artifacts (audio/quiz/flashcards — just study prompts)
notebooklm-study "Manufacturing Consent" --no-flashcards
```

---

## Workflow

```
notebooklm name
    ├── Resolve → notebook ID
    ├── 7 Study Prompts (nlm query notebook)
    │       └── All 7 saved to Obsidian journal
    │
    ├── Trigger 3 Studio Artifacts in parallel
    │       ├── 🎧 Audio Overview
    │       ├── 📝 Quiz (20 MCQ)
    │       └── 🃏 Flashcards
    │
    ├── Poll & Download
    │       ├── Audio .m4a → ~/Downloads/
    │       ├── Quiz markdown → Obsidian journal
    │       └── Flashcards → Anki (Amorn::NotebookName)
    │
    ├── PDF (6 prompts only)
    │       └── Emailed to amornj@library.readwise.io
    │
    └── Summary
```

---

## Requirements

- Python 3.10+
- `nlm` CLI (`pip install notebooklm-mcp-cli`)
- `gog` CLI (for Gmail)
- AnkiConnect addon + Anki running
- `/Users/home/.openclaw/workspace/md_to_pdf.py`
- Python packages: `fpdf2`, `requests`

---

## Troubleshooting

**"Notebook not found"** → Check exact name: `nlm notebook list`

**Anki import fails** → Make sure Anki is running with AnkiConnect installed (port 8765)

**NLM queries timeout** → Use `--timeout 180` for slower notebooks

**Studio artifacts take long** → Polling runs up to 6 minutes; use `--no-flashcards` to skip
