const SENTENCE_BOUNDARY = /(?:[。！？!?；;]+|…{2,}|\.{3,}|\n+)/g;
const TRAILING_CLOSERS = new Set(["”", "’", "」", "』", "】", '"', "'"]);
const SPOKEN_BOUNDARY = /[。！？!?；;，,：:…~～\s]$/;

export function normalizeSpeechSegment(value: string): string {
  return value
    .replace(/```[\s\S]*?```/g, "")
    .replace(/[`*_#>]/g, "")
    .replace(/[ \t]+/g, " ")
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

export class SpeechSegmenter {
  private buffer = "";
  private parentheses: Array<{ closer: string; spokenStart: number }> = [];

  reset() {
    this.buffer = "";
    this.parentheses = [];
  }

  feed(chunk: string, flush = false, speakParentheticalContent = false): string[] {
    for (const character of chunk) {
      if (character === "（" || character === "(") {
        if (
          speakParentheticalContent
          && !this.parentheses.length
          && this.buffer
          && !SPOKEN_BOUNDARY.test(this.buffer)
        ) {
          this.buffer += "。";
        }
        this.parentheses.push({
          closer: character === "（" ? "）" : ")",
          spokenStart: this.buffer.length,
        });
        continue;
      }
      if (this.parentheses.length) {
        const current = this.parentheses[this.parentheses.length - 1];
        if (character === current.closer) {
          const completed = this.parentheses.pop()!;
          if (
            speakParentheticalContent
            && !this.parentheses.length
            && this.buffer.length > completed.spokenStart
            && !SPOKEN_BOUNDARY.test(this.buffer)
          ) {
            this.buffer += "。";
          }
        } else if (character === "（" || character === "(") {
          this.parentheses.push({
            closer: character === "（" ? "）" : ")",
            spokenStart: this.buffer.length,
          });
        } else if (speakParentheticalContent) {
          this.buffer += character;
        }
        continue;
      }
      if (character === "）" || character === ")") continue;
      this.buffer += character;
    }

    if (speakParentheticalContent && this.parentheses.length && !flush) return [];
    if (speakParentheticalContent && flush && this.parentheses.length) {
      if (this.buffer && !SPOKEN_BOUNDARY.test(this.buffer)) this.buffer += "。";
      this.parentheses = [];
    }
    const extracted = extractCompleteSentences(this.buffer);
    this.buffer = extracted.remainder;
    if (!flush) return extracted.sentences;

    const tail = normalizeSpeechSegment(this.buffer);
    this.reset();
    return tail ? [...extracted.sentences, tail] : extracted.sentences;
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
