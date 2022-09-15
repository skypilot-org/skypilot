import tensorflow as tf
import tensorflow.neuron as tfn
from transformers import pipeline
from transformers import TFBertForSequenceClassification

import shutil
from preprocess import get_example_input

tf.keras.backend.set_learning_phase(0)
# tf.keras.backend.set_floatx('float16')

COMPILED_MODEL_DIR = 'compiled-keras-bert'


class TFBertForSequenceClassificationFlatIO(tf.keras.Model):

    def __init__(self, model):
        super().__init__()
        self.model = model

    def call(self, inputs):
        input_ids, attention_mask = inputs
        output = self.model({
            'input_ids': input_ids,
            'attention_mask': attention_mask
        })
        return output['logits']


model = TFBertForSequenceClassification.from_pretrained('bert-base-uncased',
                                                        num_labels=1)
loaded_model = tf.keras.models.load_model(
    'saved_model', custom_objects={'compute_loss': model.compute_loss})

model.set_weights(loaded_model.get_weights())

#wrap the original model from HuggingFace, now our model accepts a list as input
model_wrapped = TFBertForSequenceClassificationFlatIO(model)

batch_sizes = [1, 2, 4, 8]
for batch_size in batch_sizes:
    example_inputs = get_example_input(batch_size)
    #turn the dictionary input into list input
    example_inputs_list = [
        example_inputs['input_ids'], example_inputs['attention_mask']
    ]

    # Prepare export directory (old one removed)
    compiled_model_dir = f'{COMPILED_MODEL_DIR}_batch' + str(batch_size)
    shutil.rmtree(compiled_model_dir, ignore_errors=True)

    #compile the wrapped model and save it to disk
    model_wrapped_traced = tfn.trace(model_wrapped, example_inputs_list)
    model_wrapped_traced.save(compiled_model_dir)
