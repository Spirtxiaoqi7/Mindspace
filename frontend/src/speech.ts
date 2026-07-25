const SENTENCE_BOUNDARY = /(?:[。！？!?；;]+|…{2,}|\.{3,}|\n+)/g;
const TRAILING_CLOSERS = new Set(["”", "’", "」", "』", "】", '"', "'"]);
const SPOKEN_BOUNDARY = /[。！？!?；;，,：:…~～\s]$/;

export function normalizeSpeechSegment(value: string): string {
  return value
    .replace(/```[\s\S]*?```/g, "")
    .replace(/[`*_#>]/g, "")
    .replace(/\s+/g, " ")
    .replace(/\s+([。！？!?；;，,])/g, "$1")
    .trim();
}

export function stripLeadingTtsFiller(value: string): string {
  return normalizeSpeechSegment(value)
    .replace(/^嗯+(?=$|[\s，,。.!！？?…~～])[\s，,。.!！？?…~～]*/, "")
    .trim();
}

function extractCompleteSentences(value: string) {
  const sentences: string[] = [];
  let consumed = 0;
  SENTENCE_BOUNDARY.lastIndex = 0;
  let match = SENTENCE_BOUNDARY.exec(value);
  while (match) {
    let end = match.index + match[0].length;
    while (end < value.length && TRAILING_CLOSERS.has(value[end])) end += 1;
    const sentence = normalizeSpeechSegment(value.slice(consumed, end));
    if (sentence) sentences.push(sentence);
    consumed = end;
    SENTENCE_BOUNDARY.lastIndex = end;
    match = SENTENCE_BOUNDARY.exec(value);
  }
  return { sentences, remainder: value.slice(consumed) };
}

function extractFirstCompleteSentence(value: string) {
  SENTENCE_BOUNDARY.lastIndex = 0;
  const match = SENTENCE_BOUNDARY.exec(value);
  if (!match) return { sentence: "", remainder: value };
  let end = match.index + match[0].length;
  while (end < value.length && TRAILING_CLOSERS.has(value[end])) end += 1;
  return {
    sentence: normalizeSpeechSegment(value.slice(0, end)),
    remainder: value.slice(end),
  };
}

export class SpeechSegmenter {
  private buffer = "";
  private parentheses: Array<{ closer: string; spokenStart: number }> = [];
  private voiceBuffer = "";
  private voiceParentheticalBuffer = "";
  private voiceParentheses: string[] = [];
  private firstVoiceSentenceEmitted = false;

  reset() {
    this.buffer = "";
    this.parentheses = [];
    this.voiceBuffer = "";
    this.voiceParentheticalBuffer = "";
    this.voiceParentheses = [];
    this.firstVoiceSentenceEmitted = false;
  }

  feed(chunk: string, flush = false, speakParentheticalContent = false): string[] {
    if (speakParentheticalContent) return this.feedVoice(chunk, flush);

    for (const character of chunk) {
      if (character === "（" || character === "(") {
        this.parentheses.push({
          closer: character === "（" ? "）" : ")",
          spokenStart: this.buffer.length,
        });
        continue;
      }
      if (this.parentheses.length) {
        const current = this.parentheses[this.parentheses.length - 1];
        if (character === current.closer) {
          this.parentheses.pop();
        } else if (character === "（" || character === "(") {
          this.parentheses.push({
            closer: character === "（" ? "）" : ")",
            spokenStart: this.buffer.length,
          });
        }
        continue;
      }
      if (character === "）" || character === ")") continue;
      this.buffer += character;
    }

    const extracted = extractCompleteSentences(this.buffer);
    this.buffer = extracted.remainder;
    if (!flush) return extracted.sentences;

    const tail = normalizeSpeechSegment(this.buffer);
    this.reset();
    return tail ? [...extracted.sentences, tail] : extracted.sentences;
  }

  private feedVoice(chunk: string, flush: boolean): string[] {
    const segments: string[] = [];
    const emitNormalBlock = () => {
      let block = normalizeSpeechSegment(this.voiceBuffer);
      this.voiceBuffer = "";
      if (!block) return;
      if (!SPOKEN_BOUNDARY.test(block)) block += "。";
      segments.push(block);
      this.firstVoiceSentenceEmitted = true;
    };
    const emitParenthetical = () => {
      let block = normalizeSpeechSegment(this.voiceParentheticalBuffer);
      this.voiceParentheticalBuffer = "";
      if (!block) return;
      if (!SPOKEN_BOUNDARY.test(block)) block += "。";
      segments.push(block);
    };

    for (const character of chunk) {
      if (this.voiceParentheses.length) {
        const currentCloser = this.voiceParentheses[this.voiceParentheses.length - 1];
        if (character === currentCloser) {
          this.voiceParentheses.pop();
          if (!this.voiceParentheses.length) emitParenthetical();
        } else if (character === "（" || character === "(") {
          this.voiceParentheses.push(character === "（" ? "）" : ")");
        } else {
          this.voiceParentheticalBuffer += character;
        }
        continue;
      }

      if (character === "（" || character === "(") {
        // Ordinary prose after the first low-latency sentence is accumulated
        // into one block and is cut only when a parenthetical block begins.
        emitNormalBlock();
        this.voiceParentheses.push(character === "（" ? "）" : ")");
        continue;
      }
      if (character === "）" || character === ")") continue;

      this.voiceBuffer += character;
      if (!this.firstVoiceSentenceEmitted) {
        const extracted = extractFirstCompleteSentence(this.voiceBuffer);
        if (extracted.sentence) {
          segments.push(extracted.sentence);
          this.voiceBuffer = extracted.remainder;
          this.firstVoiceSentenceEmitted = true;
        }
      }
    }

    if (!flush) return segments;
    if (this.voiceParentheses.length) {
      this.voiceParentheses = [];
      emitParenthetical();
    }
    emitNormalBlock();
    this.reset();
    return segments;
  }
}

export function segmentSpeechText(text: string, speakParentheticalContent = false): string[] {
  return new SpeechSegmenter().feed(text, true, speakParentheticalContent);
}

export function estimateDeliveredPrefix(text: string, progress: number): string {
  const normalized = normalizeSpeechSegment(text);
  if (!normalized || progress <= 0.08) return "";
  if (progress >= 0.96) return normalized;
  const rawEnd = Math.max(0, Math.min(normalized.length, Math.floor(normalized.length * progress)));
  const candidate = normalized.slice(0, rawEnd);
  const boundaries = [...candidate.matchAll(/[，,、：:；;。！？!?]/g)];
  const last = boundaries.at(-1);
  if (last?.index != null && last.index + 1 >= rawEnd * 0.55) {
    return normalized.slice(0, last.index + 1).trim();
  }
  return candidate.trim();
}
