/* Shared voice capture: records 16kHz mono 16-bit PCM WAV in the browser
   (the exact format the on-prem speech engine consumes), with a
   MediaRecorder fallback. Usage:
     const rec = await captureVoiceWav();   // after user gesture
     ...user speaks...
     const blob = await rec.stop();         // -> Blob (throws on empty/fail)
   Never leaves the UI stuck: every failure path throws a displayable Error. */

const TARGET_RATE = 16000;

function encodeWav(samples, rate) {
  let data = samples;
  if (rate !== TARGET_RATE && samples.length) {
    const ratio = rate / TARGET_RATE;
    const out = new Float32Array(Math.floor(samples.length / ratio));
    for (let i = 0; i < out.length; i++) out[i] = samples[Math.min(samples.length - 1, Math.floor(i * ratio))];
    data = out;
  }
  const n = data.length;
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const wstr = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  wstr(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); wstr(8, 'WAVE');
  wstr(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
  v.setUint16(22, 1, true); v.setUint32(24, TARGET_RATE, true);
  v.setUint32(28, TARGET_RATE * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  wstr(36, 'data'); v.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) {
    const s = Math.max(-1, Math.min(1, data[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: 'audio/wav' });
}

function fallbackRecorder(stream) {
  // Browser-encoded audio fallback. The server sniffs the container and the
  // local engine transcribes WAV; anything else gets an honest error.
  let mr;
  try {
    mr = new MediaRecorder(stream);
  } catch {
    stream.getTracks().forEach((tr) => tr.stop());
    throw new Error('Microphone unavailable in this browser.');
  }
  const parts = [];
  let done;
  const finished = new Promise((res, rej) => { done = { res, rej }; });
  mr.ondataavailable = (e) => { if (e.data.size) parts.push(e.data); };
  mr.onerror = () => {
    stream.getTracks().forEach((tr) => tr.stop());
    done.rej(new Error('Recording failed in this browser.'));
  };
  mr.onstop = () => {
    stream.getTracks().forEach((tr) => tr.stop());
    const blob = new Blob(parts, { type: mr.mimeType || 'audio/webm' });
    if (!blob || blob.size < 100) done.rej(new Error('Empty recording — please speak and retry.'));
    else done.res(blob);
  };
  mr.start();
  return { stop: () => { try { mr.state !== 'inactive' && mr.stop(); } catch { /* noop */ } return finished; } };
}

export async function captureVoiceWav() {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('Microphone unavailable in this browser.');
  }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true }).catch(() => {
    throw new Error('Microphone permission denied or unavailable.');
  });
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return fallbackRecorder(stream);
  const ctx = new AC({ sampleRate: TARGET_RATE });
  const src = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  proc.onaudioprocess = (e) => { chunks.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
  const sink = ctx.createGain();
  sink.gain.value = 0; // avoid feedback; keeps callbacks firing
  src.connect(proc); proc.connect(sink); sink.connect(ctx.destination);
  let stopped = false;
  return {
    stop: async () => {
      if (stopped) throw new Error('Recording already stopped.');
      stopped = true;
      try { proc.disconnect(); src.disconnect(); sink.disconnect(); } catch { /* noop */ }
      try { await ctx.close(); } catch { /* noop */ }
      stream.getTracks().forEach((tr) => tr.stop());
      const total = chunks.reduce((a, c) => a + c.length, 0);
      if (total < TARGET_RATE / 4) throw new Error('Empty recording — please speak and retry.');
      const flat = new Float32Array(total);
      let o = 0;
      for (const c of chunks) { flat.set(c, o); o += c.length; }
      return encodeWav(flat, ctx.sampleRate || TARGET_RATE);
    },
  };
}
