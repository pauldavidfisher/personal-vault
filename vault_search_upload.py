#!/usr/bin/env python3
"""
vault_search.py — Full-text search browser for the rtf-to-md vault
Searches across all .md and .txt files, shows context around matches.

Usage:
    python3 vault_search.py
    python3 vault_search.py --vault ~/Downloads/local-repos/rtf-to-md
    Open http://localhost:5001
"""

import re
import io
import sqlite3
import argparse
from pathlib import Path
from flask import Flask, request, jsonify, render_template_string
try:
    import requests
    from bs4 import BeautifulSoup
    _URL_FETCH_OK = True
except ImportError:
    _URL_FETCH_OK = False

_UPLOADED_FILES = {}  # filename -> lines[]

app = Flask(__name__)
VAULT_DIR = None
DB_PATH = None
_INDEX = None  # list of (path, lines[])

def build_index(force=False):
    global _INDEX
    if _INDEX is not None and not force:
        # Merge uploaded files into existing index
        uploaded = [(Path(name), lines) for name, lines in _UPLOADED_FILES.items()]
        existing_names = {str(p) for p, _ in _INDEX}
        new_uploads = [(p, l) for p, l in uploaded if str(p) not in existing_names]
        return _INDEX + new_uploads
    files = []
    for ext in ('*.md', '*.txt'):
        for f in sorted(VAULT_DIR.rglob(ext)):
            try:
                lines = f.read_text(errors='ignore').splitlines()
                files.append((f, lines))
            except Exception:
                pass
    _INDEX = files
    print(f"  Indexed {len(files)} vault files")
    return _INDEX

def safe_folder(path):
    """Safely get folder name relative to VAULT_DIR, returning 'uploaded' for bare paths."""
    try:
        if path.parent == Path('.') or not path.parent.parts:
            return 'uploaded'
        return str(path.parent.relative_to(VAULT_DIR)) if path.parent != VAULT_DIR else ''
    except (ValueError, AttributeError):
        return 'uploaded'

def safe_rel(path):
    """Safely get path relative to VAULT_DIR."""
    try:
        if path.parent == Path('.') or not path.parent.parts:
            return path.name
        return str(path.relative_to(VAULT_DIR))
    except (ValueError, AttributeError):
        return path.name


def search(query, context_lines=3, max_results=200):
    if not query.strip():
        return []
    index = build_index()
    results = []
    try:
        pattern = re.compile(re.escape(query), re.IGNORECASE)
    except re.error:
        pattern = re.compile(re.escape(query), re.IGNORECASE)

    for path, lines in index:
        file_matches = []
        for i, line in enumerate(lines):
            if pattern.search(line):
                start = max(0, i - context_lines)
                end   = min(len(lines), i + context_lines + 1)
                ctx   = lines[start:end]
                # Highlight the match line within context
                highlighted = []
                for j, cline in enumerate(ctx):
                    abs_line = start + j
                    if abs_line == i:
                        highlighted.append({'text': cline, 'match': True, 'lineno': abs_line + 1})
                    else:
                        highlighted.append({'text': cline, 'match': False, 'lineno': abs_line + 1})
                file_matches.append({
                    'lineno': i + 1,
                    'context': highlighted,
                })
        if file_matches:
            rel = safe_rel(path)
            folder = safe_folder(path)
            results.append({
                'file': rel,
                'folder': folder,
                'filename': path.name,
                'matches': file_matches,
                'match_count': len(file_matches),
            })
        if len(results) >= max_results:
            break

    results.sort(key=lambda x: -x['match_count'])
    return results


def search_bookmarks(query, max_results=50):
    """Search pipeline.db bookmarks using FTS."""
    if not DB_PATH or not DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        # Try FTS first
        try:
            rows = conn.execute("""
                SELECT b.id, b.title, b.url, b.domain, b.excerpt,
                       b.tags, b.ddc_code, b.ddc_label, b.created
                FROM bookmarks_fts
                JOIN bookmarks b ON b.id = bookmarks_fts.rowid
                WHERE bookmarks_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (query, max_results)).fetchall()
        except Exception:
            # Fallback to LIKE search if FTS unavailable
            like = f'%{query}%'
            rows = conn.execute("""
                SELECT id, title, url, domain, excerpt,
                       tags, ddc_code, ddc_label, created
                FROM bookmarks
                WHERE title LIKE ? OR excerpt LIKE ? OR tags LIKE ?
                ORDER BY created DESC
                LIMIT ?
            """, (like, like, like, max_results)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f'  Bookmark search error: {e}')
        return []


@app.route('/api/bookmark-folders')
def api_bookmark_folders():
    if not DB_PATH or not DB_PATH.exists():
        return jsonify([])
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT ddc_code, ddc_label, COUNT(*) as n
            FROM bookmarks
            WHERE ddc_code != ''
            GROUP BY ddc_code
            ORDER BY n DESC
            LIMIT 40
        """).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify([])

@app.route('/api/bookmark-browse')
def api_bookmark_browse():
    ddc = request.args.get('ddc', '').strip()
    if not DB_PATH or not DB_PATH.exists() or not ddc:
        return jsonify([])
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, title, url, domain, excerpt, tags, ddc_code, ddc_label, created
            FROM bookmarks
            WHERE ddc_code = ? OR ddc_code LIKE ?
            ORDER BY created DESC
            LIMIT 100
        """, (ddc, ddc + '.%')).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify([])


@app.route('/api/serve')
def api_serve():
    """Serve raw file for browser rendering (PDFs, images)."""
    import mimetypes
    from flask import send_file as _send
    rel = request.args.get('path', '')
    # Check vault
    path = VAULT_DIR / rel
    if path.exists() and path.is_file():
        mime = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
        return _send(path, mimetype=mime)
    return jsonify({'error': 'not found'}), 404


@app.route('/api/folder-files')
def api_folder_files():
    """Return file cards for a vault folder, with extracted title/author metadata."""
    folder = request.args.get('folder', '').strip()
    index = build_index()
    results = []
    for path, lines in index:
        rel_folder = safe_folder(path)
        # Match folder
        if folder and rel_folder != folder and not rel_folder.startswith(folder):
            continue
        if not folder and rel_folder:
            continue  # 'All' handled differently
        # Extract title and author from file content
        title = path.stem.replace('-', ' ').replace('_', ' ')
        author = ''
        word_count = sum(len(l.split()) for l in lines)
        # Try to extract from first 30 lines
        head = lines[:30]
        for line in head:
            l = line.strip()
            # Markdown frontmatter or plain text patterns
            if l.lower().startswith('title:'):
                title = l[6:].strip().strip('"').strip("'") or title
            elif l.lower().startswith('author:'):
                author = l[7:].strip().strip('"').strip("'")
            elif l.lower().startswith('by ') and not author:
                author = l[3:].strip()
            # Gutenberg pattern: "Title: X" / "Author: X"
            elif l.startswith('Title: ') and not title:
                title = l[7:].strip()
            elif l.startswith('Author: ') and not author:
                author = l[8:].strip()
        # First non-empty line as fallback title for short notes
        if title == path.stem.replace('-', ' ').replace('_', ' ') and lines:
            for line in lines[:5]:
                stripped = line.strip().lstrip('#').strip()
                if stripped and len(stripped) > 3:
                    title = stripped[:80]
                    break
        rel = safe_rel(path)
        results.append({
            'filename': path.name,
            'path': rel,
            'folder': rel_folder,
            'title': title,
            'author': author,
            'words': word_count,
        })
    results.sort(key=lambda x: x['title'].lower())
    return jsonify(results)


def init_clips_table():
    """Create clips table in pipeline.db if it doesn't exist."""
    if not DB_PATH or not DB_PATH.exists():
        return
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clips (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT NOT NULL,
                source_path TEXT,
                source_folder TEXT,
                selected_text TEXT NOT NULL,
                user_note   TEXT DEFAULT '',
                line_hint   INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clips_source ON clips(source_file)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clips_created ON clips(created_at)")
        # FTS for clips
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS clips_fts USING fts5(
                selected_text, user_note, source_file,
                content='clips', content_rowid='id'
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'  clips table init error: {e}')

@app.route('/api/clips/save', methods=['POST'])
def api_clips_save():
    data = request.json or {}
    text = data.get('selected_text', '').strip()
    if not text:
        return jsonify({'error': 'no text selected'}), 400
    if not DB_PATH or not DB_PATH.exists():
        return jsonify({'error': 'pipeline.db not connected'}), 400
    from datetime import datetime as _dt
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        INSERT INTO clips (source_file, source_path, source_folder, selected_text, user_note, line_hint, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get('source_file', ''),
        data.get('source_path', ''),
        data.get('source_folder', ''),
        text,
        data.get('user_note', ''),
        data.get('line_hint', 0),
        _dt.now().isoformat(timespec='seconds')
    ))
    conn.execute("INSERT INTO clips_fts(clips_fts) VALUES('rebuild')")
    conn.commit()
    clip_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return jsonify({'ok': True, 'id': clip_id})

@app.route('/api/clips')
def api_clips_list():
    if not DB_PATH or not DB_PATH.exists():
        return jsonify([])
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        source = request.args.get('source', '').strip()
        if source:
            rows = conn.execute(
                "SELECT * FROM clips WHERE source_file = ? ORDER BY created_at DESC",
                (source,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM clips ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception:
        return jsonify([])

@app.route('/api/clips/search')
def api_clips_search():
    q = request.args.get('q', '').strip()
    if not q or not DB_PATH or not DB_PATH.exists():
        return jsonify([])
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("""
                SELECT c.* FROM clips_fts
                JOIN clips c ON c.id = clips_fts.rowid
                WHERE clips_fts MATCH ?
                ORDER BY rank LIMIT 50
            """, (q,)).fetchall()
        except Exception:
            like = f'%{q}%'
            rows = conn.execute(
                "SELECT * FROM clips WHERE selected_text LIKE ? OR user_note LIKE ? ORDER BY created_at DESC LIMIT 50",
                (like, like)
            ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception:
        return jsonify([])

@app.route('/api/clips/delete/<int:clip_id>', methods=['DELETE'])
def api_clips_delete(clip_id):
    if not DB_PATH or not DB_PATH.exists():
        return jsonify({'error': 'no db'}), 400
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    conn.execute("INSERT INTO clips_fts(clips_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/clips/count')
def api_clips_count():
    if not DB_PATH or not DB_PATH.exists():
        return jsonify({'count': 0})
    try:
        conn = sqlite3.connect(str(DB_PATH))
        count = conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        conn.close()
        return jsonify({'count': count})
    except Exception:
        return jsonify({'count': 0})

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/api/search')
def api_search():
    q      = request.args.get('q', '').strip()
    ctx    = int(request.args.get('ctx', 3))
    scope  = request.args.get('scope', 'all')  # 'all', 'vault', 'bookmarks'
    results = []
    total_matches = 0
    bookmarks = []
    if q:
        if scope in ('all', 'vault'):
            results = search(q, context_lines=ctx)
            total_matches = sum(r['match_count'] for r in results)
        if scope in ('all', 'bookmarks'):
            bookmarks = search_bookmarks(q)
    return jsonify({
        'query': q,
        'files': len(results),
        'total_matches': total_matches,
        'results': results,
        'bookmarks': bookmarks,
        'bookmark_count': len(bookmarks),
    })

@app.route('/api/file')
def api_file():
    rel = request.args.get('path', '')
    if rel in _UPLOADED_FILES:
        text = '\n'.join(_UPLOADED_FILES[rel])
        return jsonify({'path': rel, 'text': text, 'source': 'uploaded'})
    # Fall back to vault file
    path = VAULT_DIR / rel
    if not path.exists() or not path.is_file():
        return jsonify({'error': 'not found'}), 404
    text = path.read_text(errors='ignore')
    return jsonify({'path': rel, 'text': text, 'source': 'vault'})

@app.route('/api/upload', methods=['POST'])
def api_upload():
    if 'file' not in request.files:
        return jsonify({'error': 'no file'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'no filename'}), 400

    name = f.filename
    ext  = Path(name).suffix.lower()

    # Read content based on type
    raw = f.read()
    text = ''

    if ext == '.csv':
        import csv as csvlib, io as _io
        from datetime import datetime as _dt
        from urllib.parse import urlparse as _up
        text_raw = raw.decode('utf-8', errors='ignore')
        sample = csvlib.DictReader(_io.StringIO(text_raw))
        fieldnames = sample.fieldnames or []
        if 'url' in fieldnames and 'tags' in fieldnames and DB_PATH and DB_PATH.exists():
            # Raindrop export — ingest into pipeline.db with inline DDC mapping
            TAG_DDC = {
                'recipe':'641.5','cooking':'641.5','food':'641',
                'recipe-soup':'641.57','recipe-chicken':'641.63','recipe-beef':'641.61',
                'recipe-pork':'641.62','recipe-slow-cooker':'641.51','recipe-breakfast':'641.56',
                'recipe-dessert':'641.82','recipe-italian':'641.71','recipe-mexican':'641.72',
                'recipe-baking':'641.53','recipe-bbq-grill':'641.52',
                'con-project-kitchen-bath':'690.91','con-project-kitchen-bath-cabinet':'690.911',
                'con-project-kitchen-bath-appliance':'690.912','con-project-exterior-deck':'690.921',
                'con-project-structure-roof':'690.93','con-project-interior-floor':'690.941',
                'con-trade-masonry-tile':'690.3','con-trade-plumbing':'690.4',
                'con-trade-electric':'690.5','con-trade-carpentry':'690.6',
                'con-trade-framing':'690.2','con-source-supplier':'690.7',
                'con-source-homedepot':'690.71','con-source-lowes':'690.72',
                'con-source-menards':'690.73','con-trade-hardware':'690.8',
                'business-estimating':'690.96','plum-design-build':'690.96',
                'sketchup':'720','architecture':'720','design-arch':'720',
                'code':'005','github':'005','jekyll':'005','python':'005','css':'005',
                'html':'005','javascript':'005','markdown':'005',
                'history':'900','american-history':'973','biography':'920',
                'politics':'320','philosophy':'100','ramblings':'100',
                'bible':'220','theology':'230','religion':'200',
                'music':'780','travel':'910','finance':'332',
                'health':'613','medical':'610','science':'500','math':'510',
                'amazon':'381','affiliate':'381','office':'651',
                'home':'643','repair':'643','furniture':'749','design':'745',
                'map':'912','photography':'770','art':'700',
            }
            DDC_LABELS = {
                '005':'Computer programming','100':'Philosophy','200':'Religion',
                '220':'Bible','230':'Christian theology','320':'Political science',
                '332':'Financial economics','381':'Commerce','384':'Telecommunications',
                '391':'Costume','410':'Linguistics','500':'Science','510':'Mathematics',
                '610':'Medicine','613':'Personal health','620':'Engineering',
                '641':'Food & drink','641.5':'Cooking','641.51':'Cooking — slow cooker',
                '641.52':'Cooking — grilling','641.53':'Cooking — baking',
                '641.56':'Cooking — breakfast','641.57':'Cooking — soup',
                '641.61':'Cooking — beef','641.62':'Cooking — pork',
                '641.63':'Cooking — chicken','641.71':'Cooking — Italian',
                '641.72':'Cooking — Mexican','641.82':'Cooking — desserts',
                '643':'Housing & household equipment','645':'Household furnishings',
                '690.2':'Framing & structure','690.3':'Masonry & tile',
                '690.4':'Plumbing','690.5':'Electrical','690.6':'Finish & millwork',
                '690.7':'Suppliers & sources','690.71':'Source — Home Depot',
                '690.72':'Source — Lowes','690.73':'Source — Menards',
                '690.8':'Product research','690.91':'Project — kitchen & bath',
                '690.911':'Project — cabinets','690.912':'Project — appliances',
                '690.921':'Project — deck','690.93':'Project — structure & roof',
                '690.941':'Project — floors','690.96':'Business — estimating',
                '690.97':'Project — Brian House',
                '720':'Architecture','745':'Decorative arts','749':'Furniture',
                '770':'Photography','780':'Music','791':'Public performances',
                '800':'Literature','810':'American literature','820':'English literature',
                '900':'History & geography','910':'Geography & travel',
                '912':'Maps & atlases','920':'Biography','973':'US History',
            }
            def _ddc_for_row(row):
                tags = [t.strip() for t in row.get('tags','').split(',') if t.strip()]
                codes = [TAG_DDC[t] for t in tags if t in TAG_DDC]
                if not codes:
                    domain = _up(row.get('url','')).netloc.replace('www.','')
                    dom_map = {
                        'youtube.com':'791','github.com':'005','wikipedia.org':'900',
                        'amazon.com':'381','allrecipes.com':'641.5','seriouseats.com':'641.5',
                        'foodnetwork.com':'641.5','homedepot.com':'690.7',
                        'lowes.com':'690.7','menards.com':'690.7','sketchup.com':'720',
                        'spotify.com':'780','pinterest.com':'745',
                    }
                    for d, c in dom_map.items():
                        if d in domain: codes.append(c); break
                if not codes: return '', ''
                best = max(codes, key=len)
                return best, DDC_LABELS.get(best, '')
            try:
                reader2 = csvlib.DictReader(_io.StringIO(text_raw))
                rows_data = list(reader2)
                conn = sqlite3.connect(str(DB_PATH))
                conn.row_factory = sqlite3.Row
                inserted = 0
                for row in rows_data:
                    ddc_code, ddc_label = _ddc_for_row(row)
                    url = row.get('url','').strip()
                    if not url: continue
                    domain = _up(url).netloc.replace('www.','')
                    conn.execute(
                        'INSERT OR REPLACE INTO bookmarks '
                        '(id,title,url,domain,excerpt,note,cover,tags,ddc_code,ddc_label,favorite,created,indexed_at) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (int(row.get('id',0) or 0), row.get('title','').strip(), url, domain,
                         row.get('excerpt','').strip(), row.get('note','').strip(),
                         row.get('cover','').strip(), row.get('tags','').strip(),
                         ddc_code, ddc_label,
                         1 if row.get('favorite','').lower()=='true' else 0,
                         row.get('created','').strip(),
                         _dt.now().isoformat(timespec='seconds'))
                    )
                    inserted += 1
                conn.execute("INSERT INTO bookmarks_fts(bookmarks_fts) VALUES('rebuild')")
                conn.commit(); conn.close()
                return jsonify({'ok': True, 'filename': name, 'lines': inserted,
                                'words': inserted, 'csv_type': 'raindrop',
                                'message': f'{inserted} bookmarks ingested'})
            except Exception as e:
                return jsonify({'error': f'Raindrop ingest failed: {e}'}), 400
        else:
            text = text_raw
    elif ext in ('.txt', '.md'):
        text = raw.decode('utf-8', errors='ignore')
    elif ext == '.rtf':
        # Strip basic RTF control words
        s = raw.decode('latin-1', errors='ignore')
        s = re.sub(r'\\[a-z]+\d*\s?', ' ', s)
        s = re.sub(r'[{}]', '', s)
        s = re.sub(r'\s+', ' ', s)
        text = s.strip()
    elif ext == '.pdf':
        try:
            import tempfile, os, subprocess, io as _io
            # Step 1: PyMuPDF — best for text-based PDFs
            text = ''
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(stream=raw, filetype='pdf')
                pages_text = []
                for page in doc:
                    t = page.get_text('text').strip()
                    if t:
                        pages_text.append(t)
                doc.close()
                text = '\n\n'.join(pages_text).strip()
            except ImportError:
                pass
            except Exception:
                pass
            # Step 2: pdftotext fallback (poppler — brew install poppler)
            if not text:
                try:
                    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                        tmp.write(raw)
                        tmp_path = tmp.name
                    r2 = subprocess.run(['pdftotext', tmp_path, '-'],
                        capture_output=True, text=True, timeout=30)
                    os.unlink(tmp_path)
                    text = r2.stdout.strip()
                except Exception:
                    pass
            # Step 3: tesseract OCR for scanned/image PDFs
            if not text:
                try:
                    import fitz
                    doc = fitz.open(stream=raw, filetype='pdf')
                    ocr_texts = []
                    for page_num, page in enumerate(doc):
                        mat = fitz.Matrix(2, 2)  # 2x zoom = ~144dpi
                        pix = page.get_pixmap(matrix=mat)
                        img_bytes = pix.tobytes('png')
                        r_ocr = subprocess.run(
                            ['tesseract', 'stdin', 'stdout', '--dpi', '144', '-l', 'eng'],
                            input=img_bytes, capture_output=True, timeout=60
                        )
                        if r_ocr.stdout:
                            t = r_ocr.stdout.decode('utf-8', errors='ignore').strip()
                            if t:
                                ocr_texts.append(f'[Page {page_num+1}]\n{t}')
                    doc.close()
                    if ocr_texts:
                        text = '[OCR]\n\n' + '\n\n'.join(ocr_texts)
                except Exception:
                    pass
            if not text:
                text = '(No text extracted. Install: pip3 install pymupdf --break-system-packages)'
        except Exception as e:
            return jsonify({'error': f'PDF conversion failed: {e}'}), 400
    else:
        return jsonify({'error': f'Unsupported file type: {ext}'}), 400

    lines = text.splitlines()
    _UPLOADED_FILES[name] = lines
    word_count = sum(len(l.split()) for l in lines)
    return jsonify({
        'ok': True,
        'filename': name,
        'lines': len(lines),
        'words': word_count,
    })

@app.route('/api/uploaded')
def api_uploaded():
    return jsonify([
        {'filename': k, 'lines': len(v), 'words': sum(len(l.split()) for l in v)}
        for k, v in _UPLOADED_FILES.items()
    ])

@app.route('/api/upload/remove', methods=['POST'])
def api_upload_remove():
    name = request.json.get('filename', '')
    if name in _UPLOADED_FILES:
        del _UPLOADED_FILES[name]
    return jsonify({'ok': True})

@app.route('/api/fetch_url', methods=['POST'])
def api_fetch_url():
    if not _URL_FETCH_OK:
        return jsonify({'error': 'requests / beautifulsoup4 not installed'}), 500
    url = (request.json or {}).get('url', '').strip()
    if not url:
        return jsonify({'error': 'no url'}), 400
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; VaultSearch/1.0)'}
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        return jsonify({'error': f'Fetch failed: {e}'}), 400

    soup = BeautifulSoup(resp.text, 'html.parser')

    title_tag = soup.find('title')
    raw_title = title_tag.get_text(strip=True) if title_tag else ''
    if not raw_title:
        from urllib.parse import urlparse
        p = urlparse(url)
        raw_title = (p.netloc + p.path).strip('/')
    name = re.sub(r'[^\w\s\-.,()]', '', raw_title)[:80].strip() + '.txt'

    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'noscript']):
        tag.decompose()

    text = soup.get_text(separator='\n')
    text = re.sub(r'\n{3,}', '\n\n', text).strip()

    lines = text.splitlines()
    _UPLOADED_FILES[name] = lines
    word_count = sum(len(l.split()) for l in lines)
    return jsonify({'ok': True, 'filename': name, 'lines': len(lines), 'words': word_count})

@app.route('/api/save_to_vault', methods=['POST'])
def api_save_to_vault():
    """Save a fetched/uploaded file permanently to the vault's fetched/ folder."""
    data = request.json or {}
    filename = data.get('filename', '').strip()
    if not filename:
        return jsonify({'error': 'no filename'}), 400
    if filename not in _UPLOADED_FILES:
        return jsonify({'error': 'file not in session — re-fetch first'}), 404

    # Sanitise filename
    safe_name = re.sub(r'[^\w\s\-.,()]', '', filename).strip()
    if not safe_name.endswith('.txt'):
        safe_name = safe_name + '.txt'

    # Save to vault fetched/ subfolder
    fetched_dir = VAULT_DIR / 'fetched'
    fetched_dir.mkdir(exist_ok=True)
    dest = fetched_dir / safe_name

    # Don't overwrite without a counter suffix
    if dest.exists():
        stem = dest.stem
        counter = 1
        while dest.exists():
            dest = fetched_dir / f'{stem}-{counter}.txt'
            counter += 1

    lines = _UPLOADED_FILES[filename]
    dest.write_text('\n'.join(lines), encoding='utf-8')

    # Force re-index so file appears in vault immediately
    global _INDEX
    _INDEX = None
    build_index()

    return jsonify({
        'ok': True,
        'saved_as': dest.name,
        'path': safe_rel(dest),
        'words': sum(len(l.split()) for l in lines)
    })

@app.route('/api/stats')
def api_stats():
    try:
        index = build_index()
        folders = {}
        total_words = 0
        for path, lines in index:
            try:
                folder = safe_folder(path) or '(root)'
            except Exception:
                folder = '(root)'
            folders[folder] = folders.get(folder, 0) + 1
            try:
                total_words += sum(len(l.split()) for l in lines)
            except Exception:
                pass
        bookmark_count = 0
        if DB_PATH and DB_PATH.exists():
            try:
                conn = sqlite3.connect(str(DB_PATH))
                bookmark_count = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
                conn.close()
            except Exception:
                pass
        return jsonify({
            'files': len(index),
            'folders': dict(sorted(folders.items(), key=lambda x: -x[1])),
            'total_words': total_words,
            'uploaded': len(_UPLOADED_FILES),
            'bookmarks': bookmark_count,
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'files': 0, 'folders': {}, 'total_words': 0,
            'uploaded': 0, 'bookmarks': 0, 'error': str(e)
        })

HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vault Search — Upload</title>
<link href="https://fonts.googleapis.com/css2?family=Libre+Baskerville:ital,wght@0,400;0,700;1,400&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {
  --ink: #1a1714;
  --ink2: #3d3830;
  --ink3: #6b6259;
  --paper: #f7f3ed;
  --paper2: #efe9e0;
  --paper3: #e5ddd1;
  --accent: #8b3a1e;
  --accent2: #c4622d;
  --gold: #b8860b;
  --match-bg: #fdf3e0;
  --match-border: #c4622d;
  --serif: 'Libre Baskerville', Georgia, serif;
  --mono: 'JetBrains Mono', monospace;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: var(--serif);
  background: var(--paper);
  color: var(--ink);
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

/* ── Header ── */
header {
  background: var(--ink);
  color: var(--paper);
  padding: 0 32px;
  display: flex;
  align-items: center;
  gap: 24px;
  height: 64px;
  border-bottom: 3px solid var(--accent);
  position: sticky;
  top: 0;
  z-index: 100;
}

.logo {
  font-size: 1.05rem;
  letter-spacing: .12em;
  text-transform: uppercase;
  color: var(--paper3);
  white-space: nowrap;
  font-weight: 700;
}

.logo span { color: var(--accent2); }

.search-wrap {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 0;
  max-width: 680px;
}

#search-input {
  flex: 1;
  background: rgba(255,255,255,.08);
  border: 1px solid rgba(255,255,255,.2);
  border-right: none;
  color: var(--paper);
  padding: 10px 16px;
  font-family: var(--serif);
  font-size: .95rem;
  outline: none;
  border-radius: 3px 0 0 3px;
  transition: border-color .2s, background .2s;
}
#search-input:focus {
  background: rgba(255,255,255,.13);
  border-color: var(--accent2);
}
#search-input::placeholder { color: rgba(255,255,255,.35); font-style: italic; }

#search-btn {
  background: var(--accent);
  border: 1px solid var(--accent);
  color: var(--paper);
  padding: 10px 20px;
  font-family: var(--serif);
  font-size: .9rem;
  cursor: pointer;
  border-radius: 0 3px 3px 0;
  transition: background .2s;
  white-space: nowrap;
}
#search-btn:hover { background: var(--accent2); }

.ctx-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  color: rgba(255,255,255,.5);
  font-size: .78rem;
  white-space: nowrap;
}
.ctx-wrap select {
  background: rgba(255,255,255,.08);
  border: 1px solid rgba(255,255,255,.2);
  color: var(--paper);
  padding: 4px 8px;
  border-radius: 3px;
  font-family: var(--serif);
  font-size: .78rem;
  outline: none;
  cursor: pointer;
}

/* ── Layout ── */
.app-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* ── Sidebar ── */
aside {
  width: 260px;
  min-width: 260px;
  border-right: 1px solid var(--paper3);
  background: var(--paper2);
  overflow-y: auto;
  height: calc(100vh - 67px);
  position: sticky;
  top: 67px;
}

.sb-header {
  padding: 14px 16px 8px;
  font-size: .65rem;
  text-transform: uppercase;
  letter-spacing: .15em;
  color: var(--ink3);
  border-bottom: 1px solid var(--paper3);
}

.folder-list { padding: 8px 0; }
.folder-item {
  display: flex;
  justify-content: space-between;
  padding: 4px 16px;
  font-size: .78rem;
  color: var(--ink3);
  cursor: pointer;
  transition: background .12s;
}
.folder-item:hover { background: var(--paper3); color: var(--ink); }
.folder-item.active { background: var(--paper3); color: var(--accent); font-weight: 700; }
.folder-item .fc { font-family: var(--mono); font-size: .72rem; color: var(--ink3); }

/* ── Main ── */
main {
  flex: 1;
  overflow-y: auto;
  padding: 24px 32px;
}

.status-bar {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 20px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--paper3);
}
.status-bar .result-count {
  font-size: 1rem;
  color: var(--ink2);
}
.status-bar .result-count strong { color: var(--accent); font-family: var(--mono); }
.status-bar .query-echo {
  font-style: italic;
  color: var(--ink3);
  font-size: .88rem;
}

.loading {
  text-align: center;
  padding: 60px;
  color: var(--ink3);
  font-style: italic;
}

/* ── Result card ── */
.result-card {
  margin-bottom: 20px;
  border: 1px solid var(--paper3);
  border-radius: 4px;
  overflow: hidden;
  background: white;
  transition: box-shadow .15s;
}
.result-card:hover { box-shadow: 0 2px 12px rgba(0,0,0,.08); }

.card-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 10px 14px;
  background: var(--paper2);
  border-bottom: 1px solid var(--paper3);
  cursor: pointer;
  gap: 10px;
}
.card-header:hover { background: var(--paper3); }

.card-filename {
  font-family: var(--mono);
  font-size: .82rem;
  color: var(--ink);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-folder {
  font-size: .72rem;
  color: var(--ink3);
  white-space: nowrap;
}
.card-badge {
  background: var(--accent);
  color: white;
  font-family: var(--mono);
  font-size: .68rem;
  padding: 2px 7px;
  border-radius: 10px;
  white-space: nowrap;
  flex-shrink: 0;
}

/* ── Match blocks ── */
.match-block {
  border-bottom: 1px solid var(--paper3);
}
.match-block:last-child { border-bottom: none; }

.match-jump {
  font-family: var(--mono);
  font-size: .68rem;
  color: var(--ink3);
  padding: 5px 14px 0;
}

.context-lines { padding: 4px 0 8px; }

.ctx-line {
  display: flex;
  gap: 0;
  font-family: var(--mono);
  font-size: .8rem;
  line-height: 1.6;
}
.ctx-line.is-match {
  background: var(--match-bg);
  border-left: 3px solid var(--match-border);
}
.ctx-line:not(.is-match) { border-left: 3px solid transparent; }

.line-num {
  min-width: 48px;
  text-align: right;
  padding: 0 10px 0 4px;
  color: var(--ink3);
  user-select: none;
  font-size: .72rem;
  flex-shrink: 0;
  opacity: .6;
}
.ctx-line.is-match .line-num { opacity: 1; color: var(--accent); }

.line-text {
  flex: 1;
  padding: 0 14px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--ink2);
}
.ctx-line.is-match .line-text { color: var(--ink); font-weight: 500; }

mark {
  background: #ffe082;
  color: var(--ink);
  padding: 0 1px;
  border-radius: 2px;
}

/* ── Full file viewer ── */
.file-viewer {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(26,23,20,.85);
  z-index: 200;
  align-items: center;
  justify-content: center;
}
.file-viewer.open { display: flex; }
.viewer-modal {
  background: var(--paper);
  border-radius: 6px;
  max-width: 1100px;
  width: 96vw;
  max-height: 94vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 20px 60px rgba(0,0,0,.5);
}
.viewer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 18px;
  background: var(--ink);
  color: var(--paper);
  gap: 12px;
  flex-shrink: 0;
}
.viewer-title {
  font-family: var(--mono);
  font-size: .82rem;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.viewer-close {
  background: none;
  border: none;
  color: var(--paper3);
  font-size: 1.1rem;
  cursor: pointer;
  padding: 2px 8px;
  border-radius: 3px;
}
.viewer-close:hover { background: rgba(255,255,255,.1); color: white; }
.viewer-body {
  flex: 1;
  overflow-y: auto;
  padding: 24px 28px;
  font-family: var(--serif);
  font-size: .9rem;
  line-height: 1.9;
  color: var(--ink2);
  white-space: pre-wrap;
  word-break: break-word;
}
.viewer-body mark { background: #ffe082; }
.viewer-body.pdf-mode { padding: 0; overflow: hidden; }
.viewer-body.csv-mode { padding: 0; overflow: auto; white-space: normal; }
.viewer-pdf { width: 100%; height: 100%; border: none; display: block; }
.csv-table { width: 100%; border-collapse: collapse; font-family: var(--mono); font-size: .75rem; }
.csv-table th {
  background: var(--ink);
  color: var(--paper);
  padding: 8px 12px;
  text-align: left;
  position: sticky;
  top: 0;
  white-space: nowrap;
  cursor: pointer;
  user-select: none;
}
.csv-table th:hover { background: var(--ink2); }
.csv-table th .sort-arrow { margin-left: 4px; opacity: .5; font-size: .65rem; }
.csv-table th.sorted .sort-arrow { opacity: 1; }
.csv-table td { padding: 6px 12px; border-bottom: 1px solid var(--paper3); color: var(--ink2); max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.csv-table tr:hover td { background: var(--paper2); }
.csv-table tr:nth-child(even) td { background: var(--paper); }
.csv-table tr:nth-child(even):hover td { background: var(--paper2); }
.csv-meta { padding: 6px 12px; font-size: .72rem; color: var(--ink3); font-family: var(--mono); background: var(--paper2); border-bottom: 1px solid var(--paper3); }

/* ── Welcome state ── */
.welcome {
  text-align: center;
  padding: 80px 40px;
  color: var(--ink3);
}
.welcome h2 {
  font-size: 1.6rem;
  font-weight: 400;
  margin-bottom: 12px;
  color: var(--ink2);
  font-style: italic;
}
.welcome p { font-size: .9rem; line-height: 1.7; max-width: 480px; margin: 0 auto 8px; }
.welcome .hint {
  font-family: var(--mono);
  font-size: .78rem;
  background: var(--paper2);
  border: 1px solid var(--paper3);
  padding: 8px 16px;
  border-radius: 3px;
  display: inline-block;
  margin-top: 16px;
  color: var(--accent);
}


/* ── URL fetch ── */
.url-fetch-wrap {
  padding: 8px 12px 10px;
  border-bottom: 1px solid var(--paper3);
}
.url-fetch-wrap input {
  width: 100%;
  background: white;
  border: 1px solid var(--paper3);
  border-radius: 3px 3px 0 0;
  padding: 6px 10px;
  font-family: var(--mono);
  font-size: .68rem;
  color: var(--ink);
  outline: none;
  transition: border-color .2s;
}
.url-fetch-wrap input:focus { border-color: var(--accent2); }
.url-fetch-wrap input::placeholder { color: var(--ink3); font-style: italic; }
.url-fetch-wrap button {
  width: 100%;
  background: var(--accent);
  border: none;
  color: white;
  padding: 6px;
  font-family: var(--serif);
  font-size: .75rem;
  cursor: pointer;
  border-radius: 0 0 3px 3px;
  transition: background .2s;
}
.url-fetch-wrap button:hover { background: var(--accent2); }
#url-status {
  margin-top: 5px;
  font-size: .68rem;
  font-style: italic;
  color: var(--ink3);
  min-height: 14px;
  font-family: var(--mono);
}
.save-vault-btn {
  margin-top: 5px;
  width: 100%;
  background: var(--accent);
  border: none;
  color: white;
  padding: 5px 10px;
  font-family: var(--serif);
  font-size: .73rem;
  cursor: pointer;
  border-radius: 3px;
  transition: background .2s;
  display: none;
}
.save-vault-btn:hover { background: var(--accent2); }
.save-vault-btn.show { display: block; }
.save-vault-btn.saved { background: #4a7c4e; cursor: default; }
#url-status.err { color: var(--accent); }
#url-status.ok  { color: #4a7c4e; }
/* ── Scope toggle ── */
.scope-wrap {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: 8px;
}
.scope-btn {
  background: rgba(255,255,255,.08);
  border: 1px solid rgba(255,255,255,.18);
  color: rgba(255,255,255,.6);
  padding: 5px 10px;
  font-family: var(--serif);
  font-size: .72rem;
  cursor: pointer;
  border-radius: 3px;
  transition: all .15s;
  white-space: nowrap;
}
.scope-btn:hover { background: rgba(255,255,255,.14); color: var(--paper); }
.scope-btn.active { background: var(--accent); border-color: var(--accent); color: var(--paper); }

/* ── Clips ── */
.clip-btn {
  position: absolute;
  background: var(--ink);
  color: var(--paper);
  border: none;
  border-radius: 4px;
  padding: 5px 10px;
  font-family: var(--serif);
  font-size: .75rem;
  cursor: pointer;
  z-index: 300;
  display: none;
  box-shadow: 0 3px 12px rgba(0,0,0,.35);
  white-space: nowrap;
  transition: background .15s;
}
.clip-btn:hover { background: var(--accent); }

.clip-note-bar {
  display: none;
  position: absolute;
  background: var(--ink);
  border-radius: 4px;
  padding: 8px 10px;
  z-index: 300;
  box-shadow: 0 3px 12px rgba(0,0,0,.35);
  flex-direction: column;
  gap: 6px;
  min-width: 260px;
}
.clip-note-bar.show { display: flex; }
.clip-note-inp {
  background: rgba(255,255,255,.12);
  border: 1px solid rgba(255,255,255,.25);
  border-radius: 3px;
  color: var(--paper);
  padding: 5px 8px;
  font-family: var(--serif);
  font-size: .78rem;
  outline: none;
  width: 100%;
}
.clip-note-inp::placeholder { color: rgba(255,255,255,.4); font-style: italic; }
.clip-note-inp:focus { border-color: var(--accent2); }
.clip-save-row { display: flex; gap: 6px; }
.clip-save-btn {
  flex: 1;
  background: var(--accent);
  border: none;
  color: white;
  padding: 5px 8px;
  border-radius: 3px;
  font-family: var(--serif);
  font-size: .75rem;
  cursor: pointer;
}
.clip-save-btn:hover { background: var(--accent2); }
.clip-cancel-btn {
  background: rgba(255,255,255,.1);
  border: none;
  color: rgba(255,255,255,.6);
  padding: 5px 8px;
  border-radius: 3px;
  font-family: var(--serif);
  font-size: .75rem;
  cursor: pointer;
}
.clip-cancel-btn:hover { background: rgba(255,255,255,.2); color: white; }

/* Clips sidebar folder */
.clips-count-badge {
  background: var(--accent2);
  color: white;
  font-size: .62rem;
  padding: 1px 5px;
  border-radius: 7px;
  font-family: var(--mono);
}

/* Clips results */
.clip-card {
  background: white;
  border: 1px solid var(--paper3);
  border-left: 3px solid var(--gold);
  border-radius: 4px;
  padding: 12px 14px;
  margin-bottom: 8px;
  position: relative;
}
.clip-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,.07); }
.clip-text {
  font-family: var(--serif);
  font-size: .88rem;
  color: var(--ink);
  line-height: 1.65;
  font-style: italic;
  margin-bottom: 6px;
}
.clip-text::before { content: '"'; color: var(--accent); font-size: 1.1rem; }
.clip-text::after  { content: '"'; color: var(--accent); font-size: 1.1rem; }
.clip-note {
  font-size: .75rem;
  color: var(--ink2);
  margin-bottom: 6px;
  padding: 4px 8px;
  background: var(--paper2);
  border-radius: 3px;
  display: none;
}
.clip-note.show { display: block; }
.clip-meta {
  font-family: var(--mono);
  font-size: .62rem;
  color: var(--ink3);
  display: flex;
  gap: 10px;
  align-items: center;
}
.clip-source-link {
  color: var(--accent2);
  cursor: pointer;
  text-decoration: none;
}
.clip-source-link:hover { text-decoration: underline; }
.clip-del {
  position: absolute;
  top: 8px; right: 8px;
  background: none;
  border: none;
  color: var(--paper3);
  cursor: pointer;
  font-size: .85rem;
  padding: 2px 5px;
  border-radius: 3px;
  opacity: 0;
  transition: opacity .15s, color .15s;
}
.clip-card:hover .clip-del { opacity: 1; }
.clip-del:hover { color: var(--accent); background: var(--paper2); }

/* ── Folder file cards ── */
.file-card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 10px;
  margin-bottom: 24px;
}
.file-card {
  background: white;
  border: 1px solid var(--paper3);
  border-radius: 4px;
  padding: 12px 14px;
  cursor: pointer;
  transition: box-shadow .15s, border-color .15s;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.file-card:hover {
  box-shadow: 0 2px 10px rgba(0,0,0,.08);
  border-color: var(--accent2);
}
.file-card-title {
  font-family: var(--serif);
  font-size: .88rem;
  color: var(--ink);
  line-height: 1.3;
  font-weight: 700;
}
.file-card-author {
  font-size: .75rem;
  color: var(--accent2);
  font-style: italic;
}
.file-card-meta {
  font-family: var(--mono);
  font-size: .62rem;
  color: var(--ink3);
  display: flex;
  gap: 8px;
  margin-top: 2px;
}

/* ── Bookmark cards ── */
.bm-section-head {
  font-size: .65rem;
  text-transform: uppercase;
  letter-spacing: .14em;
  color: var(--ink3);
  padding: 16px 0 8px;
  border-bottom: 1px solid var(--paper3);
  margin-bottom: 12px;
}
.bm-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 8px;
  margin-bottom: 24px;
}
.bm-card {
  background: white;
  border: 1px solid var(--paper3);
  border-radius: 4px;
  padding: 10px 12px;
  transition: box-shadow .15s;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.bm-card:hover { box-shadow: 0 2px 10px rgba(0,0,0,.08); }
.bm-title {
  font-family: var(--serif);
  font-size: .83rem;
  color: var(--ink);
  line-height: 1.3;
  text-decoration: none;
}
.bm-title:hover { color: var(--accent); }
.bm-meta {
  font-family: var(--mono);
  font-size: .65rem;
  color: var(--ink3);
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
}
.bm-ddc {
  background: var(--paper2);
  border: 1px solid var(--paper3);
  border-radius: 3px;
  padding: 1px 5px;
  color: var(--accent);
  font-size: .62rem;
}
.bm-excerpt {
  font-size: .75rem;
  color: var(--ink2);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.bm-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  margin-top: 2px;
}
.bm-tag {
  padding: 1px 5px;
  background: var(--paper2);
  border-radius: 6px;
  font-size: .6rem;
  color: var(--ink3);
  font-family: var(--mono);
}

::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-track { background: var(--paper2); }
::-webkit-scrollbar-thumb { background: var(--paper3); border-radius: 3px; }

/* Upload zone */
.upload-zone {
  margin: 8px 12px;
  border: 2px dashed var(--paper3);
  border-radius: 6px;
  padding: 14px 10px;
  text-align: center;
  cursor: pointer;
  transition: border-color .2s, background .2s;
}
.upload-zone:hover, .upload-zone.drag-over {
  border-color: var(--accent2);
  background: rgba(196,98,45,.06);
}
.upload-icon {
  font-size: 1.4rem;
  color: var(--ink3);
  margin-bottom: 4px;
  line-height: 1;
}
.upload-hint {
  font-size: .72rem;
  color: var(--ink3);
  line-height: 1.5;
}
.upload-hint span { color: var(--ink3); font-family: var(--mono); font-size: .68rem; }
.upload-progress {
  margin: 4px 12px 0;
  font-size: .72rem;
  color: var(--accent);
  font-style: italic;
  padding: 0 4px;
}
.uploaded-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 5px 12px;
  font-size: .75rem;
  color: var(--ink2);
  border-bottom: 1px solid var(--paper3);
  gap: 6px;
}
.uploaded-item .uname {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--mono);
  font-size: .7rem;
}
.uploaded-item .uwc { color: var(--ink3); font-size: .68rem; white-space: nowrap; }
.uploaded-item:hover { background: var(--paper3); }
.uploaded-item .urm {
  background: none;
  border: none;
  color: var(--ink3);
  cursor: pointer;
  font-size: .8rem;
  padding: 0 2px;
  flex-shrink: 0;
}
.uploaded-item .urm:hover { color: var(--accent); }
</style>
</head>
<body>

<header>
  <div class="logo">Fisher <span>Vault</span> Search <span style='font-size:.65rem;opacity:.6'>+ upload</span></div>
  <div class="search-wrap">
    <input type="text" id="search-input" placeholder="Search notes, texts, and bookmarks…" autocomplete="off" onkeydown="if(event.key==='Enter')doSearch()">
    <button id="search-btn" onclick="doSearch()">Search</button>
  </div>
  <div class="scope-wrap">
    <button class="scope-btn active" id="scope-all" onclick="setScope('all')">All</button>
    <button class="scope-btn" id="scope-vault" onclick="setScope('vault')">Vault</button>
    <button class="scope-btn" id="scope-bm" onclick="setScope('bookmarks')">Bookmarks</button>
  </div>
  <div class="ctx-wrap">
    Context
    <select id="ctx-sel" onchange="doSearch()">
      <option value="2">2 lines</option>
      <option value="3" selected>3 lines</option>
      <option value="5">5 lines</option>
      <option value="8">8 lines</option>
    </select>
  </div>
</header>

<div class="app-body">
  <aside>
    <div id="stats-panel"></div>

    <div class="sb-header" style="margin-top:8px">Upload a file</div>
    <div class="upload-zone" id="upload-zone" ondragover="dragOver(event)" ondragleave="dragLeave(event)" ondrop="dropFile(event)" onclick="document.getElementById('file-inp').click()">
      <div class="upload-icon">⊕</div>
      <div class="upload-hint">Drop or click to upload<br><span>.txt · .md · .rtf · .pdf · .csv</span></div>
      <input type="file" id="file-inp" accept=".txt,.md,.rtf,.pdf,.csv" style="display:none" onchange="uploadFile(this.files[0])">
    </div>
    <div id="uploaded-list"></div>

    <div class="sb-header" style="margin-top:8px">Fetch a URL</div>
    <div class="url-fetch-wrap">
      <input type="text" id="url-input" placeholder="https://www.gutenberg.org/…" autocomplete="off" onkeydown="if(event.key==='Enter')fetchUrl()">
      <button type="button" id="url-btn" onclick="fetchUrl()">Fetch</button>
      <div id="url-status"></div>
      <button type="button" class="save-vault-btn" id="save-vault-btn" onclick="saveToVault()">Save to vault</button>
    </div>

    <div class="sb-header" style="margin-top:8px">Folders</div>
    <div class="folder-list" id="folder-list"></div>
    <div class="sb-header" style="margin-top:8px">
      Clips <span class="clips-count-badge" id="clips-count-badge" style="display:none">0</span>
    </div>
    <div class="folder-list">
      <div class="folder-item" onclick="browseClips('')">
        <span class="fbl">All clips</span>
        <span class="fc" id="clips-sidebar-count">—</span>
      </div>
    </div>
    <div class="sb-header" style="margin-top:8px">Bookmarks by class</div>
    <div class="folder-list" id="bm-folder-list"></div>
  </aside>

  <main id="main">
    <div class="welcome">
      <h2>"Manners are of more importance than laws."</h2>
      <p>Search across every note, essay, quote, and converted document in your vault. Type a word, phrase, or sentence fragment to find it anywhere.</p>
      <div class="hint">Press Enter to search</div>
    </div>
  </main>
</div>

<!-- File viewer modal -->
<div class="file-viewer" id="viewer" onclick="bgClose(event)">
  <div class="viewer-modal">
    <div class="viewer-header">
      <span class="viewer-title" id="viewer-title"></span>
      <button class="viewer-close" onclick="closeViewer()">✕</button>
    </div>
    <button type="button" class="clip-btn" id="clip-btn" onclick="showClipNote()">+ Save clip</button>
    <div class="clip-note-bar" id="clip-note-bar">
      <input type="text" class="clip-note-inp" id="clip-note-inp" placeholder="Add a note (optional)…" onkeydown="if(event.key==='Enter')saveClip()">
      <div class="clip-save-row">
        <button type="button" class="clip-save-btn" onclick="saveClip()">Save clip</button>
        <button type="button" class="clip-cancel-btn" onclick="hideClipNote()">Cancel</button>
      </div>
    </div>
    <div class="viewer-body" id="viewer-body"></div>
  </div>
</div>

<script>
let lastQuery = '';
let allResults = [];
let activeFolder = '';


// ── URL Fetch ─────────────────────────────────────────────────────────────
async function fetchUrl() {
  const url = document.getElementById('url-input').value.trim();
  if (!url) return;
  const status = document.getElementById('url-status');
  const btn = document.getElementById('url-btn');
  status.className = '';
  status.textContent = 'Fetching…';
  btn.disabled = true;
  try {
    const res = await (await fetch('/api/fetch_url', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url})
    })).json();
    if (res.error) {
      status.className = 'err';
      status.textContent = res.error;
      hideSaveBtn();
    } else {
      status.className = 'ok';
      status.textContent = `✓ ${res.words.toLocaleString()} words indexed`;
      document.getElementById('url-input').value = '';
      refreshUploadedList();
      if (lastQuery) doSearch();
      showSaveBtn(res.filename);
    }
  } catch(e) {
    status.className = 'err';
    status.textContent = 'Network error';
  }
  btn.disabled = false;
}

// ── Save to Vault ─────────────────────────────────────────────────────────
let _lastFetchedFilename = null;

function showSaveBtn(filename) {
  _lastFetchedFilename = filename;
  const btn = document.getElementById('save-vault-btn');
  btn.textContent = 'Save to vault';
  btn.className = 'save-vault-btn show';
  btn.disabled = false;
}

function hideSaveBtn() {
  _lastFetchedFilename = null;
  const btn = document.getElementById('save-vault-btn');
  btn.className = 'save-vault-btn';
}

async function saveToVault() {
  if (!_lastFetchedFilename) return;
  const btn = document.getElementById('save-vault-btn');
  const status = document.getElementById('url-status');
  btn.disabled = true;
  btn.textContent = 'Saving...';
  try {
    const res = await (await fetch('/api/save_to_vault', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({filename: _lastFetchedFilename})
    })).json();
    if (res.error) {
      status.className = 'err';
      status.textContent = res.error;
      btn.disabled = false;
      btn.textContent = 'Save to vault';
    } else {
      btn.textContent = 'Saved: ' + res.saved_as;
      btn.className = 'save-vault-btn show saved';
      status.className = 'ok';
      status.textContent = 'Saved — ' + res.words.toLocaleString() + ' words';
      _lastFetchedFilename = null;
      // Refresh folder list to show fetched/ folder
      const s = await(await fetch('/api/stats')).json();
      const fmt = function(n) { return n.toLocaleString(); };
      const panel2 = document.getElementById('stats-panel');
      if (panel2) panel2.innerHTML =
        '<div style="padding:5px 14px 8px;font-size:.72rem;color:var(--ink3);border-bottom:1px solid var(--paper3)">' +
        fmt(s.files) + ' files &nbsp; ' + fmt(s.total_words) + ' words</div>';
      const fl2 = document.getElementById('folder-list');
      if (fl2) {
        fl2.innerHTML = '';
        const allBtn2 = document.createElement('div');
        allBtn2.className = 'folder-item active';
        allBtn2.onclick = function() { filterFolder(); };
        allBtn2.innerHTML = '<span class="fbl">All folders</span><span class="fc">' + fmt(s.files) + '</span>';
        fl2.appendChild(allBtn2);
        Object.entries(s.folders || {}).forEach(function(entry) {
          const name = entry[0], count = entry[1];
          const d = document.createElement('div');
          d.className = 'folder-item';
          d.dataset.folder = name;
          d.onclick = function() { filterFolder(name); };
          d.innerHTML = '<span class="fbl">' + esc(name || '(root)') + '</span><span class="fc">' + count + '</span>';
          fl2.appendChild(d);
        });
      }
    }
  } catch(e) {
    status.className = 'err';
    status.textContent = 'Save failed: ' + e.message;
    btn.disabled = false;
    btn.textContent = 'Save to vault';
  }
}


// ── Clips ──────────────────────────────────────────────────────────────
let _currentViewerFile = {filename: '', path: '', folder: ''};
let _pendingClipText = '';

function positionFloater(el) {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0) return;
  const range = sel.getRangeAt(0);
  const rect = range.getBoundingClientRect();
  const modal = document.querySelector('.viewer-modal');
  const modalRect = modal ? modal.getBoundingClientRect() : {left: 0, top: 0};
  el.style.left = (rect.left - modalRect.left + rect.width / 2 - el.offsetWidth / 2) + 'px';
  el.style.top  = (rect.top  - modalRect.top  - el.offsetHeight - 8) + 'px';
}

document.addEventListener('mouseup', function() {
  // Don't clear pending text if we're clicking inside the clip note bar
  const noteBar = document.getElementById('clip-note-bar');
  if (noteBar && noteBar.classList.contains('show')) return;
  const sel = window.getSelection();
  const viewer = document.getElementById('viewer');
  if (!viewer || !viewer.classList.contains('open')) return;
  const text = sel ? sel.toString().trim() : '';
  const btn = document.getElementById('clip-btn');
  if (text.length > 5) {
    _pendingClipText = text;
    btn.style.display = 'block';
    noteBar.classList.remove('show');
    setTimeout(function() { positionFloater(btn); }, 0);
  } else if (!_pendingClipText) {
    // Only hide button if we don't have pending text already
    btn.style.display = 'none';
    noteBar.classList.remove('show');
  }
});

function showClipNote() {
  // Capture text NOW before selection is lost
  const sel = window.getSelection();
  if (sel && sel.toString().trim().length > 5) {
    _pendingClipText = sel.toString().trim();
  }
  const btn = document.getElementById('clip-btn');
  const noteBar = document.getElementById('clip-note-bar');
  btn.style.display = 'none';
  noteBar.classList.add('show');
  setTimeout(function() { positionFloater(noteBar); }, 0);
  document.getElementById('clip-note-inp').value = '';
  setTimeout(function() { document.getElementById('clip-note-inp').focus(); }, 50);
}

function hideClipNote() {
  document.getElementById('clip-btn').style.display = 'none';
  document.getElementById('clip-note-bar').classList.remove('show');
  _pendingClipText = '';
  if (window.getSelection) window.getSelection().removeAllRanges();
}

async function saveClip() {
  if (!_pendingClipText) { alert('No text selected'); return; }
  const note = document.getElementById('clip-note-inp').value.trim();
  const saveBtn = document.querySelector('.clip-save-btn');
  saveBtn.textContent = 'Saving...';
  saveBtn.disabled = true;
  try {
    const payload = {
      selected_text: _pendingClipText,
      user_note: note,
      source_file: _currentViewerFile.filename,
      source_path: _currentViewerFile.path,
      source_folder: _currentViewerFile.folder,
    };
    const res = await fetch('/api/clips/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.ok) {
      hideClipNote();
      const flash = document.createElement('div');
      flash.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#4a7c6e;color:white;padding:8px 18px;border-radius:4px;font-size:.82rem;z-index:400;pointer-events:none';
      flash.textContent = 'Clip saved';
      document.body.appendChild(flash);
      setTimeout(function() { flash.remove(); }, 2000);
      loadClipsCount();
    } else {
      alert('Save failed: ' + (data.error || 'unknown error'));
      saveBtn.textContent = 'Save clip';
      saveBtn.disabled = false;
    }
  } catch(e) {
    alert('Save error: ' + e.message);
    saveBtn.textContent = 'Save clip';
    saveBtn.disabled = false;
  }
}

async function loadClipsCount() {
  try {
    const res = await(await fetch('/api/clips/count')).json();
    const n = res.count || 0;
    document.getElementById('clips-sidebar-count').textContent = n;
    const badge = document.getElementById('clips-count-badge');
    if (n > 0) {
      badge.textContent = n;
      badge.style.display = 'inline';
    }
  } catch(e) {}
}

async function browseClips(source) {
  document.getElementById('main').innerHTML = '<div class="loading">Loading clips...</div>';
  const url = source
    ? '/api/clips?source=' + encodeURIComponent(source)
    : '/api/clips';
  const clips = await(await fetch(url)).json();
  renderClipsView(clips, source || 'All clips');
}

function renderClipsView(clips, title) {
  const main = document.getElementById('main');
  if (!clips.length) {
    main.innerHTML = '<div class="welcome"><h2>No clips yet</h2><p>Select text in any open file and click <strong>Save clip</strong> to save a highlight.</p></div>';
    return;
  }
  const cards = clips.map(function(c) {
    const note = c.user_note
      ? '<div class="clip-note show">' + esc(c.user_note) + '</div>'
      : '';
    const date = c.created_at ? c.created_at.slice(0, 10) : '';
    const src = c.source_file
      ? '<span class="clip-source-link" onclick="openFile(' + JSON.stringify(c.source_path || c.source_file) + ', \'\')">' + esc(c.source_file) + '</span>'
      : '';
    return '<div class="clip-card">' +
      '<button type="button" class="clip-del" data-id="' + c.id + '" onclick="deleteClipById(this)" title="Delete">✕</button>' +
      '<div class="clip-text">' + esc(c.selected_text) + '</div>' +
      note +
      '<div class="clip-meta">' + src + (date ? '<span>' + date + '</span>' : '') + '</div>' +
      '</div>';
  }).join('');
  main.innerHTML =
    '<div class="status-bar">' +
      '<div class="result-count"><strong>' + clips.length + '</strong> clip' + (clips.length !== 1 ? 's' : '') + '</div>' +
      '<div class="query-echo">' + esc(title) + '</div>' +
    '</div>' +
    '<div style="margin-bottom:8px">' + cards + '</div>';
}

async function deleteClip(id, cardEl) {
  await fetch('/api/clips/delete/' + id, {method: 'DELETE'});
  if (cardEl) cardEl.remove();
  loadClipsCount();
}

// ── Boot ──────────────────────────────────────────────────────────────────
// ── Upload ───────────────────────────────────────────────────────────────
function dragOver(e) {
  e.preventDefault();
  document.getElementById('upload-zone').classList.add('drag-over');
}
function dragLeave(e) {
  document.getElementById('upload-zone').classList.remove('drag-over');
}
function dropFile(e) {
  e.preventDefault();
  document.getElementById('upload-zone').classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file) uploadFile(file);
}

async function uploadFile(file) {
  if (!file) return;
  const zone = document.getElementById('upload-zone');
  zone.innerHTML = `<div class="upload-icon">↑</div><div class="upload-hint">Uploading ${esc(file.name)}…</div>`;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const res = await(await fetch('/api/upload', { method: 'POST', body: fd })).json();
    if (res.error) {
      zone.innerHTML = `<div class="upload-icon" style="color:var(--accent)">✕</div><div class="upload-hint">${esc(res.error)}</div>`;
      setTimeout(resetUploadZone, 3000);
    } else if (res.csv_type === 'raindrop') {
      zone.innerHTML = `<div class="upload-icon" style="color:var(--green)">✓</div><div class="upload-hint">${res.lines.toLocaleString()} bookmarks ingested</div>`;
      setTimeout(async () => { resetUploadZone(); await loadBookmarkFolders(); const s=await(await fetch('/api/stats')).json(); document.getElementById('bm-stat-row').innerHTML=`<div class="stat-row"><span>Bookmarks</span><strong>${s.bookmarks.toLocaleString()}</strong></div>`; }, 2500);
    } else {
      resetUploadZone();
      refreshUploadedList();
    }
  } catch(e) {
    zone.innerHTML = `<div class="upload-icon" style="color:var(--accent)">✕</div><div class="upload-hint">Upload failed</div>`;
    setTimeout(resetUploadZone, 3000);
  }
}

function resetUploadZone() {
  document.getElementById('upload-zone').innerHTML = `
    <div class="upload-icon">⊕</div>
    <div class="upload-hint">Drop or click to upload<br><span>.txt · .md · .rtf · .pdf · .csv</span></div>
    <input type="file" id="file-inp" accept=".txt,.md,.rtf,.pdf,.csv" style="display:none" onchange="uploadFile(this.files[0])">
  `;
}

async function refreshUploadedList() {
  const files = await(await fetch('/api/uploaded')).json();
  const ul = document.getElementById('uploaded-list');
  if (!files.length) { ul.innerHTML = ''; return; }
  ul.innerHTML = files.map(f => `
    <div class="uploaded-item" onclick="openUploadedFile('${esc(f.filename)}')" style="cursor:pointer" title="Click to view">
      <span class="uname" title="${esc(f.filename)}">${esc(f.filename)}</span>
      <span class="uwc">${f.words.toLocaleString()}w</span>
      <button class="urm" onclick="event.stopPropagation();removeUploaded('${esc(f.filename)}')" title="Remove">✕</button>
    </div>
  `).join('');
}

function getExt(filename) {
  return (filename.split('.').pop() || '').toLowerCase();
}

function renderCsvTable(text) {
  const lines = text.trim().split('\n');
  if (!lines.length) return '<p>Empty file</p>';
  // Simple CSV parse (handles quoted fields)
  function parseRow(line) {
    const result = []; let cur = ''; let inQ = false;
    for (let i = 0; i < line.length; i++) {
      const c = line[i];
      if (c === '"') { inQ = !inQ; }
      else if (c === ',' && !inQ) { result.push(cur); cur = ''; }
      else { cur += c; }
    }
    result.push(cur);
    return result.map(s => s.trim().replace(/^"|"$/g, ''));
  }
  const headers = parseRow(lines[0]);
  const rows = lines.slice(1).filter(l => l.trim()).map(parseRow);
  let sortCol = -1, sortAsc = true;
  const tableId = 'csv-tbl-' + Date.now();
  function buildTable(data, sc, sa) {
    const thead = headers.map((h, i) => {
      const arrow = sc === i ? (sa ? '▲' : '▼') : '⇅';
      const cls = sc === i ? 'sorted' : '';
      return `<th class="${cls}" onclick="sortCsvTable('${tableId}',${i})">${esc(h)}<span class="sort-arrow">${arrow}</span></th>`;
    }).join('');
    const tbody = data.map(row =>
      '<tr>' + headers.map((_, i) => `<td title="${esc(row[i]||'')}">${esc(row[i]||'')}</td>`).join('') + '</tr>'
    ).join('');
    return `<div class="csv-meta">${data.length} rows · ${headers.length} columns</div>
      <table class="csv-table" id="${tableId}"><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`;
  }
  window._csvData = window._csvData || {};
  window._csvData[tableId] = {headers, rows, sortCol: -1, sortAsc: true};
  window.sortCsvTable = function(id, col) {
    const d = window._csvData[id];
    if (d.sortCol === col) d.sortAsc = !d.sortAsc; else { d.sortCol = col; d.sortAsc = true; }
    const sorted = [...d.rows].sort((a, b) => {
      const av = a[col]||'', bv = b[col]||'';
      const n = Number(av) - Number(bv);
      const cmp = isNaN(n) ? av.localeCompare(bv) : n;
      return d.sortAsc ? cmp : -cmp;
    });
    const el = document.getElementById(id);
    if (el) el.closest('.viewer-body').innerHTML = buildTable(sorted, d.sortCol, d.sortAsc);
    window._csvData[id].rows = sorted;
  };
  return buildTable(rows, -1, true);
}

function openViewer(filename, content, query, isPdf, isRawUrl) {
  const body = document.getElementById('viewer-body');
  document.getElementById('viewer-title').textContent = filename;
  body.className = 'viewer-body';
  if (getExt(filename) === 'csv') {
    body.classList.add('csv-mode');
    body.innerHTML = renderCsvTable(content);
  } else {
    let html = esc(content);
    if (query) html = highlightQuery(html, query);
    body.innerHTML = html;
  }
  document.getElementById('viewer').classList.add('open');
  if (!isPdf && getExt(filename) !== 'csv') {
    setTimeout(() => {
      const m = document.querySelector('#viewer-body mark');
      if (m) m.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 50);
  }
}

async function openUploadedFile(filename) {
  const ext = getExt(filename);
  const res = await(await fetch('/api/file?path=' + encodeURIComponent(filename))).json();
  if (res.error) { alert('Could not load: ' + res.error); return; }
  _currentViewerFile = {filename, path: filename, folder: 'uploaded'};
  if (ext === 'pdf') {
    const note = '[PDF - extracted text. Original file not retained after upload.]\n\n';
    openViewer(filename, note + (res.text || '(no text extracted)'), lastQuery, false, null);
    return;
  }
  openViewer(filename, res.text, lastQuery, false, null);
}

async function removeUploaded(filename) {
  await fetch('/api/upload/remove', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({filename})
  });
  refreshUploadedList();
  if (lastQuery) doSearch();
}

let currentScope = 'all';

function setScope(scope) {
  currentScope = scope;
  ['all','vault','bookmarks'].forEach(function(s) {
    document.getElementById('scope-' + s).classList.toggle('active', s === scope);
  });
  if (lastQuery) doSearch();
}

async function boot() {
  try {
    const resp = await fetch('/api/stats');
    if (!resp.ok) throw new Error('stats ' + resp.status);
    const s = await resp.json();
    const fmt = n => (n||0).toLocaleString();
    const panel = document.getElementById('stats-panel');
    if (panel) {
      panel.innerHTML =
        '<div style="padding:5px 14px 8px;font-size:.72rem;color:var(--ink3);border-bottom:1px solid var(--paper3)">' +
        '<span style="margin-right:10px">' + fmt(s.files) + ' files</span>' +
        '<span style="margin-right:10px">' + fmt(s.total_words) + ' words</span>' +
        (s.bookmarks ? '<span>' + fmt(s.bookmarks) + ' bookmarks</span>' : '') +
        '</div>';
    }
    const fl = document.getElementById('folder-list');
    if (fl) {
      const allDiv = document.createElement('div');
      allDiv.className = 'folder-item active';
      allDiv.dataset.folder = '';
      allDiv.onclick = function() { filterFolder(); };
      allDiv.innerHTML = '<span class="fbl">All folders</span><span class="fc">' + fmt(s.files) + '</span>';
      fl.appendChild(allDiv);
      const sortedFolders = Object.entries(s.folders || {}).sort(function(a,b){return b[1]-a[1];});
      sortedFolders.forEach(function(entry) {
        const name = entry[0], count = entry[1];
        const d = document.createElement('div');
        d.className = 'folder-item';
        d.dataset.folder = name;
        d.onclick = function() { filterFolder(name); };
        d.innerHTML = '<span class="fbl">' + esc(name || '(root)') + '</span><span class="fc">' + count + '</span>';
        fl.appendChild(d);
      });
    }
  } catch(e) {
    console.warn('Boot stats failed:', e);
  }
  refreshUploadedList();
  loadBookmarkFolders();
  loadClipsCount();
}


async function loadBookmarkFolders() {
  const folders = await(await fetch('/api/bookmark-folders')).json();
  const fl = document.getElementById('bm-folder-list');
  if (!fl) return;
  if (!folders.length) { fl.innerHTML = '<div class="folder-item" style="font-style:italic;opacity:.5">Not connected</div>'; return; }
  fl.innerHTML = folders.map(f => `
    <div class="folder-item" onclick="browseBookmarks('${esc(f.ddc_code)}', '${esc(f.ddc_label)}')"
         title="DDC ${esc(f.ddc_code)}">
      <span class="fbl">${esc(f.ddc_label)}</span>
      <span class="fc">${f.n}</span>
    </div>`).join('');
}

async function browseBookmarks(ddc, label) {
  document.getElementById('main').innerHTML = '<div class="loading">Loading…</div>';
  // Clear search state
  lastQuery = '';
  document.getElementById('search-input').value = '';
  const bookmarks = await(await fetch(`/api/bookmark-browse?ddc=${encodeURIComponent(ddc)}`)).json();
  const main = document.getElementById('main');
  if (!bookmarks.length) {
    main.innerHTML = `<div class="welcome"><h2>No bookmarks</h2><p>No bookmarks found for ${esc(label)}.</p></div>`;
    return;
  }
  const cards = bookmarks.map(b => {
    const tags = (b.tags || '').split(',').map(t => t.trim())
      .filter(t => t && !t.startsWith('ddc:')).slice(0, 5)
      .map(t => `<span class="bm-tag">${esc(t)}</span>`).join('');
    const excerpt = b.excerpt ? `<div class="bm-excerpt">${esc(b.excerpt)}</div>` : '';
    const date = b.created ? new Date(b.created).toLocaleDateString('en-US',{year:'numeric',month:'short'}) : '';
    return `<div class="bm-card">
      <a class="bm-title" href="${esc(b.url)}" target="_blank" rel="noopener">${esc(b.title || b.url)}</a>
      <div class="bm-meta"><span>${esc(b.domain || '')}</span>${date ? '<span>'+date+'</span>' : ''}</div>
      ${excerpt}
      ${tags ? `<div class="bm-tags">${tags}</div>` : ''}
    </div>`;
  }).join('');
  main.innerHTML = `
    <div class="status-bar">
      <div class="result-count"><strong>${bookmarks.length}</strong> bookmark${bookmarks.length!==1?'s':''}</div>
      <div class="query-echo">${esc(ddc)} — ${esc(label)}</div>
    </div>
    <div class="bm-section-head">🔖 ${esc(label)}</div>
    <div class="bm-grid">${cards}</div>`;
}

// ── Search ────────────────────────────────────────────────────────────────
async function doSearch() {
  const q = document.getElementById('search-input').value.trim();
  if (!q) return;
  const ctx = document.getElementById('ctx-sel').value;
  lastQuery = q;
  document.getElementById('main').innerHTML = '<div class="loading">Searching…</div>';
  const res = await fetch('/api/search?q=' + encodeURIComponent(q) + '&ctx=' + ctx + '&scope=' + currentScope).then(r => r.json());
  let clips = [];
  if (currentScope !== 'bookmarks') {
    try { clips = await fetch('/api/clips/search?q=' + encodeURIComponent(q)).then(r => r.json()); } catch(e) {}
  }
  allResults = res.results;
  renderResults(allResults, res, res.bookmarks || [], clips);
}

async function filterFolder(folder) {
  folder = folder || '';
  activeFolder = folder;
  document.querySelectorAll('.folder-item').forEach(el => {
    el.classList.toggle('active', (el.dataset.folder || '') === folder);
  });
  // If a search is active, filter existing results
  if (lastQuery) {
    const filtered = folder
      ? allResults.filter(r => r.folder === folder || r.file.startsWith(folder))
      : allResults;
    const total = filtered.reduce((s, r) => s + r.match_count, 0);
    renderResults(filtered, { query: lastQuery, files: filtered.length, total_matches: total });
    return;
  }
  // No search active — show file cards for the folder
  if (!folder) {
    // All folders selected with no search — show welcome
    document.getElementById('main').innerHTML = `<div class="welcome">
      <h2>"Manners are of more importance than laws."</h2>
      <p>Search across every note, essay, quote, and converted document in your vault.</p>
      <div class="hint">Press Enter to search</div>
    </div>`;
    return;
  }
  document.getElementById('main').innerHTML = '<div class="loading">Loading...</div>';
  const files = await(await fetch('/api/folder-files?folder=' + encodeURIComponent(folder))).json();
  const main = document.getElementById('main');
  if (!files.length) {
    main.innerHTML = '<div class="loading">No files in this folder.</div>';
    return;
  }
  const cards = files.map(f => {
    const author = f.author ? '<div class="file-card-author">' + esc(f.author) + '</div>' : '';
    const words = f.words ? f.words.toLocaleString() + 'w' : '';
    const ext = f.filename.split('.').pop().toLowerCase();
    return '<div class="file-card" data-fpath="' + esc(f.path) + '" onclick="openFile(this.dataset.fpath, \'\')">' +
      '<div class="file-card-title">' + esc(f.title) + '</div>' +
      author +
      '<div class="file-card-meta"><span>' + esc(ext.toUpperCase()) + '</span>' +
      (words ? '<span>' + words + '</span>' : '') +
      '</div>' +
      '</div>';
  }).join('');
  main.innerHTML =
    '<div class="status-bar">' +
      '<div class="result-count"><strong>' + files.length + '</strong> file' + (files.length !== 1 ? 's' : '') + '</div>' +
      '<div class="query-echo">' + esc(folder) + '</div>' +
    '</div>' +
    '<div class="bm-section-head">' + esc(folder) + '</div>' +
    '<div class="file-card-grid">' + cards + '</div>';
}

function renderClipsSection(clips, query) {
  if (!clips || !clips.length) return '';
  const cards = clips.map(function(c) {
    const note = c.user_note
      ? '<div class="clip-note show">' + esc(c.user_note) + '</div>'
      : '';
    const src = c.source_file ? esc(c.source_file) : '';
    return '<div class="clip-card">' +
      '<button type="button" class="clip-del" data-id="' + c.id + '" onclick="deleteClipById(this)" title="Delete">✕</button>' +
      '<div class="clip-text">' + esc(c.selected_text) + '</div>' +
      note +
      '<div class="clip-meta">' + (src ? '<span class="clip-source-link">' + src + '</span>' : '') + '</div>' +
      '</div>';
  }).join('');
  return '<div class="bm-section-head">✂ ' + clips.length + ' clip' + (clips.length !== 1 ? 's' : '') + ' matching "' + esc(query) + '"</div>' +
    '<div style="margin-bottom:16px">' + cards + '</div>';
}

function renderBookmarks(bookmarks, query) {
  if (!bookmarks || !bookmarks.length) return '';
  const cards = bookmarks.map(b => {
    const tags = (b.tags || '').split(',')
      .map(t => t.trim()).filter(t => t && !t.startsWith('ddc:'))
      .slice(0, 6)
      .map(t => `<span class="bm-tag">${esc(t)}</span>`).join('');
    const ddc = b.ddc_code ? `<span class="bm-ddc">${esc(b.ddc_code)}</span>` : '';
    const excerpt = b.excerpt ? `<div class="bm-excerpt">${esc(b.excerpt)}</div>` : '';
    return `<div class="bm-card">
      <a class="bm-title" href="${esc(b.url)}" target="_blank" rel="noopener">${esc(b.title || b.url)}</a>
      <div class="bm-meta">${ddc}<span>${esc(b.domain || '')}</span>${b.ddc_label ? '<span>'+esc(b.ddc_label)+'</span>' : ''}</div>
      ${excerpt}
      ${tags ? `<div class="bm-tags">${tags}</div>` : ''}
    </div>`;
  }).join('');
  return `<div class="bm-section-head">🔖 ${bookmarks.length} bookmark${bookmarks.length!==1?'s':''} matching "${esc(query)}"</div>
    <div class="bm-grid">${cards}</div>`;
}

function renderResults(results, meta, bookmarks, clips) {
  const main = document.getElementById('main');
  if (!results.length && (!bookmarks || !bookmarks.length)) {
    main.innerHTML = `<div class="welcome"><h2>No matches found</h2><p>Nothing contains "<em>${esc(meta.query)}</em>".</p><p style="margin-top:8px">Try fewer words or check spelling.</p></div>`;
    return;
  }

  const bmCount = (bookmarks || []).length;
  const clCount = (clips || []).length;
  const vaultPart = meta.total_matches > 0 ? '<strong>' + meta.total_matches + '</strong> vault match' + (meta.total_matches!==1?'es':'') + ' in <strong>' + meta.files + '</strong> file' + (meta.files!==1?'s':'') : '';
  const bmPart = bmCount > 0 ? '<strong>' + bmCount + '</strong> bookmark' + (bmCount!==1?'s':'') : '';
  const clPart = clCount > 0 ? '<strong>' + clCount + '</strong> clip' + (clCount!==1?'s':'') : '';
  const countStr = [vaultPart, clPart, bmPart].filter(Boolean).join(' · ');
  const statusHTML = `<div class="status-bar">
    <div class="result-count">${countStr || 'No results'}</div>
    <div class="query-echo">for "${esc(meta.query)}"</div>
  </div>`;

  const cardsHTML = results.map(r => {
    const matchBlocks = r.matches.map(m => {
      const ctxLines = m.context.map(cl => {
        const highlighted = highlightQuery(esc(cl.text), meta.query);
        return `<div class="ctx-line ${cl.match ? 'is-match' : ''}">
          <span class="line-num">${cl.lineno}</span>
          <span class="line-text">${highlighted}</span>
        </div>`;
      }).join('');
      return `<div class="match-block">
        <div class="match-jump">Line ${m.lineno}</div>
        <div class="context-lines">${ctxLines}</div>
      </div>`;
    }).join('');

    return `<div class="result-card">
      <div class="card-header" onclick="openFile('${esc(r.file)}', '${esc(meta.query)}')">
        <div>
          <div class="card-filename">${esc(r.filename)}</div>
          ${r.folder ? `<div class="card-folder">${esc(r.folder)}</div>` : ''}
        </div>
        <div class="card-badge">${r.match_count} match${r.match_count!==1?'es':''}</div>
      </div>
      ${matchBlocks}
    </div>`;
  }).join('');

  const bmHTML = renderBookmarks(bookmarks || [], meta.query);
  const clHTML = renderClipsSection(clips || [], meta.query);
  main.innerHTML = statusHTML + clHTML + bmHTML + cardsHTML;
}

function highlightQuery(text, query) {
  if (!query) return text;
  try {
    const re = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')})`, 'gi');
    return text.replace(re, '<mark>$1</mark>');
  } catch(e) { return text; }
}

// ── File viewer ───────────────────────────────────────────────────────────
async function openFile(path, query) {
  const ext = getExt(path);
  const filename = path.split('/').pop();
  const folder = path.includes('/') ? path.substring(0, path.lastIndexOf('/')) : '';
  _currentViewerFile = {filename, path, folder};
  if (ext === 'pdf') {
    window.open('/api/serve?path=' + encodeURIComponent(path), '_blank');
    return;
  }
  const res = await(await fetch('/api/file?path=' + encodeURIComponent(path))).json();
  if (res.error) return;
  openViewer(filename, res.text, query, false, null);
}

function closeViewer() { document.getElementById('viewer').classList.remove('open'); }
function bgClose(e) { if (e.target === document.getElementById('viewer')) closeViewer(); }

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeViewer();
});

function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

boot();
</script>
</body>
</html>'''

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Vault full-text search browser')
    parser.add_argument('--vault', default=None, help='Path to vault (default: ~/Downloads/local-repos/rtf-to-md)')
    parser.add_argument('--port', type=int, default=5002)
    parser.add_argument('--db', default=None, help='Path to pipeline.db for bookmark search')
    args = parser.parse_args()

    VAULT_DIR = Path(args.vault).expanduser().resolve() if args.vault \
                else Path.home() / 'Downloads' / 'local-repos' / 'rtf-to-md'

    default_db = Path.home() / 'Downloads' / 'local-repos' / 'pipeline' / 'pipeline.db'
    db_path = Path(args.db).expanduser().resolve() if args.db else default_db
    if db_path.exists():
        globals()['DB_PATH'] = db_path
        print(f'🗄  Bookmarks: {db_path}')
    else:
        print(f'⚠  pipeline.db not found — bookmark search disabled')

    if not VAULT_DIR.exists():
        print(f'❌ Vault not found: {VAULT_DIR}')
        exit(1)

    print(f'\n📚 Vault: {VAULT_DIR}')
    print(f'🌐 http://localhost:{args.port}  (upload variant)\n')
    build_index()
    init_clips_table()
    app.run(debug=False, port=args.port)
