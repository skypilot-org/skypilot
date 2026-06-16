"""Actor (rollout) component of the disaggregated RL Job Group.

A standalone GPU inference server that generates completion groups on request,
and -- crucially -- receives updated policy weights from the trainer **over
NCCL**. This mirrors how production RL stacks (e.g. SGLang/vLLM + a training
engine) disaggregate generation from training and push fresh weights to the
inference workers via a collective rather than through disk.

This task is rank 1 of a 2-rank torch.distributed (NCCL) process group; the
trainer is rank 0 and the rendezvous master. The group is used only for
trainer -> actor weight broadcast.

Protocol (HTTP/JSON), served concurrently with the NCCL group:
  POST /generate {"prompt": str, "n": int, "max_new_tokens": int,
                  "temperature": float}
                 -> {"prompt_ids": [int],
                     "completions": [{"ids": [int], "text": str}, ...]}
  POST /sync     {} -> {"ok": true}   # enter NCCL receive; load broadcast weights
  POST /ping     {} -> {"ok": true}

A lock serializes generation and weight-sync so the model is never read while
its parameters are being overwritten.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer

import torch
import torch.distributed as dist
from transformers import AutoModelForCausalLM
from transformers import AutoTokenizer

MODEL = os.environ.get("MODEL", "Qwen/Qwen2.5-0.5B-Instruct")
PORT = int(os.environ.get("ACTOR_PORT", "8000"))
JOBGROUP = os.environ["SKYPILOT_JOBGROUP_NAME"]
MASTER_ADDR = f"trainer-0.{JOBGROUP}"
MASTER_PORT = os.environ.get("NCCL_RENDEZVOUS_PORT", "29500")

_model = None
_tokenizer = None
_device = None
_lock = threading.Lock()


def _generate(prompt, n, max_new_tokens, temperature):
    torch.cuda.set_device(0)  # CUDA current device is per-thread
    enc = _tokenizer(prompt, return_tensors="pt").to(_device)
    prompt_len = enc.input_ids.shape[1]
    with _lock, torch.no_grad():
        seqs = _model.generate(
            **enc,
            do_sample=True,
            temperature=temperature,
            top_p=1.0,
            num_return_sequences=n,
            max_new_tokens=max_new_tokens,
            pad_token_id=_tokenizer.pad_token_id,
        )
    completions = []
    for row in seqs:
        comp_ids = row[prompt_len:].tolist()
        text = _tokenizer.decode(comp_ids, skip_special_tokens=True)
        completions.append({"ids": comp_ids, "text": text})
    return {"prompt_ids": enc.input_ids[0].tolist(), "completions": completions}


def _receive_weights():
    """Match the trainer's per-parameter broadcast and overwrite the policy."""
    torch.cuda.set_device(0)  # CUDA current device is per-thread
    with _lock:
        for param in _model.parameters():
            dist.broadcast(param.data, src=0)
    torch.cuda.synchronize()


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
        elif self.path == "/generate":
            self._send(_generate(payload["prompt"],
                                 int(payload.get("n", 8)),
                                 int(payload.get("max_new_tokens", 32)),
                                 float(payload.get("temperature", 1.0))))
        elif self.path == "/sync":
            _receive_weights()
            self._send({"ok": True})
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


def main():
    global _model, _tokenizer, _device
    # Join the trainer's NCCL group as rank 1 (weight-sync channel).
    torch.cuda.set_device(0)
    _device = torch.device("cuda", 0)
    dist.init_process_group(
        backend="nccl",
        init_method=f"tcp://{MASTER_ADDR}:{MASTER_PORT}",
        world_size=2,
        rank=1,
    )
    _tokenizer = AutoTokenizer.from_pretrained(MODEL)
    if _tokenizer.pad_token_id is None:
        _tokenizer.pad_token = _tokenizer.eos_token
    _model = AutoModelForCausalLM.from_pretrained(MODEL, torch_dtype=torch.bfloat16)
    _model.to(_device)
    _model.eval()
    print(f"[actor] NCCL joined as rank 1; serving generate on :{PORT}",
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
