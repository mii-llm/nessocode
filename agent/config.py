"""Configuration management for nessocode."""
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False


@dataclass
class MCPServerConfig:
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class Config:
    model: str = "mii-llm/nesso-4B"
    api_url: str = "http://localhost:5555/v1/chat/completions"
    max_tokens: int = 8192
    repetition_penalty: float = 1.1
    stream: bool = True
    max_tool_calls: int = 20
    tavily_api_key: Optional[str] = None
    mcp_servers: Dict[str, MCPServerConfig] = field(default_factory=dict)
    skills_dir: str = "./skills"
    session_file: Optional[str] = None


def load_config(config_path: Optional[str] = None) -> Config:
    """Load configuration from YAML file with environment variable overrides."""
    search_paths = []
    if config_path:
        search_paths.append(config_path)

    # Default search locations
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    search_paths += [
        os.path.join(here, "config.yaml"),
        os.path.expanduser("~/.nessocode.yaml"),
    ]

    raw: dict = {}
    for path in search_paths:
        if os.path.exists(path):
            if not _YAML_AVAILABLE:
                print(f"Warning: pyyaml not installed; skipping config file {path}")
                break
            with open(path) as f:
                raw = yaml.safe_load(f) or {}
            break

    # Environment variable overrides
    env_map = {
        "NESSOCODE_MODEL": "model",
        "NESSOCODE_API_URL": "api_url",
        "NESSOCODE_MAX_TOKENS": "max_tokens",
        "TAVILY_API_KEY": "tavily_api_key",
    }
    for env_key, conf_key in env_map.items():
        if val := os.environ.get(env_key):
            raw[conf_key] = val

    # Parse MCP server entries separately
    mcp_servers: Dict[str, MCPServerConfig] = {}
    for name, srv in (raw.pop("mcp_servers", None) or {}).items():
        mcp_servers[name] = MCPServerConfig(
            command=srv["command"],
            args=srv.get("args", []),
            env=srv.get("env", {}),
            enabled=srv.get("enabled", True),
        )

    # Only pass known fields to the dataclass
    known = {k: v for k, v in raw.items() if k in Config.__dataclass_fields__}
    cfg = Config(**known)
    cfg.mcp_servers = mcp_servers
    return cfg
