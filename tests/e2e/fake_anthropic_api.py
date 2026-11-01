"""A stand-in for the Claude API, so CI can start a real Claude Code session without an
account. It answers every message with "ok" and never contacts anything. Prints its port."""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "claude-sonnet-5-5"
MESSAGE = {
    "id": "msg_fake", "type": "message", "role": "assistant", "model": MODEL,
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn", "stop_sequence": None,
    "usage": {"input_tokens": 1, "output_tokens": 1},
}
STREAM = [
    ("message_start", {"type": "message_start", "message": {**MESSAGE, "content": [], "stop_reason": None}}),
    ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 1}}),
    ("message_stop", {"type": "message_stop"}),
]


class Handler(BaseHTTPRequestHandler):
    def _json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length) or b"{}")
        if "count_tokens" in self.path:
            self._json({"input_tokens": 1})
        elif request.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for event, data in STREAM:
                self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
        else:
            self._json(MESSAGE)

    def do_GET(self):
        self._json({"data": [], "has_more": False})

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(server.server_address[1], flush=True)
    sys.stdout.close()
    server.serve_forever()
