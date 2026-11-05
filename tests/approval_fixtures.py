"""Explicit synthetic approvals for tests; never imported by application code."""
import io
import json
import queue


def fixture_approval(request):
    return {"decision": "allow"}  # Deliberately does not grant write permission.


class RPCInput:
    def __init__(self, request):
        self.queue = queue.Queue()
        self.put(request)

    def put(self, value):
        self.queue.put(None if value is None else json.dumps(value) + "\n")

    def __iter__(self):
        return self

    def __next__(self):
        value = self.queue.get(timeout=5)
        if value is None:
            raise StopIteration
        return value


class RPCOutput(io.StringIO):
    def __init__(self, incoming, handler):
        super().__init__()
        self.incoming, self.handler, self.buffer = incoming, handler, ""

    def write(self, text):
        result = super().write(text)
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            message = json.loads(line)
            self.handler(message, self.incoming)
            if message["type"] in ("result", "error"):
                self.incoming.put(None)
        return result
