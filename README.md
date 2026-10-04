# ebmcp

An MCP server that lets an LLM read EPUB books as Markdown.

| Tool | What it does |
|---|---|
| `get_toc(path)` | Table of contents; each entry is numbered `[n]` |
| `read_chapter(path, index)` | One TOC entry as Markdown, including its subsections |
| `search_book(path, query, offset=0, max_results=20)` | Phrase search (case and punctuation insensitive), paginated; each hit names its `[n]` |
| `get_image(path, image_path)` | An image referenced in a chapter as `![alt](Images/…)` |

## Install

```bash
uv sync
```

## Use with Claude Code

This repo's `.mcp.json` registers the server for Claude Code sessions opened here. To use it from anywhere:

```bash
claude mcp add ebmcp -- uv --directory /path/to/ebmcp run ebmcp
```

## Use with Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ebmcp": { "command": "uv", "args": ["--directory", "/path/to/ebmcp", "run", "ebmcp"] }
  }
}
```

Then ask, for example: *"Tell me about Chapter 2 of /path/to/book.epub"*.

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy ebmcp tests
uv run mcp dev ebmcp/main.py   # MCP Inspector (needs Node.js)
```
