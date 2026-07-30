const SENTENCE_BOUNDARY = /(?:[。！？!?；;]+|…{2,}|\.{3,}|\n+)/g;
const TRAILING_CLOSERS = new Set(["”", "’", "」", "』", "】", '"', "'"]);
const SPOKEN_BOUNDARY = /[。！？!?；;，,：:…~～\s]$/;
const LEADING_HESITATION_ONLY =
  /^(?:嗯+|呃+|额+|呵+|哈+|唔+|呼+|唉+|哎+)[，,。.!！？?…~～\s]*$/;

export function normalizeSpeechSegment(value: string): string {
  return value
    .replace(/```[\s\S]*?```/g, "")
    .replace(/[`*_#>]/g, "")
    .replace(/\s+/g, " ")
    .replace(/\s+([。！？!?；;，,])/g, "$1")
    .trim();
}

export function hasSpeakableContent(value: string): boolean {
  return [...normalizeSpeechSegment(value)].some((character) =>
    /[\p{L}\p{N}]/u.test(character),
  );
}

export function stripLeadingTtsFiller(value: string): string {
  const speech = normalizeSpeechSegment(value);
  // Drop a filler only when it is the entire request.  When real speech
  // follows, "嗯/嗯……" is useful spoken hesitation and removing it makes the
  // first sentence sound edited rather than conversational.
  return /^嗯+[，,。.!！？?…~～\s]*$/.test(speech) ? "" : speech;
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
    // A streamed ellipsis or dash can arrive before the words that follow it.
    // Keep punctuation-only boundaries in the buffer so they attach to the
    // next speakable sentence instead of becoming an invalid TTS request.
    if (hasSpeakableContent(sentence)) {
      sentences.push(sentence);
      consumed = end;
    }
    SENTENCE_BOUNDARY.lastIndex = end;
    match = SENTENCE_BOUNDARY.exec(value);
  }
  return { sentences, remainder: value.slice(consumed) };
}

function extractFirstCompleteSentence(value: string) {
  SENTENCE_BOUNDARY.lastIndex = 0;
  let match = SENTENCE_BOUNDARY.exec(value);
  while (match) {
    let end = match.index + match[0].length;
    while (end < value.length && TRAILING_CLOSERS.has(value[end])) end += 1;
    const sentence = normalizeSpeechSegment(value.slice(0, end));
    // "嗯……" and "呵……" carry useful delivery information but are not a
    // useful standalone TTS request.  Keep looking for the next boundary so
    // the hesitation and the first spoken sentence share one acoustic phrase.
    if (hasSpeakableContent(sentence) && !LEADING_HESITATION_ONLY.test(sentence)) {
      return { sentence, remainder: value.slice(end) };
    }
    SENTENCE_BOUNDARY.lastIndex = end;
    match = SENTENCE_BOUNDARY.exec(value);
  }
  return { sentence: "", remainder: value };
}

export class SpeechSegmenter {
  private buffer = "";
  private parentheses: Array<{ closer: string; spokenStart: number }> = [];
  private voiceBuffer = "";
  private voiceParentheses: string[] = [];
  private firstVoiceSentenceEmitted = false;

  reset() {
    this.buffer = "";
    this.parentheses = [];
    this.voiceBuffer = "";
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
    return hasSpeakableContent(tail) ? [...extracted.sentences, tail] : extracted.sentences;
  }

  private feedVoice(chunk: string, flush: boolean): string[] {
    const segments: string[] = [];
    const emitNormalBlock = () => {
      let block = normalizeSpeechSegment(this.voiceBuffer);
      this.voiceBuffer = "";
      if (!hasSpeakableContent(block)) return;
      if (!SPOKEN_BOUNDARY.test(block)) block += "。";
      segments.push(block);
      this.firstVoiceSentenceEmitted = true;
    };
    for (const character of chunk) {
      if (this.voiceParentheses.length) {
        const currentCloser = this.voiceParentheses[this.voiceParentheses.length - 1];
        if (character === currentCloser) {
          this.voiceParentheses.pop();
        } else if (character === "（" || character === "(") {
          this.voiceParentheses.push(character === "（" ? "）" : ")");
        }
        continue;
      }

      if (character === "（" || character === "(") {
        // Parenthetical content is stage prose, never spoken audio.  Use the
        // boundary only to release already collected real dialogue, then
        // discard everything until the matching closer (including nesting).
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
