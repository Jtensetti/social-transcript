"""Browser smoke test with synthetic audio and mocked model replies, never real case data.
Start python app.py first. Install playwright and its Chromium, then run this script.
CHROMIUM_PATH may point to a system Chromium. Screenshots only when SCREENSHOT_DIR is set.
"""
import io
import json
import os
import wave
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

BASE = "http://127.0.0.1:8765"
with urlopen(BASE + "/api/config") as response:
    templates = {t["id"]: t for t in json.load(response)["templates"]}
state = {"audit_fail": False, "audit_empty": False, "audit_requests": [], "fail": False, "requests": [], "audio": False, "attack": True}


def status(route):
    route.fulfill(json={"models": ["a-test:local", "gemma4:e4b"], "ollama_available": True,
                        "asr_ready": True, "asr_loaded": False, "message": ""})


def transcribe(route):
    raw = route.request.post_data_buffer
    assert raw[:4] == b"RIFF"
    with wave.open(io.BytesIO(raw)) as wav:
        assert wav.getnchannels() == 1 and wav.getsampwidth() == 2
        assert wav.getnframes() / wav.getframerate() > 0.2
        assert wav.getframerate() == 16000
    state["audio"] = True
    route.fulfill(json={"text": "Personen uppger att vardagen fungerar bättre. Ett nytt samtal har bokats till fredag.",
                        "segments": [], "duration": 1})


def draft(route):
    data = route.request.post_data_json
    state["requests"].append(data)
    if state["fail"]:
        route.fulfill(status=502, json={"detail": "Test: modellen kunde inte svara. Ditt utkast finns kvar."})
        return
    sections = [{"id": s["id"], "title": s["title"], "text": ""} for s in templates[data["template_id"]]["sections"]]
    sections[0]["text"] = "Personen uppger att vardagen fungerar bättre."
    sections[-1]["text"] = "Ett nytt samtal har bokats till fredag. Datum framgår inte av underlaget."
    review = ['<img src=x onerror="window.injected=true">'] if state["attack"] else ["Kontrollera vilket datum som avses med fredag."]
    state["attack"] = False
    route.fulfill(json={"template_id": data["template_id"], "model": data["model"],
                        "sections": sections, "review": review})


def audit(route):
    data = route.request.post_data_json
    state["audit_requests"].append(data)
    if state["audit_fail"]:
        route.fulfill(status=502, json={"detail": "Test: källkontrollen misslyckades; utkastet är oförändrat."})
        return
    findings = [] if state["audit_empty"] else [{
        "kind": "missing", "source": "transcript", "quote": data["transcript"],
        "message": '<img src=x onerror="window.injected=true"> Kontrollera underlaget.'}]
    route.fulfill(json={"model": data["model"], "findings": findings})


with sync_playwright() as p:
    kwargs = {"headless": True, "args": ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--no-sandbox"]}
    if os.environ.get("CHROMIUM_PATH"):
        kwargs["executable_path"] = os.environ["CHROMIUM_PATH"]
    browser = p.chromium.launch(**kwargs)
    context = browser.new_context(viewport={"width": 1440, "height": 1100}, permissions=["microphone", "clipboard-read", "clipboard-write"])
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/status", status)
    page.route("**/api/transcribe", transcribe)
    page.route("**/api/draft", draft)
    page.route("**/api/audit", audit)
    page.goto(BASE)
    expect(page.locator("#model")).to_have_value("gemma4:e4b")
    expect(page.locator("#template option")).to_have_count(7)
    expect(page.locator("#lifecare")).to_be_disabled()
    screenshots = Path(os.environ["SCREENSHOT_DIR"]) if os.environ.get("SCREENSHOT_DIR") else None
    if screenshots:
        screenshots.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshots / "initial.png"), full_page=True)
    page.locator("#context").fill("Detta är ett hembesök.")
    page.locator("#find-mics").click()
    page.locator("#record").click()
    expect(page.locator("#record-label")).to_have_text("Stoppa")
    expect(page.locator("#context")).to_have_value("Detta är ett hembesök.")
    page.wait_for_timeout(1100)
    page.locator("#transcribe").click()
    expect(page.locator(".note-field")).to_have_count(6)
    expect(page.locator("#record-label")).to_have_text("Start")
    assert state["audio"]
    assert not page.evaluate("Boolean(window.injected)")
    assert page.locator("#review img").count() == 0
    page.locator("#field-kontakt").fill("Manuellt redigerad formulering.")
    page.locator("#context").fill("Detta är ett hembesök. Skriv mer kortfattat.")
    page.locator("#generate").click()
    expect(page.locator("#versions option")).to_have_count(2)
    assert "Manuellt redigerad" in state["requests"][-1]["current_draft"]
    assert "Skriv mer kortfattat" in state["requests"][-1]["context"]
    page.locator("#versions").select_option("0")
    expect(page.locator("#context")).to_have_value("Detta är ett hembesök.")
    expect(page.locator("#field-kontakt")).to_have_value("Manuellt redigerad formulering.")
    for template_id in ("ibic", "asi", "freda", "esther", "iup", "lon"):
        page.locator("#template").select_option(template_id)
        page.locator("#generate").click()
        expect(page.locator(".note-field")).to_have_count(len(templates[template_id]["sections"]))
        assert state["requests"][-1]["template_id"] == template_id
        assert state["requests"][-1]["current_draft"] == ""
    expect(page.locator("#context")).to_have_attribute("placeholder", templates["lon"]["context_hint"])
    before_audit = page.locator(".note-field textarea").first.input_value()
    page.locator("#audit-button").click()
    expect(page.locator("#audit-status")).to_contain_text("möjliga avvikelser")
    assert page.locator(".note-field textarea").first.input_value() == before_audit
    assert before_audit in state["audit_requests"][-1]["current_draft"]
    assert not page.evaluate("Boolean(window.injected)")
    assert page.locator("#audit-list img").count() == 0
    assert page.locator("#audit-list blockquote").count() == 1
    page.locator(".note-field textarea").first.fill("En manuell ändring.")
    expect(page.locator("#audit-status")).to_contain_text("inaktuell")
    expect(page.locator("#audit-list li")).to_have_count(0)
    page.locator(".note-field textarea").first.fill(before_audit)
    expect(page.locator("#audit-list li")).to_have_count(1)
    old_context = page.locator("#context").input_value()
    page.locator("#context").fill("Ändrad kontext")
    expect(page.locator("#audit-status")).to_contain_text("inaktuell")
    page.locator("#context").fill(old_context)
    old_transcript = page.locator("#transcript").input_value()
    page.locator("#transcript").fill(old_transcript + " Ändring.")
    expect(page.locator("#audit-status")).to_contain_text("inaktuell")
    page.locator("#transcript").fill(old_transcript)
    current_version = page.locator("#versions").input_value()
    page.locator("#versions").select_option("0")
    expect(page.locator("#source-audit")).to_be_hidden()
    page.locator("#versions").select_option(current_version)
    expect(page.locator("#source-audit")).to_be_visible()
    state["audit_fail"] = True
    page.locator("#audit-button").click()
    expect(page.locator("#notification")).to_contain_text("källkontrollen misslyckades")
    expect(page.locator("#audit-list li")).to_have_count(1)
    assert page.locator(".note-field textarea").first.input_value() == before_audit
    state["audit_fail"] = False; state["audit_empty"] = True
    page.locator("#audit-button").click()
    expect(page.locator("#audit-status")).to_contain_text("inte ett godkännande")
    expect(page.locator("#audit-list li")).to_have_count(0)
    before = page.locator(".note-field textarea").first.input_value()
    state["fail"] = True
    page.locator("#generate").click()
    expect(page.locator("#notification")).to_contain_text("Ditt utkast finns kvar")
    assert page.locator(".note-field textarea").first.input_value() == before
    expect(page.locator("#generate")).to_be_enabled()
    state["fail"] = False
    page.locator("#copy").click()
    expect(page.locator("#notification")).to_contain_text("Inget har överförts till Lifecare")
    assert "UTKAST" in page.evaluate("navigator.clipboard.readText()")
    if screenshots:
        page.screenshot(path=str(screenshots / "filled-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    if screenshots:
        page.screenshot(path=str(screenshots / "filled-mobile.png"), full_page=True)
    page.on("dialog", lambda dialog: dialog.accept())
    page.locator("#reset").click()
    expect(page.locator("#transcript")).to_have_value("")
    expect(page.locator("#context")).to_have_value("")
    expect(page.locator("#copy")).to_be_disabled()
    expect(page.locator("#playback")).to_be_hidden()
    expect(page.locator("#source-audit")).to_be_hidden()
    expect(page.locator("#audit-button")).to_be_disabled()
    assert not errors, errors
    browser.close()
print("Browser smoke passed: microphone -> WAV -> transcript -> seven templates -> revision/history -> source audit/staleness/XSS -> error recovery -> clipboard -> mobile -> reset.")
