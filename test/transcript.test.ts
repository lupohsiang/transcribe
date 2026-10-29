import { describe, expect, test } from "bun:test";
import { convertTranscriptScript, formatTranscript } from "../src/format";
import { decodeTranscript, type Transcript } from "../src/transcript";

const transcript: Transcript = {
  schemaVersion: 1,
  source: { path: "/audio/sample.wav", duration: 10, selection: { start: 0, end: 10 } },
  models: { asr: "asr@revision", diarization: "diarization@revision" },
  timing: "speaker-turn",
  segments: [
    {
      start: 1.25,
      end: 3,
      text: "hello",
      speaker: "speaker_0",
      activeSpeakers: ["speaker_0"],
    },
    {
      start: 3,
      end: 4,
      text: "yes",
      speaker: null,
      activeSpeakers: ["speaker_0", "speaker_1"],
    },
  ],
  warnings: [],
};
const firstSegment = transcript.segments[0];
if (firstSegment === undefined) throw new Error("transcript fixture has no segment");

describe("transcript boundary", () => {
  test("accepts an ordered version 1 transcript with an overlap interval", () => {
    expect(decodeTranscript(transcript)).toEqual(transcript);
  });

  test("rejects non-finite times, inverted bounds, and source overrun", () => {
    expect(() =>
      decodeTranscript({
        ...transcript,
        segments: [{ ...firstSegment, start: Number.NaN }],
      }),
    ).toThrow();
    expect(() =>
      decodeTranscript({
        ...transcript,
        segments: [{ ...firstSegment, start: 4, end: 2 }],
      }),
    ).toThrow();
    expect(() =>
      decodeTranscript({ ...transcript, segments: [{ ...firstSegment, end: 11 }] }),
    ).toThrow();
  });

  test("rejects a speaker label that conflicts with the active speaker set", () => {
    expect(() =>
      decodeTranscript({
        ...transcript,
        segments: [{ ...firstSegment, speaker: "speaker_1" }],
      }),
    ).toThrow();
  });
});

describe("aligned transcript boundary", () => {
  test("preserves alignment provenance and unknown speaker text", () => {
    const aligned: Transcript = {
      ...transcript,
      timing: "forced-aligned",
      models: { ...transcript.models, alignment: "aligner@revision" },
      segments: [{ ...firstSegment, text: "保留文字。", speaker: null, activeSpeakers: [] }],
    };
    const decoded = decodeTranscript(aligned);
    expect(decoded).toEqual(aligned);
    expect(formatTranscript(decoded, "text")).toContain("[unknown speaker]: 保留文字。");
    expect(formatTranscript(decoded, "srt")).toContain("[unknown speaker]: 保留文字。");
    expect(() => decodeTranscript({ ...aligned, models: transcript.models })).toThrow();
    expect(() =>
      decodeTranscript({
        ...aligned,
        segments: [{ ...firstSegment, speaker: "speaker_0", activeSpeakers: [] }],
      }),
    ).toThrow();
  });
});

describe("transcript formats", () => {
  test("converts Chinese to Taiwan Traditional in JSON, text, and SRT without changing metadata", () => {
    const input: Transcript = {
      ...transcript,
      segments: [{ ...firstSegment, text: "头发发展，后台记录软件。Hello AI 2026。" }],
    };
    const expected = "頭髮發展，後臺記錄軟件。Hello AI 2026。";
    const converted = convertTranscriptScript(input);
    expect(converted).toEqual({
      ...input,
      segments: [{ ...firstSegment, text: expected }],
    });
    expect(input.segments[0]?.text).toBe("头发发展，后台记录软件。Hello AI 2026。");
    expect(JSON.parse(formatTranscript(input, "json")).segments[0].text).toBe(expected);
    expect(formatTranscript(input, "text")).toContain(expected);
    expect(formatTranscript(input, "srt")).toContain(expected);
    expect(convertTranscriptScript(converted).segments[0]?.text).toBe(expected);
  });

  test("preserves the original model text when requested", () => {
    const input: Transcript = {
      ...transcript,
      segments: [{ ...firstSegment, text: "这是录音。Hello." }],
    };
    expect(convertTranscriptScript(input, "original")).toEqual(input);
    expect(JSON.parse(formatTranscript(input, "json", "original")).segments[0].text).toBe(
      "这是录音。Hello.",
    );
  });

  test("renders SRT with speaker labels and comma decimal separators", () => {
    expect(formatTranscript(transcript, "srt")).toBe(
      "1\n00:00:01,250 --> 00:00:03,000\nspeaker_0: hello\n\n2\n00:00:03,000 --> 00:00:04,000\n[overlap: speaker_0, speaker_1]: yes\n",
    );
  });
});
