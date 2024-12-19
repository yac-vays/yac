"""
Run a shell script.

Details:

  command: Shell command(s) to run. All props are also available as env variables.
           type: format-string (with all props available)
           default: null -> required!
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

from app.model.err import ActionClientError
from app.model.err import ActionError
from app.model.err import ActionSpecsError
from app.model.plg import IAction


class ShellAction(IAction):
    async def run(self, *, details: dict, props: dict) -> None:
        try:
            command = details["command"].format(**props)
        except (KeyError, IndexError, ValueError, AttributeError) as error:
            raise ActionSpecsError(
                f"In shell action plugin details.command: {error}"
            ) from error

        proc = await asyncio.create_subprocess_shell(
            command,
            env=self.__get_env(props=props),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        try:
            stdout, _ = await proc.communicate()
        except Exception as error:  # TODO what to catch here!?
            raise ActionError(f"Shell command failed with: {error}") from error

        if proc.returncode in details.get("success", [0]):
            return

        if proc.returncode in details.get("error", []):
            raise ActionClientError(stdout.decode("utf-8"))

        raise ActionError(stdout.decode("utf-8"))

    def __get_env(self, *, props: dict) -> dict:
        env = {}
        for e in ["PATH", "HOME", "HOSTNAME", "PWD", "LANG"]:
            env[e] = os.environ.get(e, "")

        env.update(self.__dict_to_env(props))
        return env

    def __dict_to_env(self, props, prefix: str = "YAC") -> dict:
        result = {}
        if isinstance(props, dict):
            for key, value in props.items():
                result.update(
                    self.__dict_to_env(value, f"{prefix}__{self.__to_shell_var(key)}")
                )
        elif isinstance(props, list):
            for key, value in enumerate(props):
                result.update(
                    self.__dict_to_env(value, f"{prefix}__{self.__to_shell_var(key)}")
                )
        else:
            result[f"{prefix}"] = self.__to_shell_str(props)
        return result

    def __to_shell_str(self, string) -> str:
        if isinstance(string, bool):
            return "true" if string else "false"
        if string is None:
            return ""

        return str(string)

    def __to_shell_var(self, string) -> str:
        pattern = r"[A-Z0-9_]{1}"
        return "".join(char for char in str(string).upper() if re.match(pattern, char))


action = ShellAction()
