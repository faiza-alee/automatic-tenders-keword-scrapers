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
        """Title hits count triple. score = min(1, weighted_hits / 4). min_score 0.5 = a title hit, or at least two body hits;
        a single stray mention in a long PDF is not enough."""
        title_l, body_l = (title or "").lower(), (body or "").lower()
        results = []
        for cat, patterns in self._compiled:
            weighted, matched = 0.0, []
            for term, rx in patterns:
                t_hits, b_hits = len(rx.findall(title_l)), len(rx.findall(body_l))
                if t_hits or b_hits:
                    matched.append(term)
                    weighted += 3 * min(t_hits, 2) + min(b_hits, 3)
            if matched:
                results.append(CategoryMatch(cat["name"], cat.get("group"), min(1.0, weighted / 4), matched))
        results = [r for r in sorted(results, key=lambda r: -r.score) if r.score >= min_score][:top_n]
        return results or [CategoryMatch(self.fallback, None, 0.0, [])]
