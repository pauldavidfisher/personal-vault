#!/usr/bin/env python3
"""
bookmarks_index.py — Ingest Raindrop DDC export into pipeline.db

Creates a `bookmarks` table and populates it from export_ddc.csv.
Subsequent runs are safe — uses INSERT OR REPLACE on the Raindrop id.

Usage:
    python3 bookmarks_index.py
    python3 bookmarks_index.py --input export_ddc.csv
    python3 bookmarks_index.py --input export_ddc.csv --db pipeline.db
    python3 bookmarks_index.py --report
    python3 bookmarks_index.py --search "tamarind"
    python3 bookmarks_index.py --ddc 641.5
    python3 bookmarks_index.py --tag recipe-soup
"""

import csv
import sys
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE      = Path.home() / 'Downloads' / 'local-repos'
DB_PATH   = BASE / 'pipeline' / 'pipeline.db'
CSV_PATH  = BASE / 'pipeline' / 'export_ddc.csv'


# ── Schema ────────────────────────────────────────────────────────────────────
CREATE_BOOKMARKS = """
CREATE TABLE IF NOT EXISTS bookmarks (
    id           INTEGER PRIMARY KEY,   -- Raindrop id (stable)
    title        TEXT,
    url          TEXT NOT NULL,
    domain       TEXT,
    excerpt      TEXT,
    note         TEXT,
    cover        TEXT,
    tags         TEXT,                  -- original comma-separated tags
    ddc_code     TEXT,                  -- e.g. 641.57
    ddc_label    TEXT,                  -- e.g. Cooking — soup
    favorite     INTEGER DEFAULT 0,
    created      TEXT,                  -- ISO timestamp from Raindrop
    indexed_at   TEXT                   -- when we ingested it
);
"""

CREATE_BOOKMARK_TAGS = """
CREATE TABLE IF NOT EXISTS bookmark_tags (
    bookmark_id  INTEGER NOT NULL REFERENCES bookmarks(id),
    tag          TEXT NOT NULL,
    PRIMARY KEY  (bookmark_id, tag)
);
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_bookmarks_domain  ON bookmarks(domain);",
    "CREATE INDEX IF NOT EXISTS idx_bookmarks_ddc     ON bookmarks(ddc_code);",
    "CREATE INDEX IF NOT EXISTS idx_bookmarks_created ON bookmarks(created);",
    "CREATE INDEX IF NOT EXISTS idx_bookmark_tags_tag ON bookmark_tags(tag);",
]

CREATE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS bookmarks_fts USING fts5(
    title,
    excerpt,
    note,
    tags,
    ddc_label,
    content='bookmarks',
    content_rowid='id'
);
"""

# ── Database ──────────────────────────────────────────────────────────────────
def get_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn):
    conn.execute(CREATE_BOOKMARKS)
    conn.execute(CREATE_BOOKMARK_TAGS)
    for idx in CREATE_INDEXES:
        conn.execute(idx)
    conn.execute(CREATE_FTS)
    conn.commit()
    print("  ✅ Schema ready")


# ── Parsing ───────────────────────────────────────────────────────────────────
def parse_tags(tag_str: str) -> list:
    return [t.strip() for t in tag_str.split(',') if t.strip() and not t.strip().startswith('ddc:')]


def extract_domain(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.replace('www.', '')
    except Exception:
        return ''


def parse_row(row: dict) -> dict:
    return {
        'id':        int(row['id']),
        'title':     row.get('title', '').strip(),
        'url':       row.get('url', '').strip(),
        'domain':    extract_domain(row.get('url', '')),
        'excerpt':   row.get('excerpt', '').strip(),
        'note':      row.get('note', '').strip(),
        'cover':     row.get('cover', '').strip(),
        'tags':      row.get('tags', '').strip(),
        'ddc_code':  row.get('ddc_code', '').strip(),
        'ddc_label': row.get('ddc_label', '').strip(),
        'favorite':  1 if row.get('favorite', '').lower() == 'true' else 0,
        'created':   row.get('created', '').strip(),
        'indexed_at': datetime.now().isoformat(timespec='seconds'),
    }


# ── Ingest ────────────────────────────────────────────────────────────────────
def ingest(conn, csv_path: Path, verbose=False):
    print(f"\n📥 Reading {csv_path.name}...")

    rows = []
    with open(csv_path, encoding='utf-8', errors='ignore', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(parse_row(row))

    print(f"   {len(rows)} bookmarks to ingest")

    inserted = 0
    updated  = 0
    skipped  = 0
    ts = datetime.now().isoformat(timespec='seconds')

    for rec in rows:
        if not rec['url']:
            skipped += 1
            continue

        # Check if exists
        existing = conn.execute(
            "SELECT id FROM bookmarks WHERE id = ?", (rec['id'],)
        ).fetchone()

        conn.execute("""
            INSERT OR REPLACE INTO bookmarks
            (id, title, url, domain, excerpt, note, cover, tags,
             ddc_code, ddc_label, favorite, created, indexed_at)
            VALUES
            (:id, :title, :url, :domain, :excerpt, :note, :cover, :tags,
             :ddc_code, :ddc_label, :favorite, :created, :indexed_at)
        """, rec)

        # Rebuild bookmark_tags
        conn.execute("DELETE FROM bookmark_tags WHERE bookmark_id = ?", (rec['id'],))
        for tag in parse_tags(rec['tags']):
            conn.execute(
                "INSERT OR IGNORE INTO bookmark_tags (bookmark_id, tag) VALUES (?, ?)",
                (rec['id'], tag)
            )

        if existing:
            updated += 1
        else:
            inserted += 1
            if verbose:
                print(f"  + {rec['ddc_code']:<8} {rec['title'][:60]}")

    conn.commit()

    # Rebuild FTS index
    print("  🔍 Rebuilding full-text search index...")
    conn.execute("INSERT INTO bookmarks_fts(bookmarks_fts) VALUES('rebuild')")
    conn.commit()

    print(f"\n  ✅ Done")
    print(f"     Inserted:  {inserted}")
    print(f"     Updated:   {updated}")
    print(f"     Skipped:   {skipped}")


# ── Report ────────────────────────────────────────────────────────────────────
def report(conn):
    total = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
    classified = conn.execute(
        "SELECT COUNT(*) FROM bookmarks WHERE ddc_code != ''"
    ).fetchone()[0]
    domains = conn.execute(
        "SELECT COUNT(DISTINCT domain) FROM bookmarks"
    ).fetchone()[0]
    favorites = conn.execute(
        "SELECT COUNT(*) FROM bookmarks WHERE favorite = 1"
    ).fetchone()[0]

    print(f'\n{"═"*56}')
    print(f'  BOOKMARKS IN PIPELINE.DB')
    print(f'{"═"*56}')
    print(f'  Total:        {total:>6}')
    print(f'  Classified:   {classified:>6}  ({classified/total*100:.1f}%)')
    print(f'  Unclassified: {total-classified:>6}')
    print(f'  Domains:      {domains:>6}')
    print(f'  Favorites:    {favorites:>6}')

    print(f'\n  Top DDC classes:')
    rows = conn.execute("""
        SELECT ddc_code, ddc_label, COUNT(*) as n
        FROM bookmarks
        WHERE ddc_code != ''
        GROUP BY ddc_code
        ORDER BY n DESC
        LIMIT 20
    """).fetchall()
    for r in rows:
        bar = '█' * min(20, r['n'] // 10)
        print(f"    {r['ddc_code']:<10} {r['n']:>5}  {r['ddc_label'][:30]:<30}  {bar}")

    print(f'\n  Top tags:')
    rows = conn.execute("""
        SELECT tag, COUNT(*) as n
        FROM bookmark_tags
        WHERE tag NOT IN ('paul','natalie','sophia','chrisje','tyler','chloe','luke','david','marcel')
        GROUP BY tag
        ORDER BY n DESC
        LIMIT 20
    """).fetchall()
    for r in rows:
        print(f"    {r['n']:>5}  {r['tag']}")

    print(f'\n  Top domains:')
    rows = conn.execute("""
        SELECT domain, COUNT(*) as n
        FROM bookmarks
        GROUP BY domain
        ORDER BY n DESC
        LIMIT 15
    """).fetchall()
    for r in rows:
        print(f"    {r['n']:>5}  {r['domain']}")

    print(f'\n{"═"*56}\n')


# ── Search ────────────────────────────────────────────────────────────────────
def search(conn, query: str):
    print(f'\n  🔍 Searching for: "{query}"\n')
    rows = conn.execute("""
        SELECT b.id, b.title, b.url, b.domain, b.ddc_code, b.ddc_label, b.tags
        FROM bookmarks_fts
        JOIN bookmarks b ON b.id = bookmarks_fts.rowid
        WHERE bookmarks_fts MATCH ?
        ORDER BY rank
        LIMIT 20
    """, (query,)).fetchall()

    if not rows:
        print("  No results found.")
        return

    print(f"  {len(rows)} result(s):\n")
    for r in rows:
        print(f"  [{r['ddc_code'] or '—':>8}] {r['title'][:60]}")
        print(f"           {r['domain']}  |  tags: {r['tags'][:60]}")
        print(f"           {r['url'][:80]}")
        print()


# ── Filter by DDC ─────────────────────────────────────────────────────────────
def filter_ddc(conn, ddc: str):
    print(f'\n  📚 DDC {ddc} bookmarks:\n')
    rows = conn.execute("""
        SELECT title, url, domain, ddc_label, tags
        FROM bookmarks
        WHERE ddc_code = ? OR ddc_code LIKE ?
        ORDER BY created DESC
        LIMIT 30
    """, (ddc, f'{ddc}.%')).fetchall()

    if not rows:
        print("  No bookmarks found for this DDC code.")
        return

    print(f"  {len(rows)} result(s):\n")
    for r in rows:
        print(f"  [{r['ddc_label'][:35]:<35}] {r['title'][:55]}")
        print(f"    {r['domain']}  |  {r['tags'][:70]}")
        print()


# ── Filter by tag ─────────────────────────────────────────────────────────────
def filter_tag(conn, tag: str):
    print(f'\n  🏷  Tag "{tag}" bookmarks:\n')
    rows = conn.execute("""
        SELECT b.title, b.url, b.domain, b.ddc_code, b.ddc_label, b.tags
        FROM bookmarks b
        JOIN bookmark_tags bt ON bt.bookmark_id = b.id
        WHERE bt.tag = ?
        ORDER BY b.created DESC
        LIMIT 30
    """, (tag,)).fetchall()

    if not rows:
        print(f"  No bookmarks tagged '{tag}'.")
        return

    print(f"  {len(rows)} result(s):\n")
    for r in rows:
        print(f"  [{r['ddc_code'] or '—':>8}] {r['title'][:60]}")
        print(f"           {r['domain']}  |  {r['ddc_label']}")
        print()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Ingest Raindrop bookmarks into pipeline.db')
    parser.add_argument('--input',   default=None,  help=f'CSV to ingest (default: {CSV_PATH})')
    parser.add_argument('--db',      default=None,  help=f'pipeline.db path (default: {DB_PATH})')
    parser.add_argument('--report',  action='store_true', help='Show stats report')
    parser.add_argument('--search',  default=None,  help='Full-text search bookmarks')
    parser.add_argument('--ddc',     default=None,  help='Show bookmarks for a DDC code (e.g. 641.5)')
    parser.add_argument('--tag',     default=None,  help='Show bookmarks for a tag')
    parser.add_argument('--verbose', action='store_true', help='Print each inserted bookmark')
    args = parser.parse_args()

    csv_path = Path(args.input).expanduser().resolve() if args.input else CSV_PATH
    db_path  = Path(args.db).expanduser().resolve()    if args.db    else DB_PATH

    if not db_path.exists():
        print(f'❌ Database not found: {db_path}')
        print('   Run pipeline_index.py first.')
        sys.exit(1)

    conn = get_db(db_path)

    # Always ensure schema exists
    init_schema(conn)

    # If only querying, skip ingest
    if args.report:
        report(conn)
        return

    if args.search:
        search(conn, args.search)
        return

    if args.ddc:
        filter_ddc(conn, args.ddc)
        return

    if args.tag:
        filter_tag(conn, args.tag)
        return

    # Default: ingest
    if not csv_path.exists():
        print(f'❌ CSV not found: {csv_path}')
        print('   Run raindrop_ddc.py first to generate export_ddc.csv')
        sys.exit(1)

    print(f'\n🗄  Database: {db_path}')
    ingest(conn, csv_path, verbose=args.verbose)
    report(conn)
    conn.close()


if __name__ == '__main__':
    main()
