from pyfederation.records import EvidenceRecord

from spb.objects.context.handler_context import HandlerContext
from spb.utilities import evidence_renderer

EVIDENCE = {
    "uuid": "evidence-uuid",
    "entity": "entity-uuid",
    "operator": "operator-uuid",
    "confidential": False,
    "automated": False,
    "text_content": "buy <now>",
    "tag": "scan",
    "classification_flag": "MALICIOUS",
    "metadata": {"chat": {"id": -100}},
    "created": 1_700_000_000,
    "updated": 0,
}


def test_an_evidence_record_is_summarised_as_escaped_html(template: HandlerContext) -> None:
    evidence = EvidenceRecord.model_validate(EVIDENCE)

    html = evidence_renderer.info_html(
        template.languages, template.languages.default_language, evidence
    )

    assert "evidence-uuid" in html
    assert "buy &lt;now&gt;" in html
    assert "scan" in html
    assert "2023-11-14 22:13:20" in html
    assert '<pre>{\n  "chat": {\n    "id": -100\n  }\n}</pre>' in html
    assert html.endswith("</pre>\n")


def test_optional_fields_are_left_out_when_absent(template: HandlerContext) -> None:
    evidence = EvidenceRecord.model_validate(
        EVIDENCE | {"text_content": None, "tag": " ", "metadata": None}
    )

    html = evidence_renderer.info_html(
        template.languages, template.languages.default_language, evidence
    )

    assert "buy" not in html
    assert "</pre>" not in html


def test_automation_content_type_and_context_are_shown(template: HandlerContext) -> None:
    evidence = EvidenceRecord.model_validate(
        EVIDENCE
        | {"automated": True, "content_type": "HOSTNAME", "context": {"query_type": "A<AAA>"}}
    )
    lm = template.languages
    lang = lm.default_language

    html = evidence_renderer.info_html(lm, lang, evidence)

    assert lm.get(lang, "evidence", "automated", lm.get(lang, "general", "yes")) in html
    assert lm.get(lang, "evidence", "content_type", "Hostname") in html
    assert lm.get(lang, "evidence", "context", '{"query_type": "A&lt;AAA&gt;"}') in html


def test_an_evidence_record_without_context_leaves_it_out(template: HandlerContext) -> None:
    evidence = EvidenceRecord.model_validate(EVIDENCE)
    lm = template.languages
    lang = lm.default_language

    html = evidence_renderer.info_html(lm, lang, evidence)

    assert lm.get(lang, "evidence", "content_type", "Text") in html
    assert lm.get(lang, "evidence", "context", "").split("{")[0] not in html
