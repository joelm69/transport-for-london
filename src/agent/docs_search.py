"""Search the platform's own documentation: the retrieval half of the agent's RAG.

The corpus is small (the glossary, the endpoint cards and the header comments of the analytics
SQL, about a hundred chunks), so it is indexed in memory at start-up and ranked with BM25: no
embedding model or vector database is needed. Pure Python, no network.
"""

import math
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ("docs/agent/*.md", "docs/endpoints/*.md", "analytics/**/*.sql")
# The glossary is written for these answers, so its sections rank ahead of technical notes
# that mention the same words.
SOURCE_WEIGHTS = {"docs/agent/": 1.5}
STOP_WORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to was were "
    "with what which how why when do does did".split()
)


def _stem(word):
    """A light stemmer, so "bunching", "bunched" and "bunch" match: strip one common ending."""
    for ending in ("ing", "ed", "es", "s"):
        if len(word) > len(ending) + 3 and word.endswith(ending):
            return word[: -len(ending)]
    return word


def _tokens(text):
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP_WORDS]


def _markdown_chunks(path, text):
    """One chunk per '## ' section, titled with its heading."""
    parts = re.split(r"^(#{2,3} .+)$", text, flags=re.MULTILINE)
    chunks = []
    for heading, body in zip(parts[1::2], parts[2::2]):
        body = body.strip()
        if body:
            chunks.append({"source": path, "title": heading.lstrip("# ").strip(), "text": body})
    return chunks


def _sql_chunks(path, text):
    """The leading comment block of each statement: where the SQL explains its definitions."""
    chunks = []
    for block in re.findall(r"((?:^--.*\n)+)", text, flags=re.MULTILINE):
        comment = "\n".join(line[2:].strip() for line in block.splitlines()).strip()
        if len(comment) > 80:
            chunks.append({"source": path, "title": comment.splitlines()[0], "text": comment})
    return chunks


def load_chunks(root=ROOT):
    chunks = []
    for pattern in SOURCES:
        for file in sorted(root.glob(pattern)):
            text = file.read_text(encoding="utf-8")
            path = str(file.relative_to(root))
            chunks += _sql_chunks(path, text) if file.suffix == ".sql" else _markdown_chunks(path, text)
    return chunks


class DocsIndex:
    """BM25 over the chunks (k1 = 1.5, b = 0.75); titles count twice."""

    def __init__(self, chunks, k1=1.5, b=0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.terms = [Counter(_tokens(c["title"]) * 2 + _tokens(c["text"])) for c in chunks]
        self.lengths = [sum(t.values()) for t in self.terms]
        self.average = sum(self.lengths) / len(self.lengths) if self.lengths else 0
        documents = Counter(term for t in self.terms for term in t)
        n = len(chunks)
        self.idf = {term: math.log(1 + (n - df + 0.5) / (df + 0.5)) for term, df in documents.items()}

    def search(self, query, limit=4):
        words = _tokens(query)
        scored = []
        for i, terms in enumerate(self.terms):
            score = 0.0
            for word in words:
                tf = terms.get(word)
                if tf:
                    norm = tf + self.k1 * (1 - self.b + self.b * self.lengths[i] / self.average)
                    score += self.idf[word] * tf * (self.k1 + 1) / norm
            if score > 0:
                source = self.chunks[i]["source"]
                weight = next((w for prefix, w in SOURCE_WEIGHTS.items() if source.startswith(prefix)), 1.0)
                scored.append((score * weight, i))
        scored.sort(reverse=True)
        return [self.chunks[i] for _, i in scored[:limit]]


_index = None


def search(query, limit=4):
    """The best matching chunks for a question, built once per process."""
    global _index
    if _index is None:
        _index = DocsIndex(load_chunks())
    return _index.search(query, limit)
