"""
Run a shell script.

Details:

  command: Shell command(s) to run.
           type: string
           default: "" -> required!
  success: List of return codes considered a success.
           type: list[int]
           default: [0]
  error:   List of return codes considered a client-side error where the stdout and stderr will
           be printed to the user. (All other return codes will lead to a server-side error.)
           type: list[int]
           default: []
"""

import asyncio
import os
import re

from app.lib import j2
from app.model.err import ActionClientError
from app.model.err import ActionError
from app.model.err import ActionSpecsError
from app.model.plg import IAction


class ShellAction(IAction):
    async def run(self, *, details: dict, props: dict) -> None:
        try:
            d = await j2.render(details, props)
            assert isinstance(d, dict)
        except (AssertionError, j2.J2Error) as error:
            raise ActionSpecsError(
                f'In details for action plugin "shell": {error}'
            ) from error

        proc = await asyncio.create_subprocess_shell(
            d.get("command", ""),
            env=self.__get_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        try:
            stdout, _ = await proc.communicate()
        except Exception as error:  # TODO what to catch here!?
            raise ActionError(f"Shell command failed with: {error}") from error

        if proc.returncode in d.get("success", [0]):
            return

        if proc.returncode in d.get("error", []):
            raise ActionClientError(stdout.decode("utf-8"))

        raise ActionError(stdout.decode("utf-8"))

    def __get_env(self) -> dict:
        env = {}
        for e in ["PATH", "HOME", "HOSTNAME", "PWD", "LANG"]:
            env[e] = os.environ.get(e, "")
        return env


action = ShellAction()
