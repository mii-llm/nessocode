"""
Skills system for nessocode.

A skill is a pre-built agent workflow defined in a YAML file.
Skills are invoked with a slash command (e.g. /commit, /review).
They inject a custom prompt and optional system context into the agent loop.

YAML format
-----------
name: commit
description: "Analyse staged changes and create a conventional git commit"
aliases: ["/gc"]
system_addition: |   # Optional extra section appended to the system prompt
  You are creating a git commit. Use conventional commits format.
prompt: |            # The user-facing prompt injected into the conversation
  1. Run git status ...
  2. ...
require_tools: []    # Optional list of tool names this skill depends on
"""
import glob
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:
    import yaml
    _YAML_OK = True
except ImportError:
    _YAML_OK = False


@dataclass
class Skill:
    name: str
    description: str
    prompt: str
    aliases: List[str] = field(default_factory=list)
    system_addition: Optional[str] = None
    require_tools: List[str] = field(default_factory=list)

    @property
    def all_aliases(self) -> List[str]:
        """All slash-command aliases including the canonical /<name>."""
        canonical = f"/{self.name}"
        extras = [a if a.startswith("/") else f"/{a}" for a in self.aliases]
        return list(dict.fromkeys([canonical] + extras))  # stable dedup


class SkillRegistry:
    """
    Registry of loaded skills.  Skills are keyed by name; aliases are
    also registered so callers can look up by any alias.
    """

    def __init__(self) -> None:
        self._skills: Dict[str, Skill] = {}
        self._aliases: Dict[str, str] = {}   # alias -> canonical name

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_directory(self, directory: str) -> int:
        """
        Load all *.yaml files from *directory*.
        Returns the number of skills successfully loaded.
        """
        if not os.path.isdir(directory):
            return 0
        loaded = 0
        for path in sorted(glob.glob(os.path.join(directory, "*.yaml"))):
            try:
                self.load_file(path)
                loaded += 1
            except Exception as exc:
                print(f"  Warning: could not load skill {os.path.basename(path)}: {exc}")
        return loaded

    def load_file(self, path: str) -> Skill:
        """Load a single YAML skill file and register it."""
        if not _YAML_OK:
            raise ImportError("pyyaml is required to load skills: pip install pyyaml")
        with open(path) as fh:
            data = yaml.safe_load(fh)

        if not data or "name" not in data:
            raise ValueError(f"Skill file {path} is missing required 'name' field")
        if "prompt" not in data:
            raise ValueError(f"Skill file {path} is missing required 'prompt' field")

        skill = Skill(
            name=data["name"],
            description=data.get("description", ""),
            prompt=data["prompt"],
            aliases=data.get("aliases", []),
            system_addition=data.get("system_addition"),
            require_tools=data.get("require_tools", []),
        )
        self._register(skill)
        return skill

    def register(self, skill: Skill) -> None:
        """Programmatically register a skill (useful for testing)."""
        self._register(skill)

    def _register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill
        for alias in skill.all_aliases:
            self._aliases[alias] = skill.name

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, alias_or_name: str) -> Optional[Skill]:
        """
        Look up a skill by canonical name or any alias.
        The input may or may not include the leading slash.
        """
        # Normalise: ensure leading slash for alias lookup
        key = alias_or_name if alias_or_name.startswith("/") else f"/{alias_or_name}"
        name = self._aliases.get(key) or self._aliases.get(alias_or_name)
        if name:
            return self._skills.get(name)
        # Fall back to plain name lookup
        return self._skills.get(alias_or_name)

    def list_all(self) -> List[Skill]:
        return list(self._skills.values())

    def __len__(self) -> int:
        return len(self._skills)

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def format_help(self) -> str:
        if not self._skills:
            return "  (no skills loaded)"
        lines = []
        for skill in self._skills.values():
            aliases = " / ".join(skill.all_aliases)
            desc = skill.description or ""
            lines.append(f"  {aliases:<26}{desc}")
        return "\n".join(lines)
