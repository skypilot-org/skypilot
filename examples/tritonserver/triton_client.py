"""
An example triton client request. This example just sends a dummy
input. More info on how to write a client can be found here:

https://pytorch.org/TensorRT/tutorials/serving_torch_tensorrt_with_triton.html

Usage:
    $ python triton_client.py
"""

import numpy as np
import os
import tritonclient.http as httpclient
from tritonclient.utils import triton_to_np_dtype

# create the client and define the inputs and outputs
client = httpclient.InferenceServerClient(url=f"{os.environ['TRITONSERVER']}:8000")
img = np.random.rand(3, 224, 224).astype(np.float32)
inputs = httpclient.InferInput("input__0", img.shape, datatype="FP32")
inputs.set_data_from_numpy(img, binary_data=True)
outputs = httpclient.InferRequestedOutput("output__0", binary_data=True, class_count=1000)

# send a request
results = client.infer(model_name="resnet18", inputs=[inputs], outputs=[outputs])
inference_output = results.as_numpy('output__0')
print(inference_output[:5])
