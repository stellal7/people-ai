"""
The policy corpus: hand-written documents, parsed into citable clauses.

A clause is the unit of retrieval and the unit of citation. Policies are written with numbered clauses
(`COMP-3.1`) for exactly that reason: an answer can point at the sentence it used, and an eval can check it.

Documents are effective dated and can have regional editions, like the rest of this project: the leveling guide
has a superseded version, and the leave policy has US and UK editions. Retrieval must pick the right one.
"""
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from people_ai.config import REPO_ROOT

CORPUS_DIR = REPO_ROOT / "corpus" / "policies"
FRONT_MATTER = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
CLAUSE = re.compile(r"\*\*([A-Z]{2,6}-\d+\.\d+)\*\*\s*(.+?)(?=\n\*\*[A-Z]{2,6}-\d+\.\d+\*\*|\n## |\Z)", re.S)
HEADING = re.compile(r"^## (.+)$", re.M)
REQUIRED = {"doc_id", "title", "area", "version", "effective_from", "effective_to", "region", "owner", "sensitivity"}
OPEN_ENDED = date(9999, 12, 31)


@dataclass(frozen=True)
class Clause:
    doc_id: str
    version: int
    region: str
    clause_id: str
    heading: str
    text: str

    @property
    def citation(self):
        return f"{self.clause_id} ({self.doc_id} v{self.version}, {self.region})"


@dataclass(frozen=True)
class Document:
    doc_id: str
    title: str
    area: str
    version: int
    effective_from: date
    effective_to: date
    region: str
    owner: str
    sensitivity: str
    path: Path
    clauses: tuple
    superseded_by: str | None = None

    @property
    def key(self):
        return (self.doc_id, self.version, self.region)

    def applies_on(self, when):
        return self.effective_from <= when <= self.effective_to

    def clause(self, clause_id):
        return next(c for c in self.clauses if c.clause_id == clause_id)


def parse(path: Path) -> Document:
    match = FRONT_MATTER.match(path.read_text())
    if not match:
        raise ValueError(f"{path.name}: missing front matter")
    meta, body = yaml.safe_load(match.group(1)), match.group(2)
    missing = REQUIRED - meta.keys()
    if missing:
        raise ValueError(f"{path.name}: front matter is missing {sorted(missing)}")

    headings = [(m.start(), m.group(1)) for m in HEADING.finditer(body)]
    clauses = []
    for found in CLAUSE.finditer(body):
        heading = next((title for start, title in reversed(headings) if start < found.start()), "")
        clauses.append(Clause(doc_id=meta["doc_id"], version=int(meta["version"]), region=str(meta["region"]),
                              clause_id=found.group(1), heading=heading,
                              text=" ".join(found.group(2).split())))
    if not clauses:
        raise ValueError(f"{path.name}: no numbered clauses found")
    return Document(doc_id=meta["doc_id"], title=meta["title"], area=meta["area"], version=int(meta["version"]),
                    effective_from=meta["effective_from"], effective_to=meta["effective_to"],
                    region=str(meta["region"]), owner=meta["owner"], sensitivity=meta["sensitivity"],
                    path=path, clauses=tuple(clauses), superseded_by=meta.get("superseded_by"))


def load_documents(directory: Path = CORPUS_DIR):
    documents = [parse(path) for path in sorted(directory.glob("*.md"))]
    keys = [d.key for d in documents]
    if len(keys) != len(set(keys)):
        raise ValueError("two documents share a doc_id, version and region")
    return documents


def applicable(documents, as_of=None, region=None):
    """The editions in force on a date, for a region. 'global' documents apply everywhere."""
    when = as_of if isinstance(as_of, date) else (date.fromisoformat(as_of) if as_of else OPEN_ENDED)
    when = min(when, OPEN_ENDED)
    chosen = [d for d in documents if d.applies_on(when)]
    if region:
        chosen = [d for d in chosen if d.region in ("global", region)]
    return chosen


def clauses_of(documents):
    return [clause for document in documents for clause in document.clauses]
