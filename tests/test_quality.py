"""Contracts and error paths, not proof of model accuracy. All case data is fictional."""
import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest

from test_app import backend, client, headers, payload, fake_ollama, generated
from quality import SYSTEM_PROMPT, AUDIT_PROMPT, audit_schema, validate_audit

SOURCES = {"transcript": "Sara uppger att maten stod kvar. Det är inte klarlagt vad Ingrid åt.",
           "context": "Rättelse: sängkläder en gång i månaden.",
           "draft": "Ingrid beviljades hjälp varje vecka."}
FINDING = {"kind": "missing", "source": "transcript", "quote": "Sara uppger att maten stod kvar.",
           "message": "Jämför med Saras uppgift om maten; den verkar saknas."}


def audit_payload(**changes):
    return payload("ibic", transcript=SOURCES["transcript"], context=SOURCES["context"],
                   current_draft=SOURCES["draft"], **changes)


@pytest.mark.parametrize("template_id", list(backend.TEMPLATE_MAP))
def test_template_identifiers_and_schema(template_id):
    template = backend.TEMPLATE_MAP[template_id]
    keys = [s["id"] for s in template["sections"]]
    assert len(keys) == len(set(keys))
    assert all(s["title"] and s["guidance"] for s in template["sections"])
    assert template["notice"] and template["source"].startswith("https://")
    assert set(backend.output_schema(template)["properties"]["sections"]["required"]) == set(keys)


@pytest.mark.parametrize("template_id", ["ibic", "iup", "lon"])
def test_template_specific_rules_reach_model(client, headers, monkeypatch, template_id):
    mock = fake_ollama(generated(template_id)); monkeypatch.setattr(backend, "ollama_request", mock)
    assert client.post("/api/draft", json=payload(template_id), headers=headers).status_code == 200
    request = mock.call_args_list[-1].args[2]
    content = json.loads(request["messages"][1]["content"])
    assert content["mallregler"] == backend.TEMPLATE_MAP[template_id]["rules"]
    assert request["messages"][0]["content"] == SYSTEM_PROMPT
    assert "handläggarens_kontext" not in content


def test_policy_retains_conflicts_limits_corrections_and_scope():
    for phrase in ("BÅDA versionerna", "i själva", "begränsningar", "rättelse", "räckvidden", "löneförslag"):
        assert phrase in SYSTEM_PROMPT
    assert "ORDA GRANT" in AUDIT_PROMPT


@pytest.mark.parametrize("template_id,words", [
    ("iup", ["Skolans stödansvar", "läraromdömen", "åtgärdsprogram", "vårdnadshavarens"]),
    ("lon", ["lönekriterier", "medarbetarens", "chefen", "önskemål", "belopp"]),
])
def test_domain_rules_are_explicit(template_id, words):
    text = json.dumps(backend.TEMPLATE_MAP[template_id], ensure_ascii=False).lower()
    for word in words:
        assert word.lower() in text


def test_valid_audit_and_empty_audit():
    assert validate_audit({"findings": [FINDING]}, SOURCES)["findings"] == [FINDING]
    assert validate_audit({"findings": []}, SOURCES) == {"findings": []}
    assert audit_schema()["additionalProperties"] is False


def test_quote_whitespace_normalized_but_not_paraphrased():
    finding = {**FINDING, "quote": "Sara uppger att\n maten stod kvar."}
    validate_audit({"findings": [finding]}, SOURCES)
    with pytest.raises(ValueError):
        validate_audit({"findings": [{**FINDING, "quote": "Sara tror att maten stod kvar."}]}, SOURCES)


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(approved=True),
    lambda d: d.update(findings="all clear"),
    lambda d: d.update(findings=[FINDING] * 13),
    lambda d: d["findings"][0].update(extra="x"),
    lambda d: d["findings"][0].update(kind="approved"),
    lambda d: d["findings"][0].update(source="internet"),
    lambda d: d["findings"][0].update(kind="unsupported"),
    lambda d: d["findings"][0].update(source="draft"),
    lambda d: d["findings"][0].update(quote="Påhittat citat."),
    lambda d: d["findings"][0].update(quote=" "),
    lambda d: d["findings"][0].update(quote="x" * 801),
    lambda d: d["findings"][0].update(message=""),
    lambda d: d["findings"][0].update(message="x" * 1001),
    lambda d: d["findings"][0].update(message=[]),
])
def test_invalid_audit_rejected_whole(mutation):
    data = {"findings": [deepcopy(FINDING)]}; mutation(data)
    with pytest.raises(ValueError):
        validate_audit(data, SOURCES)


def test_unsupported_finding_must_quote_draft():
    finding = {"kind": "unsupported", "source": "draft", "quote": SOURCES["draft"],
               "message": "Kontrollera stödet för ett beslut."}
    validate_audit({"findings": [finding]}, SOURCES)


def test_audit_endpoint_includes_exact_input_and_does_not_return_replacement(client, headers, monkeypatch):
    mock = fake_ollama({"findings": [FINDING]}); monkeypatch.setattr(backend, "ollama_request", mock)
    response = client.post("/api/audit", json=audit_payload(), headers=headers)
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"findings", "model"}
    sent = mock.call_args_list[-1].args[2]
    assert sent["format"] == audit_schema()
    assert sent["messages"][0]["content"] == AUDIT_PROMPT
    content = json.loads(sent["messages"][1]["content"])
    assert all(content[key] == value for key, value in SOURCES.items())
    assert content["mallregler"] == backend.TEMPLATE_MAP["ibic"]["rules"]


def test_audit_invalid_quote_is_error_not_false_all_clear(client, headers, monkeypatch):
    mock = fake_ollama({"findings": [{**FINDING, "quote": "SHOULD_NOT_ECHO"}]})
    monkeypatch.setattr(backend, "ollama_request", mock)
    response = client.post("/api/audit", json=audit_payload(), headers=headers)
    assert response.status_code == 502
    assert "SHOULD_NOT_ECHO" not in response.text
    assert "oförändrat" in response.json()["detail"]


@pytest.mark.parametrize("field,value", [("current_draft", ""), ("current_draft", " "),
                                           ("template_id", "unknown"), ("transcript", "")])
def test_audit_invalid_input(client, headers, field, value):
    data = audit_payload(); data[field] = value
    assert client.post("/api/audit", json=data, headers=headers).status_code == 422


def test_audit_uses_same_security_controls(client, headers, monkeypatch):
    mock = fake_ollama(remote=True); monkeypatch.setattr(backend, "ollama_request", mock)
    assert client.post("/api/audit", json=audit_payload()).status_code == 403
    assert client.post("/api/audit", json=audit_payload(), headers=headers).status_code == 422
    assert all(call.args[1] != "/api/chat" for call in mock.call_args_list)


def test_audit_no_silent_truncation(client, headers):
    data = audit_payload(); data["transcript"] = "å" * 30000
    assert client.post("/api/audit", json=data, headers=headers).status_code == 422


def test_audit_incomplete_reply_is_error(client, headers, monkeypatch):
    monkeypatch.setattr(backend, "ollama_request", fake_ollama({"findings": []}, done_reason="length"))
    assert client.post("/api/audit", json=audit_payload(), headers=headers).status_code == 502


def test_audit_work_lock(client, headers):
    asyncio.run(backend.work_lock.acquire())
    try:
        assert client.post("/api/audit", json=audit_payload(), headers=headers).status_code == 409
    finally:
        backend.work_lock.release()
