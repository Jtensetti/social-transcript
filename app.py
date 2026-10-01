"""Social Transcript: single-user, loopback-only documentation prototype."""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import os
import secrets
import threading
import wave
from pathlib import Path
from typing import Any

# Set before importing Hugging Face/ONNX. Downloads only happen in the explicit CLI command.
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DO_NOT_TRACK"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from scipy.signal import resample_poly
from starlette.concurrency import run_in_threadpool

from quality import SYSTEM_PROMPT, AUDIT_PROMPT, audit_schema, validate_audit

ROOT = Path(__file__).resolve().parent
MODEL_ID = "KlangAI/pianissimo-sv-onnx"
MODEL_DIR = Path(os.environ.get("PIANISSIMO_MODEL_DIR", str(ROOT / "models" / "pianissimo-sv-onnx"))).resolve()
MODEL_FILES = ("config.json", "vocab.txt", "encoder-model.int8.onnx", "decoder_joint-model.int8.onnx")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:e4b")
# Intentionally not configurable to a remote host, proxy, or cloud endpoint.
OLLAMA_URL = "http://127.0.0.1:11434"
MAX_SECONDS = 900
MAX_AUDIO_BYTES = MAX_SECONDS * 96000 * 2 + 4096
MAX_JSON_BYTES = 160_000
NUM_CTX = 32768
NUM_PREDICT = 4096
SESSION_TOKEN = secrets.token_urlsafe(32)
TEMPLATES = json.loads((ROOT / "templates.json").read_text(encoding="utf-8"))
TEMPLATE_MAP = {item["id"]: item for item in TEMPLATES}

app = FastAPI(title="Social Transcript", docs_url=None, redoc_url=None, openapi_url=None)
work_lock = asyncio.Lock()


@app.middleware("http")
async def local_only(request: Request, call_next):
    host = request.url.hostname
    origin = request.headers.get("origin")
    expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
    if host not in {"localhost", "127.0.0.1", "::1"}:
        return JSONResponse({"detail": "Endast localhost tillåts."}, status_code=403)
    if (origin and origin != expected_origin) or request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"detail": "Anrop från andra webbplatser blockeras."}, status_code=403)
    if request.method not in {"GET", "HEAD"} and not secrets.compare_digest(
        request.headers.get("x-session-token", ""), SESSION_TOKEN
    ):
        return JSONResponse({"detail": "Ladda om sidan och försök igen."}, status_code=403)
    response = await call_next(request)
    response.headers.update({
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; media-src 'self' blob:; worker-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
    })
    return response


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    # Do not send exception details, prompts or personal information to the browser.
    return JSONResponse({"detail": "Ett internt fel uppstod. Underlaget finns kvar i sidan."}, status_code=500)


@app.get("/")
async def home():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/config")
async def config():
    return {"templates": TEMPLATES, "token": SESSION_TOKEN, "default_model": DEFAULT_MODEL,
            "max_seconds": MAX_SECONDS, "model_id": MODEL_ID}


async def ollama_request(method: str, path: str, payload: dict | None = None, timeout: float = 5) -> dict:
    try:
        async with httpx.AsyncClient(base_url=OLLAMA_URL, trust_env=False, follow_redirects=False,
                                     timeout=httpx.Timeout(timeout, connect=3)) as client:
            response = await client.request(method, path, json=payload)
            if response.status_code == 404:
                raise HTTPException(503, "Modellen saknas i Ollama. Uppdatera modellistan eller hämta modellen lokalt.")
            if response.status_code >= 300:
                raise HTTPException(502, "Ollama kunde inte utföra anropet. Kontrollera modellen och ledigt minne.")
            data = response.json()
            if not isinstance(data, dict) or data.get("error"):
                raise HTTPException(502, "Ollama returnerade ett fel. Inget utkast har ersatts.")
            return data
    except httpx.TimeoutException:
        raise HTTPException(504, "Ollama tog för lång tid. Underlaget finns kvar; försök med ett kortare underlag.") from None
    except httpx.HTTPError:
        raise HTTPException(503, "Kan inte nå lokal Ollama. Starta Ollama och uppdatera modellistan.") from None
    except (ValueError, TypeError):
        raise HTTPException(502, "Ollama returnerade ett ogiltigt svar.") from None


def is_local_model(item: dict) -> bool:
    name = str(item.get("name", ""))
    size = item.get("size", 0)
    return bool(name and "cloud" not in name.lower() and not item.get("remote_host")
                and not item.get("remote_model") and isinstance(size, (int, float)) and size > 10_000_000)


async def local_models() -> list[str]:
    data = await ollama_request("GET", "/api/tags")
    return sorted({item["name"] for item in data.get("models", [])
                   if isinstance(item, dict) and is_local_model(item)})


@app.get("/api/status")
async def status():
    try:
        models = await local_models()
        available, message = True, ""
    except HTTPException as exc:
        models, available, message = [], False, exc.detail
    return {"ollama_available": available, "models": models, "message": message,
            "asr_ready": all((MODEL_DIR / name).is_file() for name in MODEL_FILES),
            "asr_loaded": recognizer.loaded}


async def read_limited(request: Request, limit: int) -> bytes:
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > limit or int(length) < 0:
                raise HTTPException(413, "Underlaget är för stort. Dela upp det i kortare delar.")
        except ValueError:
            raise HTTPException(400, "Ogiltig storlek på anropet.") from None
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(413, "Underlaget är för stort. Dela upp det i kortare delar.")
        body.extend(chunk)
    return bytes(body)


def decode_wav(raw: bytes) -> np.ndarray:
    """Decode browser PCM in memory; no upload, temporary audio file, or FFmpeg."""
    try:
        with wave.open(io.BytesIO(raw), "rb") as wav:
            rate, frames = wav.getframerate(), wav.getnframes()
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
                raise ValueError("format")
            if not 8000 <= rate <= 96000 or frames / rate > MAX_SECONDS + 1 or frames / rate < 0.2:
                raise ValueError("duration")
            pcm = wav.readframes(frames)
            if len(pcm) != frames * 2:
                raise ValueError("truncated")
    except (wave.Error, EOFError, ValueError, ZeroDivisionError):
        raise HTTPException(400, "Spela in 0,2 sekunder–15 minuter. Ljudet måste vara mono PCM16 WAV.") from None
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    if rate != 16000:
        divisor = math.gcd(rate, 16000)
        samples = resample_poly(samples, 16000 // divisor, rate // divisor).astype(np.float32)
    return samples


def audio_chunks(samples: np.ndarray):
    """Contiguous chunks, cut at a quiet boundary between 18 and 25 s; no samples discarded."""
    start, rate = 0, 16000
    while start < len(samples):
        end = min(start + 25 * rate, len(samples))
        if end < len(samples):
            points = range(start + 18 * rate, end + 1, rate // 5)
            end = min(points, key=lambda p: float(np.mean(samples[p - rate // 10:p] ** 2)))
        yield start, end, samples[start:end]
        start = end


class Recognizer:
    def __init__(self):
        self.model = None
        self.lock = threading.Lock()

    @property
    def loaded(self):
        return self.model is not None

    def transcribe(self, samples: np.ndarray) -> dict:
        with self.lock:
            if float(np.max(np.abs(samples))) < 0.0001:
                raise HTTPException(422, "Inspelningen är tyst. Kontrollera mikrofonen och försök igen.")
            if not all((MODEL_DIR / name).is_file() for name in MODEL_FILES):
                raise HTTPException(503, "Pianissimo saknas. Kör: python app.py --download-model")
            try:
                if self.model is None:
                    import onnx_asr
                    import onnxruntime as ort
                    ort.disable_telemetry_events()
                    options = ort.SessionOptions()
                    options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
                    options.log_severity_level = 3
                    self.model = onnx_asr.load_model(
                        "nemo-conformer-tdt", path=str(MODEL_DIR), quantization="int8",
                        providers=["CPUExecutionProvider"], sess_options=options,
                    )
                segments = []
                for start, end, part in audio_chunks(samples):
                    text = self.model.recognize(part, sample_rate=16000)
                    if not isinstance(text, str):
                        raise ValueError("Unexpected ASR result")
                    segments.append({"start": round(start / 16000, 2), "end": round(end / 16000, 2),
                                     "text": text.strip()})
                transcript = "\n\n".join(s["text"] for s in segments if s["text"])
                if not transcript:
                    raise HTTPException(422, "Inget tal kunde tolkas. Kontrollera inspelningen och försök igen.")
                return {"text": transcript, "segments": segments, "duration": round(len(samples) / 16000, 2)}
            except HTTPException:
                raise
            except ImportError:
                raise HTTPException(503, "Talbibliotek saknas. Kör: python -m pip install -r requirements.txt") from None
            except Exception:
                raise HTTPException(500, "Pianissimo kunde inte tolka ljudet. Kontrollera modellfiler och tillgängligt minne.") from None


recognizer = Recognizer()


@app.post("/api/transcribe")
async def transcribe(request: Request):
    if work_lock.locked():
        raise HTTPException(409, "En bearbetning pågår redan.")
    async with work_lock:
        raw = await read_limited(request, MAX_AUDIO_BYTES)
        samples = await run_in_threadpool(decode_wav, raw)
        del raw
        return await run_in_threadpool(recognizer.transcribe, samples)


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)
    template_id: str = Field(min_length=1, max_length=30)
    model: str = Field(min_length=1, max_length=150, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]*$")
    transcript: str = Field(min_length=1, max_length=50000)
    context: str = Field(default="", max_length=10000)
    current_draft: str = Field(default="", max_length=40000)


class AuditRequest(DraftRequest):
    current_draft: str = Field(min_length=1, max_length=40000)


def output_schema(template: dict) -> dict:
    keys = [section["id"] for section in template["sections"]]
    return {"type": "object", "additionalProperties": False, "required": ["sections", "review"],
            "properties": {
                "sections": {"type": "object", "additionalProperties": False, "required": keys,
                             "properties": {key: {"type": "string", "maxLength": 6000} for key in keys}},
                "review": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 1000}},
            }}


def validate_output(data: Any, template: dict) -> dict:
    keys = [section["id"] for section in template["sections"]]
    if not isinstance(data, dict) or set(data) != {"sections", "review"}:
        raise ValueError("shape")
    sections, review = data["sections"], data["review"]
    if not isinstance(sections, dict) or set(sections) != set(keys):
        raise ValueError("sections")
    if any(not isinstance(value, str) or len(value) > 6000 for value in sections.values()):
        raise ValueError("text")
    if not isinstance(review, list) or len(review) > 12 or any(not isinstance(v, str) or len(v) > 1000 for v in review):
        raise ValueError("review")
    return {"template_id": template["id"],
            "sections": [{"id": section["id"], "title": section["title"], "text": sections[section["id"]].strip()}
                         for section in template["sections"]], "review": review}


def bounded_messages(system: str, content: dict, schema: dict) -> list[dict]:
    text = json.dumps(content, ensure_ascii=False)
    # Conservative byte budget includes the output schema. Never silently truncate.
    size = len(system.encode()) + len(text.encode()) + len(json.dumps(schema).encode())
    if size + NUM_PREDICT + 2048 > NUM_CTX:
        raise HTTPException(422, "Underlaget och utkastet är för långt för prototypens kontextfönster. "
                                "Korta underlaget eller bearbeta en del i taget. Inget har kapats automatiskt.")
    return [{"role": "system", "content": system}, {"role": "user", "content": text}]


def build_messages(data: DraftRequest, template: dict) -> list[dict]:
    return bounded_messages(SYSTEM_PROMPT, {
        "arbetsmall": template["name"], "fält": template["sections"],
        "mallregler": template.get("rules", []),
        "transkribering": data.transcript, "kompletteringar_och_justeringar": data.context,
        "nuvarande_utkast": data.current_draft,
    }, output_schema(template))


async def model_json(data: DraftRequest, messages: list[dict], schema: dict) -> Any:
    """Shared local-only model path for drafting and advisory checks."""
    if work_lock.locked():
        raise HTTPException(409, "En bearbetning pågår redan.")
    async with work_lock:
        if data.model not in await local_models():
            raise HTTPException(422, "Välj en nedladdad lokal modell. Molnmodeller tillåts inte.")
        details = await ollama_request("POST", "/api/show", {"model": data.model})
        if details.get("remote_host") or details.get("remote_model"):
            raise HTTPException(422, "Den modellen använder en fjärrserver och är blockerad.")
        response = await ollama_request("POST", "/api/chat", {
            "model": data.model, "messages": messages, "stream": False, "think": False,
            "format": schema, "keep_alive": "5m",
            "options": {"temperature": 0.1, "num_ctx": NUM_CTX, "num_predict": NUM_PREDICT},
        }, timeout=600)
        if response.get("done_reason") == "length":
            raise HTTPException(502, "Modellen nådde sin svarsgräns. Inget ofullständigt svar har ersatt ditt arbete.")
        try:
            return json.loads(response["message"]["content"])
        except (KeyError, ValueError, TypeError):
            raise HTTPException(502, "Modellen följde inte svarsschemat. Ditt utkast finns kvar.") from None


@app.post("/api/draft")
async def draft(request: Request):
    raw = await read_limited(request, MAX_JSON_BYTES)
    try:
        data = DraftRequest.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(422, "Välj mall och lokal modell, och ange en transkribering. Kontrollera textens längd.") from None
    template = TEMPLATE_MAP.get(data.template_id)
    if template is None:
        raise HTTPException(422, "Okänd mall.")
    messages = build_messages(data, template)
    output = await model_json(data, messages, output_schema(template))
    try:
        result = validate_output(output, template)
    except (KeyError, ValueError, TypeError):
        raise HTTPException(502, "Modellen följde inte mallens struktur. Ditt utkast finns kvar. Försök igen eller byt modell.") from None
    result["model"] = data.model
    return result


@app.post("/api/audit")
async def audit(request: Request):
    raw = await read_limited(request, MAX_JSON_BYTES)
    try:
        data = AuditRequest.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(422, "Välj mall och lokal modell, och ange både transkribering och utkast.") from None
    template = TEMPLATE_MAP.get(data.template_id)
    if template is None:
        raise HTTPException(422, "Okänd mall.")
    sources = {"transcript": data.transcript, "context": data.context, "draft": data.current_draft}
    schema = audit_schema()
    messages = bounded_messages(AUDIT_PROMPT, {
        "arbetsmall": template["name"], "fält": template["sections"],
        "mallregler": template.get("rules", []), **sources,
    }, schema)
    output = await model_json(data, messages, schema)
    try:
        result = validate_audit(output, sources)
    except (ValueError, KeyError, TypeError):
        raise HTTPException(502, "AI-kontrollen gav ogiltigt format eller citat som inte finns i underlaget. "
                                "Ingen kontroll har godkänts. Ditt utkast är oförändrat.") from None
    return {**result, "model": data.model}


# There is intentionally no Lifecare endpoint, browser automation, or clipboard side effect.
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


def download_model():
    """Only explicit setup accesses Hugging Face; never sends audio or text."""
    os.environ["HF_HUB_OFFLINE"] = "0"
    from huggingface_hub import HfApi, hf_hub_download
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    revision = HfApi().model_info(MODEL_ID).sha
    print("Hämtar Pianissimo ONNX int8 från KlangAI. Inga ljud eller texter skickas.")
    for name in MODEL_FILES:
        hf_hub_download(MODEL_ID, filename=name, revision=revision, local_dir=str(MODEL_DIR))
    (MODEL_DIR / "download.json").write_text(json.dumps({"repository": MODEL_ID, "revision": revision,
                                                       "files": MODEL_FILES}, indent=2), encoding="utf-8")
    print(f"Modellen finns i {MODEL_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lokal Social Transcript-prototyp")
    parser.add_argument("--download-model", action="store_true", help="Hämta Pianissimo en gång (internet krävs)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.download_model:
        download_model()
    else:
        import uvicorn
        print(f"Öppna http://127.0.0.1:{args.port} — Ctrl+C avslutar.")
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="critical")
