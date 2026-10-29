import { Converter } from "opencc-js/cn2t";
import type { Transcript } from "./transcript";

export type TranscriptFormat = "json" | "text" | "srt";
export type ChineseScript = "zh-TW" | "original";

const toTraditionalChinese = Converter({ from: "cn", to: "tw" });

export function convertTranscriptScript(
  transcript: Transcript,
  script: ChineseScript = "zh-TW",
): Transcript {
  if (script === "original") return transcript;
  return {
    ...transcript,
    segments: transcript.segments.map((segment) => ({
      ...segment,
      text: toTraditionalChinese(segment.text),
    })),
  };
}

function timestamp(seconds: number, separator: "," | "."): string {
  const milliseconds = Math.round(seconds * 1000);
  const hours = Math.floor(milliseconds / 3_600_000);
  const minutes = Math.floor((milliseconds % 3_600_000) / 60_000);
  const wholeSeconds = Math.floor((milliseconds % 60_000) / 1000);
  const remainder = milliseconds % 1000;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(wholeSeconds).padStart(2, "0")}${separator}${String(remainder).padStart(3, "0")}`;
}

function label(segment: Transcript["segments"][number]): string {
  if (segment.speaker !== null) return segment.speaker;
  if (segment.activeSpeakers.length === 0) return "[unknown speaker]";
  return `[overlap: ${segment.activeSpeakers.join(", ")}]`;
}

export function formatTranscript(
  input: Transcript,
  format: TranscriptFormat,
  script: ChineseScript = "zh-TW",
): string {
  const transcript = convertTranscriptScript(input, script);
  if (format === "json") return `${JSON.stringify(transcript, null, 2)}\n`;
  if (format === "text") {
    return (
      transcript.segments
        .map(
          (segment) =>
            `[${timestamp(segment.start, ".")} – ${timestamp(segment.end, ".")}] ${label(segment)}: ${segment.text}`,
        )
        .join("\n") + (transcript.segments.length > 0 ? "\n" : "")
    );
  }
  return transcript.segments
    .map(
      (segment, index) =>
        `${index + 1}\n${timestamp(segment.start, ",")} --> ${timestamp(segment.end, ",")}\n${label(segment)}: ${segment.text}\n`,
    )
    .join("\n");
}
