"""A language model (DeepSeek) reads the tender and picks the category and the keywords.

The model may only choose categories from taxonomy.yaml (anything else is discarded), so results stay filterable. Any
failure (no key, no balance, network, bad answer) falls back to the normal taxonomy + keyword path, so scraping and
keyword extraction never stop. The API key is read from DEEPSEEK_API_KEY, held as a secret, never logged or stored in
the database, and scrubbed from every error message.
"""
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field

import requests

from app.nlp.extractors import BOILERPLATE, _ok

log = logging.getLogger(__name__)


class LLMError(Exception):
    """Any LLM problem. Messages never contain the key."""


class LLMUnavailable(LLMError):
    """The service cannot be used right now (network, no balance, bad key, paused). Not the tender's fault:
    the tender stays pending and is retried later, without using up its attempts."""


class LLMFatalError(LLMUnavailable):
    """Retrying cannot help (bad key, no balance): stop calling the API for a while."""


class LLMBadAnswer(LLMError):
    """The model answered, but not usably (broken JSON, category outside the list). Counts as an attempt."""


@dataclass
class LLMResult:
    categories: list[str]            # first = primary
    keywords: list[str]
    model: str = ""
    raw: dict = field(default_factory=dict)


class LLMClient:
    def __init__(self, api_key: str, model: str = "deepseek-flash", base_url: str = "https://api.deepseek.com",
                 timeout: int = 90, session: requests.Session | None = None, sleep=time.sleep,
                 max_retries: int = 3, max_tokens: int = 700):
        # DeepSeek V4 models 'think' by default and thinking tokens use up max_tokens, which can leave the answer empty.
        # We only need a short JSON answer, so thinking is switched off.
        self._key, self.model, self.timeout, self.max_retries, self.max_tokens = api_key, model, timeout, max_retries, max_tokens
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.session, self._sleep = session or requests.Session(), sleep
        self.json_mode = True
        self.send_thinking_off = True
        self.calls = 0

    def _scrub(self, text) -> str:
        return str(text).replace(self._key, "***") if self._key else str(text)

    def chat(self, system: str, user: str) -> str:
        last = "unknown"
        for attempt in range(1, self.max_retries + 1):
            body = {"model": self.model, "temperature": 0, "max_tokens": self.max_tokens,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if self.json_mode:
                body["response_format"] = {"type": "json_object"}
            if self.send_thinking_off:
                body["thinking"] = {"type": "disabled"}
            try:
                self.calls += 1
                r = self.session.post(self.url, json=body, timeout=self.timeout,
                                      headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"})
            except requests.RequestException as e:
                last = f"network: {type(e).__name__}"
            else:
                code = r.status_code
                if code == 200:
                    try:
                        content = r.json()["choices"][0]["message"].get("content")
                    except (KeyError, IndexError, TypeError, ValueError):
                        raise LLMBadAnswer("model ke jawab ki shakl samajh nahi aayi")
                    if not content or not str(content).strip():
                        raise LLMBadAnswer("model ka jawab khali tha")
                    return str(content)
                if code == 401:
                    raise LLMFatalError("DeepSeek ne API key qabool nahi ki (HTTP 401). Key sahi hai? Aur agar .env abhi badli hai to "
                                    "`docker compose up -d --force-recreate --no-build api worker` chalayen (restart naya .env nahi padhta)")
                if code == 402:
                    raise LLMFatalError("DeepSeek account mein balance khatam hai (HTTP 402); platform.deepseek.com par top-up karein")
                if code in (400, 422) and self.json_mode and "response_format" in r.text.lower():
                    self.json_mode = False  # this model/provider has no JSON mode: ask in the prompt instead
                    continue
                if code in (400, 422) and self.send_thinking_off and "thinking" in r.text.lower():
                    self.send_thinking_off = False  # this model has no thinking switch: leave room for it in max_tokens
                    self.max_tokens = max(self.max_tokens, 2500)
                    continue
                if code == 429 or code >= 500:
                    last = f"HTTP {code}"
                    wait = r.headers.get("Retry-After", "")
                    if wait.isdigit():
                        self._sleep(min(int(wait), 30))
                else:
                    raise LLMUnavailable(f"DeepSeek ka jawab HTTP {code}: {self._scrub(r.text[:300])}")
            if attempt < self.max_retries:
                self._sleep(min(2 ** attempt, 20))
        raise LLMUnavailable(f"DeepSeek tak nahi pohancha ja saka ({last})")


SYSTEM = ("You classify public procurement notices from Gilgit-Baltistan, Pakistan (tenders, requests for proposals, "
          "evaluation reports, corrigenda). You reply with ONE json object and nothing else.")

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def parse_json(text: str) -> dict:
    t = _FENCE.sub("", _THINK.sub("", text).strip()).strip()
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        raise LLMBadAnswer("model ne JSON nahi diya")
    try:
        data = json.loads(t[a:b + 1])
    except ValueError:
        raise LLMBadAnswer("model ka JSON toota hua tha")
    if not isinstance(data, dict):
        raise LLMBadAnswer("model ka JSON object nahi tha")
    return data


class LLMAnalyzer:
    def __init__(self, client: LLMClient, categories: list[tuple[str, str]], fallback_name: str = "Other",
                 max_chars: int = 3500, max_failures: int = 3, cooldown: float = 600.0, clock=time.monotonic):
        """categories: [(name, hint)] - the only values the model may choose."""
        self.client, self.categories, self.fallback_name, self.max_chars = client, categories, fallback_name, max_chars
        self._names = {n.lower(): n for n, _ in categories} | {fallback_name.lower(): fallback_name}
        self.max_failures, self.cooldown, self._clock = max_failures, cooldown, clock
        self._failures, self._off_until = 0, 0.0

    @property
    def version(self) -> str:
        return "llm-" + self.client.model.split("/")[-1].split(":")[0]

    def build_prompt(self, title: str, department: str | None, body: str) -> str:
        cats = "\n".join(f"- {n}: {hint}" for n, hint in self.categories) + f"\n- {self.fallback_name}: none of the above"
        return (
            f"Categories (choose ONLY from these exact names):\n{cats}\n\n"
            f"Tender title: {title}\nIssuing office: {department or 'unknown'}\n"
            f"Text of the notice (may be partial or noisy):\n{(body or '')[: self.max_chars]}\n\n"
            "Reply with a json object of exactly this shape:\n"
            '{"primary_category": "<one category name>", "other_categories": ["<0-2 more names>"], '
            '"keywords": ["<6-10 short lowercase phrases of 1-4 words>"]}\n'
            "Rules: the category is decided by WHAT is being procured or built, not by who issues it. If the notice is an "
            "evaluation report, corrigendum, cancellation or result rather than an invitation to bid, use the notices category. "
            "Keywords must name the goods, works or services and a place if one is named; never office names, bid rules, "
            "dates, amounts or generic words like 'tender' or 'notice'.")

    def _clean(self, data: dict) -> LLMResult:
        def name_of(x):
            return self._names.get(str(x).strip().lower())
        primary = name_of(data.get("primary_category"))
        if not primary:
            raise LLMBadAnswer("model ne list se bahar ki category di")
        cats = [primary]
        for c in data.get("other_categories") or []:
            n = name_of(c)
            if n and n not in cats and n != self.fallback_name:
                cats.append(n)
        kws: list[str] = []
        for k in data.get("keywords") or []:
            k = re.sub(r"\s+", " ", str(k).strip().lower()).strip(" .,:;\"'")
            if 3 <= len(k) <= 60 and len(k.split()) <= 5 and _ok(k) and k not in kws:
                if not all(t in BOILERPLATE for t in re.findall(r"[a-z0-9\-/]+", k)):
                    kws.append(k)
        return LLMResult(cats[:3], kws[:10], self.client.model, data)

    def analyze(self, title: str, department: str | None, body: str) -> LLMResult:
        if self._clock() < self._off_until:
            raise LLMUnavailable("LLM abhi band hai (pichli koshishein nakam)")
        try:
            result = self._clean(parse_json(self.client.chat(SYSTEM, self.build_prompt(title, department, body))))
        except LLMFatalError:
            self._off_until = self._clock() + self.cooldown   # bad key / no balance: do not hammer the API
            raise
        except LLMError:
            self._failures += 1
            if self._failures >= self.max_failures:
                self._off_until = self._clock() + self.cooldown
            raise
        self._failures = 0
        return result


def category_hints(taxonomy) -> list[tuple[str, str]]:
    return [(c["name"], "e.g. " + ", ".join(c["terms"][:8])) for c in taxonomy.categories]


def build_llm_analyzer(settings, taxonomy):
    if not settings.use_llm:
        return None
    key = settings.deepseek_api_key.get_secret_value().strip() if settings.deepseek_api_key else ""
    if not key:
        log.warning("USE_LLM=true but DEEPSEEK_API_KEY is not set in .env: LLM classification is off")
        return None
    client = LLMClient(key, settings.deepseek_model, settings.deepseek_base_url, settings.llm_timeout_seconds)
    return LLMAnalyzer(client, category_hints(taxonomy), taxonomy.fallback, settings.llm_max_chars)


def compare_methods(session, analyzer: LLMAnalyzer, taxonomy, n: int = 30, seed: int = 1) -> list[dict]:
    """Run the LLM on n random stored tenders WITHOUT saving anything, next to the taxonomy's answer."""
    from sqlalchemy import select
    from app.models import Tender
    from app.nlp.pipeline import build_text
    tenders = list(session.scalars(select(Tender)))
    random.Random(seed).shuffle(tenders)
    rows = []
    for t in tenders[:n]:
        title, body = build_text(t)
        tax = taxonomy.classify(title, body)[0].name
        try:
            res = analyzer.analyze(title, t.department.name if t.department else None, body)
            rows.append({"id": t.id, "title": title, "taxonomy": tax, "llm": res.categories[0], "keywords": res.keywords})
        except LLMError as e:
            rows.append({"id": t.id, "title": title, "taxonomy": tax, "llm": None, "error": str(e)})
    return rows
