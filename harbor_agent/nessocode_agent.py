"""
Harbor agent wrapper for nessocode.

nessocode is installed inside the task container, pointed at the remote vLLM
server (213.171.186.42:5555), and driven via `nessocode --task "..."`.

Usage:
    harbor run -d terminal-bench@2.0 \
        --agent-import-path harbor_agent.nessocode_agent:NessocodeAgent \
        --n-concurrent 2

Verify the Harbor API surface against https://harborframework.com/docs/agents
if the import paths below need adjusting for your installed version.
"""
from __future__ import annotations

import shlex

# ---------------------------------------------------------------------------
# Harbor imports — adjust if the package layout differs in your version
# ---------------------------------------------------------------------------
try:
    from harbor.agents.base import BaseAgent  # Harbor >= 0.4
except ImportError:
    from harbor import BaseAgent              # older layout

# ---------------------------------------------------------------------------
# Remote vLLM endpoint (the server running mii-llm/nesso-4B)
# ---------------------------------------------------------------------------
VLLM_API_URL = "http://213.171.186.42:5555/v1"
VLLM_MODEL   = "mii-llm/nesso-4B"

# nessocode config written into the container
NESSOCODE_CONFIG = f"""\
model: {VLLM_MODEL}
api_url: {VLLM_API_URL}
stream: false
max_tokens: 4096
max_tool_calls: 64
"""

NESSOCODE_CONFIG_PATH = "/tmp/nessocode_config.yaml"

# How long (seconds) to wait for nessocode to finish a task
TASK_TIMEOUT = 600


class NessocodeAgent(BaseAgent):
    """Terminal-Bench agent powered by nessocode + nesso-4B."""

    @staticmethod
    def name() -> str:
        return "nessocode"

    @staticmethod
    def version() -> str | None:
        return "0.1.0"

    # ------------------------------------------------------------------
    # Setup: install nessocode in the container once per task
    # ------------------------------------------------------------------

    async def setup(self, environment) -> None:
        tmux = environment.tmux

        # Install from GitHub (public repo)
        await tmux.exec(
            "pip install --quiet git+https://github.com/mii-llm/nessocode.git",
            timeout=120,
        )

        # Write the config file pointing at the remote vLLM server
        escaped_config = NESSOCODE_CONFIG.replace("'", r"'\''")
        await tmux.exec(
            f"printf '%s' '{escaped_config}' > {NESSOCODE_CONFIG_PATH}",
            timeout=10,
        )

    # ------------------------------------------------------------------
    # Run: drive nessocode with the task instruction
    # ------------------------------------------------------------------

    async def run(self, instruction: str, environment, context) -> None:
        tmux = environment.tmux

        # Determine task working directory (Harbor sets this up)
        work_dir = getattr(environment, "work_dir", "/task")

        safe_instruction = shlex.quote(instruction)
        cmd = (
            f"cd {work_dir} && "
            f"nessocode --config {NESSOCODE_CONFIG_PATH} --task {safe_instruction}"
        )

        output = await tmux.exec(cmd, timeout=TASK_TIMEOUT)

        # Populate context so Harbor can log the result
        if hasattr(context, "result"):
            context.result = output
        elif hasattr(context, "set_result"):
            context.set_result(output)

    # ------------------------------------------------------------------
    # Post-run: nothing extra needed — Harbor evaluates container state
    # ------------------------------------------------------------------

    def populate_context_post_run(self, context) -> None:
        pass
