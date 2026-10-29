"""Compare keyword extractors / the taxonomy on labelled tender samples.

    python scripts/evaluate_keywords.py [samples.json]

samples.json: [{"title": ..., "text": ..., "categories": ["<taxonomy name>"], "keywords": ["optional gold terms"]}]
The bundled file is a small SYNTHETIC starter set. Replace it with 50-100 real tenders you have labelled by hand
before choosing a method for production.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.nlp.extractors import TfidfExtractor, YakeExtractor  # noqa: E402
from app.nlp.taxonomy import Taxonomy  # noqa: E402


def main(path: str):
    samples = json.loads(Path(path).read_text())
    tax = Taxonomy.from_yaml(str(ROOT / "config/taxonomy.yaml"))
    hit1 = hit_any = 0
    for s in samples:
        got = [m.name for m in tax.classify(s["title"], s["text"])]
        hit1 += got[0] in s["categories"]
        hit_any += bool(set(got) & set(s["categories"]))
    n = len(samples)
    print(f"Taxonomy classifier: top-1 {hit1}/{n} ({hit1/n:.0%}), any-of-top-3 {hit_any}/{n}\n")

    extractors = [YakeExtractor(), TfidfExtractor()]
    try:
        from app.nlp.extractors import KeyBertExtractor
        extractors.append(KeyBertExtractor())
    except Exception:  # noqa: BLE001
        print("(KeyBERT not installed - skipped)\n")
    for ex in extractors:
        t0, recall, prec, with_gold = time.time(), 0.0, 0.0, 0
        for s in samples:
            terms = [k.term for k in ex.extract(f"{s['title']}. {s['text']}", 10)]
            gold = [g.lower() for g in s.get("keywords", [])]
            if gold:
                with_gold += 1
                recall += sum(any(g in t or t in g for t in terms) for g in gold) / len(gold)
                prec += sum(any(g in t or t in g for g in gold) for t in terms) / max(len(terms), 1)
        extra = f"recall@10 {recall/with_gold:.2f}, precision@10 {prec/with_gold:.2f}" if with_gold else "no gold keywords given"
        print(f"{ex.version:24s} {extra}  ({(time.time()-t0)/n*1000:.0f} ms/tender)")
        print("   e.g.", [k.term for k in ex.extract(f"{samples[0]['title']}. {samples[0]['text']}", 6)])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "tests/data/keyword_samples.json"))
