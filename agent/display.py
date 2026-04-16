"""Terminal display utilities — ANSI rendering for nessocode."""
import os
import re

# ANSI escape codes
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
ITALIC  = "\033[3m"
BLUE    = "\033[34m"
CYAN    = "\033[36m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RED     = "\033[31m"
MAGENTA = "\033[35m"
WHITE   = "\033[37m"


def terminal_width() -> int:
    try:
        return min(os.get_terminal_size().columns, 100)
    except OSError:
        return 80


def separator(char: str = "─", color: str = DIM) -> str:
    return f"{color}{char * terminal_width()}{RESET}"


def render_markdown(text: str) -> str:
    """Minimal Markdown → terminal ANSI rendering."""
    # Fenced code blocks
    text = re.sub(
        r"```[a-z]*\n(.*?)```",
        lambda m: f"{CYAN}{m.group(1).rstrip()}{RESET}",
        text,
        flags=re.DOTALL,
    )
    # Headers
    text = re.sub(r"^# (.+)$",  f"{BOLD}{MAGENTA}\\1{RESET}", text, flags=re.MULTILINE)
    text = re.sub(r"^## (.+)$", f"{BOLD}{BLUE}\\1{RESET}",    text, flags=re.MULTILINE)
    text = re.sub(r"^### (.+)$",f"{BOLD}\\1{RESET}",           text, flags=re.MULTILINE)
    # Bold / italic / inline code
    text = re.sub(r"\*\*(.+?)\*\*", f"{BOLD}\\1{RESET}",   text)
    text = re.sub(r"\*(.+?)\*",     f"{ITALIC}\\1{RESET}", text)
    text = re.sub(r"`([^`]+)`",     f"{CYAN}\\1{RESET}",   text)
    return text


def fmt_tool_name(name: str, mcp_server: str | None = None) -> str:
    if mcp_server:
        return f"{DIM}[mcp:{mcp_server}]{RESET} {GREEN}{name}{RESET}"
    return f"{GREEN}{name}{RESET}"


def print_tool_call(name: str, preview: str, mcp_server: str | None = None) -> None:
    label = fmt_tool_name(name, mcp_server)
    print(f"{GREEN}⏺{RESET} {label}({DIM}{preview}{RESET})")


def print_tool_result(result: str) -> None:
    lines = result.split("\n")
    preview = lines[0][:80]
    if len(lines) > 1:
        preview += f" {DIM}(+{len(lines) - 1} lines){RESET}"
    elif len(lines[0]) > 80:
        preview += "..."
    print(f"  {DIM}⎿ {preview}{RESET}")


def print_skill_banner(name: str, description: str) -> None:
    print(f"\n{BOLD}{MAGENTA}◆ Skill:{RESET} {BOLD}{name}{RESET}  {DIM}{description}{RESET}")


def format_args_preview(args: dict) -> str:
    """Return a short human-readable preview of the most relevant tool argument."""
    if not args:
        return ""
    priority = ["command", "file_path", "path", "pattern", "url", "query", "thought"]
    for key in priority:
        if key in args:
            val = str(args[key])
            return (val[:60] + "…") if len(val) > 60 else val
    first = str(next(iter(args.values())))
    return (first[:60] + "…") if len(first) > 60 else first
