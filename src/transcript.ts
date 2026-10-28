import { Schema } from "effect";

type SegmentShape = {
  readonly start: number;
  readonly end: number;
  readonly text: string;
  readonly speaker: string | null;
  readonly activeSpeakers: ReadonlyArray<string>;
};

type TranscriptShape = {
  readonly schemaVersion: 1;
  readonly source: {
    readonly path: string;
    readonly duration: number;
    readonly selection: { readonly start: number; readonly end: number };
  };
  readonly models: { readonly asr: string; readonly diarization: string };
  readonly timing: "speaker-turn";
  readonly segments: ReadonlyArray<SegmentShape>;
  readonly warnings: ReadonlyArray<string>;
};

const FiniteSeconds = Schema.Number.pipe(
  Schema.check(Schema.isFinite(), Schema.isGreaterThanOrEqualTo(0)),
);

const SegmentSchema = Schema.Struct({
  start: FiniteSeconds,
  end: FiniteSeconds,
  text: Schema.String,
  speaker: Schema.NullOr(Schema.String),
  activeSpeakers: Schema.Array(Schema.String),
}).pipe(
  Schema.check(
    Schema.makeFilter<SegmentShape>(
      (segment) => segment.start < segment.end || "expected start to be less than end",
    ),
    Schema.makeFilter<SegmentShape>((segment) => {
      const active = segment.activeSpeakers;
      const unique = new Set(active);
      const expectedSpeaker = active.length === 1 ? active[0] : null;
      return (
        (active.length > 0 &&
          unique.size === active.length &&
          segment.speaker === expectedSpeaker) ||
        "speaker must match the active speaker set"
      );
    }),
  ),
);

const TranscriptFields = Schema.Struct({
  schemaVersion: Schema.Literal(1),
  source: Schema.Struct({
    path: Schema.String,
    duration: FiniteSeconds,
    selection: Schema.Struct({ start: FiniteSeconds, end: FiniteSeconds }),
  }),
  models: Schema.Struct({
    asr: Schema.String,
    diarization: Schema.String,
  }),
  timing: Schema.Literal("speaker-turn"),
  segments: Schema.Array(SegmentSchema),
  warnings: Schema.Array(Schema.String),
});

export const TranscriptSchema = TranscriptFields.pipe(
  Schema.check(
    Schema.makeFilter<TranscriptShape>(
      (transcript) => transcript.source.path.length > 0 || "source path must not be empty",
    ),
    Schema.makeFilter<TranscriptShape>(
      (transcript) =>
        (transcript.source.selection.start < transcript.source.selection.end &&
          transcript.source.selection.end <= transcript.source.duration) ||
        "selection bounds must be ordered and within the source duration",
    ),
    Schema.makeFilter<TranscriptShape>(
      (transcript) =>
        transcript.segments.every(
          (segment) =>
            segment.start >= transcript.source.selection.start &&
            segment.end <= transcript.source.selection.end,
        ) || "segment timestamps must be within the selected source range",
    ),
    Schema.makeFilter<TranscriptShape>((transcript) => {
      for (let index = 1; index < transcript.segments.length; index += 1) {
        const previous = transcript.segments[index - 1];
        const current = transcript.segments[index];
        if (previous !== undefined && current !== undefined && current.start < previous.end) {
          return "segments must be ordered and non-overlapping";
        }
      }
      return true;
    }),
  ),
);

export type Transcript = Schema.Schema.Type<typeof TranscriptSchema>;
export type TranscriptSegment = Transcript["segments"][number];

export const decodeTranscript = Schema.decodeUnknownSync(TranscriptSchema);

const transcriptJsonDocument = Schema.toJsonSchemaDocument(TranscriptSchema);

export const transcriptJsonSchema = {
  $schema: "https://json-schema.org/draft/2020-12/schema",
  $id: "urn:local-transcribe:transcript:v1",
  ...transcriptJsonDocument.schema,
  ...(Object.keys(transcriptJsonDocument.definitions).length > 0
    ? { $defs: transcriptJsonDocument.definitions }
    : {}),
};
