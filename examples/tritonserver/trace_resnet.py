"""
Downloads ImageNet pretrained resnet18 model, exports a `torch.jit.script`
model for use in NVIDIA triton inference server. `torch.jit.trace` can
equally work well.

Usage: 
    $ python script_resnet.py
"""

import torch
import torchvision.models as models

r18 = models.resnet18(
    pretrained=True)  # We now have an instance of the pretrained model
r18_scripted = torch.jit.script(r18)  # *** This is the TorchScript export
torch.jit.save(r18_scripted,
               "./models/resnet18/1/model.pt")  # save into triton repo
