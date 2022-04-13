import tensorflow_datasets as tfds
import tensorflow as tf
import tensorflow_text as tf_text
from transformers import TFDistilBertForSequenceClassification
from transformers import TFBertForSequenceClassification

tpu = tf.distribute.cluster_resolver.TPUClusterResolver()
tf.config.experimental_connect_to_cluster(tpu)
tf.tpu.experimental.initialize_tpu_system(tpu)
strategy = tf.distribute.experimental.TPUStrategy(tpu)

ds_train, ds_info = tfds.load('amazon_us_reviews/Books_v1_02',
                              split='train[:5%]',
                              with_info=True,
                              data_dir="gs://weilin-bert-test")

MAX_SEQ_LEN = 512
bert_tokenizer = tf_text.BertTokenizer(vocab_lookup_table='gs://weilin-bert-test/vocab.txt',
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
                           axis=0), tf.squeeze(input_mask,
                                               axis=0), tf.squeeze(input_type_ids, axis=0))

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
    return (dict(tokenizer(example['data']['review_body'].numpy().decode('utf-8')),
                 truncation=True,
                 padding=True), example['data']['star_rating'].numpy())


def process_py(inp1, inp2):
    return [
        dict(tokenizer(inp1.numpy().decode('utf-8')), truncation=True, padding=True),
        inp2.numpy()
    ]


ds_train_filtered_2 = ds_train_filtered.map(preprocessing_fn)

tf.keras.mixed_precision.experimental.set_policy('mixed_bfloat16')

with strategy.scope():
    model = TFBertForSequenceClassification.from_pretrained('bert-base-uncased', num_labels=1)

    optimizer = tf.keras.optimizers.Adam(learning_rate=5e-5)
    model.compile(optimizer=optimizer, loss=model.compute_loss)  # can also use any keras loss fn
    model.summary()

inuse_dataset = ds_train_filtered_2.shuffle(1000).batch(256).prefetch(tf.data.experimental.AUTOTUNE)
model.fit(inuse_dataset, epochs=1, batch_size=256)
