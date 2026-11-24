"""
Retreive logs from files.

Will only read the last kB if the file is bigger than 1kB. And only return the
last 10 lines.

Details:

  path:        Absolute path to the log file
               type: string
               default: "" -> required!
  required:    Do we expect the file to always exist
               type: bool
               default: false
  encoding:    File encoding
               type: string
               default: utf-8
  line_format: The format of each line
               type: regex-string (groups will be available later)
               default: "^(.*)$" -> required!
               example: "^\\[([^]]*)\\] (.*)$" # for [timestamp] message
  time:        Timestamp of the log entry
               type: string (with all j2 props + the regex groups in var "log")
               default: ""
  message:     Message of the log entry
               type: string (with all j2 props + the regex groups in var "log")
               default: "{{ log[0] }}"
  problem:     Does the log entry indicate a problem (left unset if missing)
               type: bool (with all j2 props + the regex groups in var "log")
  progress:    Progress indicated by the log entry, 0 to 100 (left unset if
               missing)
               type: int (with all j2 props + the regex groups in var "log")
"""

import re
import logging
from anyio import Path, open_file

from app.lib import j2
from app.lib import log as log_lib
from app.model import out
from app.model.err import LogError
from app.model.err import LogSpecsError
from app.model.plg import ILog

logger = logging.getLogger(__name__)


class FileLog(ILog):
    async def get(
        self,
        facility: str,
        *,
        details: dict,
        props: dict,
    ) -> list[out.Log]:
        try:
            d = await j2.render(
                details,
                props,
                skip=r"^#/(time|message|problem|progress)$",
            )
            assert isinstance(d, dict)
        except (AssertionError, j2.J2Error) as error:
            raise LogSpecsError(f'In details for log plugin "file": {error}') from error

        fn = d.get("path", "")

        try:
            async with await open_file(fn, mode="rb") as fh:
                logger.debug(f"Reading end (1kB) of file {fn}")
                if (await Path(fn).stat()).st_size > 1024:
                    # if the file is big (>1kB), only get the last 1kB
                    # and remove the first line as it might by incomplete
                    await fh.seek(-1024, 2)
                    lines = (await fh.readlines())[1:][-10:]
                else:
                    lines = (await fh.readlines())[-10:]
        except OSError as error:
            if d.get("required", False):
                raise LogError(f"Require log file {fn} not found/readable!") from error
            # Silently return to avoid non-sense errors in log if files are absent by design
            return []

        enc = d.get("encoding", "utf-8")
        try:
            pattern = re.compile(d.get("line_format", r"^(.*)$"))
        except (re.error, TypeError) as error:
            raise LogSpecsError(
                f'In details.line_format for log plugin "file": {error}'
            ) from error

        result = []
        for l in lines:
            try:
                line = l.decode(enc, errors="replace").rstrip("\r\n")
            except LookupError as error:
                raise LogSpecsError(
                    f'In details.encoding for log plugin "file": {error}'
                ) from error
            match = pattern.search(line)
            if not match:
                continue
            result.append(
                await log_lib.build_entry(
                    "file",
                    facility,
                    d,
                    props | {"log": match.groups()},
                    time_default="",
                    message_default="{{ log[0] }}",
                )
            )
        return result


log = FileLog()
