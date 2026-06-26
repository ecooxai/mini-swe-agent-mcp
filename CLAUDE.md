@AGENTS.md

# MCP development server

Create a Python 3.12 virtual environment and install dependencies:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Run the MCP server directly from the source tree without building the package:

```bash
PYTHONPATH=src .venv/bin/python -m minisweagent.run.mcp /Users/ecoo/project/tem --transport streamable-http --port 8009
```

The MCP endpoint is `http://127.0.0.1:8009/mcp`.

During development, restart the server automatically when Python files under `src` change:

```bash
.venv/bin/watchfiles --filter python 'env PYTHONPATH=src .venv/bin/python -m minisweagent.run.mcp /Users/ecoo/project/tem --transport streamable-http --port 8009' src
```
