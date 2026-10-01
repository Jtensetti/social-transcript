"""Offline DOM integration test. No network, microphone, clipboard or live model.

Loads the real HTML/CSS/JS into about:blank and replaces ONLY fetch with fixture replies.
This complements browser_smoke.py when browser policy blocks localhost/audio access.
"""
import json
import os
import re
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = json.loads((ROOT / "templates.json").read_text(encoding="utf-8"))
CONFIG = {"templates": TEMPLATES, "token": "synthetic-ui-test", "default_model": "gemma4:e4b", "max_seconds": 900}

with sync_playwright() as p:
    options = {"headless": True, "args": ["--no-sandbox"]}
    if os.environ.get("CHROMIUM_PATH"):
        options["executable_path"] = os.environ["CHROMIUM_PATH"]
    browser = p.chromium.launch(**options)
    page = browser.new_page(viewport={"width": 1440, "height": 1100})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    html = re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.S)
    html = re.sub(r'<link\b[^>]*>', '', html)
    page.set_content(html)
    page.add_style_tag(content=(ROOT / "static/style.css").read_text(encoding="utf-8"))
    page.evaluate("config => window.harness = {config, requests: [], fail: false, empty: false}", CONFIG)
    page.evaluate("""() => {
        window.fetch = async (path, options = {}) => {
            const h = window.harness;
            const input = options.body && path !== '/api/transcribe' ? JSON.parse(options.body) : null;
            h.requests.push({path, input});
            let output;
            if (path === '/api/config') output = h.config;
            else if (path === '/api/status') output = {models: ['gemma4:e4b'], ollama_available: true, asr_ready: true};
            else if (path === '/api/transcribe') output = {text: 'En ny fiktiv transkribering.', segments: [], duration: 1};
            else if (h.fail) return new Response(JSON.stringify({detail: 'Syntetiskt fel. Ditt utkast är oförändrat.'}), {status: 502});
            else if (path === '/api/draft') {
                const template = h.config.templates.find(t => t.id === input.template_id);
                output = {template_id: input.template_id, model: input.model, review: [],
                    sections: template.sections.map((s, i) => ({id: s.id, title: s.title,
                        text: i === 0 ? input.transcript : ''}))};
            } else if (path === '/api/audit') {
                output = {model: input.model, findings: h.empty ? [] : [{kind: 'missing', source: 'transcript',
                    quote: input.transcript, message: '<img src=x onerror="window.injected=true"> Jämför uppgiften.'}]};
            } else throw new Error('Unexpected request: ' + path);
            return new Response(JSON.stringify(output), {status: 200});
        };
    }""")
    # Real recorder code is included, but its hardware-dependent methods are never called.
    recorder = (ROOT / "static/recorder.js").read_text(encoding="utf-8").replace('export function ', 'function ').replace('export class ', 'class ')
    script = (ROOT / "static/app.js").read_text(encoding="utf-8").replace("import {Recorder} from './recorder.js';", '')
    page.add_script_tag(content=recorder + '\n' + script)
    expect(page.locator('#template option')).to_have_count(7)
    expect(page.locator('#template optgroup')).to_have_count(3)
    expect(page.locator('#model')).to_have_value('gemma4:e4b')
    expect(page.locator('#lifecare')).to_be_disabled()
    expect(page.locator('#audit-button')).to_be_disabled()
    page.locator('#transcript').fill('Ett fiktivt samtal. Parterna har olika uppfattning.')
    for template in TEMPLATES:
        page.locator('#template').select_option(template['id'])
        page.locator('#generate').click()
        expect(page.locator('.note-field')).to_have_count(len(template['sections']))
        request = page.evaluate('harness.requests.filter(r => r.path === "/api/draft").at(-1).input')
        assert request['template_id'] == template['id']
        assert request['current_draft'] == ''
        if 'context_hint' in template:
            expect(page.locator('#context')).to_have_attribute('placeholder', template['context_hint'])
    expect(page.locator('#versions option')).to_have_count(7)
    page.locator('.note-field textarea').first.fill('En manuellt redigerad anteckning.')
    page.locator('#context').fill('Komplettering: ett nytt möte föreslogs, inget bestämdes.')
    page.locator('#generate').click()
    expect(page.locator('#versions option')).to_have_count(8)
    request = page.evaluate('harness.requests.filter(r => r.path === "/api/draft").at(-1).input')
    assert 'manuellt redigerad' in request['current_draft']
    assert request['context'].startswith('Komplettering:')
    page.locator('#versions').select_option('6')
    expect(page.locator('.note-field textarea').first).to_have_value('En manuellt redigerad anteckning.')
    expect(page.locator('#context')).to_have_value('')
    page.locator('#versions').select_option('7')
    before = page.locator('.note-field textarea').first.input_value()
    page.locator('#audit-button').click()
    expect(page.locator('#audit-status')).to_contain_text('möjliga avvikelser')
    assert page.locator('.note-field textarea').first.input_value() == before
    assert not page.evaluate('Boolean(window.injected)')
    expect(page.locator('#audit-list img')).to_have_count(0)
    expect(page.locator('#audit-list blockquote')).to_have_count(1)
    for selector in ('.note-field textarea', '#context', '#transcript'):
        field = page.locator(selector).first
        original = field.input_value()
        field.fill(original + ' Ändring.')
        expect(page.locator('#audit-status')).to_contain_text('inaktuell')
        expect(page.locator('#audit-list li')).to_have_count(0)
        field.fill(original)
        expect(page.locator('#audit-list li')).to_have_count(1)
    page.locator('#versions').select_option('0')
    expect(page.locator('#source-audit')).to_be_hidden()
    page.locator('#versions').select_option('7')
    expect(page.locator('#source-audit')).to_be_visible()
    page.evaluate('harness.fail = true')
    for button in ('#audit-button', '#generate'):
        page.locator(button).click()
        expect(page.locator('#notification')).to_contain_text('oförändrat')
        assert page.locator('.note-field textarea').first.input_value() == before
        expect(page.locator('#audit-list li')).to_have_count(1)
    page.evaluate('harness.fail = false; harness.empty = true')
    page.locator('#audit-button').click()
    expect(page.locator('#audit-status')).to_contain_text('inte ett godkännande')
    shots = Path(os.environ['SCREENSHOT_DIR']) if os.environ.get('SCREENSHOT_DIR') else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(shots / 'desktop.png'), full_page=True)
    for width in (390, 720, 900):
        page.set_viewport_size({'width': width, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), width
    if shots:
        page.set_viewport_size({'width': 390, 'height': 844})
        page.screenshot(path=str(shots / 'mobile.png'), full_page=True)
    page.on('dialog', lambda dialog: dialog.accept())
    # Re-transcription changes the source even when subsequent drafting fails.
    page.evaluate("state.audio = new Blob(['synthetic']); harness.fail = true; updateControls()")
    page.locator('#transcribe').click()
    expect(page.locator('#transcript')).to_have_value('En ny fiktiv transkribering.')
    expect(page.locator('#notification')).to_contain_text('oförändrat')
    expect(page.locator('#audit-status')).to_contain_text('inaktuell')
    assert page.locator('.note-field textarea').first.input_value() == before
    page.locator('#reset').click()
    expect(page.locator('#transcript')).to_have_value('')
    expect(page.locator('#context')).to_have_value('')
    expect(page.locator('#source-audit')).to_be_hidden()
    expect(page.locator('#audit-button')).to_be_disabled()
    assert not errors, errors
    browser.close()
print('Offline UI passed: seven templates, revisions, history, source check, stale checks, XSS, error recovery, responsive layout, reset. No audio or live models tested.')
