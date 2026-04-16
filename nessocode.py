#!/usr/bin/env python3
"""
nessocode — local AI coding agent powered by a 4B model served via vLLM.

Quick start
-----------
  # 1. Start the model server
  vllm serve mii-llm/nesso-4B \\
       --enable-auto-tool-choice --tool-call-parser hermes --port 5555

  # 2. Run the agent
  nessocode                     # interactive REPL
  nessocode --no-stream         # disable streaming (useful for debugging)
  nessocode --session ./s.json  # persist conversation to a file
  nessocode --config my.yaml    # custom config file
"""
import argparse
import os
import sys


def _load_dotenv() -> None:
    """Best-effort .env loader — no external dependency required."""
    env_path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


def main() -> None:
    _load_dotenv()

    parser = argparse.ArgumentParser(
        prog="nessocode",
        description="nessocode — local AI coding agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config",     "-c", metavar="FILE",
                        help="Path to a YAML config file")
    parser.add_argument("--model",      "-m", metavar="NAME",
                        help="Override the model name / path")
    parser.add_argument("--api-url",          metavar="URL",
                        help="Override the vLLM API endpoint")
    parser.add_argument("--no-stream",  action="store_true",
                        help="Disable streaming (blocking mode)")
    parser.add_argument("--skills-dir",       metavar="DIR",
                        help="Override the skills directory")
    parser.add_argument("--session",    "-s", metavar="FILE",
                        help="JSON file for conversation persistence")
    parser.add_argument("--version",    action="store_true",
                        help="Print version and exit")
    args = parser.parse_args()

    if args.version:
        from agent import __version__
        print(f"nessocode {__version__}")
        sys.exit(0)

    from agent.config import load_config
    from agent.core import NessoAgent

    config = load_config(args.config)

    # CLI overrides
    if args.model:
        config.model = args.model
    if args.api_url:
        config.api_url = args.api_url
    if args.no_stream:
        config.stream = False
    if args.skills_dir:
        config.skills_dir = args.skills_dir
    if args.session:
        config.session_file = args.session

    agent = NessoAgent(config)
    agent.run_repl()


if __name__ == "__main__":
    main()
