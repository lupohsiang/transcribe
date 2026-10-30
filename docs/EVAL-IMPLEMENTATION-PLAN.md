# Evaluation implementation plan

1. `how` over the affected subsystem. Grounded by the architectural investigation recorded in EVAL-RESEARCH.md.
2. `architect` for parallel design exploration. Compare manifest-driven evaluation with alternative structures, cross-judge, then select a design.
3. Write the throughput checkpoint as four todo items.
   - Blocking first steps. Select the manifest and reference-readiness rules before implementation or acquisition.
   - Independent workstreams. Design sketches use separate output files. After synthesis, one owner implements the suite while the parent acquires data in ignored artifacts.
   - Shared mutable state. One implementation owner. Parent owns acquisition artifacts and verification reports. No concurrent source edits.
   - Smallest safe decomposition. Keep manifest validation, scoring, and the runner under one owner so their invariants agree.
4. Delegate code-writing to a subagent with the selected data shapes and observable acceptance criteria. Parent reviews every changed source file.
5. Verify on the matching surface. Exercise the evaluator and real CLI against actual audio and references, including deliberately invalid cases.
6. Rebase into small, ordered commits. Skip rebasing unless necessary; do not disturb the two existing untracked research documents. Keep local changes reviewable.
7. If the design is contested, `interrogate` before shipping. Apply if synthesis leaves a material disagreement.
8. Run Opening a PR. PR publication is outside the requested scope; deliver verified local changes.

Architect phases are Ground, Sketch, Agree, Implement, and Scrap. Arena phases are Frame, Fan out, Cross-judge, Pick, Graft, and Verify. No human checkpoint is requested.

The design rubric scores reference honesty, group leakage prevention, reproducible real-audio execution, interface simplicity, and testable failure handling. Synthetic scorer tests alone do not establish transcription accuracy.

## Completion

The selected design is implemented in `scripts/evaluate-suite.py` and `scripts/prepare-eval.py`. The versioned recipe and source pins live in `eval/`. Preparation was repeated in a clean output directory using the real downloaded corpus and private source files. All 14 cases reproduced their audio hashes, reference hashes, group assignments, selections, and script conditions.

Verification passed `bun run check`, all 17 Bun tests and 38 Python tests, and `bun run build`. The Python total includes 15 new evaluator tests. A scoped comment review found no remaining findings. Independent correctness review found and resolved the private-copy hash regression and interruption cleanup gap. The interruption tests use an initialized-child handshake and verify that a child interrupted during process creation is reaped. No model workers or test fixtures remained after verification.

All 14 real cases were attempted. Ten were scoreable and four timed out. The scorer returned status 1, preserved missing case IDs, and reported the release gate as not ready. [The baseline record](EVAL-BASELINE.md) distinguishes these model/runtime failures from passing evaluator tests. Speaker and timestamp accuracy are not claimed. AMI conversion remains deferred; downloaded source pins are retained for that future addition.

The throughput checkpoint resolved as follows. Grounding and reference-state rules preceded source work. Design sketches used separate files, a single implementation owner wrote the coupled source, and the parent handled acquisition and real runs. Review fixes were serialized. This kept shared source writes separate while acquisition and implementation proceeded independently.

Model the Domain led to manifest groups and explicit reference states. Build the Lever led to preparation and scoring commands. Exhaust the Design Space led to three sketches and a cross-judge. Separate Before Serializing Shared State kept the source under one writer. Test Behavior, Not Implementation led to literal edit counts and subprocess outcomes. Sequence Work into Verifiable Units separated manifest preparation, scorer checks, and real inference. Prove It Works required the clean rebuild, complete case-attempt record, and explicit unsuccessful baseline. Laziness Protocol kept benchmark logic outside the production worker and avoided a plugin framework.

No rebase, commit, or PR publication was necessary to deliver the requested local evaluation. The pre-existing research documents were preserved.
