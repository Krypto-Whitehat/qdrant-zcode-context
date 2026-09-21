#!/usr/bin/env python3
"""
setup_zcode.py — registers the Qdrant context engine in ZCode automatically:
  1. MCP server  -> %USERPROFILE%\\.zcode\\cli\\config.json  (mcp.servers.qdrant)
  2. Auto-sync hook -> same file (hooks.events.PostToolUse, Write|Edit|MultiEdit)

Creates a timestamped backup of config.json before writing. Safe to re-run
(existing entries are updated in place, not duplicated).

Run AFTER install.bat (needs config.json with your Qdrant URL + API key).
Tip: close ZCode completely (tray!) before confirming, restart ZCode after.
"""
import json, os, shutil, sys, time

HOME = os.environ.get("USERPROFILE") or os.path.expanduser("~")
ZCODE_CONFIG = os.path.join(HOME, ".zcode", "cli", "config.json")
REPO = os.path.dirname(os.path.abspath(__file__))
REPO_CONFIG = os.path.join(REPO, "config.json")
VENV_PY = os.path.join(REPO, ".venv", "Scripts", "python.exe")
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def main():
    if not os.path.exists(REPO_CONFIG):
        print("[!] config.json not found in this folder — run install.bat first.")
        sys.exit(1)
    if not os.path.exists(VENV_PY):
        print("[!] .venv not found — run install.bat first.")
        sys.exit(1)
    rc = load(REPO_CONFIG)
    if not rc.get("url") or not rc.get("api_key"):
        print("[!] config.json has no url/api_key — run install.bat first.")
        sys.exit(1)

    if not os.path.exists(ZCODE_CONFIG):
        os.makedirs(os.path.dirname(ZCODE_CONFIG), exist_ok=True)
        cfg = {}
    else:
        bak = ZCODE_CONFIG + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(ZCODE_CONFIG, bak)
        print(f"[i] backup: {bak}")
        cfg = load(ZCODE_CONFIG)

    # 1) MCP server
    cfg.setdefault("mcp", {}).setdefault("servers", {})["qdrant"] = {
        "type": "stdio",
        "command": "uvx",
        "args": ["mcp-server-qdrant"],
        "env": {
            "QDRANT_URL": rc["url"],
            "QDRANT_API_KEY": rc["api_key"],
            "EMBEDDING_MODEL": EMBEDDING_MODEL,
            "QDRANT_READ_ONLY": "true",
        },
        "timeout": 120000,
        "enabled": True,
    }

    # 2) Auto-sync hook (append once)
    py = VENV_PY.replace("/", "\\")
    hook_cmd = {"type": "process", "command": py,
                "args": [os.path.join(REPO, "indexer.py"), "hook"],
                "timeoutMs": 60000, "enabled": True}
    events = cfg.setdefault("hooks", {}).setdefault("events", {})
    post = events.setdefault("PostToolUse", [])
    entry = next((e for e in post if any(h.get("args", [None, None])[:2] == [os.path.join(REPO, "indexer.py"), "hook"]
                                         for h in e.get("hooks", []))), None)
    if entry:
        entry["matcher"] = "Write|Edit|MultiEdit"
        entry["hooks"] = [hook_cmd]
        print("[i] existing qdrant hook updated")
    else:
        post.append({"matcher": "Write|Edit|MultiEdit", "hooks": [hook_cmd]})
        print("[+] qdrant hook added")

    with open(ZCODE_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print(f"[+] MCP 'qdrant' + auto-sync hook written to {ZCODE_CONFIG}")
    print("[i] Restart ZCode (tray -> quit) to load them.")

if __name__ == "__main__":
    main()
