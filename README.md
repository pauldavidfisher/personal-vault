# Personal Vault

A local personal library — fetch, clip, search, and own your reading.

Personal Vault is a self-hosted web app that turns your browser into a reading and curation tool. Fetch any text from the web, highlight passages that matter, add your own notes, and build a searchable library that is entirely yours.

---

## The idea

Most reading tools are built around saving links. Personal Vault is built around saving *ideas* — the specific passages, arguments, and moments in a text that are worth keeping.

The core workflow is simple:

1. **Fetch** a text from any URL — a book on Project Gutenberg, an essay, a Wikipedia article — and it lands in your vault permanently
2. **Read** it in the built-in viewer
3. **Clip** the passages that matter, add a note, and they become part of your searchable library
4. **Search** across everything — your own notes, fetched texts, clips, and bookmarks — in one place

Over time your clips become a personal commonplace book. Organized, searchable, shareable, and owned by you.

---

## Features

- **Fetch any URL** — pulls full text from Project Gutenberg, CCEL, Wikipedia, Standard Ebooks, and most other sites
- **Save to vault** — fetched texts are saved permanently as plain `.txt` files you own
- **Clip passages** — select any text in the viewer, add a note, save it as a clip
- **Upload files** — drag and drop `.txt`, `.md`, `.rtf`, `.pdf`, or `.csv` files
- **Full-text search** — searches across all vault files, clips, and bookmarks simultaneously
- **Bookmarks** — import a Raindrop.io export and classify it using the Dewey Decimal System automatically
- **Dewey Decimal Classification** — your bookmarks are assigned DDC codes, making your library browsable by subject
- **Folder browser** — click any folder to see file cards with title and author extracted automatically
- **PDF extraction** — uploads extract text via PyMuPDF with tesseract OCR fallback for scanned documents
- **Scope filtering** — search All, Vault only, or Bookmarks only

---

## Setup

### Requirements

- Python 3.9+
- pip

### Install dependencies

```bash
pip3 install flask requests beautifulsoup4 pymupdf --break-system-packages
```

Optional — for better PDF support and OCR:
```bash
brew install tesseract poppler
```

### Run

```bash
python3 vault_search_upload.py --vault ~/path/to/your/notes
```

Open http://localhost:5002 in your browser.

### Optional — connect a bookmark database

Personal Vault can search your Raindrop.io bookmarks alongside your vault files.

**Step 1** — Export your bookmarks from Raindrop.io as CSV

**Step 2** — Classify them with DDC tags:
```bash
python3 raindrop_ddc.py --input export.csv --output export_ddc.csv
```

**Step 3** — Ingest into the database:
```bash
python3 bookmarks_index.py --input export_ddc.csv
```

**Step 4** — Run the vault with the database connected:
```bash
python3 vault_search_upload.py --vault ~/path/to/your/notes --db ~/path/to/pipeline.db
```

---

## Command line options

```
vault_search_upload.py
  --vault   Path to your vault folder (default: ~/Downloads/local-repos/rtf-to-md)
  --port    Port to run on (default: 5002)
  --db      Path to pipeline.db for bookmark search (optional)

bookmarks_index.py
  --input   Path to export_ddc.csv
  --output  Output CSV filename (default: export_ddc.csv)
  --db      Path to pipeline.db (default: ~/Downloads/local-repos/pipeline/pipeline.db)
  --report  Show classification stats
  --search  Full-text search bookmarks
  --ddc     Browse bookmarks by DDC code (e.g. 641.5)
  --tag     Browse bookmarks by tag

raindrop_ddc.py
  --input   Raindrop export CSV
  --output  Output CSV with DDC columns added
  --report  Show classification summary
  --unmatched  Show tags with no DDC mapping
```

---

## Vault folder structure

Your vault is just a folder of plain text files. Personal Vault reads it as-is. Subfolders become browsable sections in the sidebar.

```
your-vault/
  fetched/          ← texts saved via Fetch a URL
  notes/            ← your own writing
  essays/           ← longer pieces
  quotes/           ← collected passages
  projects/         ← project-related notes
  ...               ← any folders you like
```

Files can be `.md`, `.txt`, `.rtf`, or `.docx`. The app extracts title and author from frontmatter or the first few lines automatically.

---

## Clips

Clips are saved to `pipeline.db` (a local SQLite database). Each clip stores:

- The selected text
- Your note
- The source file and folder
- The date saved

Clips are searchable alongside your vault files and bookmarks. They persist across sessions.

---

## Bookmarks and Dewey Decimal Classification

Personal Vault maps your Raindrop tags to Dewey Decimal codes automatically. For example:

| Your tag | DDC code | Label |
|----------|----------|-------|
| `recipe-soup` | 641.57 | Cooking — soup |
| `con-project-kitchen-bath` | 690.91 | Project — kitchen & bath |
| `theology` | 230 | Christian theology |
| `american-history` | 973 | History of United States |
| `sketchup` | 720 | Architecture |

The full mapping is in `raindrop_ddc.py` and is easy to extend.

---

## Philosophy

Personal Vault is built around a few principles:

- **You own your data.** Everything is plain text files and a local SQLite database. No cloud, no subscription, no lock-in.
- **Structure should emerge from use.** The DDC system gives your library real intellectual organization without requiring you to invent your own taxonomy.
- **Fetch and clip is the core loop.** The best ideas come from reading. Personal Vault makes it easy to capture what matters and find it later.

---

## Good sources to start with

- [Project Gutenberg](https://www.gutenberg.org) — 70,000+ free books
- [Standard Ebooks](https://standardebooks.org) — beautifully formatted classics
- [CCEL](https://ccel.org) — Christian Classics Ethereal Library
- [Wikisource](https://en.wikisource.org) — historical documents and source texts
- [Perseus Digital Library](https://perseus.tufts.edu) — Greek and Latin classics
- [Internet Archive](https://archive.org/texts) — books, periodicals, manuscripts

---

## License

MIT License. Use it, fork it, build on it.
