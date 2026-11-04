# Memory-efficient variant of train.py: swap LLaMA attention for xformers'
# memory-efficient attention before transformers is imported, using the patch
# that ships with FastChat.
from fastchat.train.llama_xformers_attn_monkey_patch import (
    replace_llama_attn_with_xformers_attn)

replace_llama_attn_with_xformers_attn()

from train import train  # noqa: E402

if __name__ == '__main__':
    train()
