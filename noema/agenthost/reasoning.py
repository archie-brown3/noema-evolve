"""Deep coordination transport: coordination-account LLM calls via headless CLI (0174)."""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional

from noema.agenthost.config import AgentCliConfig
from noema.agenthost.inner_session_mcp import prepare_inner_mcp
from noema.agenthost.submit import ADVICE_FILENAME, coordination_response
from noema.budget.cli_runner import CliRunner, build_mutation_cli_command
from noema.budget.llm import BudgetedLLM

SpawnFn = Callable[[str, str, str], str]

_COORDINATION_DELIVERABLE_HINT = (
    "Query the noema MCP tools for population and coordination state "
    "(tools/snapshot.json holds the same data as a backup).\n"
    "Finish by calling submit_coordination with the completion text that this "
    "LLM call should return, or write that text as "
    '{"response": "..."} to ADVICE.json, then stop.'
)


class DeepCoordinationLLM:
    """BudgetedLLM-shaped adapter: all generate* calls spawn a coding CLI."""

    def __init__(
        self,
        inner: BudgetedLLM,
        *,
        cli: AgentCliConfig,
        output_dir: str,
        spawn: Optional[SpawnFn] = None,
    ) -> None:
        self._inner = inner
        self._cli = cli
        self._output_dir = output_dir
        self._spawn = spawn
        self._session = None
        self._attempts: Dict[str, int] = defaultdict(int)
        self.generation = 0
        self.iteration = inner.iteration
        self.model = inner.model
        self.tag = inner.tag

    def bind_session(self, session) -> None:
        self._session = session

    async def generate(self, prompt: str, **kwargs) -> str:
        return await self.generate_with_context(
            system_message=kwargs.pop("system_message", ""),
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )

    async def generate_with_context(
        self,
        system_message: str,
        messages: List[Dict[str, str]],
        **kwargs,
    ) -> str:
        tag = kwargs.get("tag", self.tag)
        user = messages[-1]["content"] if messages else ""
        if self._spawn is not None:
            return self._spawn(tag, system_message, user)
        return self._run_cli(tag, system_message, user)

    def _run_cli(self, tag: str, system_message: str, user_message: str) -> str:
        self._attempts[tag] += 1
        attempt = self._attempts[tag]
        generation = self._session.generation if self._session is not None else self.generation
        work = (
            Path(self._output_dir)
            / "coordination"
            / f"gen{generation:04d}"
            / tag
            / f"a{attempt:02d}"
        )
        work.mkdir(parents=True, exist_ok=True)
        system_path = work / "SYSTEM.md"
        stdout_path = work / "cli_stdout.log"
        stderr_path = work / "cli_stderr.log"
        mcp_config = None
        if self._session is not None:
            mcp_config = prepare_inner_mcp(
                work,
                self._session,
                mode="coordination",
                deliverable=work / ADVICE_FILENAME,
            )
        system_message = f"{system_message}\n\n{_COORDINATION_DELIVERABLE_HINT}".strip()
        system_path.write_text(system_message)
        (work / "BRIEF.md").write_text(user_message)

        argv = build_mutation_cli_command(
            self._cli.kind,
            work_dir=work,
            system_path=system_path,
            user_message=user_message,
            binary=self._cli.binary,
            model=self._cli.model or self.model,
            extra_args=self._cli.extra_args,
            mcp_config_path=mcp_config,
        )
        result = CliRunner().run(
            argv,
            cwd=work,
            env=os.environ.copy(),
            timeout_s=self._cli.timeout_s,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        if result.timed_out:
            raise TimeoutError(
                f"deep coordination CLI {self._cli.kind!r} timed out after "
                f"{self._cli.timeout_s}s; see {stderr_path}"
            )
        if result.exit_code != 0:
            raise RuntimeError(
                f"deep coordination CLI {self._cli.kind!r} exited with code "
                f"{result.exit_code}; see {stderr_path}"
            )
        text = coordination_response(work, tag)
        if text is not None:
            return text
        return (result.stdout or "").strip()
