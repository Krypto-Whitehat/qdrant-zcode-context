#!/usr/bin/env python3
"""
qdrant-context-indexer — semantic codebase & document indexer for coding agents.

Built on official Qdrant components only:
  - qdrant-client (LOCAL MODE path= or Cloud via url+api_key)
  - fastembed (ONNX, CPU, no API key) — model MUST match your MCP server
    (mcp-server-qdrant default: sentence-transformers/all-MiniLM-L6-v2)

Commands:
  index  <project>            full incremental index run (only changed files)
  reindex-file <project> <f>  re-index a single file (for PostToolUse hooks)
  search  <project> "<query>" quick CLI search test
  hook                        stdin: PostToolUse hook JSON -> re-index that file

Storage: Qdrant Cloud (config.json: url+api_key) — or --local for a path-based
embedded DB (dev/testing; not recommended beyond ~20k points).
"""
import sys, os, json, hashlib, uuid, re

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
BASE = os.path.dirname(os.path.abspath(__file__))
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"  # = mcp-server-qdrant default
VECTOR_SIZE = 384
MAX_FILE_BYTES = 2_000_000          # skip/limit huge text files
CHUNK_CHARS = 900                   # target chunk size
CHUNK_OVERLAP_LINES = 3
MAX_CHUNKS_PER_FILE = 150           # prevent data dumps from flooding the index

IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "vendor",
               "graft", ".serena", ".qdrant-context", "dist", "build", ".idea",
               ".vscode", ".next", "target", ".cache", "wp-content/cache"}
TEXT_EXTS = {".py", ".php", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html",
             ".htm", ".css", ".scss", ".json", ".yml", ".yaml", ".xml", ".md",
             ".txt", ".sql", ".sh", ".bat", ".cmd", ".ps1", ".ini", ".env",
             ".svg", ".csv", ".toml", ".rb", ".go", ".rs", ".java", ".c", ".cpp",
             ".h", ".cs", ".swift", ".kt", ".lua", ".twig", ".less"}
SKIP_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2",
             ".ttf", ".eot", ".mp4", ".mp3", ".zip", ".gz", ".tar", ".exe", ".dll",
             ".db", ".sqlite"}

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def get_client(local: bool, cfg: dict, collection: str):
    from qdrant_client import QdrantClient
    if local or cfg.get("mode", "local") == "local":
        os.makedirs(os.path.join(BASE, "localdb"), exist_ok=True)
        return QdrantClient(path=os.path.join(BASE, "localdb", f"{collection}.qdb"))
    if not cfg.get("url"):
        print("qdrant: not configured (no url in config.json) — skipped.")
        sys.exit(0)
    return QdrantClient(url=cfg["url"], api_key=cfg.get("api_key", ""))

def get_model():
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=EMBEDDING_MODEL)

def embed(model, texts):
    return [list(v) for v in model.embed(texts)]

def slugify(path: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", os.path.basename(os.path.normpath(path)).lower())
    return s[:40] or "projekt"

def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()

def chunk_text(text: str):
    lines = text.splitlines()
    chunks, cur, size = [], [], 0
    for ln in lines:
        cur.append(ln)
        size += len(ln) + 1
        if size >= CHUNK_CHARS:
            chunks.append("\n".join(cur))
            cur = cur[-CHUNK_OVERLAP_LINES:]
            size = sum(len(l) + 1 for l in cur)
    if cur and "\n".join(cur).strip():
        chunks.append("\n".join(cur))
    return [c for c in chunks if c.strip()] or ([text[:CHUNK_CHARS]] if text.strip() else [])

def extract(path: str):
    """Return list[(kind, text)] for a file; [] when skipped."""
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path)
    try:
        if ext in SKIP_EXTS:
            return []
        if ext == ".pdf":
            from pypdf import PdfReader
            r = PdfReader(path)
            return [("pdf-page", (p.extract_text() or "")[:6000]) for p in r.pages[:200] if (p.extract_text() or "").strip()]
        if ext == ".docx":
            from docx import Document
            d = Document(path)
            paras = [p.text for p in d.paragraphs if p.text.strip()]
            return [("docx", "\n".join(paras)[:40000])]
        if ext == ".xlsx":
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=True, data_only=True)
            out = []
            for ws in wb.worksheets[:30]:
                rows = []
                for row in ws.iter_rows(max_row=500, max_col=25, values_only=True):
                    cells = [f"{c}" for c in row if c is not None]
                    if cells:
                        rows.append(" | ".join(cells))
                if rows:
                    out.append((f"xlsx:{ws.title}", "\n".join(rows)[:20000]))
            return out
        if ext == ".pptx":
            from pptx import Presentation
            pr = Presentation(path)
            out = []
            for i, slide in enumerate(pr.slides[:200], 1):
                texts = [sh.text_frame.text for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
                if texts:
                    out.append((f"pptx-slide-{i}", "\n".join(texts)[:8000]))
            return out
        if ext in TEXT_EXTS or name in (".env", ".gitignore", "Dockerfile"):
            if os.path.getsize(path) > MAX_FILE_BYTES:
                return []
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return [("file", f.read())]
    except Exception as e:
        print(f"  ! read error {path}: {e}")
    return []

def points_for(project: str, collection: str, abs_path: str, model):
    rel = os.path.relpath(abs_path, project).replace("\\", "/")
    parts = extract(abs_path)
    pts = []
    for kind, text in parts:
        for i, chunk in enumerate(chunk_text(text)):
            if i >= MAX_CHUNKS_PER_FILE:
                break
            info = f"[{slugify(project)}] {rel} · {kind} #{i+1}\n{chunk}"
            pid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{collection}|{rel}|{kind}|{i}|{hashlib.md5(chunk.encode('utf-8', 'ignore')).hexdigest()}"))
            pts.append({
                "id": pid,
                "vector": None,  # filled below
                "payload": {
                    "information": info,
                    "metadata": {"project": slugify(project), "path": rel, "kind": kind,
                                 "chunk": i + 1, "content": chunk[:2000]},
                },
            })
    if pts:
        vecs = embed(model, [p["payload"]["information"] for p in pts])
        for p, v in zip(pts, vecs):
            p["vector"] = v
    return pts

def drop_path(client, collection: str, project: str, rel: str):
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    client.delete(collection_name=collection, points_selector=Filter(must=[
        FieldCondition(key="metadata.project", match=MatchValue(value=slugify(project))),
        FieldCondition(key="metadata.path", match=MatchValue(value=rel)),
    ]))

def ensure_collection(client, collection: str):
    from qdrant_client.models import VectorParams, Distance
    if not client.collection_exists(collection):
        client.create_collection(collection, vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE))

def load_manifest(collection: str):
    p = os.path.join(BASE, "manifests", f"{collection}.json")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_manifest(collection: str, manifest: dict):
    d = os.path.join(BASE, "manifests")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{collection}.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)

def iter_files(project: str):
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for name in files:
            yield os.path.join(root, name)

def cmd_index(project: str, local: bool, only_file: str | None = None):
    from qdrant_client.models import PointStruct
    project = os.path.abspath(project)
    collection = slugify(project)
    cfg = load_config()
    client = get_client(local, cfg, collection)
    ensure_collection(client, collection)
    model = get_model()
    manifest = load_manifest(collection)

    if only_file:
        targets = [os.path.abspath(only_file)]
    else:
        targets = list(iter_files(project))

    indexed = skipped = removed = 0
    for t in targets:
        if not os.path.isfile(t):
            continue
        rel = os.path.relpath(t, project).replace("\\", "/")
        ext = os.path.splitext(t)[1].lower()
        if ext in SKIP_EXTS and ext not in (".pdf", ".docx", ".xlsx", ".pptx"):
            continue
        try:
            sha = sha256_of(t)
        except OSError:
            continue
        prev = manifest.get(rel)
        if prev and prev.get("sha") == sha and (only_file is None):
            skipped += 1
            continue
        if prev:
            drop_path(client, collection, project, rel)
            removed += prev.get("points", 0)
        pts = points_for(project, collection, t, model)
        for i in range(0, len(pts), 100):
            batch = [PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"]) for p in pts[i:i+100]]
            client.upsert(collection, batch)
        manifest[rel] = {"sha": sha, "points": len(pts)}
        indexed += len(pts)

    if only_file is None:  # full run: clean removed files from manifest + DB
        existing = {os.path.relpath(t, project).replace("\\", "/") for t in targets if os.path.isfile(t)}
        for rel in list(manifest.keys()):
            if rel not in existing:
                drop_path(client, collection, project, rel)
                removed += manifest[rel].get("points", 0)
                del manifest[rel]
    save_manifest(collection, manifest)
    print(f"qdrant: {indexed} chunks indexed, {skipped} files unchanged, {removed} stale points removed. Collection: {collection}")

def cmd_search(project: str, query: str, local: bool, limit: int = 5):
    collection = slugify(os.path.abspath(project))
    cfg = load_config()
    client = get_client(local, cfg, collection)
    model = get_model()
    qv = embed(model, [query])[0]
    hits = client.query_points(collection, query=qv, limit=limit).points
    for h in hits:
        print(f"— ({h.score:.3f}) {h.payload.get('information', '')[:600]}\n")

def cmd_hook():
    raw = sys.stdin.read() or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    fp = (data.get("tool_input") or {}).get("file_path") or ""
    cwd = data.get("cwd") or os.getenv("CLAUDE_PROJECT_DIR") or os.getcwd()
    if fp and os.path.isfile(fp):
        try:
            cmd_index(cwd, local=False, only_file=fp)
        except SystemExit:
            pass
        except Exception as e:
            print(f"qdrant: sync failed: {e}")

def main():
    a = sys.argv[1:]
    local = "--local" in a
    a = [x for x in a if x != "--local"]
    if not a:
        print(__doc__)
        return
    if a[0] == "index" and len(a) >= 2:
        cmd_index(a[1], local)
    elif a[0] == "reindex-file" and len(a) >= 3:
        cmd_index(a[1], local, only_file=a[2])
    elif a[0] == "search" and len(a) >= 3:
        cmd_search(a[1], " ".join(a[2:]), local)
    elif a[0] == "hook":
        cmd_hook()
    else:
        print(__doc__)

if __name__ == "__main__":
    main()
