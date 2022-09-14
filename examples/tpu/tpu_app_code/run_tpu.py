from absl import app
from absl import flags

import tensorflow_datasets as tfds
import tensorflow as tf
import tensorflow_text as tf_text
from transformers import TFDistilBertForSequenceClassification
from transformers import TFBertForSequenceClassification


flags.DEFINE_string('tpu', default=None, help='tpu name')
flags.DEFINE_integer('per_core_batch_size', default=32, help='batch size for each core')
flags.DEFINE_integer('num_cores', default=8, help='number of cores to use')
flags.DEFINE_string('data_dir', default=None, help='path to the dataset')
flags.DEFINE_string('model_dir', default=None, help='path to the model')
flags.DEFINE_integer('num_epochs', default=5, help='num epochs')
flags.DEFINE_boolean('amp', default=False, help='use amp')
flags.DEFINE_boolean('xla', default=False, help='use xla')
FLAGS = flags.FLAGS

def main(unused):
    use_gpu = (FLAGS.tpu is not None and FLAGS.tpu.lower() == 'gpu')
    if use_gpu:
        strategy = tf.distribute.experimental.MultiWorkerMirroredStrategy(communication=tf.distribute.experimental.CollectiveCommunication.NCCL)
    else:
        resolver = tf.distribute.cluster_resolver.TPUClusterResolver(tpu=FLAGS.tpu)
        tf.config.experimental_connect_to_cluster(resolver)
        tf.tpu.experimental.initialize_tpu_system(resolver)
        strategy = tf.distribute.experimental.TPUStrategy(resolver)
    assert use_gpu or (not FLAGS.amp and not FLAGS.xla), 'AMP and XLA only supported on GPU.'
    if use_gpu:
        # From Nvidia Repo, explained here: htteps://github.com/NVIDIA/DeepLearningExamples/issues/57
        os.environ['CUDA_CACHE_DISABLE'] = '0'
        os.environ['TF_GPU_THREAD_MODE'] = 'gpu_private'
        os.environ['TF_GPU_THREAD_COUNT'] = '2'
        os.environ['TF_USE_CUDNN_BATCHNORM_SPATIAL_PERSISTENT'] = '1'
        os.environ['TF_ADJUST_HUE_FUSED'] = '1'
        os.environ['TF_ADJUST_SATURATION_FUSED'] = '1'
        os.environ['TF_ENABLE_WINOGRAD_NONFUSED'] = '1'
        os.environ['TF_SYNC_ON_FINISH'] = '0'
        os.environ['TF_AUTOTUNE_THRESHOLD'] = '2'
        os.environ['TF_DISABLE_NVTX_RANGES'] = '1'
    if FLAGS.amp:
        os.environ["TF_ENABLE_AUTO_MIXED_PRECISION_GRAPH_REWRITE"] = "1"
    if FLAGS.xla:
        # https://github.com/tensorflow/tensorflow/blob/8d72537c6abf5a44103b57b9c2e22c14f5f49698/tensorflow/compiler/jit/flags.cc#L78-L87
        # 1: on for things very likely to be improved
        # 2: on for everything
        # fusible: only for Tensorflow operations that XLA knows how to fuse
        #
        # os.environ['TF_XLA_FLAGS'] = '--tf_xla_auto_jit=1'
        # os.environ['TF_XLA_FLAGS'] = '--tf_xla_auto_jit=2'
        # Best Performing XLA Option
        os.environ['TF_XLA_FLAGS'] = '--tf_xla_auto_jit=fusible'
        os.environ["TF_XLA_FLAGS"] = (os.environ.get("TF_XLA_FLAGS", "") + " --tf_xla_enable_lazy_compilation=false")

    ds_train, ds_info = tfds.load('amazon_us_reviews/Books_v1_02',
                                split='train[:5%]',
                                with_info=True,
                                download=False,
                                data_dir=FLAGS.data_dir)

    MAX_SEQ_LEN = 512
    bert_tokenizer = tf_text.BertTokenizer(
        vocab_lookup_table='gs://weilin-bert-test/vocab.txt',
        token_out_type=tf.int64,
        lower_case=True)


    def preprocessing_fn(inputs):
        """Preprocess input column of text into transformed columns of.
            * input token ids
            * input mask
            * input type ids
        """

        CLS_ID = tf.constant(101, dtype=tf.int64)
        SEP_ID = tf.constant(102, dtype=tf.int64)
        PAD_ID = tf.constant(0, dtype=tf.int64)

        def tokenize_text(text, sequence_length=MAX_SEQ_LEN):
            """
            Perform the BERT preprocessing from text -> input token ids
            """

            # convert text into token ids
            tokens = bert_tokenizer.tokenize(text)

            # flatten the output ragged tensors
            tokens = tokens.merge_dims(1, 2)[:, :sequence_length]

            # Add start and end token ids to the id sequence
            start_tokens = tf.fill([tf.shape(text)[0], 1], CLS_ID)
            end_tokens = tf.fill([tf.shape(text)[0], 1], SEP_ID)
            tokens = tokens[:, :sequence_length - 2]
            tokens = tf.concat([start_tokens, tokens, end_tokens], axis=1)

            # truncate sequences greater than MAX_SEQ_LEN
            tokens = tokens[:, :sequence_length]

            # pad shorter sequences with the pad token id
            tokens = tokens.to_tensor(default_value=PAD_ID)
            pad = sequence_length - tf.shape(tokens)[1]
            tokens = tf.pad(tokens, [[0, 0], [0, pad]], constant_values=PAD_ID)

            # and finally reshape the word token ids to fit the output
            # data structure of TFT
            return tf.reshape(tokens, [-1, sequence_length])

        def preprocess_bert_input(text):
            """
            Convert input text into the input_word_ids, input_mask, input_type_ids
            """
            input_word_ids = tokenize_text(text)
            input_mask = tf.cast(input_word_ids > 0, tf.int64)
            input_mask = tf.reshape(input_mask, [-1, MAX_SEQ_LEN])

            zeros_dims = tf.stack(tf.shape(input_mask))
            input_type_ids = tf.fill(zeros_dims, 0)
            input_type_ids = tf.cast(input_type_ids, tf.int64)

            return (tf.squeeze(input_word_ids,
                            axis=0), tf.squeeze(input_mask, axis=0),
                    tf.squeeze(input_type_ids, axis=0))

        input_word_ids, input_mask, input_type_ids = preprocess_bert_input(
            [inputs['data']['review_body']])

        return (dict({
            'input_ids': input_word_ids,
            'token_type_ids': input_type_ids,
            'attention_mask': input_mask
        }), inputs['data']['star_rating'])


    from transformers import BertTokenizerFast

    tokenizer = BertTokenizerFast.from_pretrained('bert-base-uncased')


    def dataset_fn(ds):
        return ds.filter(lambda x: x['data']['helpful_votes'] >= 7)


    ds_train_filtered = ds_train.apply(dataset_fn)


    def process(example):
        return (dict(tokenizer(
            example['data']['review_body'].numpy().decode('utf-8')),
                    truncation=True,
                    padding=True), example['data']['star_rating'].numpy())


    def process_py(inp1, inp2):
        return [
            dict(tokenizer(inp1.numpy().decode('utf-8')),
                truncation=True,
                padding=True),
            inp2.numpy()
        ]


    ds_train_filtered_2 = ds_train_filtered.map(preprocessing_fn)

    tf.keras.mixed_precision.experimental.set_policy('mixed_bfloat16')

    with strategy.scope():
        model = TFBertForSequenceClassification.from_pretrained('bert-base-uncased',
                                                                num_labels=1)

        optimizer = tf.keras.optimizers.Adam(learning_rate=5e-5)
        if FLAGS.amp:
            optimizer = tf.train.experimental.enable_mixed_precision_graph_rewrite(optimizer, loss_scale='dynamic')
        model.compile(optimizer=optimizer,
                    loss=model.compute_loss)  # can also use any keras loss fn
        model.summary()

    batch_size = FLAGS.per_core_batch_size * FLAGS.num_cores
    inuse_dataset = ds_train_filtered_2.shuffle(1000).batch(batch_size).prefetch(
        tf.data.experimental.AUTOTUNE)
    model.fit(inuse_dataset, epochs=FLAGS.num_epochs, batch_size=batch_size)
    tf.logging.info('This might take a while...')
    model.save('saved_weights.h5', include_optimizer=False, save_format='h5')

    saved_weights_path = os.path.join(FLAGS.model_dir, 'saved_weights.h5')
    # Copy model.h5 over to Google Cloud Storage
    with file_io.FileIO('saved_weights.h5', mode='rb') as input_f:
        with file_io.FileIO(saved_weights_path, mode='wb+') as output_f:
        output_f.write(input_f.read())
        tf.logging.info(f'Saved model weights to {saved_weights_path}...')

if __name__ == '__main__':
    app.run(main)
