@echo off
setlocal
cd /d "%~dp0"
echo ============================================
echo  Qdrant ZCode Context Engine - Installer
echo  100%% local embeddings - Qdrant Cloud free
echo ============================================
echo.

where uv >nul 2>nul
if %errorlevel% neq 0 (
  echo [!] uv not found. Install it first:
  echo     https://docs.astral.sh/uv/getting-started/installation/
  echo     or: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
  pause
  exit /b 1
)

echo [1/5] Creating Python 3.12 venv...
uv venv --python 3.12 .venv
if %errorlevel% neq 0 ( echo [!] venv failed & pause & exit /b 1 )

echo [2/5] Installing dependencies (qdrant-client, fastembed, PDF/DOCX/XLSX/PPTX parsers)...
uv pip install --python .venv -r requirements.txt
if %errorlevel% neq 0 ( echo [!] dependency install failed & pause & exit /b 1 )

echo [3/5] Qdrant Cloud credentials
echo       Free cluster: https://cloud.qdrant.io  (1 GB forever-free)
set /p QURL=Cluster URL (https://xxxx.qdrant.io): 
set /p QKEY=API Key: 
".venv\Scripts\python.exe" -c "import json,os;c={'mode':'cloud','url':os.environ['QURL'].strip(),'api_key':os.environ['QKEY'].strip(),'collection_prefix':'','_note':'EMBEDDING_MODEL must match your MCP EMBEDDING_MODEL: sentence-transformers/all-MiniLM-L6-v2'};json.dump(c,open('config.json','w'),indent=2)"

echo [4/5] Testing connection...
".venv\Scripts\python.exe" -c "from qdrant_client import QdrantClient;import json;c=json.load(open('config.json'));cl=QdrantClient(url=c['url'],api_key=c['api_key']);print('OK - collections:',[x.name for x in cl.get_collections().collections])"
if %errorlevel% neq 0 (
  echo [!] Connection failed - check URL and API key, then re-run.
  pause
  exit /b 1
)

echo [5/5] Optional: index a project right now
set /p IDXP=Project path to index (empty = skip): 
if not "%IDXP%"=="" ".venv\Scripts\python.exe" indexer.py index "%IDXP%"

echo.
echo [6/6] Auto-configure ZCode (MCP server + auto-sync hook)
echo       If ZCode is running: close it completely first, restart afterwards.
set /p AUTOCFG=Configure ZCode now? (y/n): 
if /i "%AUTOCFG%"=="y" ".venv\Scripts\python.exe" setup_zcode.py

echo.
echo ============================================
echo  DONE. Manual fallback for ZCode:
echo   MCP:  merge mcp-config-example.json
echo   Hook: Event PostToolUse, Matcher Write^|Edit^|MultiEdit,
echo         Command %CD%\.venv\Scripts\python.exe
echo         Args: %CD%\indexer.py  +  hook   Timeout: 60
echo  Docs: README.md
echo ============================================
pause
