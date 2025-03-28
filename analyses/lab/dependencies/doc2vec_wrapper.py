# https://github.com/RaRe-Technologies/gensim/blob/develop/docs/notebooks/doc2vec-IMDB.ipynb
# https://groups.google.com/forum/#!msg/word2vec-toolkit/Q49FIrNOQRo/J6KG8mUj45sJ
import cPickle
import logging
import multiprocessing
import random
from copy import deepcopy
from os import listdir
from os.path import isfile, join

import numpy as np
import pandas as pd
from gensim.models import doc2vec

import parsing_utils as parsing

logging.basicConfig(format='%(asctime)s : %(levelname)s : %(message)s', level=logging.INFO)


class D2VWrapper:
    # content_categories might be set from outer scope
    content_categories = None
    all_content_tagged_docs = None
    docs_category_mapping = None
    inferred_vectors = None
    header_docs = None

    def __init__(self, content_categories=None, vector_length=300, window=8, train_algo="dbow"):
        self.base_doc2vec_model = doc2vec.Doc2Vec(dm=1 if train_algo == "dm" else 0, size=vector_length, negative=12,
                                                  hs=0, min_count=5, workers=multiprocessing.cpu_count(),
                                                  alpha=0.1, window=window)

        if content_categories:
            self.content_categories = content_categories

    def init_model_vocab(self, content_basepath, basepath_suffix="_content.csv", drop_short_docs=False):
        # infer the trained categories according to the files in directory
        dir_files = [f for f in listdir(content_basepath) if isfile(join(content_basepath, f))]
        self.content_categories = map(
            lambda dir_file_path: dir_file_path.replace(content_basepath, "").replace(basepath_suffix, ""),
            dir_files)

        # initializes the vocabulary by the given categories (content_categories)
        # in the given directory (content_basepath)
        assert doc2vec.FAST_VERSION > -1, "this will be painfully slow otherwise"

        all_content_df = parsing.get_content_as_dataframe(content_basepath, basepath_suffix, self.content_categories).drop_duplicates()

        # fills up the mapping of document ids (index) to its original categories
        # enabling the reverse search of all_base_vocab_docs vectors for each category and subsequent classification
        self.docs_category_mapping = pd.Series(data=all_content_df["target"])

        # selects a text of the most relevant attributes filled for each item of dataframe
        # (in format title, desc, plaintext_content)
        all_content_sens, _ = parsing.select_training_content(all_content_df, make_document_mapping=True, sent_split=False)
        all_content_headers = parsing.select_headers(all_content_df)

        logging.info("Loaded %s all_base_vocab_docs from %s categories" % (len(all_content_sens), len(self.content_categories)))

        # filter short all_base_vocab_docs from training, if required
        if drop_short_docs:
            logging.info("Filtering all_base_vocab_docs shorter than %s tokens from vocab sample" % drop_short_docs)

            content_lens = all_content_sens.apply(lambda content: len(content))
            ok_indices = content_lens >= drop_short_docs

            all_content_sens = all_content_sens[ok_indices]
            self.docs_category_mapping = self.docs_category_mapping[ok_indices.values]

            logging.info("%s all_base_vocab_docs included in vocab init" % self.docs_category_mapping.__len__())

        # transform the training sentences into TaggedDocument list
        self.all_content_tagged_docs = parsing.tagged_docs_from_content(all_content_sens,
                                                                        all_content_headers,
                                                                        self.docs_category_mapping)
        self.all_content_tagged_docs = self.all_content_tagged_docs.reset_index(drop=True)

        self.init_vocab_from_docs()

    def init_vocab_from_docs(self, docs=None):
        if docs is not None:
            self.all_content_tagged_docs = docs

        # derive training all_base_vocab_docs of header content and push it into model vocabulary
        self.header_docs = parsing.parse_header_docs(self.all_content_tagged_docs)

        self.base_doc2vec_model.build_vocab(self.all_content_tagged_docs.append(self.header_docs))

        # after this step, vectors are already inferable - though the docs vectors needs to be embedded in train_model()
        # all_base_vocab_docs vectors should be retrieved first after the training

    def train_model(self, shuffle=True, epochs=10):
        # now training on headers as well

        if self.all_content_tagged_docs is None:
            logging.error("D2V vocabulary not initialized. Training must follow the init_model_vocab()")
            return
        for epoch in range(epochs):
            logging.info("Training D2V model %s" % self.base_doc2vec_model)
            logging.info("Epoch %s convergence descent alpha: %s" % (epoch, self.base_doc2vec_model.alpha))

            # shuffle support
            train_ordered_tagged_docs = deepcopy(self.all_content_tagged_docs.values)
            train_ordered_headers = deepcopy(self.header_docs.values)
            if shuffle and epoch > 0:
                # shuffling is time-consuming and is not necessary in the first epoch (current order not seen before)
                random.shuffle(train_ordered_tagged_docs)
                random.shuffle(train_ordered_headers)
            else:
                train_ordered_tagged_docs = self.all_content_tagged_docs
            # self.base_doc2vec_model.infer_vector(self.base_doc2vec_model.vocab.keys()[:50][0:10])

            self.base_doc2vec_model.train(pd.Series(train_ordered_tagged_docs).append(pd.Series(train_ordered_headers)))
            # self.base_doc2vec_model.train(train_ordered_headers)

    def persist_trained_wrapper(self, model_save_dir, model_only=False):
        # if persisting folder does not exist, create it - Service layer will take care of it
        if not model_only:
            logging.info("Serializing wrapper model to: %s" % model_save_dir)

            logging.info("Persisting all_base_vocab_docs objects")
            with open(model_save_dir + "/doc_labeling.mod", "w") as pickle_file_writer:
                cPickle.dump(self.all_content_tagged_docs, pickle_file_writer)

            logging.info("Persisting inferred vectors")
            with open(model_save_dir + "/doc_vectors.mod", "w") as pickle_file_writer:
                cPickle.dump(self.inferred_vectors, pickle_file_writer)

        logging.info("Persisting trained Doc2Vec model")
        self.base_doc2vec_model.save(model_save_dir + "/doc2vec.mod")

    def load_persisted_wrapper(self, model_save_dir, model_only=False):
        logging.info("Loading serialized wrapper model from: %s" % model_save_dir)

        if not model_only:
            logging.info("Loading all_base_vocab_docs objects")
            with open(model_save_dir + "/doc_labeling.mod", "r") as pickle_file_reader:
                self.all_content_tagged_docs = cPickle.load(pickle_file_reader)

            self.content_categories = self.all_content_tagged_docs.apply(lambda doc: doc.category_expected).unique()

            # header content parse from base all_base_vocab_docs objects
            self.header_docs = parsing.parse_header_docs(self.all_content_tagged_docs)

            logging.info("Loading all_base_vocab_docs vectors")
            with open(model_save_dir + "/doc_vectors.mod", "r") as pickle_file_reader:
                self.inferred_vectors = cPickle.load(pickle_file_reader)

        logging.info("Loading trained Doc2Vec model")
        self.base_doc2vec_model = doc2vec.Doc2Vec.load(model_save_dir + "/doc2vec.mod")

    def infer_vocab_content_vectors(self, new_inference=False, category=None, infer_steps=10):
        if not new_inference:
            if self.inferred_vectors is not None:
                logging.info("Returning already inferred doc vectors of %s all_base_vocab_docs" % len(self.inferred_vectors))
                return self.inferred_vectors

        logging.info("Docs vector inference started")
        if category is None:
            # inference with default aprams config
            # TODO: try other inference params on new inference
            self.inferred_vectors = self.infer_content_vectors(self.all_content_tagged_docs, infer_steps=infer_steps)

            self.inferred_vectors["y"] = [doc.category_expected for doc in self.all_content_tagged_docs]

            return self.inferred_vectors
        else:
            # returns vectors of only a particular category
            # implement if needed
            return

    # gets a pd.Series of CategorizedDocument-s with unfilled categories
    # returns a vectors matrix for a content of the input CategorizedDpcument-s in the same order
    def infer_content_vectors(self, docs, infer_alpha=0.05, infer_subsample=0.05, infer_steps=10):
        # that might probably be tested on already classified data
        header_docs = parsing.parse_header_docs(docs)

        logging.info("Inferring vectors of %s documents" % len(docs))
        content_vectors = [self.base_doc2vec_model.infer_vector(doc.words, infer_alpha, infer_subsample, infer_steps)
                           for doc in docs]

        # header vectors inference
        logging.info("Inferring vectors of %s headers" % len(header_docs))
        header_vectors = [self.base_doc2vec_model.infer_vector(doc.words, infer_alpha, infer_subsample, infer_steps)
                          for doc in header_docs]

        content_vectors_df = pd.DataFrame(content_vectors)
        header_vectors_df = pd.DataFrame(header_vectors)
        new_inferred_vectors = pd.concat([content_vectors_df, header_vectors_df], axis=1)

        # rename vector columns incrementally - columns are required tu have unique id by NN classifier
        new_inferred_vectors.columns = np.arange(len(new_inferred_vectors.columns))

        return new_inferred_vectors

    def get_doc_content(self, index, word_split=False):
        if word_split:
            return self.all_content_tagged_docs.iloc[index].words
        else:
            return parsing.content_from_words(self.all_content_tagged_docs.iloc[index].words)

