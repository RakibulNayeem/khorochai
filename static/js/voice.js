// Voice input: record the mic as 16 kHz mono WAV in the browser, send it to /ai/transcribe/,
// and put the returned text into the box so the user can check it before submitting.
(function () {
  const MAX_SECONDS = 60;
  const MIN_SECONDS = 0.6;
  const TARGET_RATE = 16000;

  function csrfToken() {
    const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : '';
  }

  function downsample(chunks, inputRate) {
    const length = chunks.reduce((n, c) => n + c.length, 0);
    const input = new Float32Array(length);
    let offset = 0;
    for (const c of chunks) { input.set(c, offset); offset += c.length; }
    if (inputRate === TARGET_RATE) return input;
    const ratio = inputRate / TARGET_RATE;
    const out = new Float32Array(Math.floor(length / ratio));
    for (let i = 0; i < out.length; i++) {   // average the samples that fall into each output sample
      const start = Math.floor(i * ratio), end = Math.min(Math.floor((i + 1) * ratio), length);
      let sum = 0;
      for (let j = start; j < end; j++) sum += input[j];
      out[i] = sum / Math.max(end - start, 1);
    }
    return out;
  }

  function encodeWav(samples) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const text = (pos, s) => { for (let i = 0; i < s.length; i++) view.setUint8(pos + i, s.charCodeAt(i)); };
    text(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); text(8, 'WAVE');
    text(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, TARGET_RATE, true); view.setUint32(28, TARGET_RATE * 2, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    text(36, 'data'); view.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Blob([view], { type: 'audio/wav' });
  }

  function showError(btn, message) {
    const field = btn.closest('.voice-field');
    let box = field.parentElement.querySelector('.voice-error');
    if (!box) {
      box = document.createElement('div');
      box.className = 'voice-error small';
      box.setAttribute('role', 'alert');
      field.insertAdjacentElement('afterend', box);
    }
    box.textContent = message;
    clearTimeout(box._timer);
    box._timer = setTimeout(() => box.remove(), 7000);
  }

  function setState(btn, state) {
    btn.dataset.state = state;
    const label = btn.dataset['label' + state[0].toUpperCase() + state.slice(1)];
    btn.setAttribute('aria-label', label);
    btn.title = label;
    btn.setAttribute('aria-pressed', state === 'recording' ? 'true' : 'false');
  }

  function insertText(target, text) {
    const current = target.value.trim();
    target.value = current ? current + ' ' + text : text;
    target.dispatchEvent(new Event('input', { bubbles: true }));
    target.focus();
    target.setSelectionRange(target.value.length, target.value.length);
  }

  function setup(btn) {
    const target = document.getElementById(btn.dataset.voiceTarget);
    if (!target) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !(window.AudioContext || window.webkitAudioContext)) {
      btn.hidden = true;   // very old browser: just type
      return;
    }
    let rec = null;

    async function start() {
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      } catch (e) {
        showError(btn, btn.dataset.errorMic);
        return;
      }
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const source = ctx.createMediaStreamSource(stream);
      const processor = ctx.createScriptProcessor(4096, 1, 1);
      const chunks = [];
      processor.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      source.connect(processor);
      processor.connect(ctx.destination);
      const startedAt = performance.now();
      const timeEl = btn.querySelector('.voice-time');
      timeEl.textContent = '0:00';
      const tick = setInterval(() => {
        const s = Math.floor((performance.now() - startedAt) / 1000);
        timeEl.textContent = Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
        if (s >= MAX_SECONDS) stop();
      }, 250);
      rec = { stream, ctx, source, processor, chunks, startedAt, tick };
      setState(btn, 'recording');
    }

    async function stop() {
      if (!rec) return;
      const { stream, ctx, source, processor, chunks, startedAt, tick } = rec;
      rec = null;
      clearInterval(tick);
      processor.disconnect(); source.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      const seconds = (performance.now() - startedAt) / 1000;
      const rate = ctx.sampleRate;
      ctx.close();
      if (seconds < MIN_SECONDS || !chunks.length) {
        setState(btn, 'idle');
        showError(btn, btn.dataset.errorShort);
        return;
      }
      setState(btn, 'busy');
      const form = new FormData();
      form.append('audio', encodeWav(downsample(chunks, rate)), 'voice.wav');
      try {
        const res = await fetch(btn.dataset.url, {
          method: 'POST', body: form, credentials: 'same-origin',
          headers: { 'X-CSRFToken': csrfToken() },
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.text) insertText(target, data.text);
        else showError(btn, data.error || btn.dataset.errorNetwork);
      } catch (e) {
        showError(btn, btn.dataset.errorNetwork);
      }
      setState(btn, 'idle');
    }

    setState(btn, 'idle');
    btn.addEventListener('click', () => {
      if (btn.dataset.state === 'recording') stop();
      else if (btn.dataset.state === 'idle') start();
    });
  }

  document.querySelectorAll('.voice-btn').forEach(setup);
})();
