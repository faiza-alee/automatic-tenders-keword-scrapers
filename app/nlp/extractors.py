"""Keyword extractors. All return the same shape so they can be swapped via KEYWORD_METHOD."""
import re
from dataclasses import dataclass

# Words that are boilerplate in virtually every procurement notice and say nothing about *what* is bought.
BOILERPLATE = set("""tender tenders sealed bid bids bidding invites invited inviting notice ppra rules rule government
gilgit baltistan pakistan authority procuring agency reserves right reject accept accepting officer firms firm contractors
contractor suppliers supplier interested eligible following date time hours closing opening submission documents document
fee security deposit per year financial office department section page also available website www org pk gov http https
pec registered category categories work works required details shall must may will please
 public regulatory camscanner scanned procurement items item misc govt division executive engineer deputy assistant
 director commissioner dated subject""".split())


@dataclass
class ExtractedKeyword:
    term: str
    score: float  # 0..1, higher is better
    snippet: str | None = None


def _ok(term: str) -> bool:
    toks = re.findall(r"[a-z0-9\-/]+", term.lower())
    if not toks or len(term) < 3 or len(term) > 80:
        return False
    if all(t.isdigit() for t in toks) or all(t in BOILERPLATE for t in toks):
        return False
    return toks[0] not in BOILERPLATE or len(toks) > 1 and toks[-1] not in BOILERPLATE


def snippet_for(term: str, text: str, width: int = 280) -> str | None:
    m = re.search(re.escape(term), text, flags=re.I)
    if not m:
        return None
    start = max(0, m.start() - width // 2)
    end = min(len(text), m.end() + width // 2)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


class BaseExtractor:
    name = "base"
    version = "base"

    def extract(self, text: str, top_n: int = 15) -> list[ExtractedKeyword]:
        raise NotImplementedError


class YakeExtractor(BaseExtractor):
    name = "yake"

    def __init__(self, ngram: int = 3):
        import yake
        self.version = f"yake-{getattr(yake, '__version__', '0')}"
        self._kw = yake.KeywordExtractor(lan="en", n=ngram, dedupLim=0.7, top=40)

    def extract(self, text: str, top_n: int = 15) -> list[ExtractedKeyword]:
        out = []
        for term, score in self._kw.extract_keywords(text):  # lower score = better in YAKE
            if _ok(term):
                out.append(ExtractedKeyword(term.lower(), float(1.0 / (1.0 + 10.0 * float(score))), snippet_for(term, text)))
        return out[:top_n]


class TfidfExtractor(BaseExtractor):
    """Sentence-level TF-IDF: each sentence is a 'document', so terms concentrated in few sentences rank higher.
    Pass a `corpus` (list of past tender texts) to use real corpus statistics."""
    name = "tfidf"
    version = "tfidf-sklearn"

    def __init__(self, corpus: list[str] | None = None):
        self.corpus = corpus

    def extract(self, text: str, top_n: int = 15) -> list[ExtractedKeyword]:
        from sklearn.feature_extraction.text import TfidfVectorizer
        docs = self.corpus or [s for s in re.split(r"[.\n;]+", text) if len(s.split()) >= 2]
        if len(docs) < 2:
            docs = [text, text + " ."]
        vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z\-]{2,}\b")
        vec.fit(docs)
        weights = vec.transform([text]).toarray()[0]
        terms = vec.get_feature_names_out()
        ranked = sorted(zip(terms, weights), key=lambda x: -x[1])
        mx = ranked[0][1] if ranked and ranked[0][1] > 0 else 1.0
        out = [ExtractedKeyword(t, float(w / mx), snippet_for(t, text)) for t, w in ranked if w > 0 and _ok(t)]
        return out[:top_n]


class KeyBertExtractor(BaseExtractor):
    """Optional (heavy): pip install keybert sentence-transformers"""
    name = "keybert"
    version = "keybert-all-MiniLM-L6-v2"

    def __init__(self):
        from keybert import KeyBERT
        self._kb = KeyBERT("all-MiniLM-L6-v2")

    def extract(self, text: str, top_n: int = 15) -> list[ExtractedKeyword]:
        res = self._kb.extract_keywords(text, keyphrase_ngram_range=(1, 3), stop_words="english", top_n=top_n * 2)
        return [ExtractedKeyword(t.lower(), float(s), snippet_for(t, text)) for t, s in res if _ok(t)][:top_n]


def get_extractor(method: str) -> BaseExtractor:
    method = (method or "yake").lower()
    if method == "yake":
        return YakeExtractor()
    if method == "tfidf":
        return TfidfExtractor()
    if method == "keybert":
        return KeyBertExtractor()
    raise ValueError(f"unknown keyword method '{method}' (use yake | tfidf | keybert)")
