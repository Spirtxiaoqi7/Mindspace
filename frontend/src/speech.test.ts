import { describe, expect, it } from "vitest";
import { estimateDeliveredPrefix, segmentSpeechText, SpeechSegmenter, stripLeadingTtsFiller } from "./speech";

describe("TTS speech segmentation", () => {
  it("removes Chinese and ASCII parenthetical stage directions", () => {
    expect(segmentSpeechText("（轻轻靠近）你好。(低声说)今天过得好吗？")).toEqual([
      "你好。",
      "今天过得好吗？",
    ]);
  });

  it("emits complete sentences for punctuation and ellipsis across chunks", () => {
    const segmenter = new SpeechSegmenter();
    expect(segmenter.feed("第一句。第二句还没")).toEqual(["第一句。"]);
    expect(segmenter.feed("结束……第三句...")).toEqual(["第二句还没结束……", "第三句..."]);
    expect(segmenter.feed("最后一句", true)).toEqual(["最后一句"]);
  });

  it("keeps punctuation inside a split parenthetical out of speech", () => {
    const segmenter = new SpeechSegmenter();
    expect(segmenter.feed("你好（她停顿。", false)).toEqual([]);
    expect(segmenter.feed("然后笑了）今天下雨。", false)).toEqual(["你好今天下雨。"]);
  });

  it("keeps closing quotation marks with the sentence", () => {
    expect(segmentSpeechText("她说：“我在这里。”然后继续。 ")).toEqual([
      "她说：“我在这里。”",
      "然后继续。",
    ]);
  });

  it("removes only a standalone leading 嗯 from the first TTS segment", () => {
    expect(stripLeadingTtsFiller("嗯。 ")).toBe("");
    expect(stripLeadingTtsFiller("嗯，今天想和你聊聊。 ")).toBe("今天想和你聊聊。");
    expect(stripLeadingTtsFiller("嗯……我在听。 ")).toBe("我在听。");
    expect(stripLeadingTtsFiller("嗯哼，这次不错。 ")).toBe("嗯哼，这次不错。");
  });

  it("maps PCM playback progress to the nearest safe text prefix", () => {
    expect(estimateDeliveredPrefix("先说第一点，然后再说第二点。", 0.65)).toBe("先说第一点，");
    expect(estimateDeliveredPrefix("很短。", 0.03)).toBe("");
    expect(estimateDeliveredPrefix("已经说完。", 1)).toBe("已经说完。");
  });
});
