# notebooklm-study

Generate a full study guide (Markdown + PDF) and Anki flashcards from a Google NotebookLM notebook — in one command.

```
$ notebooklm-study "The Denial of Death"
```

**What it does:**
1. Resolves your notebook name to an ID via `nlm` CLI
2. Runs 7 study prompts against the notebook (big-picture, chapter breakdown, key terms, exam questions, hard concepts, themes, testable traps)
3. Creates + downloads flashcards from NotebookLM
4. Assembles everything into a Markdown study guide
5. Converts to PDF via `md_to_pdf.py`
6. Emails the PDF to your Readwise Reader inbox
7. Imports flashcards into Anki under `Amorn::<NotebookName>`

---

## Installation

```bash
# 1. Install the CLI wrapper
chmod +x notebooklm-study
ln -sf "$(pwd)/notebooklm-study" /usr/local/bin/notebooklm-study

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Make sure these are on your PATH
nlm        # NotebookLM CLI  → ~/.local/bin/nlm
gog        # Gmail CLI       → check 'which gog'

# 4. Make sure AnkiConnect is installed and Anki is running
```

---

## Usage

```bash
# Basic — full run (prompts + flashcards + PDF + email + Anki)
notebooklm-study "The Denial of Death"

# Skip Anki (just PDF + email)
notebooklm-study "Manufacturing Consent" --no-anki

# Skip email (just PDF + Anki)
notebooklm-study "Manufacturing Consent" --no-email

# Skip PDF (just markdown + Anki)
notebooklm-study "Manufacturing Consent" --no-pdf

# Skip flashcard generation (faster — just study prompts)
notebooklm-study "Manufacturing Consent" --no-flashcards
```

---

## The 7 Study Prompts

| # | Title | What it asks |
|---|-------|-------------|
| 1 | Big Picture First | 10 most important concepts for an exam, in simple terms |
| 2 | Chapter-by-Chapter Breakdown | Key ideas, definitions, 2-3 exam Qs per chapter |
| 3 | Key Terms & Definitions | Full glossary / study list |
| 4 | Exam-Style Questions | 20 MCQ + short answer + essay questions |
| 5 | Hard Concepts Simplified | Most confusing ideas, explained simply |
| 6 | Connections & Themes | How ideas relate; cause-effect; big themes |
| 7 | Testable Topics & Common Traps | Most examable content + student pitfalls |

---

## Output Files

- **Markdown**: `~/Library/.../Tmp/notebooklm-study/<name>-study-prompts.md`
- **PDF**: `~/Downloads/<name>-study-prompts.pdf`
- **Email**: PDF sent to `amornj@library.readwise.io`
- **Anki**: Flashcards imported to `Amorn::<NotebookName>`

---

## Requirements

- Python 3.10+
- `nlm` CLI (`~/.local/bin/nlm`, installed via `pip install notebooklm-mcp-cli`)
- `gog` CLI (for Gmail)
- AnkiConnect addon installed in Anki + Anki running
- `/Users/home/.openclaw/workspace/md_to_pdf.py` (used for PDF generation)
- Python packages: `fpdf2`, `requests`

---

## Troubleshooting

**"Notebook not found"**
→ Check exact notebook name with `nlm notebook list`

**"nlm: command not found"**
→ Run `ln -sf ~/.local/bin/nlm /usr/local/bin/nlm`

**Anki import fails**
→ Make sure Anki is running with AnkiConnect installed (port 8765)

**PDF generation fails**
→ The script will email the `.md` file instead as a fallback

**NLM queries timeout**
→ Default timeout is 120s; use `--timeout 180` for slower notebooks
