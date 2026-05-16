# Personal Vault

**A local personal library. Fetch texts, clip passages, search everything.**

Personal Vault is a self-hosted web app that turns your browser into a reading and curation tool. Fetch any text from the web, highlight the passages that matter, add your own notes, and build a searchable library that is entirely yours — no cloud, no subscription, no lock-in.

---

![Personal Vault welcome screen](https://raw.githubusercontent.com/pauldavidfisher/personal-vault/main/assets/images/screenshot-welcome.png)

---

## Get started

The fastest way is the starter repo:

```bash
git clone https://github.com/pauldavidfisher/personal-vault-starter.git
cd personal-vault-starter
# edit config.yaml to point at your vault folder
./start.sh
```

→ **[personal-vault-starter](https://github.com/pauldavidfisher/personal-vault-starter)** — clone this to get running in minutes

---

## The idea

Most reading tools are built around saving links. Personal Vault is built around saving *ideas* — the specific passages, arguments, and moments in a text that are worth keeping.

**The core workflow:**

1. **Fetch** a text from any URL — a book on Project Gutenberg, an essay, a Wikipedia article — and it lands in your vault permanently
2. **Read** it in the built-in viewer
3. **Clip** the passages that matter, add a note, and they become part of your searchable library
4. **Search** across everything — your own notes, fetched texts, clips, and bookmarks — in one place

Over time your clips become a personal commonplace book. Organized, searchable, shareable, and owned by you.

---

## Search

Search across your entire vault — notes, fetched texts, clips, and bookmarks — simultaneously. Results show highlighted context with the matching lines visible inline.

![Search results showing vault matches and bookmarks](https://raw.githubusercontent.com/pauldavidfisher/personal-vault/main/assets/images/screenshot-search.png)

---

## Folders

Click any folder in the sidebar to browse its contents as a card grid. Title and author are extracted automatically from the file content.

![Folder browser showing file cards with titles and authors](https://raw.githubusercontent.com/pauldavidfisher/personal-vault/main/assets/images/screenshot-folders.png)

---

## Clips

Select any text in the viewer and click **+ Save clip**. Add an optional note. The clip is saved permanently and becomes searchable across your entire vault.

![Clip interface showing text selection and save button](https://raw.githubusercontent.com/pauldavidfisher/personal-vault/main/assets/images/screenshot-clip.png)

---

## Bookmarks

Import a Raindrop.io export and your bookmarks are automatically classified using the Dewey Decimal System. Browse by subject — Cooking, History, Philosophy, Architecture — with cover images, excerpts, and your own notes.

![Bookmark cards with cover images and excerpts](https://raw.githubusercontent.com/pauldavidfisher/personal-vault/main/assets/images/screenshot-bookmarks.png)

---

## Features

- **Fetch any URL** — pulls full text from Project Gutenberg, CCEL, Wikipedia, Standard Ebooks, and most other sites
- **Save to vault** — fetched texts saved permanently as plain `.txt` files you own
- **Clip passages** — select text in the viewer, add a note, save permanently
- **Upload files** — drag and drop `.txt`, `.md`, `.rtf`, `.pdf`, or `.csv`
- **Full-text search** — searches vault files, clips, and bookmarks simultaneously
- **Folder browser** — click any folder to see file cards with title and author
- **Bookmarks** — import Raindrop.io exports, auto-classified by DDC subject
- **PDF extraction** — text extracted via PyMuPDF
- **Scope filtering** — search All, Vault only, or Bookmarks only
- **Config file** — single `config.yaml` to configure vault path, port, and app name

---

## Good sources to fetch

- [Project Gutenberg](https://www.gutenberg.org) — 70,000+ free books
- [Standard Ebooks](https://standardebooks.org) — beautifully formatted classics
- [CCEL](https://ccel.org) — Christian Classics Ethereal Library
- [Wikisource](https://en.wikisource.org) — historical documents and source texts
- [Perseus Digital Library](https://perseus.tufts.edu) — Greek and Latin classics
- [Internet Archive](https://archive.org/texts) — books, periodicals, manuscripts

---

## Setup

### Requirements

- Python 3.9+
- macOS, Linux, or Windows

### Install

```bash
pip3 install flask requests beautifulsoup4 pymupdf --break-system-packages
```

### Run

```bash
python3 vault_search_upload.py --vault ~/path/to/your/notes
```

Or with a config file — edit `config.yaml` then:

```bash
python3 vault_search_upload.py
```

Open http://localhost:5002 in your browser.

### Config

```yaml
vault:
  path: ~/Documents/vault

database:
  path: ~/Documents/vault/vault.db

server:
  port: 5002
  host: 127.0.0.1

app:
  name: Personal Vault
  welcome_quote: "Manners are of more importance than laws."
  welcome_quote_author: Edmund Burke
```

### Optional — import bookmarks

```bash
python3 raindrop_ddc.py --input export.csv --output export_ddc.csv
python3 bookmarks_index.py --input export_ddc.csv
```

---

## Philosophy

- **You own your data.** Everything is plain text files and a local SQLite database.
- **Structure should emerge from use.** DDC gives your library real intellectual organization without inventing your own taxonomy.
- **Fetch and clip is the core loop.** The best ideas come from reading. Personal Vault makes it easy to capture what matters and find it later.

---

## License

MIT — use it, fork it, build on it.
