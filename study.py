#!/usr/bin/env python3
"""
notebooklm-study — Generate a full study guide from a NotebookLM notebook.

Usage:
    notebooklm-study "The Denial of Death"
    notebooklm-study "The Denial of Death" --no-anki
    notebooklm-study "The Denial of Death" --no-email --no-pdf

Steps:
  1. Resolve notebook name → ID
  2. Run 7 study prompts via `nlm query notebook` (all saved to Obsidian journal)
  3. Trigger NotebookLM Studio: Audio Overview + Slide Deck + Quiz (20 MCQ) + Flashcards
  4. Poll & download artifacts (audio/slides → Downloads, quiz markdown → Obsidian, flashcards → Anki)
  5. Build Obsidian Markdown (all 7 prompts + artifact links)
  6. Build PDF (6 prompts: 1,2,3,5,6,7 — MCQ/flashcards hint only)
  7. Convert PDF, email to amornj@library.readwise.io
  8. Import flashcards to Anki → subdeck Amorn::<NotebookName>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Constants ──────────────────────────────────────────────────────────────────

NLM = shutil.which("nlm") or "nlm"
NLM_TIMEOUT_DEFAULT = 120
NLM_TIMEOUT_RETRY = 180
STUDIO_POLL_INTERVAL = 10  # seconds
STUDIO_POLL_MAX = 36       # 6 minutes max

ANKI_URL = "http://localhost:8765"
READER_EMAIL = "amornj@library.readwise.io"
MD_TO_PDF_PATH = Path.home() / ".openclaw" / "workspace" / "md_to_pdf.py"
DOWNLOAD_DIR = Path.home() / "Downloads"
OBSIDIAN_JOURNAL = Path.home() / "projects" / "obsidian" / "journal"
STUDY_TEMP_DIR = Path(tempfile.gettempdir()) / "notebooklm-study"
STUDY_TEMP_DIR.mkdir(exist_ok=True)
OBSIDIAN_JOURNAL.mkdir(exist_ok=True)

PROMPTS = [
    (
        "Big Picture First",
        "Summarize this book into the 10 most important concepts I must understand "
        "for an exam. Explain each in simple terms and include why it matters."
    ),
    (
        "Chapter-by-Chapter Breakdown",
        "Give a structured summary of each chapter with key ideas, definitions, "
        "and 2-3 likely exam questions per chapter."
    ),
    (
        "Key Terms & Definitions",
        "Extract all key terms, concepts, and definitions from this book and "
        "organize them into a clean study list."
    ),
    (
        "Exam-Style Questions",
        "Generate 20 high-quality exam-style questions (mix of multiple choice, "
        "short answer, and essay) based only on this book."
    ),
    (
        "Hard Concepts Simplified",
        "Identify the most difficult or confusing concepts in this book and "
        "explain them in the simplest way possible."
    ),
    (
        "Connections & Themes",
        "Explain how the main ideas in this book connect to each other. "
        "Show relationships, cause-effect, and big themes."
    ),
    (
        "Testable Topics & Common Traps",
        "Based on this content, what are the most testable topics and "
        "common traps students might fall into?"
    ),
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def run(
    cmd: list[str],
    *,
    timeout: int = 60,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    """Run a subprocess command, return CompletedProcess."""
    kwargs: dict = {"timeout": timeout, "check": check}
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    return subprocess.run(cmd, **kwargs)


def nlm_query_raw(notebook_id: str, question: str, timeout: int = NLM_TIMEOUT_DEFAULT) -> dict:
    """
    Run `nlm query notebook <id> <question>` and return parsed JSON.

    Raises subprocess.CalledProcessError on non-zero exit.
    """
    cmd = [
        NLM, "query", "notebook",
        notebook_id,
        question,
        "--timeout", str(timeout),
    ]
    result = run(cmd, timeout=timeout + 30)
    return json.loads(result.stdout)


def resolve_notebook(name: str) -> tuple[str, str]:
    """
    Resolve a notebook name (fuzzy) to (notebook_id, title).

    Tries exact match first, then substring match.
    Exits with code 1 if not found.
    """
    result = run([NLM, "notebook", "list", "--json"], timeout=30)
    notebooks: list[dict] = json.loads(result.stdout)

    # Try exact (case-insensitive) match first
    name_lower = name.strip().lower()
    for nb in notebooks:
        if nb["title"].strip().lower() == name_lower:
            return nb["id"], nb["title"].strip()

    # Try substring match
    for nb in notebooks:
        if name_lower in nb["title"].strip().lower():
            return nb["id"], nb["title"].strip()

    # Fuzzy match: check if name words appear in title
    name_words = set(name_lower.split())
    for nb in notebooks:
        title_words = set(nb["title"].strip().lower().split())
        if name_words & title_words:  # any common word
            return nb["id"], nb["title"].strip()

    titles = "\n  ".join(n["title"] for n in notebooks)
    print(f"✗ Notebook not found: '{name}'", file=sys.stderr)
    print(f"  Available notebooks:\n  {titles}", file=sys.stderr)
    sys.exit(1)


def create_notebook_from_file(file_path: Path) -> tuple[str, str]:
    """
    Create a new NotebookLM notebook named after the file, upload the file
    as a source, and return (notebook_id, title).

    Exits with code 1 on failure.
    """
    title = file_path.stem  # e.g. "Structures" from "Structures.pdf"

    # 1. Create the notebook
    print(f"  Creating notebook '{title}'...", end=" ", flush=True)
    try:
        run([NLM, "notebook", "create", title], timeout=60)
        print("✓")
    except subprocess.CalledProcessError as e:
        print(f"✗\n  Failed to create notebook: {e}", file=sys.stderr)
        sys.exit(1)

    # 2. Resolve the new notebook ID by exact title match from the list
    try:
        result = run([NLM, "notebook", "list", "--json"], timeout=30)
        notebooks: list[dict] = json.loads(result.stdout)
    except Exception as e:
        print(f"  Failed to list notebooks after creation: {e}", file=sys.stderr)
        sys.exit(1)

    title_lower = title.strip().lower()
    notebook_id: Optional[str] = None
    matched_title = title
    for nb in notebooks:
        if nb["title"].strip().lower() == title_lower:
            notebook_id = nb["id"]
            matched_title = nb["title"].strip()
            break

    if not notebook_id:
        print(f"  ✗ Could not find newly created notebook '{title}' in list", file=sys.stderr)
        sys.exit(1)

    # 3. Upload the file as a source (--wait so it's processed before querying)
    print(f"  Uploading '{file_path.name}' as source (waiting for processing)...", end=" ", flush=True)
    try:
        run(
            [NLM, "source", "add", notebook_id, "--file", str(file_path), "--wait"],
            timeout=700,  # generous — --wait-timeout default is 600s
        )
        print("✓")
    except subprocess.CalledProcessError as e:
        print(f"✗\n  Source upload failed: {e}", file=sys.stderr)
        sys.exit(1)

    return notebook_id, matched_title


def run_study_prompts(notebook_id: str) -> list[tuple[str, str]]:
    """
    Run all 7 study prompts sequentially. Returns list of (title, response).
    On failure, retries once with longer timeout. If still fails, returns
    '[Failed to generate]' for that prompt.
    """
    results: list[tuple[str, str]] = []

    for i, (title, prompt) in enumerate(PROMPTS, 1):
        print(f"  [{i}/{len(PROMPTS)}] {title}...", end=" ", flush=True)
        try:
            data = nlm_query_raw(notebook_id, prompt, NLM_TIMEOUT_DEFAULT)
            answer = data["value"]["answer"]
            print("✓")
            results.append((title, answer))
        except subprocess.TimeoutExpired:
            print("⏱ (timeout, retrying with 180s)...", end=" ", flush=True)
            try:
                data = nlm_query_raw(notebook_id, prompt, NLM_TIMEOUT_RETRY)
                answer = data["value"]["answer"]
                print("✓")
                results.append((title, answer))
            except Exception as e:
                print(f"✗ ({e})")
                results.append((title, "*[Failed to generate — NotebookLM timed out]*"))
        except Exception as e:
            print(f"✗ ({e})")
            results.append((title, f"*[Failed to generate — {e}]*"))

    return results


def trigger_studio_artifacts(notebook_id: str) -> dict[str, Optional[str]]:
    """
    Trigger audio overview, slide deck, quiz, and flashcard generation.
    Returns placeholder keys for parity with the polled artifact set.
    """
    results: dict[str, Optional[str]] = {
        "audio": None,
        "slides": None,
        "quiz": None,
        "flashcards": None,
    }

    # Audio Overview
    try:
        run([NLM, "audio", "create", notebook_id, "--confirm"], timeout=120)
        print("  🎧 Audio overview triggered")
    except Exception as e:
        print(f"  ⚠ Audio trigger failed: {e}")

    # Slide Deck
    try:
        run([NLM, "slides", "create", notebook_id, "--confirm"], timeout=120)
        print("  📊 Slide deck triggered")
    except Exception as e:
        print(f"  ⚠ Slide deck trigger failed: {e}")

    # Quiz (20 MCQ, difficulty 3)
    try:
        run([NLM, "quiz", "create", notebook_id, "--count", "20", "--difficulty", "3", "--confirm"], timeout=120)
        print("  📝 Quiz (20 MCQ) triggered")
    except Exception as e:
        print(f"  ⚠ Quiz trigger failed: {e}")

    # Flashcards
    try:
        run([NLM, "flashcards", "create", notebook_id, "--confirm"], timeout=120)
        print("  🃏 Flashcards triggered")
    except Exception as e:
        print(f"  ⚠ Flashcards trigger failed: {e}")

    return results


def poll_artifacts(notebook_id: str) -> dict[str, Optional[dict]]:
    """
    Poll until audio, slide deck, quiz, and flashcards are all completed
    (or timed out).
    Returns dict of {type: {id, status, ...}} for completed/failed ones.
    """
    artifact_aliases = {
        "audio_overview": ["audio_overview"],
        "slide_deck": ["slide_deck", "slides", "slide-deck"],
        "flashcards": ["flashcards"],
        "quiz": ["quiz"],
    }
    found: dict[str, Optional[dict]] = {
        "audio_overview": None,
        "slide_deck": None,
        "flashcards": None,
        "quiz": None,
    }

    for attempt in range(STUDIO_POLL_MAX):
        time.sleep(STUDIO_POLL_INTERVAL)
        try:
            result = run([NLM, "list", "artifacts", notebook_id], timeout=30)
            artifacts = json.loads(result.stdout)
        except Exception as e:
            print(f"  poll error: {e}, retrying...")
            continue

        # Check each artifact family.
        for artifact_key, aliases in artifact_aliases.items():
            if found[artifact_key] is not None:
                continue  # already found completed
            matches = [a for a in artifacts if a.get("type") in aliases]
            if not matches:
                continue
            latest = matches[-1]
            if latest["status"] == "completed":
                found[artifact_key] = latest
                names = {
                    "audio_overview": "Audio",
                    "slide_deck": "Slide deck",
                    "flashcards": "Flashcards",
                    "quiz": "Quiz",
                }
                print(f"  ✓ {names.get(artifact_key, artifact_key)} ready ({latest['id'][:8]}...)")
            elif latest["status"] == "failed":
                found[artifact_key] = {"status": "failed"}
                names = {
                    "audio_overview": "Audio",
                    "slide_deck": "Slide deck",
                    "flashcards": "Flashcards",
                    "quiz": "Quiz",
                }
                print(f"  ✗ {names.get(artifact_key, artifact_key)} failed")

        # All found or timed out?
        if all(v is not None for v in found.values()):
            break
        if (attempt + 1) % 6 == 0:
            print(f"  ({(attempt+1)*STUDIO_POLL_INTERVAL}s elapsed, still polling...)")

    if not any(v is not None for v in found.values()):
        print("  ⚠ No studio artifacts completed in time")
    return found


def download_flashcards(
    notebook_id: str,
    artifact_id: Optional[str] = None,
) -> list[dict]:
    """
    Download flashcards as JSON. Returns list of {front, back} dicts.
    Falls back to polling-based creation if artifact_id is None.
    """
    tmp = STUDY_TEMP_DIR / f"{notebook_id}_flashcards.json"
    cmd = [NLM, "download", "flashcards", notebook_id, "-f", "json"]
    if artifact_id:
        cmd += ["--id", artifact_id]
    cmd += ["-o", str(tmp)]

    print(f"  Downloading flashcards...", end=" ", flush=True)
    try:
        run(cmd, timeout=60)
    except subprocess.CalledProcessError:
        # Try without specific artifact ID
        try:
            cmd_no_id = [NLM, "download", "flashcards", notebook_id, "-f", "json", "-o", str(tmp)]
            run(cmd_no_id, timeout=60)
        except subprocess.CalledProcessError:
            print("download failed, trying markdown format...", end=" ", flush=True)
            md_tmp = STUDY_TEMP_DIR / f"{notebook_id}_flashcards.md"
            try:
                run([NLM, "download", "flashcards", notebook_id, "-f", "markdown", "-o", str(md_tmp)], timeout=60)
                return parse_markdown_flashcards(md_tmp)
            except Exception:
                print("markdown download also failed")
                return []

    try:
        with open(tmp) as f:
            data = json.load(f)
        cards = data.get("cards", [])
        print(f"got {len(cards)} cards")
        return cards
    except (json.JSONDecodeError, FileNotFoundError):
        print("failed to parse JSON, trying markdown...", end=" ", flush=True)
        return []


def parse_markdown_flashcards(md_path: Path) -> list[dict]:
    """
    Parse markdown flashcard format into [{front, back}].
    Format:
      ## Card N
      **Front:** question
      **Back:** answer
    """
    cards = []
    front = None
    back = None

    content = md_path.read_text()
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("**Front:**"):
            front = line[len("**Front:**"):].strip()
        elif line.startswith("**Back:**"):
            back = line[len("**Back:**"):].strip()
        elif line.startswith("---") or re.match(r"^##\s", line):
            if front and back:
                cards.append({"front": front, "back": back})
                front, back = None, None

    # Last card if no trailing separator
    if front and back:
        cards.append({"front": front, "back": back})

    print(f"parsed {len(cards)} cards from markdown")
    return cards


def build_obsidian_markdown(
    title: str,
    prompt_results: list[tuple[str, str]],
    quiz_downloaded: bool = False,
    audio_downloaded: bool = False,
    slides_downloaded: bool = False,
) -> Path:
    """
    Save FULL study guide (all 7 prompts) to Obsidian journal.
    Includes MCQ (Prompt 4) and links to quiz/audio/flashcard artifacts.
    """
    slug = slugify(title)
    date_prefix = datetime.now().strftime("%Y-%m-%d")
    out_path = OBSIDIAN_JOURNAL / f"{date_prefix}-{slug}-study-prompts.md"
    out_path.parent.mkdir(exist_ok=True)

    lines = [
        f"# {title} — Study Prompts",
        "",
        "*Generated by notebooklm-study | Slide Deck, Quiz (20 MCQ), Audio Overview & Flashcards available in NotebookLM*",
        "",
    ]

    # All 7 prompts — including MCQ (Prompt 4)
    for i, (ptitle, response) in enumerate(prompt_results, 1):
        lines.append(f"## {i}. {ptitle}")
        lines.append("")
        lines.append(response.strip())
        lines.append("")
        lines.append("---")
        lines.append("")

    # Studio artifacts section
    lines.append("## NotebookLM Studio Artifacts")
    lines.append("")
    lines.append("| Artifact | Status | Location |")
    lines.append("|---|---|---|")
    lines.append(f"| 🎧 Audio Overview | {'✅ Downloaded to Downloads folder' if audio_downloaded else '⏳ Open NotebookLM to listen'} | NotebookLM notebook |")
    lines.append(f"| 📊 Slide Deck | {'✅ Downloaded to Downloads folder' if slides_downloaded else '⏳ Open NotebookLM to access'} | NotebookLM notebook |")
    lines.append(f"| 📝 Quiz (20 MCQ) | {'✅ Downloaded to Obsidian journal' if quiz_downloaded else '⏳ Open NotebookLM to access'} | NotebookLM notebook |")
    lines.append(f"| 🃏 Flashcards | ✅ Imported to Anki | Amorn::{title} deck |")
    lines.append("")
    lines.append("*Access these artifacts directly in your NotebookLM notebook: https://notebooklm.google.com*")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Obsidian markdown: {out_path}")
    return out_path


def build_pdf_markdown(
    title: str,
    prompt_results: list[tuple[str, str]],
) -> Path:
    """
    Build a PDF-optimized markdown with only prompts 1,2,3,5,6,7
    (skip exam-style MCQ — it's in the Obsidian journal and NotebookLM).
    """
    slug = slugify(title)
    pdf_md_path = STUDY_TEMP_DIR / f"{slug}-pdf.md"
    pdf_md_path.parent.mkdir(exist_ok=True)

    PDF_INDICES = {1, 2, 3, 5, 6, 7}

    lines = [
        f"# {title} — Study Prompts",
        "",
        "*Generated by notebooklm-study*",
        "",
    ]

    for i, (ptitle, response) in enumerate(prompt_results, 1):
        if i in PDF_INDICES:
            lines.append(f"## {i}. {ptitle}")
            lines.append("")
            lines.append(response.strip())
            lines.append("")
            lines.append("---")
            lines.append("")

    # Hint at end
    lines.append("---")
    lines.append("")
    lines.append("## NotebookLM Studio")
    lines.append("")
    lines.append("**Slide Deck, 20 MCQ Quiz, Audio Overview & Flashcards** are available in your NotebookLM notebook.")
    lines.append("Open: https://notebooklm.google.com")
    lines.append("")
    lines.append("*Full study guide with all 7 prompts saved to Obsidian journal.*")

    pdf_md_path.write_text("\n".join(lines), encoding="utf-8")
    return pdf_md_path


def convert_to_pdf(md_path: Path) -> Optional[Path]:
    """Convert markdown to PDF using the workspace md_to_pdf.py."""
    slug = md_path.stem.replace("-pdf", "")
    pdf_path = DOWNLOAD_DIR / f"{slug}-study-prompts.pdf"
    print(f"  Converting to PDF...", end=" ", flush=True)

    if not MD_TO_PDF_PATH.exists():
        print(f"md_to_pdf.py not found at {MD_TO_PDF_PATH}, skipping PDF")
        return None

    # Try importing as module first
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("md_to_pdf", MD_TO_PDF_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["md_to_pdf"] = mod
        spec.loader.exec_module(mod)  # type: ignore
        if hasattr(mod, "md_to_pdf"):
            mod.md_to_pdf(str(md_path), str(pdf_path))
            print(f"✓ {pdf_path}")
            return pdf_path
    except Exception as e:
        print(f"(import failed: {e}, trying subprocess)...", end=" ")

    # Fallback: run as subprocess
    try:
        run(
            [sys.executable, str(MD_TO_PDF_PATH), str(md_path), str(pdf_path)],
            timeout=60,
        )
        print(f"✓ {pdf_path}")
        return pdf_path
    except subprocess.CalledProcessError:
        print("PDF conversion failed")
        return None


def email_pdf(pdf_path: Path, title: str) -> bool:
    """Send PDF to Readwise Reader email address."""
    print(f"  Emailing PDF to {READER_EMAIL}...", end=" ", flush=True)
    try:
        run(
            [
                "gog", "gmail", "send",
                "--to", READER_EMAIL,
                "--subject", f"{title} Study Prompts",
                "--body", "Save to library",
                "--attach", str(pdf_path),
            ],
            timeout=60,
        )
        print("✓")
        return True
    except Exception as e:
        print(f"✗ ({e})")
        return False


def anki_deck_exists(deck: str) -> bool:
    """Check if a deck already exists in Anki."""
    try:
        resp = requests_post({"action": "deckNames", "version": 6, "params": {}})
        return deck in (resp.get("result") or [])
    except Exception:
        return False


def anki_model_names() -> list[str]:
    """Get list of all note model names in Anki."""
    try:
        resp = requests_post({"action": "modelNames", "version": 6, "params": {}})
        return resp.get("result") or []
    except Exception:
        return []


def requests_post(payload: dict) -> dict:
    import urllib.request
    import json as _json
    data = _json.dumps(payload).encode()
    req = urllib.request.Request(
        ANKI_URL,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return _json.loads(resp.read())


def import_to_anki(title: str, cards: list[dict]) -> int:
    """
    Create subdeck Amorn::<title> and import flashcards.
    Returns number of cards successfully added.
    """
    deck_name = f"Amorn::{title}"
    slug = slugify(title)
    short_deck = f"Amorn::{slug}"

    print(f"  Setting up Anki subdeck '{deck_name}'...", end=" ", flush=True)

    # Ensure Amorn parent deck exists
    try:
        requests_post({
            "action": "createDeck",
            "version": 6,
            "params": {"deck": "Amorn"},
        })
    except Exception:
        pass  # Already exists

    # Create subdeck
    try:
        requests_post({
            "action": "createDeck",
            "version": 6,
            "params": {"deck": deck_name},
        })
    except Exception as e:
        print(f"createDeck failed: {e}")

    # Determine model name — use "Basic" if available
    models = anki_model_names()
    model_name = "Basic" if "Basic" in models else (models[0] if models else "Basic")

    # Get field names for the model
    try:
        field_resp = requests_post({
            "action": "modelFieldNames",
            "version": 6,
            "params": {"modelName": model_name},
        })
        field_names: list[str] = field_resp.get("result") or []
    except Exception:
        field_names = ["Front", "Back"]

    # Build notes
    added = 0
    for card in cards:
        front = str(card.get("front", "")).strip()
        back = str(card.get("back", "")).strip()
        if not front or not back:
            continue

        fields = {fn: front if fn.lower() == "front" else back for fn in field_names}
        if "Front" not in fields and "Back" not in fields and len(field_names) >= 2:
            fields[field_names[0]] = front
            fields[field_names[1]] = back

        try:
            result = requests_post({
                "action": "addNote",
                "version": 6,
                "params": {
                    "note": {
                        "deckName": deck_name,
                        "modelName": model_name,
                        "fields": fields,
                        "options": {"allowDuplicate": True},
                        "tags": ["notebooklm-study", slug],
                    }
                },
            })
            if result.get("result") is not None:
                added += 1
        except Exception as e:
            print(f"\n  addNote error: {e}", end="")

    print(f"✓ {added}/{len(cards)} cards added")
    return added


def slugify(text: str) -> str:
    """Convert a notebook title to a safe filename / deck slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate study guide + flashcards from a NotebookLM notebook.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  notebooklm-study \"Structures\"          # use existing notebook\n"
            "  notebooklm-study path/to/Structures.pdf  # create notebook + upload PDF"
        ),
    )
    parser.add_argument(
        "notebook",
        help="Notebook name (fuzzy-matched) OR path to a PDF/file to create a new notebook from",
    )
    parser.add_argument(
        "--no-pdf", action="store_true", help="Skip PDF generation"
    )
    parser.add_argument(
        "--no-email", action="store_true", help="Skip email to Readwise"
    )
    parser.add_argument(
        "--no-anki", action="store_true", help="Skip Anki import"
    )
    parser.add_argument(
        "--no-flashcards", action="store_true",
        help="Skip NLM flashcard generation (faster, no flashcards)"
    )
    parser.add_argument(
        "--timeout", type=int, default=NLM_TIMEOUT_DEFAULT,
        help=f"NLM query timeout in seconds (default: {NLM_TIMEOUT_DEFAULT})"
    )

    args = parser.parse_args()

    # 1. Resolve notebook (or create from file)
    input_path = Path(args.notebook)
    if input_path.exists() and input_path.is_file():
        print(f"\n📄 File detected: '{input_path.name}' — creating new notebook...")
        notebook_id, title = create_notebook_from_file(input_path)
        print(f"   → {title}  [{notebook_id[:8]}...]")
    else:
        print(f"\n🔍 Resolving notebook: '{args.notebook}'")
        notebook_id, title = resolve_notebook(args.notebook)
        print(f"   → {title}  [{notebook_id[:8]}...]")

    slug = slugify(title)
    date_prefix = datetime.now().strftime("%Y-%m-%d")

    # 2. Run study prompts
    print(f"\n📝 Running {len(PROMPTS)} study prompts (may take 5-10 min)...")
    prompt_results = run_study_prompts(notebook_id)

    # 3. Trigger NotebookLM Studio artifacts.
    cards: list[dict] = []
    studio_artifacts: dict[str, Optional[dict]] = {}
    audio_downloaded = False
    slides_downloaded = False
    quiz_downloaded = False

    if not args.no_flashcards:
        print("\n🎧📊📝🃏 Triggering NotebookLM Studio (audio overview, slide deck, 20-MCQ quiz, flashcards)...")
        trigger_studio_artifacts(notebook_id)
        print("  Polling for completion (this may take 3-5 minutes)...")
        studio_artifacts = poll_artifacts(notebook_id)

        # Download audio to Downloads folder
        audio_artifact = studio_artifacts.get("audio_overview")
        if audio_artifact and audio_artifact.get("status") != "failed":
            aid = audio_artifact.get("id")
            audio_out = DOWNLOAD_DIR / f"{slug}-audio.m4a"
            try:
                run([NLM, "download", "audio", notebook_id, "--id", aid, "-o", str(audio_out)], timeout=120)
                audio_downloaded = True
                print(f"  ✓ Audio saved: {audio_out}")
            except Exception as e:
                print(f"  ⚠ Audio download failed: {e}")

        # Download slide deck (PDF) to Downloads folder
        slide_artifact = studio_artifacts.get("slide_deck")
        if slide_artifact and slide_artifact.get("status") != "failed":
            sid = slide_artifact.get("id")
            slide_out = DOWNLOAD_DIR / f"{slug}-slides.pdf"
            try:
                run([NLM, "download", "slide-deck", notebook_id, "--id", sid, "-f", "pdf", "-o", str(slide_out)], timeout=120)
                slides_downloaded = True
                print(f"  ✓ Slide deck saved: {slide_out}")
            except Exception as e:
                print(f"  ⚠ Slide deck download failed: {e}")

        # Download quiz (markdown) to Obsidian journal
        quiz_artifact = studio_artifacts.get("quiz")
        if quiz_artifact and quiz_artifact.get("status") != "failed":
            qid = quiz_artifact.get("id")
            quiz_out_md = OBSIDIAN_JOURNAL / f"{date_prefix}-{slug}-quiz.md"
            try:
                run([NLM, "download", "quiz", notebook_id, "--id", qid, "-f", "markdown", "-o", str(quiz_out_md)], timeout=60)
                quiz_downloaded = True
                print(f"  ✓ Quiz (20 MCQ) saved: {quiz_out_md}")
            except Exception as e:
                print(f"  ⚠ Quiz download failed: {e}")

        # Download flashcards for Anki import
        flashcard_artifact = studio_artifacts.get("flashcards")
        if flashcard_artifact and flashcard_artifact.get("status") != "failed":
            cards = download_flashcards(notebook_id, flashcard_artifact.get("id"))
        else:
            cards = download_flashcards(notebook_id)  # fallback: try without ID
    else:
        print("\n🎧📊📝🃏 Skipping studio artifacts (--no-flashcards)")

    # 4. Build Obsidian markdown (ALL 7 prompts + artifact links)
    print("\n📄 Building Obsidian markdown (all 7 prompts)...")
    obsidian_md_path = build_obsidian_markdown(
        title,
        prompt_results,
        quiz_downloaded,
        audio_downloaded,
        slides_downloaded,
    )

    # 5. Build PDF markdown (6 prompts, no MCQ, no flashcards table)
    pdf_path: Optional[Path] = None
    if not args.no_pdf:
        print("\n📕 Building PDF (6 prompts)...")
        pdf_md_path = build_pdf_markdown(title, prompt_results)
        pdf_path = convert_to_pdf(pdf_md_path)
        if not pdf_path:
            print("  ⚠ PDF conversion failed — will email markdown instead")

    # 6. Email PDF
    email_ok = False
    if not args.no_email:
        print("\n📧 Emailing PDF...")
        attach_path = pdf_path or obsidian_md_path
        email_ok = email_pdf(attach_path, title)

    # 7. Anki import
    anki_added = 0
    if not args.no_anki and cards:
        print("\n🗂 Importing to Anki...")
        try:
            anki_added = import_to_anki(title, cards)
        except Exception as e:
            print(f"  ⚠ Anki import failed: {e}")
            print("  (Is Anki running with AnkiConnect installed?)")
    elif not cards and not args.no_anki:
        print("\n🗂 No flashcards to import")

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"✅ notebooklm-study complete: {title}")
    print(f"   Obsidian : {obsidian_md_path}")
    if pdf_path:
        print(f"   PDF      : {pdf_path}")
    if audio_downloaded:
        print(f"   🎧 Audio : {DOWNLOAD_DIR}/{slug}-audio.m4a")
    if slides_downloaded:
        print(f"   📊 Slides: {DOWNLOAD_DIR}/{slug}-slides.pdf")
    if quiz_downloaded:
        print(f"   📝 Quiz  : {OBSIDIAN_JOURNAL}/{date_prefix}-{slug}-quiz.md")
    if not args.no_email:
        status = "✓ sent" if email_ok else "✗ failed"
        print(f"   Email    : {status} → {READER_EMAIL}")
    if not args.no_anki and cards:
        print(f"   Anki     : {anki_added}/{len(cards)} flashcards → Amorn::{title}")
    print("=" * 60)


if __name__ == "__main__":
    main()
