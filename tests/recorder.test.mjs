import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {wavBlob} from '../static/recorder.js';

function capture(maxSeconds = 15) {
  const messages = [];
  let Processor;
  const sandbox = {
    sampleRate: 16000, Int16Array, Math,
    AudioWorkletProcessor: class { constructor() { this.port = {postMessage: data => messages.push(data)}; } },
    registerProcessor: (name, implementation) => { Processor = implementation; },
  };
  vm.runInNewContext(fs.readFileSync(new URL('../static/pcm-worklet.js', import.meta.url), 'utf8'), sandbox);
  return {processor: new Processor({processorOptions: {maxSeconds}}), messages};
}

test('WAV header has correct mono PCM16 format, rate, length and data', async () => {
  const bytes = new Int16Array([0, 32767, -32768]);
  const blob = wavBlob([bytes.buffer], 16000);
  const raw = await blob.arrayBuffer(); const view = new DataView(raw);
  assert.equal(blob.type, 'audio/wav'); assert.equal(raw.byteLength, 50);
  assert.equal(new TextDecoder().decode(raw.slice(0, 4)), 'RIFF');
  assert.equal(view.getUint16(22, true), 1); assert.equal(view.getUint32(24, true), 16000);
  assert.equal(view.getUint16(34, true), 16); assert.equal(view.getUint32(40, true), 6);
  assert.equal(view.getInt16(46, true), 32767); assert.equal(view.getInt16(48, true), -32768);
});

test('stop flushes the partial audio frame before acknowledgement', () => {
  const {processor, messages} = capture();
  processor.process([[new Float32Array(128).fill(0.5)]]);
  assert.equal(messages.length, 0);
  processor.port.onmessage({data: 'stop'});
  assert.equal(messages[0].type, 'pcm'); assert.equal(messages[0].buffer.byteLength, 256);
  assert.equal(messages[1].type, 'stopped'); assert.equal(messages[0].level, 0.5);
});

test('maximum duration retains exactly the permitted number of samples', () => {
  const {processor, messages} = capture(1);
  for (let i = 0; i < 200; i++) processor.process([[new Float32Array(128).fill(0.1)]]);
  assert.equal(messages.filter(m => m.type === 'pcm').reduce((n, m) => n + m.buffer.byteLength / 2, 0), 16000);
  assert.equal(messages.filter(m => m.type === 'limit').length, 1);
});

test('stereo inputs mix to mono instead of discarding a channel', () => {
  const {processor, messages} = capture();
  processor.process([[new Float32Array(128).fill(0.5), new Float32Array(128).fill(-0.5)]]);
  processor.port.onmessage({data: 'stop'});
  assert.ok(new Int16Array(messages[0].buffer).every(n => n === 0));
});

test('out of range samples are clipped, not wrapped', () => {
  const {processor, messages} = capture();
  processor.process([[new Float32Array([2, -2])]]);
  processor.port.onmessage({data: 'stop'});
  assert.deepEqual([...new Int16Array(messages[0].buffer)], [32767, -32768]);
});
