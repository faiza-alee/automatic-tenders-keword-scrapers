"""Taxonomy-driven category classification (transparent, editable, no model needed)."""
import re
from dataclasses import dataclass

import yaml


@dataclass
class CategoryMatch:
    name: str
    group: str | None
    score: float
    matched_terms: list[str]


class Taxonomy:
    def __init__(self, data: dict):
        self.version = str(data.get("version", "0"))
        self.fallback = data.get("fallback_category", "Other")
        self.categories = data["categories"]
        self._compiled = [
            (c, [(t, re.compile(r"(?<![a-z0-9])" + re.escape(t.lower()) + r"(?![a-z0-9])")) for t in c["terms"]])
            for c in self.categories
        ]

    @classmethod
    def from_yaml(cls, path: str) -> "Taxonomy":
        with open(path, encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh))

    def all_categories(self) -> list[tuple[str, str | None]]:
        return [(c["name"], c.get("group")) for c in self.categories] + [(self.fallback, None)]

    def classify(self, title: str, body: str = "", top_n: int = 3, min_score: float = 0.5) -> list[CategoryMatch]:
        """Score = min(1, weighted_hits / 4): a term found in the title counts 3 (once, however often it repeats),
        each hit in the body counts 1 (at most 3 per term). min_score 0.5 = a title hit, or two body hits.
        Ranking: a 'Notices' category matched in the title (evaluation report, corrigendum...) comes first, then
        categories matched in the title, then the rest by score. The title says what is bought; PDF text is noisy."""
        title_l, body_l = (title or "").lower(), (body or "").lower()
        found = []
        for cat, patterns in self._compiled:
            t_w, b_w, matched = 0.0, 0.0, []
            for term, rx in patterns:
                t_hits, b_hits = len(rx.findall(title_l)), len(rx.findall(body_l))
                if t_hits or b_hits:
                    matched.append(term)
                    t_w += 3 * min(t_hits, 1)
                    b_w += min(b_hits, 3)
            if matched:
                found.append((CategoryMatch(cat["name"], cat.get("group"), min(1.0, (t_w + b_w) / 4), matched), t_w, cat.get("group") == "Notices"))
        found = [f for f in found if f[0].score >= min_score]
        found.sort(key=lambda f: (-(f[2] and f[1] > 0), -min(1.0, f[1] / 4), -f[0].score))
        results = [f[0] for f in found][:top_n]
        return results or [CategoryMatch(self.fallback, None, 0.0, [])]
