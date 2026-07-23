class MindspaceTTSPlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.offset = 0;
    this.bufferedFrames = 0;
    this.prebufferFrames = Math.round(sampleRate * 0.12);
    this.sourceRate = 24000;
    this.started = false;
    this.ended = false;
    this.reportedEnd = false;
    this.levelTick = 0;
    this.playedFrames = 0;
    this.port.onmessage = (event) => this.handleMessage(event.data || {});
  }

  reset() {
    this.queue = [];
    this.offset = 0;
    this.bufferedFrames = 0;
    this.started = false;
    this.ended = false;
    this.reportedEnd = false;
    this.playedFrames = 0;
  }

  handleMessage(message) {
    if (message.type === "configure") {
      this.reset();
      this.sourceRate = Number(message.sampleRate) || 24000;
      this.prebufferFrames = Math.max(
        128,
        Math.round(sampleRate * (Number(message.prebufferMs) || 120) / 1000),
      );
      return;
    }
    if (message.type === "push" && message.pcm) {
      const input = new Int16Array(message.pcm);
      if (!input.length) return;
      const ratio = sampleRate / this.sourceRate;
      const output = new Float32Array(Math.max(1, Math.round(input.length * ratio)));
      for (let index = 0; index < output.length; index += 1) {
        const position = index / ratio;
        const left = Math.min(input.length - 1, Math.floor(position));
        const right = Math.min(input.length - 1, left + 1);
        const fraction = position - left;
        output[index] = ((input[left] * (1 - fraction)) + (input[right] * fraction)) / 32768;
      }
      this.queue.push(output);
      this.bufferedFrames += output.length;
      return;
    }
    if (message.type === "end") {
      this.ended = true;
      return;
    }
    if (message.type === "stop") {
      this.reset();
      this.reportedEnd = true;
      this.port.postMessage({ type: "ended" });
    }
  }

  process(_inputs, outputs) {
    const output = outputs[0] && outputs[0][0];
    if (!output) return true;
    output.fill(0);

    if (!this.started && (this.bufferedFrames >= this.prebufferFrames || (this.ended && this.bufferedFrames))) {
      this.started = true;
      this.port.postMessage({ type: "started" });
    }

    let written = 0;
    let energy = 0;
    if (this.started) {
      while (written < output.length && this.queue.length) {
        const current = this.queue[0];
        const available = current.length - this.offset;
        const count = Math.min(output.length - written, available);
        const part = current.subarray(this.offset, this.offset + count);
        output.set(part, written);
        for (let index = 0; index < part.length; index += 1) energy += part[index] * part[index];
        written += count;
        this.offset += count;
        this.bufferedFrames -= count;
        if (this.offset >= current.length) {
          this.queue.shift();
          this.offset = 0;
        }
      }
      this.playedFrames += written;
    }

    this.levelTick += 1;
    if (this.levelTick >= 4) {
      this.levelTick = 0;
      this.port.postMessage({
        type: "level",
        value: Math.min(1, Math.sqrt(energy / Math.max(1, written)) * 4),
        playedFrames: this.playedFrames,
        outputSampleRate: sampleRate,
      });
    }

    if (this.ended && this.bufferedFrames <= 0 && !this.reportedEnd) {
      this.reportedEnd = true;
      this.port.postMessage({ type: "ended" });
    }
    return true;
  }
}

registerProcessor("mindspace-tts-playback", MindspaceTTSPlaybackProcessor);
