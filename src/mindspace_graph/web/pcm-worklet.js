class MindspacePCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 16000;
    this.blocks = [];
    this.inputLength = 0;
    this.targetSamples = Math.max(320, Math.round(sampleRate * 0.04));
    this.previousInput = 0;
    this.previousHighPass = 0;
    const rc = 1 / (2 * Math.PI * 100);
    this.highPassAlpha = rc / (rc + 1 / sampleRate);
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel || channel.length === 0) return true;
    this.blocks.push(channel.slice());
    this.inputLength += channel.length;
    if (this.inputLength < this.targetSamples) return true;

    const merged = new Float32Array(this.inputLength);
    let offset = 0;
    let sumSquares = 0;
    for (const block of this.blocks) {
      for (let index = 0; index < block.length; index += 1) {
        const input = block[index];
        const filtered = this.highPassAlpha
          * (this.previousHighPass + input - this.previousInput);
        this.previousInput = input;
        this.previousHighPass = filtered;
        merged[offset] = filtered;
        sumSquares += filtered * filtered;
        offset += 1;
      }
    }
    this.blocks = [];
    this.inputLength = 0;

    const ratio = sampleRate / this.targetRate;
    const output = new Int16Array(Math.max(1, Math.floor(merged.length / ratio)));
    for (let index = 0; index < output.length; index += 1) {
      const start = Math.floor(index * ratio);
      const end = Math.max(start + 1, Math.min(merged.length, Math.floor((index + 1) * ratio)));
      let sum = 0;
      for (let sourceIndex = start; sourceIndex < end; sourceIndex += 1) {
        sum += merged[sourceIndex];
      }
      const sample = Math.max(-1, Math.min(1, sum / Math.max(1, end - start)));
      output[index] = Math.round(sample * 32767);
    }

    const rms = Math.sqrt(sumSquares / Math.max(1, merged.length));
    this.port.postMessage({
      pcm: output.buffer,
      level: Math.min(1, rms * 7),
      inputDb: 20 * Math.log10(Math.max(rms, 1e-9)),
    }, [output.buffer]);
    return true;
  }
}

registerProcessor("mindspace-pcm", MindspacePCMProcessor);
