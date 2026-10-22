"""Merge a SkyRL LoRA export into its base model, for faster serving.

    python tools/merge_lora.py --base /outputs/RUN/sft_model \
        --lora /outputs/RUN/export/global_step_30/policy --out ~/models/step30-merged

Serving a merged model avoids vLLM's per-token LoRA overhead, which was enough
to push the trained pilot's decisions over the 0.2 s interval.
"""
import argparse

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--lora', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    model = AutoModelForCausalLM.from_pretrained(args.base, torch_dtype=torch.bfloat16)
    model = PeftModel.from_pretrained(model, args.lora).merge_and_unload()
    model.save_pretrained(args.out, safe_serialization=True)
    AutoTokenizer.from_pretrained(args.base).save_pretrained(args.out)
    print('merged ->', args.out)


if __name__ == '__main__':
    main()
