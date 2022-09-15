import tensorflow as tf
import tensorflow_text as tf_text

MAX_SEQ_LEN = 512
bert_tokenizer = tf_text.BertTokenizer(
    vocab_lookup_table='gs://weilin-bert-test/vocab.txt',
    token_out_type=tf.int64,
    lower_case=True)
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


def preprocess_bert_input(text, sequence_length=MAX_SEQ_LEN):
    """
    Convert input text into the input_word_ids, input_mask, input_type_ids
    """
    input_word_ids = tokenize_text(text, sequence_length)
    input_mask = tf.cast(input_word_ids > 0, tf.int64)
    input_mask = tf.reshape(input_mask, [-1, sequence_length])

    zeros_dims = tf.stack(tf.shape(input_mask))
    input_type_ids = tf.fill(zeros_dims, 0)
    input_type_ids = tf.cast(input_type_ids, tf.int64)

    return (tf.squeeze(input_word_ids, axis=0), tf.squeeze(input_mask, axis=0),
            tf.squeeze(input_type_ids, axis=0))


def preprocessing_fn(inputs):
    """Preprocess input column of text into transformed columns of.
        * input token ids
        * input mask
        * input type ids
    """

    input_word_ids, input_mask, input_type_ids = preprocess_bert_input(
        [inputs['data']['review_body']])

    return (dict({
        'input_ids': input_word_ids,
        'token_type_ids': input_type_ids,
        'attention_mask': input_mask
    }), inputs['data']['star_rating'])


def get_example_input(per_core_batch_size):
    string_inputs = 'I really do not want to type out 128 strings to create batch 128 data.',

    input_word_ids, input_mask, input_type_ids = preprocess_bert_input(
        string_inputs, sequence_length=384)
    input_ids = tf.repeat(tf.expand_dims(input_word_ids, axis=0),
                          per_core_batch_size,
                          axis=0,
                          name='input_ids/input_ids')
    input_masks = tf.repeat(tf.expand_dims(input_mask, axis=0),
                            per_core_batch_size,
                            axis=0,
                            name='input_ids/attention_mask')

    example_input = {'input_ids': input_ids, 'attention_mask': input_masks}
    return example_input
