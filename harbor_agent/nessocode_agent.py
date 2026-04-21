"""Harbor agent wrapper for nessocode (Harbor 0.4.0 API)."""
from __future__ import annotations

import logging
import shlex
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

# vLLM server — running on the Docker host (reached via bridge gateway)
_VLLM_PORT  = 5555
_VLLM_MODEL = "mii-llm/nesso-4B"

_SRC_PATH     = "/opt/nessocode"
_TASK_TIMEOUT = 600


class NessocodeAgent(BaseAgent):
    """Terminal-Bench agent powered by nessocode + nesso-4B."""

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        logger: logging.Logger | None = None,
        **kwargs,
    ):
        super().__init__(logs_dir, model_name, logger, **kwargs)

    @staticmethod
    def name() -> str:
        return "nessocode"

    def version(self) -> str | None:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        # Ensure python3 and git are present (many task images are bare)
        await environment.exec(
            "apt-get install -y -qq python3 python3-pip git 2>/dev/null || true",
            timeout_sec=90,
            user="root",
        )

        # Try git clone first; fall back to curl tarball if git is unavailable
        clone = await environment.exec(
            f"git clone --quiet --depth 1 "
            f"https://github.com/mii-llm/nessocode.git {_SRC_PATH} 2>&1",
            timeout_sec=60,
            user="root",
        )
        if clone.return_code != 0:
            await environment.exec(
                f"curl -sL https://github.com/mii-llm/nessocode/archive/refs/heads/main.tar.gz"
                f" | tar xz -C /tmp && mv /tmp/nessocode-main {_SRC_PATH}",
                timeout_sec=60,
                user="root",
            )

        # Install pyyaml — try every available method in order
        await environment.exec(
            "pip install pyyaml 2>/dev/null || "
            "pip3 install pyyaml 2>/dev/null || "
            "python3 -m pip install pyyaml 2>/dev/null || "
            "apt-get install -y -qq python3-yaml 2>/dev/null || true",
            timeout_sec=60,
            user="root",
        )

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        # Detect Docker host gateway via /proc/net/route (no ip/route needed)
        gw = await environment.exec(
            "python3 -c \""
            "import struct;"
            "lines=open('/proc/net/route').readlines();"
            "gw=[l.split() for l in lines if l.split()[1]=='00000000'];"
            "h=gw[0][2] if gw else 'AC110001';"
            "b=bytes.fromhex(h);"
            "print('.'.join(str(b[i]) for i in (3,2,1,0)))"
            "\"",
            timeout_sec=5,
        )
        host_ip = (gw.stdout or "").strip() or "172.17.0.1"

        result = await environment.exec(
            f"python3 {_SRC_PATH}/nessocode.py --no-stream "
            f"--task {shlex.quote(instruction)}",
            env={
                "NESSOCODE_API_URL": f"http://{host_ip}:{_VLLM_PORT}/v1/chat/completions",
                "NESSOCODE_MODEL":   _VLLM_MODEL,
            },
            timeout_sec=_TASK_TIMEOUT,
        )
        context.metadata = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.return_code,
            "host_ip": host_ip,
        }
