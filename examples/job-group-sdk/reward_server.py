"""Reward (verifier) component of the disaggregated RL Job Group.

A standalone CPU service that scores completions. It runs as its own Job Group
task -- it needs no GPU and no ML libraries, only the standard library -- which
is the point of disaggregation: the verifier scales and fails independently of
the GPU actor and trainer.

Protocol (HTTP/JSON):
  POST /score  {"items": [{"text": str, "a": int, "b": int}, ...]}
               -> {"rewards": [float, ...]}
  POST /ping   {} -> {"ok": true}

The reward is verifiable: a small bonus for emitting a well-formed
'#### <int>' answer, plus a larger bonus when that integer equals a + b.
"""

import json
import os
import re
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer

PORT = int(os.environ.get("REWARD_PORT", "8001"))
_ANSWER = re.compile(r"####\s*(-?\d+)")


def score(text, a, b):
    match = _ANSWER.search(text)
    if match is None:
        return 0.0
    reward = 0.1  # well-formed answer
    if int(match.group(1)) == a + b:
        reward += 1.0  # and correct
    return reward


class Handler(BaseHTTPRequestHandler):

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/ping":
            self._send({"ok": True})
        elif self.path == "/score":
            rewards = [score(it["text"], it["a"], it["b"])
                       for it in payload["items"]]
            self._send({"rewards": rewards})
        else:
            self.send_error(404)

    def log_message(self, *args):  # quiet the default access log
        pass


def main():
    print(f"[reward] serving on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
