import shutil
import tensorflow.neuron as tfn
import numpy as np

model_dir = 'resnet50'
# model_dir = 'resnet50_neuron'

for batch_size in [1, 2, 4, 8, 16]:
    example_input = np.zeros([batch_size, 224, 224, 3], dtype='float16')

    # Prepare export directory (old one removed)
    compiled_model_dir = 'resnet50_neuron_batch' + str(batch_size)
    shutil.rmtree(compiled_model_dir, ignore_errors=True)

    # Compile using Neuron
    # import IPython; IPython.embed()
    # import ipdb; ipdb.set_trace()
    # tfn.saved_model.compile(model_dir, compiled_model_dir, batch_size=batch_size, dynamic_batch_size=True, model_feed_dict = {'input_1': example_input})
    tfn.saved_model.compile(model_dir,
                            compiled_model_dir,
                            batch_size=batch_size)
