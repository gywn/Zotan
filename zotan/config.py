"""Configuration management module

Provides unified configuration loading and management functionality
with support for overlaying multiple YAML config files.
"""

from __future__ import annotations

import dataclasses
import getpass
import itertools
import subprocess
import tomllib
from pathlib import Path
from tomllib import TOMLDecodeError
from typing import Any, Literal, Mapping, cast

from pydantic import TypeAdapter, ValidationError

from .toml import deep_merge_dict

# As seen by the agents
WORKSPACE = "/workspace"


@dataclasses.dataclass(frozen=True)
class LLMConfig:
    model_name: str = ""
    api_key: str = ""
    base_url: str = ""


@dataclasses.dataclass(frozen=True)
class Config:
    """Main configuration class. All default values are invalid so that configs can be merged"""

    llm_configs: Mapping[str, LLMConfig | str] = dataclasses.field(default_factory=dict[str, LLMConfig | str])

    # ui.terminal
    editing_mode: Literal["emacs", "vi", ""] = ""

    # tools.serper_tools
    serper_api_key: str | None = None

    # tools.http_tools
    firefox_profile: Path | None = None

    # tools.rich_file_tools
    llamacloud_api_key: str | None = None

    @staticmethod
    def _merge(*configs: Config) -> Config:
        """Merge multiple configurations"""
        config_obj = cast(Any, dict())
        for config in reversed(configs):
            config_obj = deep_merge_dict(config_obj, TypeAdapter(Config).dump_python(config, exclude_defaults=True))
        return TypeAdapter(Config).validate_python(config_obj)

    @staticmethod
    def _load_and_validate_single(config_path: Path) -> Config:
        """Load configuration from disks"""
        try:
            return TypeAdapter(Config).validate_python(tomllib.load(open(config_path, "rb")))
        except (TOMLDecodeError, ValidationError):
            raise ValueError(f"{config_path} is not valid configuration")

    @staticmethod
    def load(workspace_dir: Path | None = None, override_config: Config | None = None) -> Config:
        """Load and merge configurations"""
        # Priority 1: Passed-in override config
        configs: list[Config] = [override_config] if override_config is not None else []

        # Priority 2: Project mode
        if workspace_dir is not None:
            workspace_dir = workspace_dir.expanduser().absolute()
            for project_dir in itertools.chain([workspace_dir], workspace_dir.parents):
                if project_dir.owner() != getpass.getuser() or project_dir == Path.home():
                    break
                project_config_path = project_dir / ".zotan" / "config.toml"
                if project_config_path.exists():
                    config = Config._load_and_validate_single(project_config_path)
                    configs.append(config)

        # Priority 4: User configuration directory
        user_config_path = Path.home() / ".config" / "zotan.toml"
        if user_config_path.exists():
            config = Config._load_and_validate_single(user_config_path)
            configs.append(config)

        # Priority 4: Default configuration
        configs.append(Config())

        # Merge configurations
        config = Config._merge(*configs)

        # Validate required external configurations
        config = dataclasses.replace(
            config,
            firefox_profile=config.firefox_profile.expanduser().absolute() if config.firefox_profile else None,
        )

        if not any(isinstance(config, LLMConfig) for config in config.llm_configs.values()):
            raise ValueError("No LLM configured")

        if config.firefox_profile is not None and not config.firefox_profile.is_dir():
            raise ValueError(f"{config.firefox_profile} is not a directory")

        return config

    def get_llm_config(self, name: str) -> LLMConfig:
        if name in self.llm_configs:
            if isinstance(config := self.llm_configs[name], LLMConfig):
                return config
            else:
                return self.get_llm_config(config)

        for name in ("reasoning", "text_processing"):
            if name in self.llm_configs:
                return self.get_llm_config(name)

        return next(config for config in self.llm_configs.values() if isinstance(config, LLMConfig))


def _assert_podman_images(tag: Literal["base", "python", "c++", "rust"]) -> None:
    proc = subprocess.run(
        ["podman", "images", "--quiet", "--filter", f"reference=zotan:{tag}"],
        capture_output=True,
        text=True,
    )
    if not proc.stdout.strip():
        raise RuntimeError(f"Required Podman image 'zotan:{tag}' is not installed")


# "normal": Running in normal POSIX environments:
#   - workspace_dir can be arbitrary
#   - bash execution executed in Podman containers
# "wsl": Running in WSL
#   - workspace_dir need to be remapped to /mnt/
#   - bash execution executed in Podman containers
# "container": Running in containers
#   - workspace_dir must be /workspace
#   - bash execution executed in host
def _get_working_mode() -> Literal["normal", "wsl", "container"]:
    if (osrelease := Path("/proc/sys/kernel/osrelease")).is_file():
        content = osrelease.read_text().lower()
        if "microsoft" in content or "wsl" in content:
            _assert_podman_images("base")
            return "wsl"

    if (mountinfo := Path("/proc/self/mountinfo")).is_file():
        for line in mountinfo.read_text().splitlines():
            fields = line.split(" ", maxsplit=5)
            if len(fields) >= 5 and fields[4] == "/" and "overlay" in fields[5]:
                return "container"

    _assert_podman_images("base")
    return "normal"


WORKING_MODE = _get_working_mode()
