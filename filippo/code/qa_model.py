from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import time
import logging

import numpy as np
from six.moves import xrange  # pylint: disable=redefined-builtin
import tensorflow as tf
from tensorflow.python.ops import variable_scope as vs

from util import Progbar, minibatches

from evaluate import exact_match_score, f1_score

#from IPython import embed

logging.basicConfig(level=logging.INFO)


def get_optimizer(opt):
    if opt == "adam":
        optfn = tf.train.AdamOptimizer
    elif opt == "sgd":
        optfn = tf.train.GradientDescentOptimizer
    else:
        assert (False)
    return optfn


class Encoder(object):
    """
    Arguments:
        -size: dimension of the hidden states
        -vocab_dim: dimension of the embeddings
    """
    def __init__(self, size, vocab_dim, name):
        self.size = size
        self.vocab_dim = vocab_dim
        self.name = name

    def encode(self, inputs, masks, encoder_state_input=None):
        """
        In a generalized encode function, you pass in your inputs,
        masks, and an initial
        hidden state input into this function.

        :param inputs: Symbolic representations of your input
        :param masks: this is to make sure tf.nn.dynamic_rnn doesn't iterate
                      through masked steps
        :param encoder_state_input: (Optional) pass this as initial hidden state
                                    to tf.nn.dynamic_rnn to build conditional representations
        :return: an encoded representation of your input.
                 It can be context-level representation, word-level representation,
                 or both.
        """
        with tf.variable_scope(self.name):
            cell = tf.nn.rnn_cell.BasicLSTMCell(self.size)
            outputs, final_state = tf.nn.dynamic_rnn(cell, inputs, 
                                           sequence_length=masks, 
                                           dtype=tf.float32,
                                           initial_state=encoder_state_input)
        return outputs, final_state

        # cell_fw = tf.nn.rnn_cell.BasicLSTMCell(self.size)
        # cell_bw = tf.nn.rnn_cell.BasicLSTMCell(self.size)


        # # TODO: see shape of output_states
        # # concatenate hidden vectors of both directions together
        # encoded_outputs = tf.concat(outputs, 2)

        # # return all hidden states and the final hidden state
        # return encoded_outputs, encoded_outputs[:, -1, :]


class Decoder(object):
    def __init__(self, output_size):
        self.output_size = output_size

    def decode(self, knowledge_rep):
        """
        takes in a knowledge representation
        and output a probability estimation over
        all paragraph tokens on which token should be
        the start of the answer span, and which should be
        the end of the answer span.

        :param knowledge_rep: it is a representation of the paragraph and question,
                              decided by how you choose to implement the encoder
        :return:
        """
        knowledge_rep = knowledge_rep[-1]
        input_size = knowledge_rep.get_shape()[-1]
        W_start = tf.get_variable("W_start", shape=(input_size, self.output_size),
                initializer=tf.contrib.layers.xavier_initializer())
        b_start = tf.get_variable("b_start", shape=(self.output_size))

        W_end = tf.get_variable("W_end", shape=(input_size, self.output_size),
                initializer=tf.contrib.layers.xavier_initializer())
        b_end = tf.get_variable("b_end", shape=(self.output_size))

        start_probs = tf.matmul(knowledge_rep, W_start) + b_start
        end_probs = tf.matmul(knowledge_rep, W_end) + b_end

        return start_probs, end_probs

class QASystem(object):
    def __init__(self, encoder, decoder, pretrained_embeddings, max_ctx_len, max_q_len, flags):
        """
        Initializes your System

        :param encoder: tuple of 2 encoders that you constructed in train.py
        :param decoder: a decoder that you constructed in train.py
        :param args: pass in more arguments as needed
        """
        self.pretrained_embeddings = pretrained_embeddings
        self.question_encoder, self.context_encoder = encoder # unpack tuple of encoders
        self.decoder = decoder
        self.max_ctx_len = max_ctx_len
        self.max_q_len = max_q_len
        self.embed_size = encoder[0].vocab_dim
        self.flags = flags
        # ==== set up placeholder tokens ========

        self.context_placeholder = tf.placeholder(tf.int32, shape=(None, self.max_ctx_len), name='context_placeholder')
        self.question_placeholder = tf.placeholder(tf.int32, shape=(None, self.max_q_len), name='question_placeholder')
        self.answer_span_placeholder = tf.placeholder(tf.int32, shape=(None, 2), name='answer_span_placeholder')
        self.mask_q_placeholder = tf.placeholder(tf.int32, shape=(None,), name='mask_q_placeholder')
        self.mask_ctx_placeholder = tf.placeholder(tf.int32, shape=(None,), name='mask_ctx_placeholder')
        self.dropout_placeholder = tf.placeholder(tf.float32, shape=(), name='dropout_placeholder')

        # ==== assemble pieces ====
        with tf.variable_scope("qa", initializer=tf.uniform_unit_scaling_initializer(1.0)):
            self.setup_embeddings()
            self.setup_system()
            self.setup_loss()

        # ==== set up training/updating procedure ====
        self.global_step = tf.Variable(0, trainable=False)
        self.starter_learning_rate = self.flags.learning_rate
        self.learning_rate = tf.train.exponential_decay(self.starter_learning_rate, self.global_step,
                                           100000, 0.96, staircase=True)
        self.optimizer = get_optimizer("adam")
        self.train_op = self.optimizer(self.learning_rate).minimize(self.loss)

    def pad(self, sequence,max_length):
        # assumes sequence is a list of lists of word, pads to the longest "sentence"
        # returns (padded_sequence, mask)
        from qa_data import PAD_ID
        #max_length = max(map(len, sequence))
        #print('max_length : {}'.format(max_length))
        padded_sequence = []
        mask = []
        for sentence in sequence:
            mask.append(len(sentence))
            sentence.extend([PAD_ID] * (max_length - len(sentence)))
            padded_sequence.append(sentence)
        return (padded_sequence, mask)

    # def setup_attention_vector(self, context_vectors, question_rep):
    #     #context_vectors is a list of the hidden states of the context
    #     #question_rep are the final forward and backward states of the encoder for the question concatenated
    #     #Does part 3 in original handout
    #     W = tf.get_variable("W", shape=[context_vectors[0].get_shape()[0], question_rep.get_shape()[0]],
    #                              initializer=tf.contrib.layers.xavier_initializer())
    #     #attention = [tf.nn.softmax(tf.matmul(tf.matmul(tf.transpose(ctx), W), question_rep)) for ctx in context_vectors]
        

    #     # TODO: ask TA how to handle batch size stuff here...
    #     attention = tf.nn.softmax(tf.sum(tf.matmul(tf.matmul(question_rep, W), context_vectors)))
    #     return attention

    # def concat_most_aligned(self, question_states, cur_ctx):
    #     #Does part 4 in original handout
    #     #question_states is a list of all of the hidden states for the question, cur_ctx is the current context word
    #     #returns a concatenation of [cur_ctx, q*] where q* is the most aligned question word
    #     U = tf.get_variable("U", shape=[cur_ctx.get_shape()[0], question_states[0].get_shape()[0]],
    #                              initializer=tf.contrib.layers.xavier_initializer())#maybe need to add reuse variable?
    #     attention = [tf.nn.softmax(tf.matmul(tf.matmul(tf.transpose(cur_ctx), W), q)) for q in question_states]
    #     most_aligned = (0.0, None)

    #     # TODO: change this to completely use tensorflow functions (like argmax)
    #     for i in range(len(attention)):
    #         if attention[i] > most_aligned:
    #             most_aligned = (attention[i], question_states[i])
    #     return tf.concat([cur_ctx,most_aligned[0]], 1)


    def setup_system(self):
        """
        After your modularized implementation of encoder and decoder
        you should call various functions inside encoder, decoder here
        to assemble your reading comprehension system!
        :return:
        """

        # simple encoder stuff here
        question_states, final_question_state = self.question_encoder.encode(self.question_embeddings, self.mask_q_placeholder, None)
        ctx_states, final_ctx_state = self.context_encoder.encode(self.context_embeddings, self.mask_ctx_placeholder, final_question_state)

        # decoder takes encoded representation to probability dists over start / end index
        self.start_probs, self.end_probs = self.decoder.decode(final_ctx_state)

        # TODO: put predictions here?




        # TODO: is this correct for the baseline?
        # question_states, question_rep = self.question_encoder.encode(self.question_placeholder, self.mask_q_placeholder, None)
        # ctx_states, ctx_rep = self.context_encoder.encode(self.context_placeholder, self.mask_ctx_placeholder, None)
        # attention = setup_attention_vector(question_rep, ctx_states)
        # weighted_ctx = tf.matmul(self.question_placeholder, attention)#(hidden_size x max_ctx_len) (max_ctx_len x 1)=>(hidden_size x 1)
        
        # TODO: how to do stuff like packing operations together
        #new_ctx = [self.concat_most_aligned(question_states, ctx) for ctx in ctx_states]

    def setup_loss(self):
        """
        Set up your loss computation here
        :return:
        """
        with vs.variable_scope("loss"):
            self.loss = tf.reduce_mean(tf.nn.sparse_softmax_cross_entropy_with_logits(self.start_probs, self.answer_span_placeholder[:, 0])) + \
                        tf.reduce_mean(tf.nn.sparse_softmax_cross_entropy_with_logits(self.end_probs, self.answer_span_placeholder[:, 1]))
            #pass

    def setup_embeddings(self):
        """
        Loads distributed word representations based on placeholder tokens
        :return:
        """
        with vs.variable_scope("embeddings"):
            embeddings = tf.Variable(self.pretrained_embeddings, name='embedding', dtype=tf.float32) #only learn one common embedding

            question_embeddings = tf.nn.embedding_lookup(embeddings, self.question_placeholder)
            self.question_embeddings = tf.reshape(question_embeddings, [-1, self.max_q_len, self.embed_size])

            context_embeddings = tf.nn.embedding_lookup(embeddings, self.context_placeholder)
            self.context_embeddings = tf.reshape(context_embeddings, [-1, self.max_ctx_len, self.embed_size])


    def optimize(self, session, context_batch, question_batch, answer_span_batch, mask_ctx_batch, mask_q_batch):
        """
        Takes in actual data to optimize your model
        This method is equivalent to a step() function
        :return:
        """
        input_feed = {}

        # fill in this feed_dictionary like:
        # input_feed['train_x'] = train_x

        input_feed[self.context_placeholder] = context_batch
        input_feed[self.question_placeholder] = question_batch
        input_feed[self.mask_ctx_placeholder] = mask_ctx_batch
        input_feed[self.mask_q_placeholder] = mask_q_batch
        input_feed[self.dropout_placeholder] = self.flags.dropout
        input_feed[self.answer_span_placeholder] = answer_span_batch

        output_feed = [self.train_op, self.loss]

        _, loss = session.run(output_feed, input_feed)

        return loss

    # def test(self, session, valid_x, valid_y):
    #     """
    #     in here you should compute a cost for your validation set
    #     and tune your hyperparameters according to the validation set performance
    #     :return:
    #     """
    #     input_feed = {}

    #     # fill in this feed_dictionary like:
    #     # input_feed['valid_x'] = valid_x

    #     input_feed[self.context_placeholder] = valid_x[:][0]
    #     input_feed[self.question_placeholder] = valid_x[:][1]
    #     input_feed[self.mask_ctx_placeholder] = valid_x[:][2]
    #     input_feed[self.mask_q_placeholder] = valid_x[:][3]
    #     input_feed[self.dropout_placeholder] = self.flags.dropout
    #     input_feed[self.answer_span_placeholder] = valid_y

    #     # TODO: compute cost for validation set, tune hyperparameters

    #     output_feed = [self.loss]

    #     outputs = session.run(output_feed, input_feed)

    #     return outputs

    def test(self, session, context_batch, question_batch, answer_span_batch, mask_ctx_batch, mask_q_batch):
        """
        in here you should compute a cost for your validation set
        and tune your hyperparameters according to the validation set performance
        :return:
        """
        input_feed = {}

        # fill in this feed_dictionary like:
        # input_feed['valid_x'] = valid_x

        input_feed[self.context_placeholder] = context_batch
        input_feed[self.question_placeholder] = question_batch
        input_feed[self.mask_ctx_placeholder] = mask_ctx_batch
        input_feed[self.mask_q_placeholder] = mask_q_batch
        input_feed[self.dropout_placeholder] = self.flags.dropout
        input_feed[self.answer_span_placeholder] = answer_span_batch

        # TODO: compute cost for validation set, tune hyperparameters

        output_feed = [self.loss]

        outputs = session.run(output_feed, input_feed)

        return outputs

    def decode(self, session, test_x):
        """
        Returns the probability distribution over different positions in the paragraph
        so that other methods like self.answer() will be able to work properly
        :return:
        """
        input_feed = {}

        # fill in this feed_dictionary like:
        # input_feed['test_x'] = test_x

        input_feed[self.context_placeholder] = test_x[:][0]
        input_feed[self.question_placeholder] = test_x[:][1]
        input_feed[self.mask_ctx_placeholder] = test_x[:][2]
        input_feed[self.mask_q_placeholder] = test_x[:][3]
        input_feed[self.dropout_placeholder] = self.flags.dropout

        output_feed = [self.start_probs, self.end_probs]

        outputs = session.run(output_feed, input_feed)

        return outputs

    def answer(self, session, test_x):

        yp, yp2 = self.decode(session, test_x)

        a_s = np.argmax(yp, axis=1)
        a_e = np.argmax(yp2, axis=1)

        return a_s, a_e

    #def validate(self, sess, valid_dataset):
    def validate(self, sess, context_batch, question_batch, answer_span_batch, mask_ctx_batch, mask_q_batch):
        """
        Iterate through the validation dataset and determine what
        the validation cost is.

        This method calls self.test() which explicitly calculates validation cost.

        How you implement this function is dependent on how you design
        your data iteration function

        :return:
        """
        valid_cost = 0
        x = context_batch,question_batch,mask_ctx_batch,mask_q_batch
        y = answer_span_batch
        # valid_dataset = zip(x,y)

        # for valid_x, valid_y in valid_dataset:
        #   valid_cost += self.test(sess, valid_x, valid_y)

        valid_cost = self.test(sess, context_batch, question_batch, answer_span_batch, mask_ctx_batch, mask_q_batch)


        return valid_cost

    def evaluate_answer(self, session, dataset, sample=100, log=False):
        """
        Evaluate the model's performance using the harmonic mean of F1 and Exact Match (EM)
        with the set of true answer labels

        This step actually takes quite some time. So we can only sample 100 examples
        from either training or testing set.

        :param session: session should always be centrally managed in train.py
        :param dataset: a representation of our data, in some implementations, you can
                        pass in multiple components (arguments) of one dataset to this function
        :param sample: how many examples in dataset we look at
        :param log: whether we print to std out stream
        :return:
        """

        # iterate over dataset, calling answer method

        # TODO: do we have to loop here over batches?
        # TODO: be explicit about structure of dataset input for all of these functions.

        a_s, a_e = self.answer(session, dataset[:4])


        f1 = f1_score([a_s,a_e],dataset[2])
        em = exact_match_score([a_s,a_e],dataset[2])

        if log:
            logging.info("F1: {}, EM: {}, for {} samples".format(f1, em, sample))

        return f1, em


    ### Imported from NERModel
    def run_epoch(self, sess, train_examples, dev_set):
        # prog_train = Progbar(target=1 + int(len(train_examples) / self.flags.batch_size))
        # for i, batch in enumerate(minibatches(train_examples, self.flags.batch_size)):
        #     loss = self.optimize(sess, *batch)
        #     prog_train.update(i + 1, [("train loss", loss)])
        # print("")


        prog_val = Progbar(target=1 + int(len(dev_set) / self.flags.batch_size))
        for i, batch in enumerate(minibatches(dev_set, self.flags.batch_size)):
        	val_loss = self.validate(sess, *batch)
        	val_f1, val_em = self.evaluate_answer(sess,dev_set)
        	prog_val.update(i + 1, [("val loss", val_loss)])
        	prog_val.update(i + 1, [("val f1", val_f1)])
        	prog_val.update(i + 1, [("val em", val_em)])

        #logger.info("Evaluating on training data")
        #token_cm, entity_scores = self.evaluate(sess, train_examples, train_examples_raw)
        #logger.debug("Token-level confusion matrix:\n" + token_cm.as_table())
        #logger.debug("Token-level scores:\n" + token_cm.summary())
        #logger.info("Entity level P/R/F1: %.2f/%.2f/%.2f", *entity_scores)

        # logger.info("Evaluating on development data")
        # token_cm, entity_scores = self.evaluate(sess, dev_set, dev_set_raw)
        # logger.debug("Token-level confusion matrix:\n" + token_cm.as_table())
        # logger.debug("Token-level scores:\n" + token_cm.summary())
        # logger.info("Entity level P/R/F1: %.2f/%.2f/%.2f", *entity_scores)

        # f1 = entity_scores[-1]
        # return f1

    # def evaluate(self, sess, examples, examples_raw):
    #     """Evaluates model performance on @examples.

    #     This function uses the model to predict labels for @examples and constructs a confusion matrix.

    #     Args:
    #         sess: the current TensorFlow session.
    #         examples: A list of vectorized input/output pairs.
    #         examples: A list of the original input/output sequence pairs.
    #     Returns:
    #         The F1 score for predicting tokens as named entities.
    #     """
    #     token_cm = ConfusionMatrix(labels=LBLS)

    #     correct_preds, total_correct, total_preds = 0., 0., 0.
    #     for _, labels, labels_  in self.output(sess, examples_raw, examples):
    #         for l, l_ in zip(labels, labels_):
    #             token_cm.update(l, l_)
    #         gold = set(get_chunks(labels))
    #         pred = set(get_chunks(labels_))
    #         correct_preds += len(gold.intersection(pred))
    #         total_preds += len(pred)
    #         total_correct += len(gold)

    #     p = correct_preds / total_preds if correct_preds > 0 else 0
    #     r = correct_preds / total_correct if correct_preds > 0 else 0
    #     f1 = 2 * p * r / (p + r) if correct_preds > 0 else 0
    #     return token_cm, (p, r, f1)

    # def consolidate_predictions(self, examples_raw, examples, preds):
    #     """Batch the predictions into groups of sentence length.
    #     """
    #     assert len(examples_raw) == len(examples)
    #     assert len(examples_raw) == len(preds)

    #     ret = []
    #     for i, (sentence, labels) in enumerate(examples_raw):
    #         _, _, mask = examples[i]
    #         labels_ = [l for l, m in zip(preds[i], mask) if m] # only select elements of mask.
    #         assert len(labels_) == len(labels)
    #         ret.append([sentence, labels, labels_])
    #     return ret

    # def predict_on_batch(self, sess, inputs_batch, mask_batch):
    #     feed = self.create_feed_dict(inputs_batch=inputs_batch, mask_batch=mask_batch)
    #     predictions = sess.run(tf.argmax(self.pred, axis=2), feed_dict=feed)
    #     return predictions

    # def output(self, sess, inputs_raw, inputs=None):
    #     """
    #     Reports the output of the model on examples (uses helper to featurize each example).
    #     """
    #     if inputs is None:
    #         inputs = self.preprocess_sequence_data(self.helper.vectorize(inputs_raw))

    #     preds = []
    #     prog = Progbar(target=1 + int(len(inputs) / self.config.batch_size))
    #     for i, batch in enumerate(minibatches(inputs, self.config.batch_size, shuffle=False)):
    #         # Ignore predict
    #         batch = batch[:1] + batch[2:]
    #         preds_ = self.predict_on_batch(sess, *batch)
    #         preds += list(preds_)
    #         prog.update(i + 1, [])
    #     return self.consolidate_predictions(inputs_raw, inputs, preds)    




    def train(self, session, saver, dataset, train_dir):
        """
        Implement main training loop

        TIPS:
        You should also implement learning rate annealing (look into tf.train.exponential_decay)
        Considering the long time to train, you should save your model per epoch.

        More ambitious approach can include implement early stopping, or reload
        previous models if they have higher performance than the current one

        As suggested in the document, you should evaluate your training progress by
        printing out information every fixed number of iterations.

        We recommend you evaluate your model performance on F1 and EM instead of just
        looking at the cost.

        :param session: it should be passed in from train.py
        :param dataset: a representation of our data, in some implementations, you can
                        pass in multiple components (arguments) of one dataset to this function
        :param train_dir: path to the directory where you should save the model checkpoint
        :return:
        """



        # some free code to print out number of parameters in your model
        # it's always good to check!
        # you will also want to save your model parameters in train_dir
        # so that you can use your trained model to make predictions, or
        # even continue training

        tic = time.time()
        params = tf.trainable_variables()
        num_params = sum(map(lambda t: np.prod(tf.shape(t.value()).eval()), params))
        toc = time.time()
        logging.info("Number of params: %d (retreival took %f secs)" % (num_params, toc - tic))

        train_dataset = dataset[0]
        val_dataset = dataset[1]
        train_mask = [None, None]
        val_mask = [None, None]
        train_dataset[0], train_mask[0] = self.pad(train_dataset[0],self.max_ctx_len) #train_context_ids
        train_dataset[1], train_mask[1] = self.pad(train_dataset[1],self.max_q_len) #train_question_ids

        val_dataset[0], val_mask[0] = self.pad(val_dataset[0],self.max_ctx_len) #val_context_ids
        val_dataset[1], val_mask[1] = self.pad(val_dataset[1],self.max_q_len) #val_question_ids


        for i in range(1,len(train_dataset[0])):
            assert len(train_dataset[0][i]) == len(train_dataset[0][i - 1]), "Incorrectly padded train context"
            assert len(train_dataset[1][i]) == len(train_dataset[1][i - 1]), "Incorrectly padded train question"

        for i in range(1,len(val_dataset[0])):
            assert len(val_dataset[0][i]) == len(val_dataset[0][i - 1]), "Incorrectly padded val context"
            assert len(val_dataset[1][i]) == len(val_dataset[1][i - 1]), "Incorrectly padded val question"

        print("Training/val data padding verification completed.")
        
        train_dataset.extend(train_mask)
        val_dataset.extend(val_mask)
        train_dataset = np.array(train_dataset).T
        val_dataset = np.array(val_dataset).T

        for epoch in range(self.flags.epochs):
            logging.info("Epoch %d out of %d", epoch + 1, self.flags.epochs)
            self.run_epoch(sess=session, train_examples=train_dataset, dev_set=val_dataset)
            logging.info("Saving model in %s", train_dir)
            saver.save(session, train_dir)





