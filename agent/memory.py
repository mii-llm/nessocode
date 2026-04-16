"""
Simple file-based memory for nessocode.

One file — .nesso_memory.md — in the working directory.
The model reads it via the system prompt and writes to it with MemoryWrite.
Hard cap at 300 words; the model is asked to consolidate when near the limit.
"""
import os

_memory_path: str = ".nesso_memory.md"
MAX_WORDS = 300


def init(path: str) -> None:
    """Set the memory file path (called at agent startup)."""
    global _memory_path
    _memory_path = path


def read() -> str:
    """Return current memory content, or empty string if file doesn't exist."""
    if not os.path.exists(_memory_path):
        return ""
    try:
        return open(_memory_path).read().strip()
    except Exception:
        return ""


def write(content: str) -> str:
    """
    Overwrite memory with new content.
    Enforces the word cap and returns a status string.
    """
    content = content.strip()
    words = len(content.split())

    if words > MAX_WORDS:
        return (
            f"error: memory too long ({words} words, max {MAX_WORDS}). "
            "Consolidate and remove outdated entries before writing."
        )

    try:
        with open(_memory_path, "w") as fh:
            fh.write(content + "\n")
        return f"Memory saved ({words}/{MAX_WORDS} words)."
    except Exception as e:
        return f"error writing memory: {e}"


def word_count() -> int:
    content = read()
    return len(content.split()) if content else 0


def clear() -> None:
    if os.path.exists(_memory_path):
        os.remove(_memory_path)


def format_for_prompt() -> str:
    """Return a system-prompt block, or empty string if memory is empty."""
    content = read()
    if not content:
        return ""
    used = len(content.split())
    warning = f"  ⚠ Approaching limit — consolidate soon." if used > MAX_WORDS * 0.8 else ""
    return (
        f"\n\n<memory ({used}/{MAX_WORDS} words){warning}>\n"
        f"{content}\n"
        f"</memory>"
    )
