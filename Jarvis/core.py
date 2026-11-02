"""
JARVIS core (no UI code): memory database, safe file scanning,
study-file detection, and the local AI provider.
"""
import os
import re
import difflib
import html
import sqlite3
import threading
import zipfile
from datetime import datetime
from pathlib import Path

try:
    import pymupdf as _fitz
except Exception:
    try:
        import fitz as _fitz
    except Exception:
        _fitz = None

try:
    import ollama
except ImportError:
    ollama = None

APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "JARVIS"
APP_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = APP_DIR / "jarvis.db"

DOC_EXT = {".pdf", ".docx", ".pptx", ".txt", ".md"}
SKIP_DIRS = {
    "appdata", "node_modules", ".git", "__pycache__", "venv", ".venv", "site-packages",
    "windows", "program files", "program files (x86)", "programdata", "$recycle.bin",
    "system volume information", "steam", "steamapps", "games", "dist", "build",
}
MAX_BYTES = 60 * 1024 * 1024
MAX_PAGES = 400
STUDY_THRESHOLD = 4.0

SUBJECTS = {
    "Mathematics": ["math", "algebra", "calculus", "logarithm", "trigonometry", "geometry", "equation",
                    "derivative", "integral", "polynomial", "sequence", "probability", "statistics",
                    "matrix", "vector", "radical", "exponent", "quadratic"],
    "Physics": ["physics", "velocity", "acceleration", "newton", "momentum", "energy", "wavelength",
                "frequency", "circuit", "voltage", "current", "gravity", "optics", "quantum", "kinematics"],
    "Chemistry": ["chemistry", "chemical", "molecule", "atom", "reaction", "equilibrium", "acid",
                  "stoichiometry", "periodic", "electron", "bond", "titration", "organic", "molar"],
    "Biology": ["biology", "cell", "dna", "genetics", "evolution", "photosynthesis", "ecosystem",
                "protein", "enzyme", "mitosis", "organism", "bacteria"],
    "English": ["english", "essay", "thesis", "novel", "poem", "literature", "shakespeare",
                "paragraph", "author", "narrative", "grammar", "metaphor"],
    "Social Studies": ["social studies", "history", "government", "economy", "geography", "revolution",
                       "democracy", "constitution", "treaty", "nationalism", "ideology"],
    "Computer Science": ["python", "algorithm", "programming", "variable", "loop", "database",
                         "computer science", "cs15", "cs25", "cs35", "recursion", "function"],
}
STUDY_HINTS = ["school", "class", "notes", "homework", "assignment", "worksheet", "study", "lesson",
               "unit", "chapter", "exam", "quiz", "review", "lecture", "textbook", "course",
               "syllabus", "lab", "stem", "diploma", "practice", "revision"]
NEGATIVE = ["invoice", "receipt", "tax", "passport", "visa", "contract", "bank", "statement",
            "boarding", "ticket", "minecraft", "installer", "license", "licence", "manual",
            "readme", "changelog", "terms", "privacy"]
STOPWORDS = set("""a an the and or of to in on for with is are was were be been it this that these those
what which who whom how why when where do does did can could should would will you your me my i we they
explain tell about give show find please from by as at into than then there their them his her its
""".split())


# ------------------------------------------------------------ extraction ----
def _segments(text, size=3000):
    text = text.strip()
    return [text[i:i + size] for i in range(0, len(text), size)] or []


def extract_pages(path):
    """Return a list of page/slide/part texts. Empty list if unreadable."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        if _fitz is None:
            return []
        doc = _fitz.open(path)
        try:
            return [doc[i].get_text() for i in range(min(len(doc), MAX_PAGES))]
        finally:
            doc.close()
    if ext == ".docx":
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        return _segments(html.unescape(re.sub(r"<[^>]+>", "", xml)))
    if ext == ".pptx":
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
            names.sort(key=lambda n: int(re.findall(r"\d+", n)[-1]))
            out = []
            for n in names[:MAX_PAGES]:
                xml = z.read(n).decode("utf-8", "ignore")
                out.append(html.unescape(" ".join(re.findall(r"<a:t>(.*?)</a:t>", xml, flags=re.S))))
            return out
    if ext in (".txt", ".md"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return _segments(f.read(400_000))
    return []


def chunk_text(text, size=900):
    text = re.sub(r"[ \t]+", " ", text)
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) + 1 > size:
            chunks.append(cur)
            cur = ""
        while len(p) > size:
            chunks.append(p[:size])
            p = p[size:]
        cur = (cur + " " + p).strip()
    if cur:
        chunks.append(cur)
    return [c for c in chunks if len(c) > 40]


# ---------------------------------------------------------- classification ----
def classify(path, name, text):
    """Return (score, subject). Higher score = more likely schoolwork."""
    low_path = str(path).lower()
    low_name = name.lower()
    sample = text.lower()
    score = 0.0

    hint_hits = sum(1 for h in STUDY_HINTS if h in low_path)
    score += min(hint_hits, 3) * 2

    best_subject, best_hits = "General", 0
    for subject, words in SUBJECTS.items():
        name_hits = sum(1 for w in words if w in low_name or w in low_path)
        text_hits = sum(len(re.findall(r"\b" + re.escape(w) + r"\w*", sample)) for w in words)
        total = name_hits * 3 + text_hits
        if total > best_hits:
            best_subject, best_hits = subject, total
    score += min(best_hits, 12) * 0.6

    if low_name.endswith((".pdf", ".docx", ".pptx")):
        score += 1
    score -= 4 * sum(1 for n in NEGATIVE if n in low_name)
    if len(sample) < 200:
        score -= 3
    return score, (best_subject if best_hits >= 2 else "General")


# ----------------------------------------------------------------- scanner ----
def scan_folders(roots, memory, progress, should_stop):
    """Walk only the folders the user chose. Returns (documents_checked, study_files_added)."""
    seen = added = 0
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                if should_stop():
                    return seen, added
                if os.path.splitext(fn)[1].lower() not in DOC_EXT:
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                # Skip cloud-only OneDrive files so scanning never triggers big downloads.
                attrs = getattr(st, "st_file_attributes", 0)
                if attrs & (0x400000 | 0x40000 | 0x1000):
                    continue
                if st.st_size == 0 or st.st_size > MAX_BYTES:
                    continue
                seen += 1
                if memory.is_known(p, st.st_mtime):
                    continue
                progress(seen, added, fn)
                try:
                    pages = extract_pages(p)
                except Exception:
                    memory.mark_skipped(p, st.st_mtime)
                    continue
                sample = "\n".join(pages[:3])[:6000]
                score, subject = classify(p, fn, sample)
                if score < STUDY_THRESHOLD:
                    memory.mark_skipped(p, st.st_mtime)
                    continue
                chunks = []
                for page_no, page_text in enumerate(pages, start=1):
                    for c in chunk_text(page_text):
                        chunks.append((page_no, c))
                if not chunks:
                    memory.mark_skipped(p, st.st_mtime)
                    continue
                memory.add_document(p, fn, subject, score, st.st_mtime, len(pages), chunks)
                added += 1
    return seen, added


# ------------------------------------------------------------------ memory ----
class Memory:
    def __init__(self):
        self.db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS documents(
                    id INTEGER PRIMARY KEY, path TEXT UNIQUE, name TEXT, subject TEXT,
                    score REAL, mtime REAL, pages INTEGER);
                CREATE TABLE IF NOT EXISTS skipped(path TEXT PRIMARY KEY, mtime REAL);
                CREATE TABLE IF NOT EXISTS ignored(path TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS notes(
                    id INTEGER PRIMARY KEY, question TEXT, answer TEXT, sources TEXT, created TEXT);
                CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(content, doc_id UNINDEXED, page UNINDEXED);
            """)
            self.db.commit()

    def is_known(self, path, mtime):
        with self.lock:
            if self.db.execute("SELECT 1 FROM ignored WHERE path=?", (path,)).fetchone():
                return True
            for table in ("documents", "skipped"):
                row = self.db.execute(f"SELECT mtime FROM {table} WHERE path=?", (path,)).fetchone()
                if row and abs(row[0] - mtime) < 1:
                    return True
        return False

    def mark_skipped(self, path, mtime):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO skipped VALUES(?,?)", (path, mtime))
            self.db.commit()

    def add_document(self, path, name, subject, score, mtime, pages, chunks):
        with self.lock:
            row = self.db.execute("SELECT id FROM documents WHERE path=?", (path,)).fetchone()
            if row:
                self.db.execute("DELETE FROM passages WHERE doc_id=?", (row[0],))
                self.db.execute("DELETE FROM documents WHERE id=?", (row[0],))
            cur = self.db.execute(
                "INSERT INTO documents(path,name,subject,score,mtime,pages) VALUES(?,?,?,?,?,?)",
                (path, name, subject, score, mtime, pages))
            doc_id = cur.lastrowid
            self.db.executemany("INSERT INTO passages(content,doc_id,page) VALUES(?,?,?)",
                                [(c, doc_id, pg) for pg, c in chunks])
            self.db.commit()

    def forget(self, doc_id):
        with self.lock:
            row = self.db.execute("SELECT path FROM documents WHERE id=?", (doc_id,)).fetchone()
            if row:
                self.db.execute("INSERT OR IGNORE INTO ignored VALUES(?)", (row[0],))
            self.db.execute("DELETE FROM passages WHERE doc_id=?", (doc_id,))
            self.db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            self.db.commit()

    def wipe(self):
        with self.lock:
            for t in ("documents", "skipped", "ignored", "notes", "passages"):
                self.db.execute(f"DELETE FROM {t}")
            self.db.commit()

    def list_documents(self):
        with self.lock:
            return self.db.execute(
                "SELECT id,name,subject,path FROM documents ORDER BY subject,name").fetchall()

    def stats(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT subject,COUNT(*) FROM documents GROUP BY subject ORDER BY COUNT(*) DESC").fetchall()
        return sum(r[1] for r in rows), rows

    def search(self, query, k=5):
        words = [w for w in re.findall(r"[a-zA-Z0-9]+", query.lower())
                 if len(w) > 2 and w not in STOPWORDS]
        if not words:
            return []
        match = " OR ".join(f'"{w}"' for w in words[:12])
        try:
            with self.lock:
                rows = self.db.execute(
                    "SELECT p.content,p.page,d.name,d.subject,d.path FROM passages p "
                    "JOIN documents d ON d.id=p.doc_id WHERE passages MATCH ? "
                    "ORDER BY bm25(passages) LIMIT ?", (match, k)).fetchall()
        except sqlite3.Error:
            return []
        return [{"text": r[0], "page": r[1], "name": r[2], "subject": r[3], "path": r[4]} for r in rows]

    def save_note(self, question, answer, sources):
        with self.lock:
            self.db.execute("INSERT INTO notes(question,answer,sources,created) VALUES(?,?,?,?)",
                            (question, answer, sources, datetime.now().strftime("%Y-%m-%d %H:%M")))
            self.db.commit()

    def search_notes(self, query="", limit=20):
        with self.lock:
            if query.strip():
                like = f"%{query.strip()}%"
                return self.db.execute(
                    "SELECT question,answer,sources,created FROM notes WHERE question LIKE ? OR answer LIKE ? "
                    "ORDER BY id DESC LIMIT ?", (like, like, limit)).fetchall()
            return self.db.execute(
                "SELECT question,answer,sources,created FROM notes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


# ------------------------------------------------------------- AI provider ----
SYSTEM_PROMPT = (
    "You are JARVIS, a calm, sharp study assistant that is spoken aloud. "
    "Answer in plain sentences with no markdown, no bullet symbols and no headings. "
    "Keep answers to two to five sentences unless the student asks for more detail. "
    "When excerpts from the student's own files are provided, base your answer on them and say which "
    "file you used. If the excerpts do not cover the question, say so, then answer from general "
    "knowledge and make clear that part is general knowledge. Never invent file contents."
)


class OllamaProvider:
    """Local model through Ollama. The UI only calls stream()."""

    def __init__(self, model="llama3.2"):
        self.model = model
        self.history = []

    def stream(self, question, passages):
        if ollama is None:
            raise RuntimeError("The ollama package is missing. Run: py -m pip install ollama")
        user = question
        if passages:
            user = "Excerpts from my files:\n" + "\n\n".join(passages) + "\n\nQuestion: " + question
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.history[-6:] + \
                   [{"role": "user", "content": user}]
        reply = ""
        try:
            for chunk in ollama.chat(model=self.model, messages=messages, stream=True):
                token = chunk["message"]["content"]
                reply += token
                yield token
        except Exception as e:
            raise RuntimeError(
                f"I couldn't reach the Ollama model '{self.model}'. Make sure Ollama is running and "
                f"the model is installed (ollama pull {self.model}). Details: {e}")
        self.history += [{"role": "user", "content": question}, {"role": "assistant", "content": reply}]


# -------------------------------------------------------------- wake word ----
def find_wake(text):
    """Find 'jarvis' (or a close mis-hearing) in recognised speech.
    Returns (start, end) character positions, or None."""
    toks = [(m.group(), m.start(), m.end()) for m in re.finditer(r"[a-z']+", text.lower())]
    for i, (t, s, e) in enumerate(toks):
        if t == "travis":
            continue
        if t == "jar" and i + 1 < len(toks) and toks[i + 1][0].startswith("vis"):
            return s, toks[i + 1][2]
        if len(t) >= 5 and difflib.SequenceMatcher(None, t, "jarvis").ratio() >= 0.8:
            return s, e
    return None
