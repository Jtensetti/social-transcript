import {Recorder} from './recorder.js';

const $ = id => document.getElementById(id);
const state = {config: null, busy: false, recording: false, audio: null, audioUrl: null,
  versions: [], active: -1, currentTemplate: '', models: [], timer: null, started: 0};
const recorder = new Recorder(
  level => { $('level').value = Math.min(1, level * 5); },
  () => finishFromDevice('Inspelningen stoppades vid 15 minuter. Du kan nu transkribera den.'),
  () => finishFromDevice('Mikrofonen kopplades bort. Det inspelade ljudet finns kvar.', true),
);

function notify(message, error = false) {
  $('notification').textContent = message;
  $('notification').classList.toggle('error', error);
  $('notification').hidden = !message;
}
async function finishFromDevice(message, error = false) {
  if (!state.recording) return;
  const alreadyBusy = state.busy; setBusy(true);
  try { await stopRecording(); notify(message, error); }
  catch (failure) { showError(failure); }
  finally { setBusy(alreadyBusy); }
}
function showError(error) { notify(error.message || 'Något gick fel. Ditt underlag finns kvar.', true); }
function template() { return state.config?.templates.find(t => t.id === $('template').value); }
function updateControls() {
  $('record').disabled = state.busy || !state.config;
  $('find-mics').disabled = state.busy || state.recording;
  $('microphone').disabled = state.busy || state.recording;
  $('template').disabled = state.busy || !state.config;
  $('transcribe').disabled = state.busy || (!state.audio && !state.recording);
  $('generate').disabled = state.busy || state.recording || !$('transcript').value.trim() || !state.models.includes($('model').value);
  $('refresh-models').disabled = state.busy || state.recording;
  $('model').disabled = state.busy || state.recording || !state.models.length;
  $('versions').disabled = state.busy || state.recording || !state.versions.length;
  $('copy').disabled = state.busy || state.active < 0;
  $('audit-button').disabled = state.busy || state.recording || state.active < 0 ||
    !$('transcript').value.trim() || !draftText().trim() || !state.models.includes($('model').value);
  $('reset').disabled = state.busy;
  $('transcript').readOnly = state.busy;
  $('context').readOnly = state.busy;
  document.querySelectorAll('.note-field textarea').forEach(el => { el.readOnly = state.busy; });
  $('generate').textContent = state.versions.length ? 'Skapa ny version' : 'Skapa utkast';
  document.body.classList.toggle('busy', state.busy);
  $('word-count').textContent = `${$('transcript').value.trim().split(/\s+/).filter(Boolean).length} ord`;
}
function setBusy(value, message = '') {
  state.busy = value;
  $('transcribe').textContent = 'Transkribera';
  updateControls();
  if (message) notify(message);
}
async function api(path, body, raw = false) {
  const options = {cache: 'no-store'};
  if (body !== undefined) {
    options.method = 'POST';
    options.headers = {'X-Session-Token': state.config.token, 'Content-Type': raw ? 'audio/wav' : 'application/json'};
    options.body = raw ? body : JSON.stringify(body);
  }
  let response;
  try { response = await fetch(path, options); }
  catch { throw new Error('Den lokala servern svarar inte. Kontrollera terminalfönstret. Texten finns kvar i sidan.'); }
  let data;
  try { data = await response.json(); }
  catch { throw new Error('Servern gav ett oväntat svar. Starta om appen utan att stänga sidan.'); }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Anropet kunde inte genomföras.');
  return data;
}
async function refreshModels() {
  $('refresh-models').disabled = true;
  try {
    const data = await api('/api/status');
    const preferred = state.models.includes($('model').value) ? $('model').value : state.config.default_model;
    state.models = data.models;
    $('model').replaceChildren();
    for (const name of state.models) $('model').add(new Option(name, name));
    if (!state.models.length) $('model').add(new Option(data.ollama_available ? 'Ingen lokal modell hittades' : 'Starta Ollama', ''));
    else $('model').value = state.models.includes(preferred) ? preferred : state.models[0];
    $('ollama-status').textContent = data.ollama_available ? `Ollama · ${state.models.length} lokal(a) modell(er)` : 'Ollama · inte anslutet';
    $('ollama-status').className = data.ollama_available && state.models.length ? 'ok' : 'not-ready';
    $('asr-status').textContent = data.asr_ready ? 'Pianissimo · redo' : 'Pianissimo · behöver hämtas';
    $('asr-status').className = data.asr_ready ? 'ok' : 'not-ready';
    $('asr-status').title = data.asr_ready ? 'ONNX int8 · lokal CPU' : 'Kör python app.py --download-model';
    if (data.message) notify(data.message, true);
  } finally { updateControls(); }
}
async function listMicrophones(requestPermission = false) {
  if (!navigator.mediaDevices) return;
  if (requestPermission) {
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({audio: true, video: false}); }
    finally { stream?.getTracks().forEach(track => track.stop()); }
  }
  const previous = $('microphone').value;
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'audioinput');
  $('microphone').replaceChildren(new Option('Systemets standardmikrofon', ''));
  devices.filter(d => d.deviceId && d.deviceId !== 'default').forEach((device, i) => {
    $('microphone').add(new Option(device.label || `Mikrofon ${i + 1}`, device.deviceId));
  });
  if ([...$('microphone').options].some(option => option.value === previous)) $('microphone').value = previous;
}
function clearAudio() {
  if (state.audioUrl) URL.revokeObjectURL(state.audioUrl);
  state.audio = state.audioUrl = null;
  $('playback').pause(); $('playback').removeAttribute('src'); $('playback').load(); $('playback').hidden = true;
}
function clearText() {
  $('transcript').value = $('context').value = '';
  state.versions = []; state.active = -1;
  $('versions').replaceChildren(new Option('Ingen version ännu', ''));
  renderEmpty();
}
async function startRecording() {
  const existingWork = state.audio || $('transcript').value.trim() || state.versions.length;
  if (existingWork &&
      !confirm('Starta ett nytt samtal? Nuvarande ljud, transkribering, kontext och versioner tas bort ur appen.')) return;
  setBusy(true);
  try {
    // Do not discard existing work until the microphone has actually opened.
    await recorder.start($('microphone').value, state.config.max_seconds);
    const initialContext = existingWork ? '' : $('context').value;
    clearAudio(); clearText(); $('context').value = initialContext;
    state.recording = true; state.started = Date.now();
    $('record-label').textContent = 'Stoppa'; $('record-status').textContent = 'Spelar in'; $('timer').textContent = '00:00';
    document.body.classList.add('recording');
    state.timer = setInterval(() => {
      const seconds = Math.min(state.config.max_seconds, Math.floor((Date.now() - state.started) / 1000));
      $('timer').textContent = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
    }, 200);
    notify('');
    listMicrophones().catch(() => {});
  } finally { setBusy(false); }
}
async function stopRecording() {
  if (!state.recording) return;
  state.recording = false;
  clearInterval(state.timer);
  state.audio = await recorder.stop();
  document.body.classList.remove('recording');
  $('record-label').textContent = 'Start'; $('record-status').textContent = 'Inspelning klar'; $('level').value = 0;
  if (state.audio) {
    state.audioUrl = URL.createObjectURL(state.audio);
    $('playback').src = state.audioUrl; $('playback').hidden = false;
  }
  updateControls();
}
function renderEmpty() {
  state.active = -1; state.currentTemplate = $('template').value;
  const chosen = template();
  $('draft-name').textContent = chosen ? `${chosen.name} · arbetsmall` : 'Arbetsmall';
  $('template-subtitle').textContent = chosen?.subtitle || '';
  $('template-notice').textContent = chosen?.notice || '';
  setContextHint(chosen);
  $('source-audit').hidden = true; $('audit-list').replaceChildren();
  $('review').hidden = true; $('review-list').replaceChildren();
  if (state.versions.length) {
    const placeholder = new Option('Tidigare versioner', '', true, true); placeholder.disabled = true;
    $('versions').prepend(placeholder);
  }
  const box = document.createElement('div'); box.className = 'empty-state';
  const icon = document.createElement('span'); icon.className = 'empty-number'; icon.textContent = 'Aa';
  const heading = document.createElement('h3'); heading.textContent = 'Din anteckning hamnar här.';
  const message = document.createElement('p'); message.textContent = 'Transkribera ett samtal eller börja med en inklistrad text.';
  box.append(icon, heading, message); $('draft-content').replaceChildren(box);
  $('draft-status').textContent = 'Granska alltid texten mot underlaget.';
  updateControls();
}
function readSections() {
  return [...document.querySelectorAll('.note-field textarea')].map(el => ({id: el.dataset.id, title: el.dataset.title, text: el.value}));
}
function draftText() {
  return readSections().filter(s => s.text.trim()).map(s => `${s.title}\n${s.text.trim()}`).join('\n\n');
}
function stashCurrent() {
  if (state.active < 0) return;
  state.versions[state.active].sections = readSections();
}
function renderVersion(index) {
  const version = state.versions[index];
  state.active = index; state.currentTemplate = version.template_id;
  $('template').value = version.template_id;
  $('transcript').value = version.transcript; $('context').value = version.context;
  const chosen = template();
  $('draft-name').textContent = `${chosen.name} · arbetsmall`;
  $('template-subtitle').textContent = chosen.subtitle; $('template-notice').textContent = chosen.notice;
  setContextHint(chosen);
  $('draft-content').replaceChildren();
  for (const section of version.sections) {
    const group = document.createElement('div'); group.className = 'note-field';
    const label = document.createElement('label'); label.htmlFor = `field-${section.id}`; label.textContent = section.title;
    const field = document.createElement('textarea'); field.id = label.htmlFor;
    field.dataset.id = section.id; field.dataset.title = section.title;
    field.rows = Math.min(10, Math.max(2, Math.ceil(section.text.length / 75)));
    field.value = section.text; field.placeholder = 'Ingen uppgift i underlaget.';
    field.spellcheck = false; field.autocomplete = 'off';
    field.addEventListener('input', () => {
      $('draft-status').textContent = 'Manuellt redigerat · granska mot underlaget.';
      renderAudit(); updateControls();
    });
    group.append(label, field); $('draft-content').append(group);
  }
  $('review-list').replaceChildren();
  version.review.forEach(text => { const li = document.createElement('li'); li.textContent = text; $('review-list').append(li); });
  $('review').hidden = !version.review.length;
  $('versions').replaceChildren();
  state.versions.forEach((v, i) => $('versions').add(new Option(`Version ${i + 1} · ${state.config.templates.find(t => t.id === v.template_id)?.name || v.template_id}`, String(i))));
  $('versions').value = String(index);
  $('draft-status').textContent = `Version ${index + 1} · ${version.model} · granska mot underlaget.`;
  renderAudit(); updateControls();
}
function setContextHint(chosen) {
  $('context').placeholder = chosen?.context_hint || 'Komplettera med sammanhang, rätta en uppgift eller be om en annan formulering.';
}
function auditSnapshot() {
  return JSON.stringify({template_id: $('template').value, transcript: $('transcript').value,
    context: $('context').value, current_draft: draftText()});
}
function renderAudit() {
  const audit = state.active >= 0 ? state.versions[state.active]?.audit : null;
  $('source-audit').hidden = !audit;
  $('audit-list').replaceChildren();
  if (!audit) return;
  if (audit.snapshot !== auditSnapshot()) {
    $('audit-status').textContent = 'Texten eller underlaget har ändrats. Kör kontrollen igen; föregående kontroll är inaktuell.';
    return;
  }
  const findings = audit.result.findings;
  $('audit-status').textContent = findings.length
    ? `${findings.length} möjliga avvikelser · ${audit.result.model}. Kontrollera själv; AI-kontrollen kan ha fel.`
    : `AI-kontrollen hittade inga avvikelser · ${audit.result.model}. Detta är inte ett godkännande eller en garanti för att allt finns med.`;
  const kinds = {missing: 'Möjligt bortfall', changed: 'Möjligen ändrad innebörd', unsupported: 'Möjligen utan stöd'};
  const sources = {transcript: 'Transkribering', context: 'Kontext', draft: 'Utkast'};
  for (const finding of findings) {
    const li = document.createElement('li');
    const heading = document.createElement('strong'); heading.textContent = kinds[finding.kind];
    const message = document.createElement('p'); message.textContent = finding.message;
    const quote = document.createElement('blockquote'); quote.textContent = `${sources[finding.source]}: ”${finding.quote}”`;
    li.append(heading, message, quote); $('audit-list').append(li);
  }
}
$('audit-button').addEventListener('click', async () => {
  if (state.active < 0) return;
  setBusy(true, 'Jämför utkast och underlag lokalt. Texten ändras inte…');
  try {
    stashCurrent();
    const snapshot = auditSnapshot();
    const result = await api('/api/audit', {...JSON.parse(snapshot), model: $('model').value});
    state.versions[state.active].audit = {snapshot, result};
    renderAudit(); notify('AI-kontrollen är klar. Utkastet är oförändrat; granska eventuella avvikelser själv.');
  } catch (error) { showError(error); }
  finally { setBusy(false); }
});
async function generateDraft() {
  stashCurrent();
  const input = {template_id: $('template').value, model: $('model').value,
    transcript: $('transcript').value, context: $('context').value,
    current_draft: state.currentTemplate === $('template').value ? draftText() : ''};
  const result = await api('/api/draft', input);
  state.versions.push({...result, transcript: input.transcript, context: input.context});
  renderVersion(state.versions.length - 1);
}
$('record').addEventListener('click', async () => {
  try {
    if (state.recording) { setBusy(true); try { await stopRecording(); } finally { setBusy(false); } }
    else await startRecording();
  } catch (error) { showError(error); }
});
$('transcribe').addEventListener('click', async () => {
  if (!state.recording && $('transcript').value.trim() && !confirm('Transkribera inspelningen igen? Redigerad transkribering ersätts. Tidigare utkast finns kvar.')) return;
  setBusy(true, 'Transkriberar lokalt med Pianissimo…');
  try {
    stashCurrent();
    await stopRecording();
    if (!state.audio) throw new Error('Spela in ett samtal först.');
    $('transcribe').textContent = 'Transkriberar…';
    const result = await api('/api/transcribe', state.audio, true);
    $('transcript').value = result.text;
    renderAudit(); // A new transcript invalidates old checks even when draft generation fails.
    if (state.models.includes($('model').value)) {
      notify('Transkriberingen är klar. Den lokala språkmodellen fyller arbetsmallen…');
      await generateDraft();
      notify('Utkastet är klart. Granska texten och komplettera vid behov.');
    } else {
      notify('Transkriberingen är klar. Starta Ollama, uppdatera modellistan och klicka på Skapa utkast.');
    }
  } catch (error) { showError(error); }
  finally { setBusy(false); }
});
$('generate').addEventListener('click', async () => {
  setBusy(true, 'Skapar en ny version lokalt i Ollama…');
  try { await generateDraft(); notify('Den nya versionen är klar. Tidigare versioner finns kvar i listan.'); }
  catch (error) { showError(error); }
  finally { setBusy(false); }
});
$('template').addEventListener('change', () => { stashCurrent(); renderEmpty(); });
$('versions').addEventListener('change', () => { const next = Number($('versions').value); stashCurrent(); renderVersion(next); });
$('transcript').addEventListener('input', () => { renderAudit(); updateControls(); });
$('context').addEventListener('input', renderAudit);
$('model').addEventListener('change', updateControls);
$('refresh-models').addEventListener('click', () => refreshModels().catch(showError));
$('find-mics').addEventListener('click', () => listMicrophones(true).catch(() => notify('Tillåt mikrofonen i webbläsaren och försök igen.', true)));
$('copy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(`UTKAST — ${template().name} arbetsmall — ej granskad\n\n${draftText()}`);
    notify('Texten är kopierad. Inget har överförts till Lifecare.');
  } catch { notify('Webbläsaren tillät inte kopiering. Markera och kopiera texten manuellt.', true); }
});
$('reset').addEventListener('click', async () => {
  if ((state.audio || state.recording || $('transcript').value || $('context').value || state.versions.length) &&
      !confirm('Rensa ljud, transkribering, kontext och alla versioner ur appen?')) return;
  setBusy(true);
  try {
    clearInterval(state.timer); state.recording = false; await recorder.dispose();
    clearAudio(); clearText(); $('timer').textContent = '00:00'; $('level').value = 0;
    $('record-label').textContent = 'Start'; $('record-status').textContent = 'Redo att spela in';
    document.body.classList.remove('recording'); notify('');
  } finally { setBusy(false); }
});
window.addEventListener('beforeunload', event => {
  if (state.recording || state.audio || $('transcript').value || $('context').value || state.versions.length) {
    event.preventDefault(); event.returnValue = '';
  }
});
window.addEventListener('pagehide', () => { recorder.dispose(); });
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
navigator.mediaDevices?.addEventListener('devicechange', () => { if (!state.recording) listMicrophones().catch(() => {}); });
(async () => {
  try {
    state.config = await api('/api/config');
    $('template').replaceChildren();
    const groups = new Map();
    state.config.templates.forEach(item => {
      const category = item.category || 'Övriga mallar';
      if (!groups.has(category)) {
        const group = document.createElement('optgroup'); group.label = category;
        groups.set(category, group); $('template').append(group);
      }
      groups.get(category).append(new Option(item.name, item.id));
    });
    renderEmpty();
    await Promise.all([refreshModels(), listMicrophones()]);
    updateControls();
  } catch (error) { showError(error); }
})();
