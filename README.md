# qdrant-zcode-context

**Augment-style semantic context engine for ZCode (and any Claude-Code-compatible CLI) — 100 % Qdrant, zero paid services, one-click installer.**

Statt dass dein Coding-Agent bei jeder Frage mit `grep` sucht und ganze Dateien liest, indexiert diese Pipeline **alles** im Projekt — Code, HTML, Markdown und **PDF / DOCX / XLSX / PPTX** — semantisch in Qdrant. Der Agent fragt dann per MCP (`qdrant-find`) in natürlicher Sprache und bekommt zielgenaue Chunks mit exakter Pfadangabe zurück. Nach jedem `Write/Edit` synchronisiert ein Hook automatisch.

*English quickstart below · [Deutsche Kurzanleitung](#deutsche-kurzanleitung)*

---

## How it works

```
Write/Edit in ZCode
      │  (PostToolUse hook)
      ▼
indexer.py  ──chunk──►  fastembed (local ONNX, no API key)  ──upsert──►  Qdrant Cloud (free 1 GB)
                                                                              ▲
Agent question ("where is X handled?") ──qdrant-find (official MCP)───────────┘
      │
      ▼
exact chunk + file:line-style location  →  no more whole-file reads
```

- **Indexer** (`indexer.py`): incremental (SHA-manifest, only changed files), chunking with overlap, per-file chunk cap, ignore list, multi-format extraction
- **Embeddings**: `fastembed` (Qdrant's ONNX library) — runs on CPU, **no API key, no PyTorch**
- **Storage**: Qdrant Cloud **free tier** (1 GB forever-free) or fully local embedded DB (`--local`)
- **Query layer**: Qdrant's **official MCP server** (`mcp-server-qdrant`, read-only against your index)
- Everything else (grep, targeted reads) stays available — semantic search replaces the *search wandering*, not the tools themselves

## Requirements

- Windows (Linux/macOS work for the Python part; the hook wiring below is ZCode-on-Windows)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (manages Python + deps) and Node.js (for `uvx`/MCP server)
- Free [Qdrant Cloud](https://cloud.qdrant.io) account (1 GB forever-free cluster)

## Quickstart (1 click)

1. Double-click **`install.bat`**
2. Paste your Qdrant Cloud **Cluster URL** and **API key**
3. Optionally index a project immediately
4. Register the MCP server + hook in ZCode (exact values are printed at the end, see below)
5. Restart ZCode — done

## ZCode integration

### MCP server (`~/.zcode/cli/config.json` → `mcp.servers`)

```json
"qdrant": {
  "type": "stdio",
  "command": "uvx",
  "args": ["mcp-server-qdrant"],
  "env": {
    "QDRANT_URL": "https://<your-cluster>.qdrant.io",
    "QDRANT_API_KEY": "<your-api-key>",
    "EMBEDDING_MODEL": "sentence-transformers/all-MiniLM-L6-v2",
    "QDRANT_READ_ONLY": "true"
  },
  "timeout": 120000,
  "enabled": true
}
```

`QDRANT_READ_ONLY=true` keeps the agent from modifying your index — only the indexer writes.

### Auto-sync hook (ZCode → Settings → Hooks → New hook)

| Field | Value |
|---|---|
| Event | `PostToolUse` |
| Matcher | `Write\|Edit\|MultiEdit` |
| Runner | Process |
| Command | `<repo>\.venv\Scripts\python.exe` |
| Arguments | `<repo>\indexer.py` ⏎ `hook` (one per line) |
| Timeout | `60` |

Why via the UI? ZCode persists UI hooks in its internal app store and re-writes `config.json` on restart — hooks created in the UI survive, file-only edits can be overwritten.

### Claude Code / Codex

Same MCP server works there: `mcpServers.graft`-style entry in `~/.claude.json` (Claude Code) or `[mcp_servers.qdrant]` in `~/.codex/config.toml` (Codex). See `mcp-config-example.json`.

## CLI usage

```bash
python indexer.py index  <project>              # incremental index (run once per project)
python indexer.py search <project> "query"      # test a semantic search from the CLI
python indexer.py reindex-file <project> <file> # re-index one file
python indexer.py index <project> --local       # embedded local DB instead of cloud (dev)
```

- Collection name = project folder name, slugified (`Die Möbelpacker` → `die_m_belpacker`)
- Agents should pass this as `collection_name` in `qdrant-find`
- Re-run `index` after bulk changes (git pull, refactors) — the hook only covers Write/Edit

## Manual setup (without install.bat)

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv -r requirements.txt
# create config.json (see install.bat step 3 for the exact shape)
.venv/Scripts/python.exe indexer.py index <project>
```

## Deutsche Kurzanleitung

1. **`install.bat` doppelklicken** — installiert Python-Umgebung, Qdrant-Client, fastembed und die Dokument-Parser (PDF/DOCX/XLSX/PPTX)
2. **Qdrant Cloud URL + API-Key einfügen** (kostenloser Forever-free-Cluster auf [cloud.qdrant.io](https://cloud.qdrant.io))
3. Optional direkt ein Projekt indexieren lassen
4. In ZCode: **MCP-Server** eintragen (`mcp-config-example.json`) und den **Auto-Sync-Hook** anlegen (Werte stehen oben bzw. werden vom Installer ausgegeben)
5. ZCode neu starten — ab jetzt fragt der Agent per `qdrant-find` statt Dateien zu lesen, und jeder Edit synchronisiert automatisch

**Regel für Agents (in AGENTS.md übernehmen):** Bei „wo/was/wie"-Fragen zu Code- oder Dokumenteninhalten zuerst `qdrant-find` mit `collection_name` (Projektordner slugified) — Treffer enthalten `[projekt] pfad · art #n` + Inhalt; ganze Dateien nur lesen, wenn der Chunk nicht reicht. Exhaustive Vorkommenslisten bleiben grep-Sache.

## Notes & limitations

- Embedding model is MiniLM-class (`all-MiniLM-L6-v2`) — great for size/speed, not code-specialized. The indexer is model-agnostic; swap models in indexer.py **and** the MCP env together
- Local mode (`--local`) is for testing; Qdrant's embedded engine recommends ≤ 20k points per collection — use the Cloud free tier for real projects
- Hook auto-sync covers `Write|Edit|MultiEdit` — after bulk operations (git pull, scripts), re-run `index`
- Deleting a collection: drop it in the Qdrant dashboard, then delete `manifests/<collection>.json` and re-run `index`
- License: MIT. Built on [Qdrant](https://github.com/qdrant/qdrant), [fastembed](https://github.com/qdrant/fastembed), [mcp-server-qdrant](https://github.com/qdrant/mcp-server-qdrant)
