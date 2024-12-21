import logging


class PlainFormatter(logging.Formatter):
    def format(self, record) -> str:

        message = (
            f"[{self.formatTime(record)}] {record.process} {record.levelname:<8}"
            f" {record.getMessage()}"
        )

        if record.exc_info:
            message = f"{message}{self.formatException(record.exc_info)}"

        if record.stack_info:
            message = f"{message}{self.formatStack(record.stack_info)}"

        return message


formatter = PlainFormatter()
