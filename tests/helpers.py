import pymupdf

from app.normalize import RawTender


def make_pdf(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def raw(ref="T-1", title="Supply of laptops and printers", dept="Education Dept", **kw) -> RawTender:
    d = dict(reference_number=ref, title=title, department=dept, closing_date="01-01-2099",
             publication_date="01-01-2026", source_url=f"https://x.pk/t/{ref}")
    d.update(kw)
    return RawTender(**d)


class FakeSource:
    """Stands in for TenderSource in monitor tests."""

    def __init__(self, tenders, docs=None, fail_after=None):
        self.tenders, self.docs, self.fail_after, self.pages_fetched = tenders, docs or {}, fail_after, 1

    def iter_tenders(self):
        for i, t in enumerate(self.tenders):
            if self.fail_after is not None and i >= self.fail_after:
                raise RuntimeError("listing page 2 timed out")
            yield t

    def download(self, url):
        if url not in self.docs:
            raise RuntimeError("404")
        return self.docs[url], "application/pdf"
