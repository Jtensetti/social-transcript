/* PCM16 mono capture. No encoding service, worker download, or microphone data on disk. */
class PCMCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.buffer = new Int16Array(4096);
    this.offset = 0;
    this.frames = 0;
    this.maxFrames = Math.floor(options.processorOptions.maxSeconds * sampleRate);
    this.running = true;
    this.energy = 0;
    this.port.onmessage = ({data}) => {
      if (data === 'stop') {
        this.running = false;
        this.flush();
        this.port.postMessage({type: 'stopped'});
      }
    };
  }
  flush() {
    if (!this.offset) return;
    const pcm = this.buffer.slice(0, this.offset);
    this.port.postMessage({type: 'pcm', buffer: pcm.buffer,
      level: Math.sqrt(this.energy / this.offset)}, [pcm.buffer]);
    this.offset = 0;
    this.energy = 0;
  }
  process(inputs) {
    const channels = inputs[0];
    if (!this.running || !channels?.length) return true;
    for (let i = 0; i < channels[0].length; i++) {
      if (this.frames >= this.maxFrames) {
        this.running = false;
        this.flush();
        this.port.postMessage({type: 'limit'});
        break;
      }
      let value = 0;
      for (const channel of channels) value += channel[i] || 0;
      value = Math.max(-1, Math.min(1, value / channels.length));
      this.energy += value * value;
      this.buffer[this.offset++] = value < 0 ? value * 32768 : value * 32767;
      this.frames++;
      if (this.offset === this.buffer.length) this.flush();
    }
    return true;
  }
}
registerProcessor('pcm-capture', PCMCapture);
