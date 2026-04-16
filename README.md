# nessocode

A minimal yet powerful local AI coding agent powered by **[Nesso-4B](https://huggingface.co/mii-llm/nesso-4B)** — a 4-billion-parameter model served locally via [vLLM](https://github.com/vllm-project/vllm).

It runs entirely on your machine. No cloud API, no data leaving your server.

```
nessocode  mii-llm/nesso-4B  │  /home/user/myproject
15 built-in tools  +12 MCP  +6 skills  memory 22/300w  │  /help for commands

────────────────────────────────────────────────────────
❯ Fix the bug in calculator.py and commit the result
────────────────────────────────────────────────────────

⏺ Read(calculator.py)
  ⎿ def add(a, b): ... (+4 lines)
⏺ Edit(calculator.py)
  ⎿ Replaced 1 occurrence
⏺ InvokeSkill(commit)
  ◆ Skill: commit — Analyse staged changes and create a conventional git commit
⏺ mcp_git__git_add(calculator.py)
  ⎿ 
⏺ mcp_git__git_commit(fix(calculator): correct subtraction bug in add())
  ⎿ [master a1b2c3d] fix(calculator): correct subtraction bug in add()
  memory: Memory saved (31/300 words).
```

---

## Features

- **Local-first** — model runs on your GPU via vLLM, nothing leaves your machine
- **Tool-calling** — file read/write/edit, shell, web search, todo tracking
- **MCP support** — connect any [Model Context Protocol](https://modelcontextprotocol.io) server as a subprocess; tools are auto-discovered and injected into the model
- **Skills** — slash-command workflows defined in YAML (`/commit`, `/review`, `/fix`, …); the model also invokes them autonomously
- **Memory** — persistent `.nesso_memory.md` per project; the model writes to it automatically after productive turns
- **Streaming** — tokens stream to the terminal in real-time
- **Session persistence** — optional `--session` flag saves conversation history

---

## Requirements

- Python 3.10+
- A CUDA-capable GPU (the 4B model fits in ~10 GB VRAM)
- `git` (for MCP git server and skills)

---

## 1 — Install vLLM and serve the model

```bash
pip install vllm
```

Start the model server (keep this running in a separate terminal):

```bash
vllm serve mii-llm/nesso-4B \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --port 5555
```

The first run downloads the model weights (~8 GB). Subsequent starts are instant.

Verify it is running:

```bash
curl http://localhost:5555/v1/models
```

---

## 2 — Install nessocode

Clone the repository:

```bash
git clone https://github.com/mii-llm/nessocode.git
cd nessocode
```

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

pip install pyyaml tavily-python
```

Optional — install as a CLI command:

```bash
pip install -e .
# now you can run: nessocode
```

---

## 3 — Configure

Copy the example env file:

```bash
cp .env.example .env
```

Edit `.env` and add your [Tavily](https://tavily.com) API key for web search (free tier — 1 000 searches/month):

```
TAVILY_API_KEY=tvly-your-key-here
```

All other settings live in `config.yaml`. The defaults work out of the box:

```yaml
model: mii-llm/nesso-4B
api_url: http://localhost:5555/v1/chat/completions
max_tokens: 8192
stream: true
max_tool_calls: 20
skills_dir: ./skills
```

---

## 4 — MCP servers (optional)

MCP servers extend the agent with external tool sets. The **git MCP server** is the easiest to add — it gives the model structured git tools (`git_log`, `git_diff`, `git_add`, `git_commit`, …) instead of raw shell commands.

Install:

```bash
pip install mcp-server-git
```

Enable in `config.yaml`:

```yaml
mcp_servers:
  git:
    command: /path/to/.venv/bin/python
    args: ['-m', 'mcp_server_git', '--repository', '.']
    enabled: true
```

Other servers you can add (Node.js 18+ required):

```yaml
mcp_servers:
  filesystem:
    command: npx
    args: ['-y', '@modelcontextprotocol/server-filesystem', '/home', '/tmp']

  github:
    command: npx
    args: ['-y', '@modelcontextprotocol/server-github']
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: ghp_your_token

  postgres:
    command: npx
    args: ['-y', '@modelcontextprotocol/server-postgres', 'postgresql://localhost/mydb']
```

A full list of available servers: [modelcontextprotocol.io/servers](https://modelcontextprotocol.io/servers)

---

## 5 — Run

```bash
# from your project directory
PYTHONPATH=/path/to/nessocode \
  /path/to/nessocode/.venv/bin/python3 \
  /path/to/nessocode/nessocode.py
```

Or if installed with `pip install -e .`:

```bash
cd /your/project
nessocode
```

With session persistence (resumes within a few hours):

```bash
nessocode --session ~/.nessocode/session.json
```

Override model or endpoint at runtime:

```bash
nessocode --model ./my-local-model --api-url http://localhost:8000/v1
nessocode --no-stream   # disable streaming (useful for debugging)
```

---

## 6 — REPL commands

| Command | Description |
|---|---|
| `/help` | Show all commands |
| `/tools` | List built-in + MCP tools |
| `/skills` | List available skills |
| `/memory` | Show persistent memory |
| `/memory clear` | Wipe the memory file |
| `/bashes` | List running background shells |
| `/clear` | Clear conversation history |
| `/quit` | Exit |

---

## 7 — Skills

Skills are slash-command workflows defined in `skills/*.yaml`. The model also invokes them autonomously when the task matches.

| Command | Aliases | Description |
|---|---|---|
| `/commit` | `/gc` | Analyse staged changes and create a conventional git commit |
| `/review` | `/cr` | Review code for bugs, security issues, and quality |
| `/explain` | `/ex` | Explain a file, function, or concept in plain language |
| `/tests` | `/test` | Generate comprehensive unit tests |
| `/fix` | `/f` | Debug and fix a failing test or error |
| `/refactor` | `/rf` | Refactor code without changing behaviour |

Add your own skill by creating a new file in `skills/`:

```yaml
# skills/deploy.yaml
name: deploy
description: "Build and deploy the project to production"
aliases: ["/d"]
prompt: |
  1. Run the test suite and ensure all tests pass
  2. Build the production artefact
  3. Deploy using the project's deploy script
  4. Verify the deployment succeeded
```

---

## 8 — Memory

The agent automatically builds a persistent memory file (`.nesso_memory.md`) in your project directory. It is injected into the system prompt at every startup so the model already knows your project on the second session.

The model writes to memory autonomously after:
- Reading a file for the first time
- Fixing a bug
- Learning a project convention
- Making an architectural decision

Inspect or clear it:

```bash
# inside the REPL
/memory
/memory clear
```

---

## 9 — Test prompts

A sequence that exercises tools, MCP, skills, and memory together:

```
# Prompt 1 — exercises MCP git + memory auto-write
Use the git log tool to check recent commits, then read calculator.py and tell me what it does

# Prompt 2 — exercises Edit + InvokeSkill + MCP git commit + memory update
Fix the bugs in calculator.py and commit the result

# Prompt 3 — verify memory persisted without re-reading any file
/memory
```

---

## Project structure

```
nessocode/
├── nessocode.py          # CLI entry point
├── config.yaml           # Configuration
├── .env.example          # API key template
├── pyproject.toml
├── agent/
│   ├── config.py         # Config loading (YAML + env vars)
│   ├── display.py        # Terminal UI / ANSI rendering
│   ├── tools.py          # Built-in tools (Read, Edit, Bash, Grep, …)
│   ├── memory.py         # Persistent memory system
│   ├── mcp_client.py     # MCP stdio client + manager
│   ├── skills.py         # YAML skill loader
│   ├── api.py            # vLLM streaming + blocking API client
│   └── core.py           # Agent loop + REPL
└── skills/
    ├── commit.yaml
    ├── review.yaml
    ├── explain.yaml
    ├── tests.yaml
    ├── fix.yaml
    └── refactor.yaml
```

---

## Licence

MIT
