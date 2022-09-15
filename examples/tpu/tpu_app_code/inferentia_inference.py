import argparse
import os
import time
import numpy as np
import tensorflow as tf
from tensorflow.keras.preprocessing import image
from tensorflow.keras.applications.resnet50 import preprocess_input
from concurrent import futures

from transformers import TFBertForSequenceClassification

import pandas as pd

from preprocess import get_example_input

# The following line creates 4 groups, each having 1 core.
#
# If pipelining (e.g., across 4 cores) is used, this line must be commented
# out, as the pipeline requires 1 group with 4 cores.
os.environ['NEURON_RT_NUM_CORES'] = '4'
# os.environ['NEURONCORE_GROUP_SIZES'] = '1,1,1,1'
# num_workers = 4


class TFBertForSequenceClassificationDictIO(tf.keras.Model):

    def __init__(self, model_wrapped):
        super().__init__()
        self.model_wrapped = model_wrapped
        self.aws_neuron_function = model_wrapped.aws_neuron_function

    def call(self, inputs):
        input_ids = inputs['input_ids']
        attention_mask = inputs['attention_mask']
        logits = self.model_wrapped([input_ids, attention_mask])
        return [logits]


original_model = TFBertForSequenceClassification.from_pretrained(
    'bert-base-uncased', num_labels=1)


def RunOneBatch(model, inputs):
    start = time.time()
    _ = model(inputs, training=False)
    duration_ms = (time.time() - start) * 1e3
    return duration_ms


mean_latencies = []
p99_latencies = []
p90_latencies = []
throughputs = []

COMPILED_MODEL_DIR = 'compiled-keras-bert'
batch_sizes = [1]
batch_sizes = [1, 2, 4, 8, 16]
# batch_sizes = [16]

for batch_size in batch_sizes:
    # for batch_size in [4]:
    USER_BATCH_SIZE = batch_size
    print("batch_size: {}, USER_BATCH_SIZE: {}".format(batch_size,
                                                       USER_BATCH_SIZE))

    # Load model
    compiled_model_dir = f'{COMPILED_MODEL_DIR}_batch' + str(batch_size)

    model = tf.keras.models.load_model(
        compiled_model_dir,
        custom_objects={'compute_loss': original_model.compute_loss})
    model = TFBertForSequenceClassificationDictIO(model)

    predictor_inferentia = model

    # Create input from image.
    example_input = get_example_input(USER_BATCH_SIZE)

    # Warmup.
    _ = predictor_inferentia(model_feed_dict, training=False)

    num_loops = 10000
    num_inferences = num_loops * USER_BATCH_SIZE

    num_inferences = 25000  # MLPerf: Offline.
    num_inferences = 50000  # Imagenet val set
    num_inferences = int(1e6)  # MLPerf: Offline.
    num_loops = num_inferences // USER_BATCH_SIZE

    # Durations for all batches.
    duration_ms = [None] * num_loops
    fut_list = [None] * num_loops

    # Run inference.
    start = time.time()
    with futures.ThreadPoolExecutor(8) as exe:
        for i in range(num_loops):
            fut = exe.submit(RunOneBatch, predictor_inferentia, model_feed_dict)
            fut_list[i] = fut
        for i, fut in enumerate(fut_list):
            duration_ms[i] = fut.result()
            if i != 0 and i % 100 == 0:
                print(
                    f'Finished {i} / {num_loops} -- throughput: {i*USER_BATCH_SIZE / (time.time() - start):.2f} images/sec'
                )
    elapsed_time = time.time() - start

    mean_latency = np.mean(duration_ms)
    p99_latency = np.quantile(duration_ms, 0.99)
    p90_latency = np.quantile(duration_ms, 0.90)
    throughput = num_inferences / elapsed_time

    mean_latencies.append(mean_latency)
    p99_latencies.append(p99_latency)
    p90_latencies.append(p90_latency)
    throughputs.append(throughput)

    print()
    print('num_inferences:{:>6}[images], elapsed_time:{:6.2f}[sec]'.format(
        num_inferences, elapsed_time))
    print('Latency (ms): mean {:.1f}, p99 {:.1f} p90{:.1f}'.format(
        mean_latency, p99_latency, p90_latency))
    print('Throughput (images/sec):{:8.2f}'.format(throughput))

print()
df = pd.DataFrame({
    'batch_size': batch_sizes,
    'throughput': throughputs,
    'p90_ms': p90_latencies,
    'p99_ms': p99_latencies,
    'mean_ms': mean_latencies,
    'num_images': [num_inferences] * len(batch_sizes),
})
print(df)
df.to_csv('results.csv', index=False, header=True)
