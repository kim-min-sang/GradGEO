"""BM25 for arbitrary text against an existing Lucene field's statistics (Lucene 9 BM25Similarity, k1=0.9, b=0.4).

Field length is quantized like Lucene's norms (SmallFloat.intToByte4 / byte4ToInt), so scores match the index.
Editing one document changes collection statistics by ~1/150,000, which is ignored.
"""
import math
from collections import Counter
from jnius import autoclass
from pyserini.analysis import Analyzer, get_lucene_analyzer
from pyserini.index.lucene import LuceneIndexReader

JTerm = autoclass("org.apache.lucene.index.Term")
_analyzer = Analyzer(get_lucene_analyzer())
K1, B = 0.9, 0.4
NUM_FREE = 24


def _long_to_int4(i):
    nbits = i.bit_length()
    if nbits < 4:
        return i
    shift = nbits - 4
    return ((i >> shift) & 0x07) | ((shift + 1) << 3)


def _int4_to_long(i):
    bits, shift = i & 0x07, (i >> 3) - 1
    return bits if shift == -1 else (bits | 0x08) << shift


def quantize(length):
    if length < NUM_FREE:
        return length
    return NUM_FREE + _int4_to_long(_long_to_int4(length - NUM_FREE))


def tokens(text):
    return _analyzer.analyze(text or "")


class Field:
    def __init__(self, index_dir, field="contents"):
        self.reader = LuceneIndexReader(str(index_dir)).reader
        self.field = field
        self.n = self.reader.getDocCount(field)
        self.avgdl = self.reader.getSumTotalTermFreq(field) / self.n
        self._idf = {}

    def idf(self, term):
        if term not in self._idf:
            df = self.reader.docFreq(JTerm(self.field, term))
            self._idf[term] = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
        return self._idf[term]

    def score(self, query_counts, text_tokens):
        tf = Counter(text_tokens)
        norm = K1 * (1 - B + B * quantize(len(text_tokens)) / self.avgdl)
        return sum(qc * self.idf(t) * tf[t] / (tf[t] + norm) for t, qc in query_counts.items() if tf[t])


def query_counts(q):
    return Counter(tokens(q))
