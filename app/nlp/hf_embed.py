"""Semantic (KeyBERT-style) keyword extraction using Hugging Face Inference Providers embeddings.

Candidate phrases come from the tender text; the phrases whose embedding is closest to the whole tender's embedding
win, with MMR so the list is not ten near-copies of one phrase. The token is read from HF_TOKEN (see app/config.py),
is never logged, never stored in the database and is scrubbed from every error message this module raises.
If Hugging Face is unreachable the pipeline silently falls back to YAKE, so scraping never stops.
"""
import logging
import re
import time

import numpy as np
import requests

from app.nlp.extractors import BaseExtractor, ExtractedKeyword, YakeExtractor, _ok, snippet_for

log = logging.getLogger(__name__)
MAX_INPUT_CHARS = 2000  # bge-small reads at most 512 tokens


class HFError(Exception):
    """Raised for any Hugging Face problem. Messages never contain the token."""


class HFEmbeddingClient:
    def __init__(self, token: str, model: str = "BAAI/bge-small-en-v1.5",
                 api_base: str = "https://router.huggingface.co/hf-inference/models", timeout: int = 30,
                 session: requests.Session | None = None, sleep=time.sleep, max_retries: int = 3, batch_size: int = 32):
        self._token = token
        self.model, self.timeout, self.max_retries, self.batch_size = model, timeout, max_retries, batch_size
        self.session, self._sleep = session or requests.Session(), sleep
        base = api_base.rstrip("/")
        self._urls = [f"{base}/{model}/pipeline/feature-extraction", f"{base}/{model}"]  # second = older route
        self._url_idx = 0
        self.calls = 0

    def _scrub(self, text) -> str:
        return str(text).replace(self._token, "***") if self._token else str(text)

    def _post(self, texts: list[str]):
        last = "unknown"
        for attempt in range(1, self.max_retries + 1):
            try:
                self.calls += 1
                r = self.session.post(self._urls[self._url_idx], timeout=self.timeout, json={"inputs": texts},
                                      headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"})
            except requests.RequestException as e:
                last = f"network: {type(e).__name__}"
            else:
                code = r.status_code
                if code == 200:
                    return r.json()
                if code in (401, 403):
                    raise HFError(f"Hugging Face ne token qabool nahi kiya (HTTP {code}); HF_TOKEN aur uski "
                                  "'Inference Providers' ijazat check karein")
                if code in (404, 405) and self._url_idx + 1 < len(self._urls):
                    self._url_idx += 1  # try the other route once
                    continue
                if code == 429 or code >= 500:  # busy or model loading: wait and retry
                    last = f"HTTP {code}"
                    wait = r.headers.get("Retry-After", "")
                    if wait.isdigit():
                        self._sleep(min(int(wait), 30))
                else:
                    raise HFError(f"Hugging Face ka jawab HTTP {code}: {self._scrub(r.text[:200])}")
            if attempt < self.max_retries:
                self._sleep(min(2 ** attempt, 20))
        raise HFError(f"Hugging Face tak nahi pohancha ja saka ({last})")

    @staticmethod
    def _parse(data, n: int) -> list[np.ndarray]:
        if isinstance(data, dict):
            raise HFError("Hugging Face ne embeddings ki jagah error bheja")
        if data and isinstance(data[0], (int, float)):
            data = [data]  # a single flat vector
        out = []
        for item in data:
            a = np.asarray(item, dtype=float)
            if a.ndim == 2:  # token-level vectors: average them
                a = a.mean(axis=0)
            elif a.ndim != 1:
                raise HFError("Hugging Face ke jawab ki shakl samajh nahi aayi")
            out.append(a)
        if len(out) != n:
            raise HFError(f"{n} texts bheje, {len(out)} embeddings wapas aaye")
        return out

    def embed(self, texts: list[str]) -> np.ndarray:
        """L2-normalised embeddings, shape (len(texts), dim)."""
        vecs: list[np.ndarray] = []
        for i in range(0, len(texts), self.batch_size):
            batch = [t[:MAX_INPUT_CHARS] for t in texts[i:i + self.batch_size]]
            vecs.extend(self._parse(self._post(batch), len(batch)))
        m = np.vstack(vecs)
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return m / norms


class HFEmbedKeywordExtractor(BaseExtractor):
    name = "hf"

    def __init__(self, client, model: str = "BAAI/bge-small-en-v1.5", max_candidates: int = 60, diversity: float = 0.3):
        self.client, self.max_candidates, self.diversity = client, max_candidates, diversity
        self.version = f"hf-{model.split('/')[-1]}"

    def candidates(self, text: str) -> list[str]:
        from sklearn.feature_extraction.text import CountVectorizer
        low = text.lower()
        vec = CountVectorizer(stop_words="english", ngram_range=(1, 3), lowercase=True,
                              token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z\-]{2,}\b")
        try:
            counts = vec.fit_transform([low]).toarray()[0]
        except ValueError:  # empty vocabulary
            return []
        rows = [(t, int(c)) for t, c in zip(vec.get_feature_names_out(), counts) if t in low and _ok(t)]
        rows.sort(key=lambda r: (-r[1], low.find(r[0])))  # frequent first, then earliest in the text
        return [t for t, _ in rows[: self.max_candidates]]

    def extract(self, text: str, top_n: int = 15) -> list[ExtractedKeyword]:
        text = (text or "").strip()
        cands = self.candidates(text[:4000])
        if not cands:
            return []
        vecs = self.client.embed([text[:MAX_INPUT_CHARS]] + cands)
        doc, cv = vecs[0], vecs[1:]
        sims = cv @ doc
        chosen: list[int] = []
        remaining = list(range(len(cands)))
        while remaining and len(chosen) < top_n:  # MMR: relevant to the tender, but not a copy of what is chosen
            best, best_val = remaining[0], -9.0
            for i in remaining:
                redundancy = max((float(cv[i] @ cv[j]) for j in chosen), default=0.0)
                val = (1 - self.diversity) * float(sims[i]) - self.diversity * redundancy
                if val > best_val:
                    best, best_val = i, val
            chosen.append(best)
            remaining.remove(best)
        return [ExtractedKeyword(cands[i], float(min(1.0, max(0.0, sims[i]))), snippet_for(cands[i], text)) for i in chosen]


class FallbackExtractor(BaseExtractor):
    """Tries the Hugging Face extractor; on any failure uses YAKE. After `max_failures` failures in a row it stops
    trying for `cooldown` seconds, so an outage costs a few seconds instead of a timeout per tender."""
    name = "hf+yake"

    def __init__(self, primary: BaseExtractor, fallback: BaseExtractor, max_failures: int = 3, cooldown: float = 600.0,
                 clock=time.monotonic):
        self.primary, self.fallback, self.max_failures, self.cooldown, self._clock = primary, fallback, max_failures, cooldown, clock
        self._failures, self._off_until, self._used = 0, 0.0, primary

    @property
    def version(self) -> str:  # the method actually used for the last tender is what gets stored with it
        return self._used.version

    def extract(self, text: str, top_n: int = 15) -> list[ExtractedKeyword]:
        if self._clock() >= self._off_until:
            try:
                result = self.primary.extract(text, top_n)
                self._failures, self._used = 0, self.primary
                return result
            except Exception as e:  # noqa: BLE001 - never let an outage stop keyword extraction
                self._failures += 1
                log.warning("Hugging Face unavailable, using YAKE", extra={"ctx": {"error": str(e)[:200], "failures": self._failures}})
                if self._failures >= self.max_failures:
                    self._off_until = self._clock() + self.cooldown
        self._used = self.fallback
        return self.fallback.extract(text, top_n)


def build_hf_extractor(settings) -> BaseExtractor:
    token = settings.hf_token.get_secret_value().strip() if settings is not None and settings.hf_token else ""
    if not token:
        log.warning("KEYWORD_METHOD=hf but HF_TOKEN is not set in .env: using YAKE instead")
        return YakeExtractor()
    client = HFEmbeddingClient(token, settings.hf_model, settings.hf_api_base, settings.hf_timeout_seconds)
    primary = HFEmbedKeywordExtractor(client, settings.hf_model, settings.hf_max_candidates, settings.hf_mmr_diversity)
    return FallbackExtractor(primary, YakeExtractor())
