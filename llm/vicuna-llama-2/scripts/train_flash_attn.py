# Make it more memory efficient by monkey patching the LLaMA model with FlashAttn.

# Need to call this before importing transformers.
from flash_attn_patch import replace_llama_attn_with_flash_attn

replace_llama_attn_with_flash_attn()

from train import train

if __name__ == "__main__":
    train()
