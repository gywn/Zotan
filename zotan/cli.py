import asyncio
import dataclasses
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import cappa

from .config import WORKING_MODE, Config
from .types_ import MainRunContext
from .ui.one_round import run_one_round


@cappa.command(
    name="zotan",
)
@dataclasses.dataclass(frozen=True)
class Args:
    llm_name: Annotated[str | None, cappa.Arg(long=True, help="Override reasoning LLM")]
    workspace: Annotated[Path | None, cappa.Arg(long=True, help="The workspace directory")]


def main() -> None:
    args = cappa.parse(Args)

    workspace_dir = args.workspace
    if workspace_dir is None and sys.stdin.isatty():
        # Only load config and prompt_toolkit history in the project directory if we are going to enter interacting mode
        workspace_dir = Path.cwd()

    if workspace_dir and WORKING_MODE == "wsl" and not workspace_dir.exists():
        result = subprocess.check_output(["wslpath", "-a", "-u", workspace_dir], text=True)
        workspace_dir = Path(result.strip())

    override_config = Config()
    if args.llm_name is not None:
        override_config = dataclasses.replace(
            override_config,
            llm_configs={"reasoning": args.llm_name},
        )

    main_ctx = MainRunContext(
        config=Config.load(workspace_dir, override_config),
        workspace_dir=workspace_dir,
    )

    try:
        asyncio.run(run_one_round(main_ctx, sys.stdin.read().strip()))
    except KeyboardInterrupt:
        # Critical edge case where `KeyboardInterrupt` is raised in poll()/I/O operations, not converted to `CancelledError`
        exit(1)


if __name__ == "__main__":
    main()
