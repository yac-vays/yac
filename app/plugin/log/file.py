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
               example: "^[([^]]*)] (.*)$" # for [timestamp] message
  time:        Timestamp of the log entry
               type: string (with all j2 props + the regex groups in var "log")
               default: ""
  message:     Message of the log entry
               type: string (with all j2 props + the regex groups in var "log")
               default: "{{ log[1] }}"
  problem:     Does the log entry indicate a problem
               type: bool (with all j2 props + the regex groups in var "log")
               default: false
  progress:    Progress indicated by the log entry
               type: int (with all j2 props + the regex groups in var "log")
               default: 0
"""

import re
import logging
from anyio import Path, open_file

from app.lib import j2
from app.model import out
from app.model.err import LogError
from app.model.err import LogSpecsError
from app.model.plg import ILog

logger = logging.getLogger(__name__)


class FileLog(ILog):
    async def get(
        self,
        facility: str,
        problem: bool,
        progress: bool,
        *,
        details: dict,
        props: dict,
    ) -> list[out.Log]:
        try:
            d = await j2.render(
                details, props, skip=["time", "message", "problem", "progress"]
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
        except Exception as error:
            raise LogSpecsError(
                f'In details.line_format for log plugin "file": {error}'
            )

        result = []
        for l in lines:
            line = l.decode(enc).rstrip("\r\n")
            match = pattern.search(line)
            if not match:
                continue

            log_props = props.copy()
            log_props.update({"log": match.groups()})

            time = await self.__render(d, "time", str, "", log_props)
            message = await self.__render(d, "message", str, "{{ log[1] }}", log_props)

            entry = out.Log(
                name=facility,
                time=time,
                message=message,
            )

            if problem:
                entry.problem = await self.__render(
                    d, "problem", bool, False, log_props
                )

            if progress:
                prog = await self.__render(d, "progress", (int, float), 0, log_props)

                if 0 <= prog <= 100:
                    entry.progress = int(prog)
                else:
                    entry.progress = 100 if 100 < prog else 0

            result.append(entry)

        return result

    async def __render(self, data: dict, key: str, typ, default, props: dict):
        try:
            result = await j2.render(data.get(key, default), props)
            assert isinstance(result, typ)
        except (AssertionError, j2.J2Error) as error:
            raise LogSpecsError(
                f'In details.{key} for log plugin "file": {error}'
            ) from error
        return result


log = FileLog()
