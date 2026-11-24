"""
Run a program (no shell involved).

The command is an argv list and every element is j2-rendered individually, so
untrusted values (token claims, entity data) can never break out of their
argument — this plugin replaces the former `shell` plugin, whose j2-rendered
command string was interpolated into a shell and therefore injectable.

Three channels hand data to the process:
  - argv elements (visible in /proc/<pid>/cmdline: fine for plain parameters)
  - env vars (visible only to same-uid processes: fine for private values)
  - stdin (visible to nobody else: use this for secrets and bulk data)

Details:

  command: The program to run as an argv list (["/path/prog", "arg", ...]).
           Each element is a j2-string rendering to exactly one argument.
           The program is looked up in the PATH of the minimal base env;
           prefer an absolute path.
           type: list[string]
           default: [] -> required!
  env:     Extra environment variables for the process (values are
           j2-strings), merged over a minimal base env (PATH, HOME,
           HOSTNAME, PWD, LANG).
           type: dict[string, string]
           default: {}
  stdin:   Data piped to the process's stdin (j2-string; a template that
           renders to an object or list is JSON-encoded).
           type: string
           default: "" -> stdin is closed
  success: List of return codes considered a success.
           type: list[int]
           default: [0]
  error:   List of return codes considered a client-side error where the stdout and stderr will
           be printed to the user. (All other return codes will lead to a server-side error.)
           type: list[int]
           default: []
  timeout: Seconds after which the process (with its process group) is
           killed (leading to a server-side error); 0 for no timeout.
           type: int
           default: 60

Of the output (stdout and stderr), at most 1 MiB is kept.
"""

from asyncio.subprocess import Process
import asyncio
import json
import os
import re
import signal

from app.lib import j2
from app.model.err import ActionClientError
from app.model.err import ActionError
from app.model.err import ActionSpecsError
from app.model.plg import IAction

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_TIMEOUT = 60
# The output is only used for error messages.
MAX_OUTPUT_BYTES = 1024 * 1024


async def _communicate(proc: Process, stdin: bytes | None) -> bytes:
    """
    Like `proc.communicate`, but only the first MAX_OUTPUT_BYTES of the
    output are kept (the rest is still read, so the process is not blocked).
    """

    async def feed() -> None:
        if stdin is None:
            return
        try:
            proc.stdin.write(stdin)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the process does not read (all of) it
        proc.stdin.close()

    async def read() -> bytes:
        output = bytearray()
        while chunk := await proc.stdout.read(65536):
            output += chunk[: MAX_OUTPUT_BYTES - len(output)]
        return bytes(output)

    output, _ = await asyncio.gather(read(), feed())
    await proc.wait()
    return output


async def _kill(proc: Process) -> None:
    """Kill the process and whatever it started, and reap it."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await asyncio.shield(proc.wait())


def _as_str(value, loc: str) -> str:
    """
    j2 renders full-expression templates to their JSON value, so a rendered
    element may be a number or bool — coerce those; anything without an
    obvious single-argument form is a specs error.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    raise ActionSpecsError(
        f'In details for action plugin "exec": {loc} must render to a single'
        f" string (got {type(value).__name__})"
    )


class ExecAction(IAction):
    async def run(self, *, details: dict, props: dict) -> None:
        try:
            d = await j2.render(details, props)
            assert isinstance(d, dict)
        except (AssertionError, j2.J2Error) as error:
            raise ActionSpecsError(
                f'In details for action plugin "exec": {error}'
            ) from error

        argv = self.__get_argv(d)
        stdin = self.__get_stdin(d)

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                env=self.__get_env(d),
                stdin=(
                    asyncio.subprocess.PIPE
                    if stdin is not None
                    else asyncio.subprocess.DEVNULL
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                # Own process group: its children can then be killed with it.
                start_new_session=True,
            )
        except (OSError, ValueError) as error:
            raise ActionError(f"Could not execute {argv[0]}: {error}") from error

        timeout = d.get("timeout", DEFAULT_TIMEOUT)
        try:
            async with asyncio.timeout(timeout if timeout else None):
                stdout = await _communicate(proc, stdin)
        except TimeoutError as error:
            await _kill(proc)
            raise ActionError(
                f"{argv[0]} was killed after the timeout of {timeout}s"
            ) from error
        except asyncio.CancelledError:
            # The request is gone: nothing is to keep running for it.
            await _kill(proc)
            raise
        except Exception as error:
            await _kill(proc)
            raise ActionError(f"Command failed with: {error}") from error

        if proc.returncode in d.get("success", [0]):
            return

        if proc.returncode in d.get("error", []):
            raise ActionClientError(stdout.decode("utf-8", errors="replace"))

        raise ActionError(stdout.decode("utf-8", errors="replace"))

    def __get_argv(self, d: dict) -> list[str]:
        command = d.get("command", [])
        if isinstance(command, str):
            raise ActionSpecsError(
                'In details for action plugin "exec": command must be an argv'
                ' list (["/path/prog", "arg", ...]), not a shell command'
                " string — pass every argument as its own list element"
            )
        if not isinstance(command, list) or len(command) == 0:
            raise ActionSpecsError(
                'In details for action plugin "exec": command must be a'
                " non-empty argv list"
            )
        return [_as_str(a, f"command[{i}]") for i, a in enumerate(command)]

    def __get_stdin(self, d: dict) -> bytes | None:
        stdin = d.get("stdin", "")
        if stdin is None or stdin == "":
            return None
        if isinstance(stdin, (dict, list)):
            return json.dumps(stdin).encode("utf-8")
        return _as_str(stdin, "stdin").encode("utf-8")

    def __get_env(self, d: dict) -> dict:
        env = {}
        for e in ["PATH", "HOME", "HOSTNAME", "PWD", "LANG"]:
            env[e] = os.environ.get(e, "")

        extra = d.get("env", {})
        if not isinstance(extra, dict):
            raise ActionSpecsError(
                'In details for action plugin "exec": env must be an object'
                " of NAME: value pairs"
            )
        for name, value in extra.items():
            if not _ENV_NAME.match(str(name)):
                raise ActionSpecsError(
                    f'In details for action plugin "exec": "{name}" is not a'
                    " valid environment variable name"
                )
            env[str(name)] = _as_str(value, f"env.{name}")
        return env


action = ExecAction()
