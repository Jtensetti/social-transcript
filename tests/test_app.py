"""No model downloads or real personal data. Run: python -m pytest -q."""
import asyncio
import io
import json
import sys
import wave
from pathlib import Path
from unittest.mock import AsyncMock

import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as backend


@pytest.fixture
def client():
    with TestClient(backend.app, base_url="http://127.0.0.1:8765", raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def headers():
    return {"X-Session-Token": backend.SESSION_TOKEN, "Origin": "http://127.0.0.1:8765"}


def payload(template_id="bbic", **kwargs):
    return {"template_id": template_id, "model": "gemma4:e4b",
            "transcript": "Personen uppger att sömnen har förbättrats. Ingen åtgärd beslutades.", **kwargs}


def generated(template_id="bbic"):
    return {"sections": {s["id"]: "" for s in backend.TEMPLATE_MAP[template_id]["sections"]}, "review": []}


def wav_bytes(seconds=1, rate=16000, channels=1, silence=False):
    samples = np.zeros(int(rate * seconds), dtype="<i2") if silence else (
        np.sin(np.arange(int(rate * seconds)) * 2 * np.pi * 220 / rate) * 5000).astype("<i2")
    result = io.BytesIO()
    with wave.open(result, "wb") as wav:
        wav.setnchannels(channels); wav.setsampwidth(2); wav.setframerate(rate)
        wav.writeframes(samples.tobytes() * channels)
    return result.getvalue()


def fake_ollama(result=None, remote=False, done_reason="stop"):
    async def call(method, path, body=None, timeout=5):
        if path == "/api/tags":
            return {"models": [{"name": "gemma4:e4b", "size": 6_600_000_000}]}
        if path == "/api/show":
            return {"remote_host": "example.invalid"} if remote else {"capabilities": ["completion"]}
        if path == "/api/chat":
            return {"message": {"content": json.dumps(result or generated())}, "done_reason": done_reason}
        raise AssertionError(path)
    return AsyncMock(side_effect=call)


def test_config_and_no_external_assets(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    cfg = client.get("/api/config").json()
    assert [t["name"] for t in cfg["templates"]] == ["BBIC", "IBIC", "ASI", "FREDA", "ESTHER"]
    assert cfg["default_model"] == "gemma4:e4b"
    assert cfg["max_seconds"] == 900
    assert cfg["token"]
    assert 'id="lifecare"' in response.text
    assert 'disabled aria-describedby="lifecare-note"' in response.text


def test_localhost_and_csrf(client, headers):
    assert client.post("/api/draft", json=payload()).status_code == 403
    assert client.get("/api/config", headers={"Host": "attacker.invalid"}).status_code == 403
    assert client.get("/api/config", headers={"Origin": "https://attacker.invalid"}).status_code == 403
    assert client.get("/api/config", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/draft", json=payload(), headers={**headers, "Origin": "null"}).status_code == 403


def test_lifecare_not_implemented(client, headers):
    assert client.post("/api/lifecare", headers=headers).status_code == 404


def test_ollama_unavailable_does_not_break_app(client, monkeypatch):
    monkeypatch.setattr(backend, "local_models", AsyncMock(side_effect=HTTPException(503, "Starta Ollama")))
    result = client.get("/api/status")
    assert result.status_code == 200
    assert not result.json()["ollama_available"]
    assert result.json()["models"] == []


@pytest.mark.parametrize("rate", [8000, 16000, 44100, 48000, 96000])
def test_decode_and_resample(rate):
    samples = backend.decode_wav(wav_bytes(rate=rate))
    assert samples.dtype == np.float32
    assert len(samples) == 16000
    assert 0.1 < np.max(samples) < 0.2


@pytest.mark.parametrize("raw", [b"", b"not audio", wav_bytes(channels=2), wav_bytes(seconds=0.1), wav_bytes()[:-100]])
def test_bad_audio(raw):
    with pytest.raises(HTTPException) as exc:
        backend.decode_wav(raw)
    assert exc.value.status_code == 400


def test_audio_segments_cover_every_sample_once():
    samples = np.ones(74 * 16000 + 123, dtype=np.float32)
    chunks = list(backend.audio_chunks(samples))
    assert chunks[0][0] == 0 and chunks[-1][1] == len(samples)
    assert all(a[1] == b[0] for a, b in zip(chunks, chunks[1:]))
    assert sum(len(c[2]) for c in chunks) == len(samples)
    assert all(len(c[2]) <= 25 * 16000 for c in chunks)


def test_silence_rejected_without_loading_model():
    recognizer = backend.Recognizer()
    with pytest.raises(HTTPException) as exc:
        recognizer.transcribe(np.zeros(16000, dtype=np.float32))
    assert exc.value.status_code == 422
    assert not recognizer.loaded


def test_transcription_endpoint(client, headers, monkeypatch):
    monkeypatch.setattr(backend.recognizer, "transcribe", lambda samples: {"text": "Syntetiskt test.", "segments": [], "duration": len(samples)/16000})
    response = client.post("/api/transcribe", content=wav_bytes(rate=48000), headers=headers)
    assert response.status_code == 200
    assert response.json()["duration"] == 1
    assert response.json()["text"] == "Syntetiskt test."


def test_missing_weights_error_is_actionable(client, headers, monkeypatch, tmp_path):
    monkeypatch.setattr(backend, "MODEL_DIR", tmp_path)
    response = client.post("/api/transcribe", content=wav_bytes(), headers=headers)
    assert response.status_code == 503
    assert "--download-model" in response.json()["detail"]


@pytest.mark.parametrize("template_id", ["bbic", "ibic", "asi", "freda", "esther"])
def test_each_template_generates(client, headers, monkeypatch, template_id):
    mock = fake_ollama(generated(template_id))
    monkeypatch.setattr(backend, "ollama_request", mock)
    response = client.post("/api/draft", json=payload(template_id), headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["template_id"] == template_id
    assert [s["id"] for s in result["sections"]] == [s["id"] for s in backend.TEMPLATE_MAP[template_id]["sections"]]
    assert all(s["text"] == "" for s in result["sections"])
    request = mock.call_args_list[-1].args[2]
    assert request["format"]["additionalProperties"] is False
    assert request["stream"] is False
    assert request["think"] is False


def test_revision_keeps_original_context_and_current_draft(client, headers, monkeypatch):
    mock = fake_ollama()
    monkeypatch.setattr(backend, "ollama_request", mock)
    response = client.post("/api/draft", json=payload(context="Det var ett hembesök.", current_draft="Personen uppger att ..."), headers=headers)
    assert response.status_code == 200
    sent = json.loads(mock.call_args_list[-1].args[2]["messages"][1]["content"])
    assert sent["transkribering"] == payload()["transcript"]
    assert sent["handläggarens_kontext"] == "Det var ett hembesök."
    assert sent["nuvarande_utkast"] == "Personen uppger att ..."


@pytest.mark.parametrize("value", ["", "   ", "x" * 50001])
def test_invalid_transcript(client, headers, value):
    assert client.post("/api/draft", json=payload(transcript=value), headers=headers).status_code == 422


def test_unknown_template(client, headers):
    assert client.post("/api/draft", json=payload("unknown"), headers=headers).status_code == 422


def test_oversized_prompt_is_not_silently_truncated(client, headers):
    response = client.post("/api/draft", json=payload(transcript="å" * 30000), headers=headers)
    assert response.status_code == 422
    assert "Inget har kapats" in response.json()["detail"]


def test_oversized_http_body(client, headers):
    response = client.post("/api/draft", content=b"x" * (backend.MAX_JSON_BYTES + 1), headers=headers)
    assert response.status_code == 413


def test_cloud_model_is_blocked(client, headers, monkeypatch):
    mock = fake_ollama()
    monkeypatch.setattr(backend, "ollama_request", mock)
    response = client.post("/api/draft", json=payload(model="gemma4:31b-cloud"), headers=headers)
    assert response.status_code == 422
    assert all(call.args[1] != "/api/chat" for call in mock.call_args_list)


def test_renamed_remote_model_is_blocked(client, headers, monkeypatch):
    mock = fake_ollama(remote=True)
    monkeypatch.setattr(backend, "ollama_request", mock)
    response = client.post("/api/draft", json=payload(), headers=headers)
    assert response.status_code == 422
    assert all(call.args[1] != "/api/chat" for call in mock.call_args_list)


@pytest.mark.parametrize("item", [
    {"name": "test:cloud", "size": 123456789}, {"name": "renamed", "remote_model": "remote", "size": 123456789},
    {"name": "renamed", "remote_host": "example.invalid", "size": 123456789}, {"name": "stub", "size": 12},
])
def test_filter_cloud_and_stub_models(item):
    assert not backend.is_local_model(item)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(extra="unexpected"),
    lambda d: d["sections"].pop("kontakt"),
    lambda d: d["sections"].update(kontakt=123),
    lambda d: d["sections"].update(kontakt="x"*6001),
    lambda d: d.update(review="not an array"),
    lambda d: d.update(review=[None]),
])
def test_schema_rejects_invalid_model_output(client, headers, monkeypatch, mutate):
    data = generated(); mutate(data)
    monkeypatch.setattr(backend, "ollama_request", fake_ollama(data))
    response = client.post("/api/draft", json=payload(), headers=headers)
    assert response.status_code == 502
    assert "följde inte" in response.json()["detail"]


def test_incomplete_model_output_is_not_accepted(client, headers, monkeypatch):
    monkeypatch.setattr(backend, "ollama_request", fake_ollama(done_reason="length"))
    assert client.post("/api/draft", json=payload(), headers=headers).status_code == 502


def test_no_prompt_in_validation_error(client, headers):
    response = client.post("/api/draft", json={"secret": "SHOULD_NOT_ECHO"}, headers=headers)
    assert "SHOULD_NOT_ECHO" not in response.text


def test_parallel_work_rejected(client, headers):
    asyncio.run(backend.work_lock.acquire())
    try:
        assert client.post("/api/transcribe", content=wav_bytes(), headers=headers).status_code == 409
    finally:
        backend.work_lock.release()
