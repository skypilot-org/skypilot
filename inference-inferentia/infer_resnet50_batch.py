import os
import time
import numpy as np
import tensorflow as tf
from tensorflow.keras.preprocessing import image
from tensorflow.keras.applications.resnet50 import preprocess_input
from concurrent import futures

import pandas as pd

# The following line creates 4 groups, each having 1 core.
#
# If pipelining (e.g., across 4 cores) is used, this line must be commented
# out, as the pipeline requires 1 group with 4 cores.
os.environ['NEURONCORE_GROUP_SIZES'] = '4x1'
# os.environ['NEURONCORE_GROUP_SIZES'] = '1,1,1,1'
# num_workers = 4


def RunOneBatch(model, inputs):
    start = time.time()
    _ = model(inputs)
    duration_ms = (time.time() - start) * 1e3
    return duration_ms


mean_latencies = []
p99_latencies = []
p90_latencies = []
throughputs = []

batch_sizes = [1]
batch_sizes = [1, 2, 4, 8, 16]

for batch_size in batch_sizes:
    # for batch_size in [4]:
    USER_BATCH_SIZE = batch_size
    print("batch_size: {}, USER_BATCH_SIZE: {}".format(batch_size,
                                                       USER_BATCH_SIZE))

    # Load model
    compiled_model_dir = 'resnet50_neuron_batch' + str(batch_size)

    predictor_inferentia = tf.contrib.predictor.from_saved_model(
        compiled_model_dir)

    # Create input from image.
    img_sgl = image.load_img('kitten_small.jpg', target_size=(224, 224))
    img_arr = image.img_to_array(img_sgl)
    img_arr2 = np.expand_dims(img_arr, axis=0)
    img_arr3 = preprocess_input(np.repeat(img_arr2, USER_BATCH_SIZE, axis=0))
    model_feed_dict = {'input': img_arr3}

    # Warmup.
    _ = predictor_inferentia(model_feed_dict)

    num_loops = 10000
    num_inferences = num_loops * USER_BATCH_SIZE

    num_inferences = 25000  # MLPerf: Offline.
    num_inferences = int(1e6)  # MLPerf: Offline.
    num_loops = num_inferences // USER_BATCH_SIZE

    # Durations for all batches.
    duration_ms = [None] * num_loops
    fut_list = [None] * num_loops

    # Run inference.
    start = time.time()
    with futures.ThreadPoolExecutor(8) as exe:
        for i in range(num_loops):
            fut = exe.submit(RunOneBatch, predictor_inferentia,
                             model_feed_dict)
            fut_list[i] = fut
        for i, fut in enumerate(fut_list):
            duration_ms[i] = fut.result()
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
