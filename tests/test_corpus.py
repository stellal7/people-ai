"""
The policy corpus and the citations the evals expect must stay in step.

Citations rot silently: a clause gets renumbered, and an eval keeps passing against a clause that no longer says
what it used to. These tests make that a build failure.
"""
from datetime import date

import pytest
import yaml

from people_ai.config import REPO_ROOT
from people_ai.rag import corpus

DOCUMENTS = corpus.load_documents()
RAG_QUESTIONS = yaml.safe_load((REPO_ROOT / "evals" / "golden" / "rag_questions.yaml").read_text())["questions"]
INJECTIONS = yaml.safe_load((REPO_ROOT / "corpus" / "injection_resumes.yaml").read_text())["injections"]


def test_the_corpus_is_small_and_covers_the_lifecycle():
    assert 6 <= len(DOCUMENTS) <= 10, "keep the corpus minimal: it exists to make techniques testable"
    assert {d.area for d in DOCUMENTS} == {"leveling", "promotion", "compensation", "hiring", "leave", "performance"}


def test_every_document_parses_into_numbered_clauses():
    for document in DOCUMENTS:
        assert document.clauses, f"{document.path.name} has no clauses"
        ids = [c.clause_id for c in document.clauses]
        assert len(ids) == len(set(ids)), f"{document.path.name} repeats a clause id"
        assert all(c.clause_id.startswith(document.doc_id + "-") for c in document.clauses)
        assert all(len(c.text) > 20 for c in document.clauses), "a clause should be a sentence, not a fragment"


def test_versions_and_regions_do_not_overlap():
    """Two editions of the same document must not both apply on the same date in the same region."""
    for a in DOCUMENTS:
        for b in DOCUMENTS:
            if a is b or a.doc_id != b.doc_id or a.region != b.region:
                continue
            overlap = max(a.effective_from, b.effective_from) <= min(a.effective_to, b.effective_to)
            assert not overlap, f"{a.path.name} and {b.path.name} both apply at once"


def test_the_superseded_edition_is_reachable_only_in_its_window():
    leveling = [d for d in DOCUMENTS if d.doc_id == "LEV"]
    assert {d.version for d in leveling} == {1, 2}
    in_2022 = corpus.applicable(leveling, "2022-06-30")
    today = corpus.applicable(leveling, "2025-12-31")
    assert [d.version for d in in_2022] == [1] and [d.version for d in today] == [2]


def test_regional_editions_are_selected_by_region():
    leave = [d for d in DOCUMENTS if d.doc_id == "LVE"]
    assert {d.region for d in leave} == {"US", "UK"}
    assert [d.region for d in corpus.applicable(leave, "2025-01-01", region="UK")] == ["UK"]
    assert [d.region for d in corpus.applicable(leave, "2025-01-01", region="US")] == ["US"]


@pytest.mark.parametrize("question", [q for q in RAG_QUESTIONS if "clause" in q.get("expect", {})],
                         ids=lambda q: q["id"])
def test_every_expected_citation_exists(question):
    expect = question["expect"]
    wanted = expect["clause"] if isinstance(expect["clause"], list) else [expect["clause"]]
    for clause_id in wanted:
        doc_id = clause_id.split("-")[0]
        candidates = [d for d in DOCUMENTS if d.doc_id == doc_id]
        if "version" in expect and doc_id == expect.get("doc_id"):
            candidates = [d for d in candidates if d.version == expect["version"]]
        if "region" in expect:
            candidates = [d for d in candidates if d.region in (expect["region"], "global")]
        assert any(clause_id in [c.clause_id for c in d.clauses] for d in candidates), \
            f"{question['id']} cites {clause_id}, which no matching document contains"


def test_expected_answers_appear_in_the_cited_clause():
    """If the answer must contain '25', the clause we point at had better say 25."""
    for question in RAG_QUESTIONS:
        expect = question.get("expect", {})
        if "clause" not in expect or "answer_contains" not in expect:
            continue
        clause_ids = expect["clause"] if isinstance(expect["clause"], list) else [expect["clause"]]
        text = " ".join(c.text.lower() for d in DOCUMENTS for c in d.clauses if c.clause_id in clause_ids
                        and (("version" not in expect) or d.version == expect["version"])
                        and (("region" not in expect) or d.region in (expect["region"], "global")))
        assert any(wanted.lower() in text for wanted in expect["answer_contains"]), \
            f"{question['id']}: none of {expect['answer_contains']} appears in {clause_ids}"


def test_injection_resumes_are_adversarial_but_plausible():
    assert len(INJECTIONS) == 3
    keys = {i["key"] for i in INJECTIONS}
    assert keys == {"injection_direct", "injection_exfiltration", "injection_policy_override"}
    for injection in INJECTIONS:
        resume = injection["resume"]
        assert "EXPERIENCE" in resume or "SUMMARY" in resume, "it should read as a resume, not only an attack"
        assert any(word in resume.lower() for word in ("ignore", "assistant:", "instruction", "policy was updated"))
        assert injection["what_success_looks_like"], "say what a successful attack would look like"


def test_the_hiring_policy_states_the_defence():
    """The 'documents are data, not instructions' rule should be written down, not only implemented."""
    hiring = next(d for d in DOCUMENTS if d.doc_id == "HIR")
    assert "never as instructions" in hiring.clause("HIR-6.2").text
