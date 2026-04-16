"""Built-in tools for nessocode — filesystem, shell, web, and task management."""
import glob as globlib
import html
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
import urllib.error
import uuid
from typing import Any, Dict, List, Optional

# Shared state for background shells
BACKGROUND_SHELLS: Dict[str, Dict[str, Any]] = {}

# Shared todo list
TODO_LIST: List[Dict[str, str]] = []

# Optional Tavily client (lazy-initialised)
_tavily_client = None


def _get_tavily(api_key: Optional[str] = None):
    global _tavily_client
    if _tavily_client is not None:
        return _tavily_client
    key = api_key or os.environ.get("TAVILY_API_KEY", "")
    if not key:
        return None
    try:
        from tavily import TavilyClient
        _tavily_client = TavilyClient(key)
        return _tavily_client
    except ImportError:
        return None


def init_tavily(api_key: str) -> None:
    """Initialise the Tavily client with the provided key."""
    global _tavily_client
    try:
        from tavily import TavilyClient
        _tavily_client = TavilyClient(api_key)
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# File-system tools
# ---------------------------------------------------------------------------

def tool_read(args: Dict[str, Any]) -> str:
    path = args["file_path"]
    if not os.path.exists(path):
        return f"error: file not found: {path}"
    if os.path.isdir(path):
        return f"error: is a directory — use LS instead: {path}"
    try:
        with open(path, "rb") as f:
            raw = f.read(200_000)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return f"error: binary file: {path}"
        lines = text.splitlines()
        total = len(lines)
        offset = args.get("offset", 0)
        limit = args.get("limit", 2000)
        selected = lines[offset : offset + limit]
        result_lines = []
        for i, line in enumerate(selected):
            if len(line) > 2000:
                line = line[:2000] + "…"
            result_lines.append(f"{offset + i + 1:6}\t{line}")
        result = "\n".join(result_lines)
        remaining = total - offset - len(selected)
        if remaining > 0:
            result += f"\n… ({remaining} more lines, use offset={offset + limit})"
        return result or "(empty file)"
    except Exception as e:
        return f"error: {e}"


def tool_write(args: Dict[str, Any]) -> str:
    path = args["file_path"]
    content = args["content"]
    try:
        parent = os.path.dirname(path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent)
        with open(path, "w") as f:
            f.write(content)
        return f"Wrote {len(content.splitlines())} lines to {path}"
    except Exception as e:
        return f"error: {e}"


def tool_edit(args: Dict[str, Any]) -> str:
    path = args["file_path"]
    old = args["old_string"]
    new = args["new_string"]
    replace_all = args.get("replace_all", False)

    if not os.path.exists(path):
        if old == "":
            return tool_write({"file_path": path, "content": new})
        return f"error: file not found: {path}"

    try:
        with open(path) as f:
            text = f.read()
    except Exception as e:
        return f"error reading: {e}"

    if old == new:
        return "error: old_string and new_string are identical"

    if old not in text:
        preview = "\n".join(f"{i+1:4}| {l}" for i, l in enumerate(text.splitlines()[:20]))
        return f"error: old_string not found.\nFirst 20 lines:\n{preview}"

    count = text.count(old)
    if not replace_all and count > 1:
        return (
            f"error: found {count} occurrences — must be unique. "
            "Use replace_all=true or provide more context."
        )

    result = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    try:
        with open(path, "w") as f:
            f.write(result)
        replaced = count if replace_all else 1
        return f"Replaced {replaced} occurrence{'s' if replaced > 1 else ''} in {path}"
    except Exception as e:
        return f"error writing: {e}"


def tool_multi_edit(args: Dict[str, Any]) -> str:
    path = args["file_path"]
    edits = args["edits"]

    if not os.path.exists(path):
        if edits and edits[0].get("old_string", "") == "":
            try:
                parent = os.path.dirname(path)
                if parent and not os.path.exists(parent):
                    os.makedirs(parent)
                text = edits[0]["new_string"]
                edits = edits[1:]
            except Exception as e:
                return f"error creating file: {e}"
        else:
            return f"error: file not found: {path}"
    else:
        try:
            with open(path) as f:
                text = f.read()
        except Exception as e:
            return f"error reading: {e}"

    current = text
    for i, edit in enumerate(edits):
        old, new = edit["old_string"], edit["new_string"]
        replace_all = edit.get("replace_all", False)
        if old == new:
            return f"error in edit {i+1}: old_string and new_string are identical"
        if old not in current:
            return f"error in edit {i+1}: old_string not found (after previous edits)"
        count = current.count(old)
        if not replace_all and count > 1:
            return f"error in edit {i+1}: {count} occurrences — use replace_all=true"
        current = current.replace(old, new) if replace_all else current.replace(old, new, 1)

    try:
        with open(path, "w") as f:
            f.write(current)
        return f"Applied {len(edits)} edit{'s' if len(edits) > 1 else ''} to {path}"
    except Exception as e:
        return f"error writing: {e}"


def tool_ls(args: Dict[str, Any]) -> str:
    path = args.get("path", os.getcwd())
    ignore = args.get("ignore", [])
    if not os.path.isabs(path):
        return f"error: path must be absolute, got: {path}"
    if not os.path.exists(path):
        return f"error: not found: {path}"
    if not os.path.isdir(path):
        return f"error: not a directory: {path}"
    try:
        import fnmatch
        entries = sorted(os.listdir(path))
        if ignore:
            entries = [
                e for e in entries
                if not any(fnmatch.fnmatch(e, p) for p in ignore)
            ]
        result = []
        for name in entries:
            suffix = "/" if os.path.isdir(os.path.join(path, name)) else ""
            result.append(f"{name}{suffix}")
        return "\n".join(result) or "(empty directory)"
    except Exception as e:
        return f"error: {e}"


def tool_glob(args: Dict[str, Any]) -> str:
    pattern = args["pattern"]
    path = args.get("path", os.getcwd())
    if path and not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)
    full_pattern = os.path.join(path, pattern) if path else pattern
    try:
        matches = globlib.glob(full_pattern, recursive=True)
        matches = sorted(
            [m for m in matches if os.path.exists(m)],
            key=lambda f: os.path.getmtime(f),
            reverse=True,
        )
        if len(matches) > 100:
            return "\n".join(matches[:100]) + f"\n… ({len(matches) - 100} more)"
        return "\n".join(matches) or "No matches found"
    except Exception as e:
        return f"error: {e}"


def tool_grep(args: Dict[str, Any]) -> str:
    pattern_str = args["pattern"]
    path = args.get("path", os.getcwd())
    glob_pattern = args.get("glob")
    output_mode = args.get("output_mode", "files_with_matches")
    before_ctx = args.get("-B", 0)
    after_ctx = args.get("-A", 0)
    both_ctx = args.get("-C", 0)
    show_line_nums = args.get("-n", False)
    ignore_case = args.get("-i", False)
    file_type = args.get("type")
    head_limit = args.get("head_limit")
    multiline = args.get("multiline", False)

    if both_ctx > 0:
        before_ctx = after_ctx = both_ctx

    TYPE_GLOBS = {
        "py": "*.py", "js": "*.js", "ts": "*.ts", "tsx": "*.tsx",
        "jsx": "*.jsx", "go": "*.go", "rs": "*.rs", "java": "*.java",
        "c": "*.c", "cpp": "*.cpp", "rb": "*.rb", "sh": "*.sh",
        "json": "*.json", "yaml": "*.yaml", "yml": "*.yml",
        "md": "*.md", "txt": "*.txt", "html": "*.html", "css": "*.css",
    }
    if file_type and file_type in TYPE_GLOBS:
        glob_pattern = f"**/{TYPE_GLOBS[file_type]}"

    try:
        flags = re.IGNORECASE if ignore_case else 0
        if multiline:
            flags |= re.DOTALL | re.MULTILINE
        pat = re.compile(pattern_str, flags)
    except re.error as e:
        return f"error: invalid regex: {e}"

    results: List[str] = []
    file_counts: Dict[str, int] = {}

    SKIP_EXTS = {
        ".pyc", ".pyo", ".so", ".o", ".a", ".exe", ".dll",
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf",
        ".zip", ".tar", ".gz", ".bz2", ".7z", ".woff", ".woff2",
        ".ttf", ".mp3", ".mp4",
    }

    if os.path.isfile(path):
        files = [path]
    else:
        search_pattern = os.path.join(path, glob_pattern or "**/*")
        files = [f for f in globlib.glob(search_pattern, recursive=True) if os.path.isfile(f)]

    for filepath in files:
        if os.path.splitext(filepath)[1].lower() in SKIP_EXTS:
            continue
        try:
            with open(filepath, errors="ignore") as f:
                content = f.read()
            lines = content.splitlines()

            if multiline:
                for match in pat.finditer(content):
                    sl = content[: match.start()].count("\n")
                    if output_mode == "files_with_matches":
                        if filepath not in file_counts:
                            results.append(filepath)
                            file_counts[filepath] = 1
                    elif output_mode == "count":
                        file_counts[filepath] = file_counts.get(filepath, 0) + 1
                    else:
                        line_text = lines[sl] if sl < len(lines) else ""
                        prefix = f"{filepath}:{sl+1}: " if show_line_nums else f"{filepath}: "
                        results.append(prefix + line_text)
            else:
                for i, line in enumerate(lines):
                    if not pat.search(line):
                        continue
                    if output_mode == "files_with_matches":
                        if filepath not in file_counts:
                            results.append(filepath)
                            file_counts[filepath] = 1
                        break
                    elif output_mode == "count":
                        file_counts[filepath] = file_counts.get(filepath, 0) + 1
                    else:
                        if before_ctx > 0 or after_ctx > 0:
                            start = max(0, i - before_ctx)
                            end = min(len(lines), i + after_ctx + 1)
                            for j in range(start, end):
                                marker = ">" if j == i else " "
                                prefix = f"{filepath}:{j+1}:{marker} " if show_line_nums else f"{filepath}:{marker} "
                                results.append(prefix + lines[j])
                            results.append("")
                        else:
                            prefix = f"{filepath}:{i+1}: " if show_line_nums else f"{filepath}: "
                            results.append(prefix + line)

                    if head_limit and len(results) >= head_limit:
                        break

            if head_limit and len(results) >= head_limit:
                break
        except Exception:
            continue

    if output_mode == "count":
        results = [f"{fp}: {cnt}" for fp, cnt in sorted(file_counts.items())]

    if head_limit and len(results) > head_limit:
        results = results[:head_limit]

    return "\n".join(results) or "No matches found"


# ---------------------------------------------------------------------------
# Shell tools
# ---------------------------------------------------------------------------

def tool_bash(args: Dict[str, Any]) -> str:
    cmd = args["command"]
    timeout = min(args.get("timeout", 120_000), 600_000) / 1000
    run_bg = args.get("run_in_background", False)
    description = args.get("description", "")

    if run_bg:
        shell_id = str(uuid.uuid4())[:8]

        def _run():
            try:
                proc = subprocess.Popen(
                    cmd, shell=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, cwd=os.getcwd(),
                )
                BACKGROUND_SHELLS[shell_id]["process"] = proc
                BACKGROUND_SHELLS[shell_id]["output"] = []
                for line in iter(proc.stdout.readline, ""):
                    BACKGROUND_SHELLS[shell_id]["output"].append(line)
                proc.wait()
                BACKGROUND_SHELLS[shell_id]["status"] = "completed"
                BACKGROUND_SHELLS[shell_id]["exit_code"] = proc.returncode
            except Exception as e:
                BACKGROUND_SHELLS[shell_id]["status"] = "error"
                BACKGROUND_SHELLS[shell_id]["error"] = str(e)

        BACKGROUND_SHELLS[shell_id] = {
            "command": cmd, "status": "running",
            "output": [], "read_index": 0,
            "description": description, "started_at": time.time(),
        }
        threading.Thread(target=_run, daemon=True).start()
        return f"Background shell {shell_id} started. Use BashOutput(bash_id={shell_id!r}) to check."

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True,
            text=True, timeout=timeout, cwd=os.getcwd(),
        )
        output = (result.stdout + result.stderr).strip()
        code = result.returncode
        if len(output) > 30_000:
            output = output[:30_000] + "\n… (truncated)"
        if not output:
            return f"(no output, exit code {code})"
        if code != 0:
            return f"{output}\n(exit code {code})"
        return output
    except subprocess.TimeoutExpired:
        return f"error: timed out after {timeout:.0f}s"
    except Exception as e:
        return f"error: {e}"


def tool_bash_output(args: Dict[str, Any]) -> str:
    bash_id = args["bash_id"]
    if bash_id not in BACKGROUND_SHELLS:
        return f"error: no background shell '{bash_id}'"
    shell = BACKGROUND_SHELLS[bash_id]
    new_out = shell["output"][shell["read_index"]:]
    shell["read_index"] = len(shell["output"])
    if fp := args.get("filter"):
        try:
            pat = re.compile(fp)
            new_out = [l for l in new_out if pat.search(l)]
        except re.error as e:
            return f"error: invalid filter regex: {e}"
    status = shell["status"]
    result = f"Status: {status}\n"
    if status == "completed":
        result += f"Exit code: {shell.get('exit_code', '?')}\n"
    output_text = "".join(new_out)
    result += f"\nOutput:\n{output_text}" if output_text else "\n(no new output)"
    return result


def tool_kill_bash(args: Dict[str, Any]) -> str:
    sid = args["shell_id"]
    if sid not in BACKGROUND_SHELLS:
        return f"error: no background shell '{sid}'"
    shell = BACKGROUND_SHELLS[sid]
    if shell["status"] != "running":
        return f"Shell {sid} is not running (status: {shell['status']})"
    try:
        proc = shell.get("process")
        if proc:
            proc.terminate()
            time.sleep(0.5)
            if proc.poll() is None:
                proc.kill()
        shell["status"] = "killed"
        return f"Killed shell {sid}"
    except Exception as e:
        return f"error: {e}"


# ---------------------------------------------------------------------------
# Web tools
# ---------------------------------------------------------------------------

def tool_web_fetch(args: Dict[str, Any]) -> str:
    url = args["url"]
    prompt = args["prompt"]
    if url.startswith("http://"):
        url = "https://" + url[7:]
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; nessocode/1.0)",
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            final_url = resp.geturl()

        from urllib.parse import urlparse
        if urlparse(url).netloc != urlparse(final_url).netloc:
            return f"Redirected to a different host. Fetch: {final_url}"

        content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r"<style[^>]*>.*?</style>",   "", content, flags=re.DOTALL | re.IGNORECASE)
        title_m = re.search(r"<title[^>]*>(.*?)</title>", content, re.IGNORECASE | re.DOTALL)
        title = html.unescape(title_m.group(1).strip()) if title_m else ""
        text = html.unescape(re.sub(r"<[^>]+>", " ", content))
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 50_000:
            text = text[:50_000] + "… (truncated)"

        result = f"URL: {final_url}\n"
        if title:
            result += f"Title: {title}\n"
        result += f"\n{text}\n\n--- Task ---\n{prompt}"
        return result
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return f"URL error: {e.reason}"
    except Exception as e:
        return f"error: {e}"


def tool_web_search(args: Dict[str, Any]) -> str:
    query = args["query"]
    num = args.get("num_results", 5)
    client = _get_tavily()
    if client is None:
        return (
            "Web search unavailable: set TAVILY_API_KEY or add tavily_api_key to config.yaml.\n"
            "Get a free key at https://tavily.com"
        )
    try:
        resp = client.search(query=query, search_depth="advanced", num_results=num)
        parts = []
        for i, item in enumerate(resp.get("results", []), 1):
            snippet = item.get("content", "")[:200]
            parts.append(f"[{i}] {item['title']}\nURL: {item['url']}\n{snippet}")
        return "\n---\n".join(parts) or "No results found"
    except Exception as e:
        return f"Search error: {e}"


# ---------------------------------------------------------------------------
# Task / utility tools
# ---------------------------------------------------------------------------

def tool_todo_write(args: Dict[str, Any]) -> str:
    global TODO_LIST
    TODO_LIST = args["todos"]
    icons = {"pending": "○", "in_progress": "◐", "completed": "●"}
    lines = ["Todo list updated:"]
    for t in TODO_LIST:
        icon = icons.get(t["status"], "?")
        lines.append(f"  {icon} [{t['id']}] {t['content']}")
    return "\n".join(lines)


def tool_think(args: Dict[str, Any]) -> str:
    return "(thinking complete)"


def tool_memory_write(args: Dict[str, Any]) -> str:
    from . import memory
    return memory.write(args["content"])


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOLS: Dict[str, Dict[str, Any]] = {
    "Bash": {
        "description": (
            "Execute a shell command with optional timeout.\n"
            "- Use run_in_background=true for long-running processes.\n"
            "- Monitor background processes with BashOutput.\n"
            "- NEVER use bash grep/rg/find — use Grep and Glob tools instead.\n"
            "- Separate multiple commands with ; or &&."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command":           {"type": "string", "description": "Shell command to run"},
                "timeout":           {"type": "number", "description": "Timeout in ms (max 600000)"},
                "description":       {"type": "string", "description": "5-10 word description of what this does"},
                "run_in_background": {"type": "boolean", "description": "Run in background; use BashOutput to read"},
            },
            "required": ["command"],
        },
        "function": tool_bash,
    },
    "BashOutput": {
        "description": "Get output from a background Bash shell started with run_in_background=true.",
        "parameters": {
            "type": "object",
            "properties": {
                "bash_id": {"type": "string", "description": "ID returned by the background Bash call"},
                "filter":  {"type": "string", "description": "Optional regex to filter output lines"},
            },
            "required": ["bash_id"],
        },
        "function": tool_bash_output,
    },
    "KillBash": {
        "description": "Terminate a running background shell by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "shell_id": {"type": "string", "description": "ID of the background shell to kill"},
            },
            "required": ["shell_id"],
        },
        "function": tool_kill_bash,
    },
    "Glob": {
        "description": (
            "Find files matching a glob pattern (e.g. **/*.py, src/**/*.ts).\n"
            "Results sorted by modification time, newest first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern"},
                "path":    {"type": "string", "description": "Root directory (default: cwd)"},
            },
            "required": ["pattern"],
        },
        "function": tool_glob,
    },
    "Grep": {
        "description": (
            "Search file contents with a regex pattern.\n"
            "- output_mode: files_with_matches (default) | content | count\n"
            "- Use -A/-B/-C for context lines (content mode)\n"
            "- Use -i for case-insensitive, -n for line numbers\n"
            "- Use type= for language filter (py, js, ts, go, …)\n"
            "- Use multiline=true for cross-line patterns"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern":     {"type": "string"},
                "path":        {"type": "string"},
                "glob":        {"type": "string"},
                "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"]},
                "-B":          {"type": "number"},
                "-A":          {"type": "number"},
                "-C":          {"type": "number"},
                "-n":          {"type": "boolean"},
                "-i":          {"type": "boolean"},
                "type":        {"type": "string"},
                "head_limit":  {"type": "number"},
                "multiline":   {"type": "boolean"},
            },
            "required": ["pattern"],
        },
        "function": tool_grep,
    },
    "LS": {
        "description": "List directory contents. Use Glob/Grep when you know what you're looking for.",
        "parameters": {
            "type": "object",
            "properties": {
                "path":   {"type": "string", "description": "Absolute directory path"},
                "ignore": {"type": "array", "items": {"type": "string"}, "description": "Glob patterns to exclude"},
            },
            "required": ["path"],
        },
        "function": tool_ls,
    },
    "Read": {
        "description": (
            "Read a file with line numbers.\n"
            "- ALWAYS read a file before editing it.\n"
            "- Use offset and limit for large files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "offset":    {"type": "number", "description": "Start line (0-indexed)"},
                "limit":     {"type": "number", "description": "Number of lines to read"},
            },
            "required": ["file_path"],
        },
        "function": tool_read,
    },
    "Edit": {
        "description": (
            "Exact-string replacement in a file.\n"
            "- Read the file first.\n"
            "- old_string must match EXACTLY including whitespace.\n"
            "- Fails if old_string is not unique — add more context or use replace_all=true.\n"
            "- Pass empty old_string to create a new file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path":   {"type": "string"},
                "old_string":  {"type": "string"},
                "new_string":  {"type": "string"},
                "replace_all": {"type": "boolean", "description": "Replace every occurrence (default false)"},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
        "function": tool_edit,
    },
    "MultiEdit": {
        "description": (
            "Apply multiple ordered edits to a single file atomically.\n"
            "Each edit uses the same rules as Edit. All must succeed or none are applied."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_string":  {"type": "string"},
                            "new_string":  {"type": "string"},
                            "replace_all": {"type": "boolean"},
                        },
                        "required": ["old_string", "new_string"],
                    },
                },
            },
            "required": ["file_path", "edits"],
        },
        "function": tool_multi_edit,
    },
    "Write": {
        "description": (
            "Write (or overwrite) a file.\n"
            "- Read the file first if it already exists.\n"
            "- Prefer Edit for modifying existing files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content":   {"type": "string"},
            },
            "required": ["file_path", "content"],
        },
        "function": tool_write,
    },
    "WebFetch": {
        "description": "Fetch a URL and convert it to text for analysis.",
        "parameters": {
            "type": "object",
            "properties": {
                "url":    {"type": "string"},
                "prompt": {"type": "string", "description": "What to extract or analyse from the page"},
            },
            "required": ["url", "prompt"],
        },
        "function": tool_web_fetch,
    },
    "WebSearch": {
        "description": (
            "Search the web with Tavily and return ranked snippets.\n"
            "Requires TAVILY_API_KEY env variable or tavily_api_key in config.yaml."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query":       {"type": "string"},
                "num_results": {"type": "number", "description": "Results to return (default 5)"},
            },
            "required": ["query"],
        },
        "function": tool_web_search,
    },
    "TodoWrite": {
        "description": (
            "Track progress on a multi-step task.\n"
            "States: pending | in_progress (max one at a time) | completed"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id":      {"type": "string"},
                            "content": {"type": "string"},
                            "status":  {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                        },
                        "required": ["id", "content", "status"],
                    },
                },
            },
            "required": ["todos"],
        },
        "function": tool_todo_write,
    },
    "Think": {
        "description": "Internal scratchpad for reasoning through complex problems before acting.",
        "parameters": {
            "type": "object",
            "properties": {
                "thought": {"type": "string"},
            },
            "required": ["thought"],
        },
        "function": tool_think,
    },
    "MemoryWrite": {
        "description": (
            "Persist important facts to long-term memory across sessions.\n"
            "Write the COMPLETE updated memory — this overwrites the previous content.\n"
            "Use bullet points. Keep it under 300 words.\n"
            "Save things worth remembering: project structure, key decisions, "
            "recurring patterns, important file locations, known bugs.\n"
            "Do NOT save transient task state or conversation summaries."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Full memory content in markdown bullet points (max 300 words)",
                },
            },
            "required": ["content"],
        },
        "function": tool_memory_write,
    },
}


def run_tool(name: str, args: Dict[str, Any]) -> str:
    """Dispatch a tool call to its implementation."""
    if name not in TOOLS:
        return f"error: unknown tool '{name}'"
    try:
        return TOOLS[name]["function"](args)
    except Exception as e:
        return f"error ({type(e).__name__}): {e}"
