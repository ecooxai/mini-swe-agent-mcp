# MCP server

The MCP server lets an MCP-capable host provide the language model while mini-SWE-agent provides its coding
instructions and local command environment. No model API key is needed.

Start a stdio server for a local client such as Claude Desktop:

```bash
mini-mcp /path/to/project
```

The equivalent subcommand is:

```bash
miniswe mcp /path/to/project
```

Configure the client to launch that command with the project path as its argument. The server exposes:

- the `coding_agent` prompt with mini-SWE-agent's normal coding workflow;
- the `miniswe://context` resource containing workspace instructions;
- the `get_coding_prompt` tool for clients that do not expose MCP prompts to the model;
- the `upload_file` tool for saving ChatGPT attachments into the workspace;
- the `get_image` tool for returning workspace images inline;
- the `get_host_screen` tool for capturing the macOS or Linux host screen and returning it inline;
- the `move_host_pointer` and `click_host_pointer` tools for moving/clicking the host mouse pointer;
- the `bash` tool for inspecting, editing, and testing files through mini-SWE-agent's local environment.

`upload_file` uses ChatGPT's MCP file parameter extension. ChatGPT passes an authorized temporary file reference,
and the server downloads its bytes into the requested relative workspace path. After changing the tool list, refresh
the connector metadata in ChatGPT before using it.

`get_host_screen` saves a medium-quality JPEG screenshot to `.miniswe-agent/host-screen.jpg` by default and returns
both a text summary and the inline image. The text includes the saved path, MIME type, quality, resolution, and file
size. On macOS, the terminal or Python process that starts the MCP server may need Screen Recording permission. On
Linux, install one screenshot backend such as `gnome-screenshot`, `grim`, `spectacle`, `scrot`, `maim`, or
ImageMagick's `import`.

`move_host_pointer` and `click_host_pointer` use absolute screen coordinates. On macOS, install `cliclick`. On Linux,
install `xdotool`.

For a client that connects by URL, use Streamable HTTP:

```bash
mini-mcp /path/to/project --transport streamable-http
```

The endpoint is `http://127.0.0.1:8000/mcp` by default. Use `--host` and `--port` to change it.

!!! warning

    The `bash` tool can execute arbitrary commands with your user permissions. Keep the HTTP server bound to
    `127.0.0.1` unless you add authentication and understand the security implications.
