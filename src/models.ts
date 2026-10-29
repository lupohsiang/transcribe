import { Schema } from "effect";
import pins from "../model-pins.json";

const ModelSnapshotSchema = Schema.Struct({
  id: Schema.String,
  revision: Schema.String,
  path: Schema.String,
});

export const ModelsLockSchema = Schema.Struct({
  schemaVersion: Schema.Literal(1),
  models: Schema.Struct({
    asr: ModelSnapshotSchema,
    diarization: ModelSnapshotSchema,
    alignment: ModelSnapshotSchema,
  }),
}).pipe(
  Schema.check(
    Schema.makeFilter((state) => {
      for (const name of ["asr", "diarization", "alignment"] as const) {
        const expected = pins.models[name];
        const installed = state.models[name];
        if (installed.id !== expected.id || installed.revision !== expected.revision) {
          return `installed ${name} model does not match model-pins.json; run transcribe setup`;
        }
      }
      return true;
    }),
  ),
);

export type ModelState = Schema.Schema.Type<typeof ModelsLockSchema>;
