"""
Retreive logs from files.

Will only read the last kB if the file is bigger than 1kB. And only return the
last 10 lines.

Details:
              
  path:        Absolute path to the log file
               type: format-string (with all props available)
               default: null -> required!
  required:    Do we expect the file to always exist
               type: bool
               default: false
  encoding:    File encoding
               type: string
               default: utf-8
  line_format: The format of each line
               type: format-string (with defined vars will be added to props in "log")
               default: null -> required!
               example: '[{time}] {message}'
  time:        Timestamp of the log entry
               type: jinja2-string (with all props + the line_format vars in "log")
               default: ''
  message:     Message of the log entry
               type: jinja2-string (with all props + the line_format vars in "log")
               default: ''
  problem:     Does the log entry indicate a problem
               type: jinja2-bool (with all props + the line_format vars in "log")
               default: 'false'
  progress:    Progress indicated by the log entry
               type: jinja2-int (with all props + the line_format vars in "log")
               default: '0'
"""

import logging
from parse import parse
from anyio import Path, open_file

from app.lib import j2
from app.model import out
from app.model.err import LogError
from app.model.err import LogSpecsError

logger = logging.getLogger(__name__)


async def get(
    log_name: str, problem: bool, progress: bool, *, details: dict, props: dict
) -> list[out.Log]:
    try:
        fn = details["path"].format(**props)
    except (KeyError, IndexError, ValueError, AttributeError) as error:
        raise LogSpecsError(f"In file log plugin details.path: {error}") from error

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
        if details.get("required", False):
            raise LogError(f"Require log file {fn} not found/readable!") from error
        else:
            # Silently return to avoid non-sense errors in log if files are absent by design
            return []

    encoding = details.get("encoding", "utf-8")
    result = []
    for l in lines:
        try:
            line = (
                parse(details["line_format"], l.decode(encoding).rstrip("\r\n")).named
                or {}
            )
        except (KeyError, IndexError, ValueError, AttributeError) as error:
            raise LogSpecsError(
                f"In file log plugin details.line_format: {error}"
            ) from error

        log_props = props.copy()
        log_props.update({"log": line})

        try:
            entry = out.Log(
                name=log_name,
                time=j2.render_print(details.get("time", '""'), log_props),
                message=j2.render_print(details.get("message", '""'), log_props),
            )
        except j2.J2Error as error:
            raise LogSpecsError(
                f"In file log plugin details.time/message: {error}"
            ) from error

        if problem:
            try:
                entry.problem = j2.render_test(
                    details.get("problem", "false"), log_props
                )
            except j2.J2Error as error:
                raise LogSpecsError(
                    f"In file log plugin details.problem: {error}"
                ) from error

        if progress:
            try:
                percent = int(j2.render_print(details.get("progress", "0"), log_props))
            except j2.J2Error as error:
                raise LogSpecsError(
                    f"In file log plugin details.progress: {error}"
                ) from error

            if 0 <= percent <= 100:
                entry.progress = percent
            else:
                entry.progress = 100 if 100 < percent else 0

        result.append(entry)

    return result
