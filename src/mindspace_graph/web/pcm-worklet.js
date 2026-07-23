class MindspacePCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 16000;
    this.input = [];
    this.inputLength = 0;
    this.targetSamples = Math.max(320, Math.round(sampleRate * 0.04));
    this.noiseGateDb = -45;
    this.noiseGateLinear = 10 ** (this.noiseGateDb / 20);
    this.adaptive = true;
    this.calibrationMs = 1500;
    this.noiseMarginDb = 8;
    this.elapsedMs = 0;
    this.noiseFloorDb = -60;
    this.calibrationValues = [];
    this.lastReportedFloor = -100;
    this.gateHoldFrames = 0;
    this.previousInput = 0;
    this.previousHighPass = 0;
    const rc = 1 / (2 * Math.PI * 100);
    this.highPassAlpha = rc / (rc + 1 / sampleRate);
    this.port.onmessage = (event) => {
      if (event.data?.type !== "configure") return;
      const next = Number(event.data.noiseGateDb);
      if (Number.isFinite(next)) {
        this.noiseGateDb = Math.max(-70, Math.min(-20, next));
        this.noiseGateLinear = 10 ** (this.noiseGateDb / 20);
      }
      this.adaptive = event.data.adaptive !== false;
      const calibrationMs = Number(event.data.calibrationMs);
      if (Number.isFinite(calibrationMs)) this.calibrationMs = Math.max(500, Math.min(5000, calibrationMs));
      const marginDb = Number(event.data.noiseMarginDb);
      if (Number.isFinite(marginDb)) this.noiseMarginDb = Math.max(4, Math.min(20, marginDb));
    };
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel || channel.length === 0) return true;
    this.input.push(channel.slice());
    this.inputLength += channel.length;
    if (this.inputLength < this.targetSamples) return true;

    const merged = new Float32Array(this.inputLength);
    let offset = 0;
    let sum = 0;
    for (const block of this.input) {
      for (let index = 0; index < block.length; index += 1) {
        const input = block[index];
        const filtered = this.highPassAlpha
          * (this.previousHighPass + input - this.previousInput);
        this.previousInput = input;
        this.previousHighPass = filtered;
        merged[offset] = filtered;
        sum += filtered * filtered;
        offset += 1;
      }
    }
    this.input = [];
    this.inputLength = 0;

    const rms = Math.sqrt(sum / Math.max(1, merged.length));
    const inputDb = 20 * Math.log10(Math.max(rms, 1e-9));
    const frameMs = merged.length / sampleRate * 1000;
    this.elapsedMs += frameMs;
    if (this.adaptive && this.elapsedMs <= this.calibrationMs) {
      this.calibrationValues.push(inputDb);
      const ordered = [...this.calibrationValues].sort((a, b) => a - b);
      this.noiseFloorDb = ordered[Math.floor(ordered.length * 0.6)] ?? inputDb;
    } else if (this.adaptive && inputDb < this.noiseFloorDb + 6 && this.gateHoldFrames === 0) {
      this.noiseFloorDb = this.noiseFloorDb * 0.985 + inputDb * 0.015;
    }
    const effectiveGateDb = this.adaptive
      ? Math.max(this.noiseGateDb, Math.min(-20, this.noiseFloorDb + this.noiseMarginDb))
      : this.noiseGateDb;
    const effectiveGateLinear = 10 ** (effectiveGateDb / 20);
    if (rms >= effectiveGateLinear) this.gateHoldFrames = 4;
    else this.gateHoldFrames = Math.max(0, this.gateHoldFrames - 1);
    const gateOpen = this.gateHoldFrames > 0;
    const ratio = sampleRate / this.targetRate;
    const output = new Int16Array(Math.max(1, Math.floor(merged.length / ratio)));
    for (let index = 0; index < output.length; index += 1) {
      const sample = gateOpen
        ? merged[Math.min(merged.length - 1, Math.floor(index * ratio))]
        : 0;
      output[index] = Math.max(-32768, Math.min(32767, Math.round(sample * 32767)));
    }
    const level = gateOpen ? Math.min(1, rms * 7) : 0;
    const calibrated = !this.adaptive || this.elapsedMs >= this.calibrationMs;
    this.port.postMessage({
      pcm: output.buffer,
      level,
      inputDb,
      noiseFloorDb: this.noiseFloorDb,
      gateThresholdDb: effectiveGateDb,
      calibrated,
    }, [output.buffer]);
    return true;
  }
}

registerProcessor("mindspace-pcm", MindspacePCMProcessor);
