"""
Core REPL and agent loop for nessocode.

Architecture
------------
  NessoAgent
    ├── Config           — loaded from config.yaml / env vars
    ├── MCPManager       — zero or more external MCP servers
    ├── SkillRegistry    — slash-command workflows from skills/*.yaml
    └── REPL loop
          └── _run_turn() — iterates until no more tool calls remain
"""
import json
import os
import sys
from typing import List, Optional

from .api import build_messages, make_tool_defs, stream_response, call_response
from .config import Config
from .display import (
    RESET, BOLD, DIM, BLUE, CYAN, GREEN, YELLOW, RED, MAGENTA,
    format_args_preview, print_skill_banner, print_tool_call,
    print_tool_result, render_markdown, separator,
)
from .mcp_client import MCPManager
from .skills import SkillRegistry
from .tools import TOOLS, run_tool, init_tavily
from . import memory


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are Nesso, an expert coding CLI agent. Working directory: {cwd}

<behavior>
- Be CONCISE. No preamble, no trailing summaries unless asked.
- Be DIRECT. Act instead of asking permission.
- Be THOROUGH. Always read files before editing. Understand context first.
- Be CAREFUL. Verify changes work. Don't break working code.
</behavior>

<workflow>
1. UNDERSTAND: Use Glob/Grep to find files, Read to understand code.
2. PLAN: For complex tasks, use Think to reason through the approach.
3. EXECUTE: Make precise changes with Edit (preferred) or Write (new files).
4. VERIFY: Run tests / linters / builds when available.
</workflow>

<tool-guidelines>
- Read   : ALWAYS read a file before editing.
- Edit   : Surgical string replacement. old_string must match EXACTLY.
- MultiEdit : Multiple changes to one file atomically.
- Write  : New files or full rewrites only.
- Bash   : git, tests, builds. Use run_in_background for long commands.
- Grep   : ALWAYS use Grep for searching — never bash grep/rg.
- Glob   : Find files by pattern. Prefer over bash find.
- Think  : Complex multi-step reasoning before acting.
- TodoWrite : Track progress on tasks with 3+ steps.
- WebSearch : Search the web for docs or context.
- WebFetch  : Fetch and read a specific URL.
- InvokeSkill : Run a pre-built skill workflow by name (see <skills> below).
</tool-guidelines>

<skill-rules>
- ALWAYS use InvokeSkill(name="commit") instead of running git commit manually.
- ALWAYS use InvokeSkill(name="review") instead of manually reading and listing issues.
- ALWAYS use InvokeSkill(name="tests") instead of writing tests from scratch without it.
- Use InvokeSkill whenever a skill matches the current task — it provides better,
  more structured instructions than improvising the steps yourself.
</skill-rules>

<code-style>
- Match existing project conventions exactly.
- No comments unless asked or truly necessary.
- No new dependencies without explicit approval.
- Never commit secrets or credentials.
</code-style>

<response-format>
- Simple questions → direct answer.
- Coding tasks → just do the work, minimal narration.
- Skip: "I'll help you…", "Let me…", "Here's what I did…"
- Explain only when explicitly asked.
</response-format>

<critical-rules>
- NEVER assume file contents. Always read first.
- NEVER commit unless explicitly asked.
- NEVER run rm -rf, DROP TABLE, or similar without explicit confirmation.
- If blocked, state the blocker concisely and ask.
</critical-rules>

<skills>
{skill_catalogue}
Call InvokeSkill(name="<skill>") whenever a listed skill is the right tool for
the job — e.g. after finishing a fix, call InvokeSkill(name="commit").
</skills>

<memory-rules>
Call MemoryWrite with the COMPLETE updated memory after:
- Reading a file for the first time → save its purpose and key functions
- Fixing a bug → save: file, what was wrong, what the fix was
- Learning a project convention or pattern → save the rule
- Making an architectural or design decision → save the decision and reason
- Discovering important project structure → save file/module layout

Do NOT save: current task status, conversation summaries, or anything
that will be irrelevant next session.

Always rewrite the full memory content (it replaces the previous version).
Keep it under 300 words. Use short bullet points.
</memory-rules>{skill_section}"""


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------

def _save_session(messages: list, path: str) -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(messages, fh, indent=2)
    except Exception:
        pass


def _load_session(path: str) -> list:
    try:
        if os.path.exists(path):
            with open(path) as fh:
                return json.load(fh)
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class NessoAgent:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.messages: List[dict] = []
        self._active_skill: Optional[object] = None  # Skill | None

        self.mcp = MCPManager()
        self.skills = SkillRegistry()

        # Init Tavily if key provided
        if config.tavily_api_key:
            init_tavily(config.tavily_api_key)

        # Init memory (per working directory)
        memory.init(os.path.join(os.getcwd(), ".nesso_memory.md"))

        self._connect_mcp_servers()
        self._load_skills()

        if config.session_file:
            self.messages = _load_session(config.session_file)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _connect_mcp_servers(self) -> None:
        for name, srv in self.config.mcp_servers.items():
            if not srv.enabled:
                continue
            print(f"  {DIM}MCP:{RESET} connecting to {BOLD}{name}{RESET}…", end="", flush=True)
            ok = self.mcp.add_server(name, srv.command, srv.args, srv.env)
            if ok:
                n = len(self.mcp.clients[name].tools)
                print(f"  {GREEN}✓{RESET} {n} tool{'s' if n != 1 else ''}")
            else:
                print(f"  {YELLOW}✗ (skipped){RESET}")

    def _load_skills(self) -> None:
        skills_dir = self.config.skills_dir
        if not os.path.isabs(skills_dir):
            # Resolve relative to the project root (one level above agent/)
            skills_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                skills_dir,
            )
        n = self.skills.load_directory(skills_dir)
        if n:
            print(f"  {DIM}Skills:{RESET} loaded {n} skill{'s' if n != 1 else ''}")

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def _system_prompt(self) -> str:
        # Build skill catalogue for the model
        skills = self.skills.list_all()
        if skills:
            lines = [f'- {s.name}: {s.description}' for s in skills]
            skill_catalogue = "\n".join(lines)
        else:
            skill_catalogue = "(no skills loaded)"

        skill_section = ""
        if self._active_skill and self._active_skill.system_addition:
            skill_section = (
                f"\n\n<active-skill name=\"{self._active_skill.name}\">\n"
                f"{self._active_skill.system_addition}\n"
                f"</active-skill>"
            )
        return _SYSTEM_PROMPT.format(
            cwd=os.getcwd(),
            skill_catalogue=skill_catalogue,
            skill_section=skill_section,
        ) + memory.format_for_prompt()

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    def _invoke_skill_def(self) -> dict:
        """OpenAI tool definition for InvokeSkill, built from the live registry."""
        names = [s.name for s in self.skills.list_all()]
        return {
            "type": "function",
            "function": {
                "name": "InvokeSkill",
                "description": (
                    "Run a pre-built skill workflow. "
                    "Call this whenever a task matches one of the available skills — "
                    "e.g. after finishing a bug fix call InvokeSkill(name='commit'). "
                    "The skill returns step-by-step instructions; follow them using "
                    "the other available tools."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "enum": names or ["(no skills)"],
                            "description": "Skill name to invoke",
                        },
                        "context": {
                            "type": "string",
                            "description": "Optional extra context passed to the skill",
                        },
                    },
                    "required": ["name"],
                },
            },
        }

    def _all_tool_defs(self) -> list:
        defs = make_tool_defs(TOOLS, self.mcp.get_openai_tools())
        if self.skills.list_all():
            defs.append(self._invoke_skill_def())
        return defs

    def _call_tool(self, name: str, args: dict) -> str:
        if name == "InvokeSkill":
            return self._handle_invoke_skill(args)
        if name in TOOLS:
            return run_tool(name, args)
        if self.mcp.is_mcp_tool(name):
            return self.mcp.call_tool(name, args)
        return f"error: unknown tool '{name}'"

    def _handle_invoke_skill(self, args: dict) -> str:
        skill_name = args.get("name", "")
        extra_context = args.get("context", "")
        skill = self.skills.get(skill_name)
        if skill is None:
            available = [s.name for s in self.skills.list_all()]
            return f"error: unknown skill '{skill_name}'. Available: {available}"
        self._active_skill = skill
        print_skill_banner(skill.name, skill.description)
        prompt = skill.prompt
        if extra_context:
            prompt += f"\n\nAdditional context: {extra_context}"
        return (
            f"Skill '{skill_name}' activated. "
            f"Execute every step below using the available tools. "
            f"Do NOT call InvokeSkill again.\n\n"
            f"{prompt}"
        )

    def _mcp_server_for(self, name: str) -> Optional[str]:
        return self.mcp.tool_server(name)

    # ------------------------------------------------------------------
    # Agent turn (inner loop)
    # ------------------------------------------------------------------

    def _run_turn(self) -> None:
        """Run the agent loop until the model stops calling tools."""
        tool_defs = self._all_tool_defs()
        system = self._system_prompt()
        tool_calls_total = 0

        while True:
            oai_msgs = build_messages(self.messages, system)
            blocks: list = []
            print()   # single blank line before each LLM response
            if self.config.stream:
                blocks = self._stream_turn(oai_msgs, tool_defs)
            else:
                blocks = self._blocking_turn(oai_msgs, tool_defs)

            if blocks is None:          # error already printed
                return

            # ---- execute tool calls ----
            tool_results = []
            for block in blocks:
                if block["type"] != "tool_use":
                    continue
                tool_calls_total += 1
                if tool_calls_total > self.config.max_tool_calls:
                    print(f"\n{YELLOW}⚠ max_tool_calls ({self.config.max_tool_calls}) reached — stopping.{RESET}")
                    self.messages.append({"role": "assistant", "content": blocks})
                    return
                server = self._mcp_server_for(block["name"])
                print_tool_call(block["name"], format_args_preview(block["input"]), server)
                result = self._call_tool(block["name"], block["input"])
                print_tool_result(result)
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block["id"],
                    "content":     result,
                })

            self.messages.append({"role": "assistant", "content": blocks})

            if not tool_results:
                break

            self.messages.append({"role": "user", "content": tool_results})

        if self.config.session_file:
            _save_session(self.messages, self.config.session_file)

        if tool_calls_total > 0:
            self._memory_nudge()

    def _memory_nudge(self) -> None:
        """
        After a productive turn, ask the model once whether anything is worth
        saving. If it calls MemoryWrite, great. If it just replies with text,
        we discard that response so it doesn't pollute the conversation.
        """
        nudge = (
            "Based on what you just did, call MemoryWrite if you learned "
            "anything worth remembering long-term (new file understood, bug "
            "fixed, pattern discovered). If nothing is worth saving, do nothing."
        )
        oai_msgs = build_messages(
            self.messages + [{"role": "user", "content": nudge}],
            self._system_prompt(),
        )
        tool_defs = self._all_tool_defs()

        if self.config.stream:
            blocks = self._stream_turn(oai_msgs, tool_defs)
        else:
            blocks = self._blocking_turn(oai_msgs, tool_defs)

        if not blocks:
            return

        # Only act on MemoryWrite calls — discard any text response
        wrote = False
        for block in blocks:
            if block["type"] == "tool_use" and block["name"] == "MemoryWrite":
                result = self._call_tool("MemoryWrite", block["input"])
                wc = memory.word_count()
                print(f"  {DIM}memory: {result} ({wc}/{memory.MAX_WORDS} words){RESET}")
                wrote = True

        # Don't append nudge or response to self.messages to keep history clean

    def _stream_turn(self, oai_msgs: list, tool_defs: list) -> Optional[list]:
        """Handle one streaming LLM call.  Returns assembled blocks."""
        blocks = None
        printed_text = False

        for event in stream_response(
            self.config.api_url, self.config.model, oai_msgs, tool_defs,
            self.config.max_tokens, self.config.repetition_penalty,
        ):
            etype = event["type"]

            if etype == "text_delta":
                printed_text = True
                print(event["text"], end="", flush=True)

            elif etype == "tool_start":
                if printed_text:
                    print()
                    printed_text = False
                # Tool name printed later with full args in print_tool_call

            elif etype == "done":
                if printed_text:
                    print()
                blocks = event["blocks"]

                # Print text blocks (already streamed above, just render markdown)
                for b in blocks:
                    if b["type"] == "text" and not self.config.stream:
                        print(render_markdown(b["text"].strip()))

            elif etype == "error":
                print(f"\n{RED}API error: {event['message']}{RESET}")
                return None

        return blocks

    def _blocking_turn(self, oai_msgs: list, tool_defs: list) -> Optional[list]:
        """Handle one blocking LLM call.  Returns assembled blocks."""
        try:
            blocks = call_response(
                self.config.api_url, self.config.model, oai_msgs, tool_defs,
                self.config.max_tokens, self.config.repetition_penalty,
            )
        except RuntimeError as exc:
            print(f"\n{RED}API error: {exc}{RESET}")
            return None

        for block in blocks:
            if block["type"] == "text":
                text = block["text"].strip()
                if text:
                    print(render_markdown(text))

        return blocks

    # ------------------------------------------------------------------
    # Headless / single-shot
    # ------------------------------------------------------------------

    def run_once(self, instruction: str) -> str:
        """Run a single non-interactive turn and return the final text response."""
        self.messages.append({"role": "user", "content": instruction})
        self._run_turn()
        for msg in reversed(self.messages):
            if msg["role"] == "assistant":
                content = msg["content"]
                if isinstance(content, list):
                    return "\n".join(
                        b["text"] for b in content if b.get("type") == "text"
                    ).strip()
                if isinstance(content, str):
                    return content.strip()
        return ""

    # ------------------------------------------------------------------
    # REPL
    # ------------------------------------------------------------------

    def run_repl(self) -> None:
        """Start the interactive REPL."""
        model_short = os.path.basename(self.config.model.rstrip("/"))
        tool_count = len(TOOLS)
        mcp_count  = sum(len(c.tools) for c in self.mcp.clients.values())
        skill_count = len(self.skills)

        print(f"\n{BOLD}nessocode{RESET}  "
              f"{DIM}{model_short}  │  {os.getcwd()}{RESET}")
        mem_status = f"  memory {memory.word_count()}/{memory.MAX_WORDS}w" if memory.read() else ""
        print(f"{DIM}{tool_count} built-in tools"
              f"{f'  +{mcp_count} MCP' if mcp_count else ''}"
              f"{f'  +{skill_count} skills' if skill_count else ''}"
              f"{mem_status}"
              f"  │  /help for commands{RESET}\n")

        while True:
            try:
                print(separator())
                user_input = input(f"{BOLD}{BLUE}❯{RESET} ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                break

            if not user_input:
                continue

            # ---- slash commands ----
            if user_input.startswith("/"):
                cmd, _, rest = user_input.partition(" ")
                cmd = cmd.lower()

                if cmd in ("/q", "/quit", "/exit"):
                    break

                elif cmd in ("/c", "/clear"):
                    self.messages.clear()
                    self._active_skill = None
                    print(f"{GREEN}✓{RESET} Conversation cleared")
                    continue

                elif cmd in ("/t", "/tools"):
                    self._print_tools()
                    continue

                elif cmd in ("/s", "/skills"):
                    self._print_skills()
                    continue

                elif cmd in ("/b", "/bashes"):
                    self._print_bashes()
                    continue

                elif cmd in ("/memory", "/mem"):
                    content = memory.read()
                    if not content:
                        print(f"{DIM}Memory is empty.{RESET}")
                    else:
                        wc = memory.word_count()
                        print(f"\n{BOLD}Memory{RESET} {DIM}({wc}/{memory.MAX_WORDS} words){RESET}\n")
                        print(content)
                    if rest.strip() == "clear":
                        memory.clear()
                        print(f"\n{GREEN}✓{RESET} Memory cleared.")
                    print()
                    continue

                elif cmd in ("/session",):
                    if self.config.session_file:
                        print(f"Session file: {self.config.session_file}  "
                              f"({len(self.messages)} messages)")
                    else:
                        print(f"{DIM}No session file configured (use --session){RESET}")
                    continue

                elif cmd in ("/h", "/help"):
                    self._print_help()
                    continue

                # Try skill lookup
                skill = self.skills.get(cmd)
                if skill:
                    self._active_skill = skill
                    print_skill_banner(skill.name, skill.description)
                    user_prompt = skill.prompt
                    if rest:
                        user_prompt += f"\n\nAdditional context: {rest}"
                else:
                    print(f"{YELLOW}Unknown command '{cmd}'. Try /help{RESET}")
                    continue
            else:
                self._active_skill = None
                user_prompt = user_input

            # ---- agent turn ----
            try:
                print(separator())
                self.messages.append({"role": "user", "content": user_prompt})
                self._run_turn()
                print()
            except KeyboardInterrupt:
                print(f"\n{DIM}(interrupted){RESET}")
                # Discard the unanswered user message
                if self.messages and self.messages[-1]["role"] == "user":
                    self.messages.pop()
            except Exception as exc:
                print(f"\n{RED}Unexpected error: {exc}{RESET}")

        self.mcp.shutdown_all()

    # ------------------------------------------------------------------
    # Help / listing helpers
    # ------------------------------------------------------------------

    def _print_tools(self) -> None:
        print(f"\n{BOLD}Built-in tools ({len(TOOLS)}){RESET}")
        for name, defn in TOOLS.items():
            first_line = defn["description"].splitlines()[0]
            print(f"  {GREEN}{name:<14}{RESET} {first_line}")

        if self.mcp.clients:
            print(f"\n{BOLD}MCP tools{RESET}")
            for sname, client in self.mcp.clients.items():
                print(f"  {MAGENTA}[{sname}]{RESET}  {len(client.tools)} tool(s)")
                for t in client.tools:
                    short_desc = t["description"][:60]
                    print(f"    {GREEN}{t['prefixed_name']}{RESET}  {DIM}{short_desc}{RESET}")
        print()

    def _print_skills(self) -> None:
        skills = self.skills.list_all()
        if not skills:
            print(f"{DIM}No skills loaded.{RESET}")
        else:
            print(f"\n{BOLD}Skills ({len(skills)}){RESET}")
            print(self.skills.format_help())
        print()

    def _print_bashes(self) -> None:
        from .tools import BACKGROUND_SHELLS
        if not BACKGROUND_SHELLS:
            print(f"{DIM}No background shells.{RESET}")
        else:
            print(f"\n{BOLD}Background shells{RESET}")
            for sid, sh in BACKGROUND_SHELLS.items():
                cmd_preview = sh["command"][:50]
                print(f"  {sid}  [{sh['status']}]  {cmd_preview}")
        print()

    def _print_help(self) -> None:
        print(f"""
{BOLD}Commands{RESET}
  /help, /h      This message
  /clear, /c     Clear conversation history
  /tools, /t     List built-in + MCP tools
  /skills, /s    List available skills
  /bashes, /b    List background shell processes
  /memory, /mem  Show persistent memory
  /memory clear  Wipe the memory file
  /session       Show session file status
  /quit, /q      Exit

{BOLD}Skills{RESET}
{self.skills.format_help()}

{BOLD}Tips{RESET}
  • Run a skill:  /commit  or  /review src/main.py
  • Ctrl+C interrupts a running response
  • Pipe input:   echo "refactor this" | nessocode
""")
