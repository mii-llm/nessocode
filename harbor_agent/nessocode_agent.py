"""Harbor agent wrapper for nessocode (Harbor 0.4.0 API)."""
from __future__ import annotations

import logging
import os
import shlex
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

# vLLM server — running on the Docker host (reached via bridge gateway).
# Override at run time to switch models without editing code:
#   NESSO_HARBOR_MODEL=giux78/nesso-27b-merged harbor run ...
_VLLM_PORT  = int(os.environ.get("NESSO_HARBOR_PORT", "5555"))
_VLLM_MODEL = os.environ.get("NESSO_HARBOR_MODEL", "mii-llm/nesso-4B")

_SRC_PATH     = "/opt/nessocode"
_TASK_TIMEOUT = 1200

# Gateway detection: try python3 first (most reliable), then fall back to
# POSIX awk (no strtonum — busybox/mawk compatible), then hardcoded default.
# All stderr is suppressed so errors never leak into host_ip.
_GATEWAY_CMD = (
    "python3 -c \""
    "import struct;"
    "lines=open('/proc/net/route').readlines();"
    "gw=[l.split() for l in lines if l.split()[1]=='00000000'];"
    "h=gw[0][2] if gw else 'AC110001';"
    "b=bytes.fromhex(h);"
    "print('.'.join(str(b[i]) for i in (3,2,1,0)))"
    "\" 2>/dev/null || "
    "awk 'BEGIN{split(\"0123456789abcdef\",h,\"\");for(i in h)v[h[i]]=i-1} "
    "/\\t00000000\\t/{"
    "gw=tolower($3);"
    "b1=v[substr(gw,7,1)]*16+v[substr(gw,8,1)];"
    "b2=v[substr(gw,5,1)]*16+v[substr(gw,6,1)];"
    "b3=v[substr(gw,3,1)]*16+v[substr(gw,4,1)];"
    "b4=v[substr(gw,1,1)]*16+v[substr(gw,2,1)];"
    "printf \"%d.%d.%d.%d\\n\",b1,b2,b3,b4;exit"
    "}' /proc/net/route 2>/dev/null || "
    "echo 172.17.0.1"
)


class NessocodeAgent(BaseAgent):
    """Terminal-Bench agent powered by nessocode."""

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
        # Install python3 — update package lists first (required on bare Ubuntu
        # images that ship without an apt cache), then try every common package
        # manager so we work on Debian/Ubuntu (apt-get) and Alpine (apk).
        # Only install python3, not pip — pyyaml is handled in the next step.
        await environment.exec(
            "apt-get update -qq 2>/dev/null; "
            "apt-get install -y -qq --no-install-recommends python3 git 2>/dev/null || "
            "apk add --no-cache python3 git 2>/dev/null || "
            "yum install -y python3 git 2>/dev/null || "
            "true",
            timeout_sec=120,
            user="root",
        )

        # Upload nessocode source from host — no network needed inside container.
        # _HOST_SRC resolves to two levels up from this file (repo root).
        host_src = Path(__file__).resolve().parent.parent
        await environment.upload_dir(host_src, _SRC_PATH)

        # Install pyyaml for skill loading — try every available method.
        await environment.exec(
            "pip install pyyaml 2>/dev/null || "
            "pip3 install pyyaml 2>/dev/null || "
            "python3 -m pip install pyyaml 2>/dev/null || "
            "apt-get install -y -qq python3-yaml 2>/dev/null || "
            "apk add --no-cache py3-yaml 2>/dev/null || "
            "true",
            timeout_sec=60,
            user="root",
        )

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        # Detect Docker host gateway via /proc/net/route using pure awk —
        # no python3 or ip/route command required.
        gw = await environment.exec(_GATEWAY_CMD, timeout_sec=5)
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
