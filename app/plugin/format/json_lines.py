import logging
import json


class JsonLineFormatter(logging.Formatter):
    def format(self, record) -> str:
        data = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "pid": record.process,
            "msg": record.getMessage(),
        }

        if record.exc_info:
            data.update({"exc_info": self.formatException(record.exc_info)})

        if record.stack_info:
            data.update({"stack_info": self.formatStack(record.stack_info)})

        return json.dumps(data)


formatter = JsonLineFormatter()
