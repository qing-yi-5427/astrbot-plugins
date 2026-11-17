"""Extend only this image tool's native execution budget, not global timeouts."""

import copy
from typing import Any

from .delivery_receipt import CHECK_TIMEOUT

NOTICE_TIMEOUT = 10
IMAGE_SEND_TIMEOUT = 120


class ImageExecutionBudget:
    def __init__(self, runner: Any, original: Any, name: str, seconds: int):
        self.runner = runner
        self.original = original
        self.name = name
        self.seconds = seconds
        self.run_context = runner.run_context
        self.request = runner.req

    async def execute(self, tool, run_context, **tool_args):
        """Delegate native execution with a copy of the image call's timeout.

        Args:
            tool: Original native function tool.
            run_context: Native context supplied by the runner.
            **tool_args: Original validated tool arguments.

        Yields:
            Unmodified native executor results.
        """
        scoped = run_context
        if (
            getattr(tool, "name", None) == self.name
            and run_context is self.run_context
            and self.runner.run_context is self.run_context
            and self.runner.req is self.request
        ):
            # Keep event, messages and all context state shared. Only the native
            # executor's timeout changes; concurrent memory/other tools see 60s.
            scoped = copy.copy(run_context)
            scoped.tool_call_timeout = max(run_context.tool_call_timeout, self.seconds)
        iterator = self.original.execute(tool=tool, run_context=scoped, **tool_args)
        try:
            async for result in iterator:
                yield result
        finally:
            # Preserve native abort/stop semantics, including generator cleanup.
            close = getattr(iterator, "aclose", None)
            if callable(close):
                await close()


def install_execution_budget(
    runner: Any, name: str, api_timeout: int, count: int
) -> bool:
    """Install an idempotent, request-local delegate around the existing executor.

    Allow generation/download plus bounded progress, sends and receipt checks.
    Reused runners bypass the old delegate unless this hook installs a new one.

    Args:
        runner: Current local runner with its original native executor.
        name: Image tool name whose timeout may be extended.
        api_timeout: Validated generation/download timeout in seconds.
        count: Validated maximum number of generated images.

    Returns:
        Whether a compatible executor was found and protected.
    """
    original = getattr(runner, "tool_executor", None)
    context = getattr(runner, "run_context", None)
    timeout = getattr(context, "tool_call_timeout", None)
    if (
        not callable(getattr(original, "execute", None))
        or type(timeout) is not int
        or timeout < 1
        or getattr(runner, "req", None) is None
    ):
        return False
    seconds = (
        api_timeout + NOTICE_TIMEOUT + count * (IMAGE_SEND_TIMEOUT + CHECK_TIMEOUT) + 10
    )
    if isinstance(original, ImageExecutionBudget):
        if original.run_context is context and original.request is runner.req:
            original.seconds = seconds
            return True
        original = original.original
    runner.tool_executor = ImageExecutionBudget(runner, original, name, seconds)
    return True
