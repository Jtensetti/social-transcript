export function wavBlob(parts, sampleRate) {
  const bytes = parts.reduce((sum, part) => sum + part.byteLength, 0);
  const buffer = new ArrayBuffer(44);
  const view = new DataView(buffer);
  const text = (offset, value) => [...value].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  text(0, 'RIFF'); view.setUint32(4, 36 + bytes, true); text(8, 'WAVE'); text(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, 'data');
  view.setUint32(40, bytes, true);
  return new Blob([buffer, ...parts], {type: 'audio/wav'});
}

export class Recorder {
  constructor(onLevel, onLimit, onDeviceEnded) {
    this.onLevel = onLevel;
    this.onLimit = onLimit;
    this.onDeviceEnded = onDeviceEnded;
    this.parts = [];
    this.stream = null;
    this.context = null;
    this.node = null;
  }
  async start(deviceId, maxSeconds) {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('Öppna sidan via http://127.0.0.1:8765 i Chrome eller Edge.');
    this.parts = [];
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({audio: {
        deviceId: deviceId ? {exact: deviceId} : undefined, channelCount: 1,
        echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      }, video: false});
      this.stream.getAudioTracks().forEach(track => track.addEventListener('ended', this.onDeviceEnded));
      try { this.context = new AudioContext({sampleRate: 16000}); }
      catch { this.context = new AudioContext(); }
      await this.context.audioWorklet.addModule('/static/pcm-worklet.js');
      this.node = new AudioWorkletNode(this.context, 'pcm-capture', {processorOptions: {maxSeconds}});
      this.node.port.onmessage = ({data}) => {
        if (data.type === 'pcm') { this.parts.push(data.buffer); this.onLevel(data.level); }
        if (data.type === 'limit') this.onLimit();
        if (data.type === 'stopped') this.stopped?.();
      };
      this.source = this.context.createMediaStreamSource(this.stream);
      this.mute = this.context.createGain(); this.mute.gain.value = 0;
      this.source.connect(this.node); this.node.connect(this.mute); this.mute.connect(this.context.destination);
      await this.context.resume();
    } catch (error) {
      await this.dispose();
      if (error.name === 'NotAllowedError') throw new Error('Tillåt mikrofonen i webbläsaren och kontrollera Windows mikrofonbehörighet.');
      if (['NotFoundError', 'OverconstrainedError'].includes(error.name)) throw new Error('Mikrofonen hittades inte. Klicka på Hitta mikrofoner och välj igen.');
      if (error.name === 'NotReadableError') throw new Error('Mikrofonen kunde inte öppnas. Kontrollera att den fungerar och inte är låst av ett annat program.');
      throw error;
    }
  }
  async stop() {
    if (!this.context || !this.node) return null;
    const rate = this.context.sampleRate;
    await new Promise(resolve => {
      const timeout = setTimeout(resolve, 1500);
      this.stopped = () => { clearTimeout(timeout); resolve(); };
      this.node.port.postMessage('stop');
    });
    const blob = wavBlob(this.parts, rate);
    await this.dispose();
    this.parts = [];
    return blob;
  }
  async dispose() {
    this.stream?.getTracks().forEach(track => {
      track.removeEventListener('ended', this.onDeviceEnded); track.stop();
    });
    this.node?.disconnect(); this.source?.disconnect(); this.mute?.disconnect();
    if (this.context && this.context.state !== 'closed') await this.context.close();
    this.stream = this.node = this.context = this.source = this.mute = null;
    this.parts = [];
  }
}
