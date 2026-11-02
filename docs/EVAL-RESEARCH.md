# Evaluation research for transcribe

Researched on 2026-10-29. This report applies Hamel Husain and Shreya Shankar's `evals-start` and `eval-audit` skills to the existing CLI, reference draft, and quality artifacts. It specifies a proposed evaluation. No new accuracy measurement, human annotation, corpus download, or inference run was performed.

The recommendation is to build a local, audio-grounded evaluation with separate text, speaker, timing, and usability results. Use the supplied recording for development and regression. Measure generalization on different recordings and speakers. Public datasets provide additional coverage, but do not replace fresh Taiwan Mandarin workplace recordings.

## What the reference can support

The requested file exists at `~/Downloads/AI 工作訪談草稿 商品組 8_24.md`. It contains interview guidance and meeting notes before the `**逐字稿**` marker on line 229. Only the following transcript section is a candidate speech reference.

Inspection found 256 timed transcript blocks and 27 repeated time ranges. Four printed labels occur, but one is the shared `團隊成員` label. Printed labels therefore do not establish four distinct acoustic speakers. The last interval ends at 89:26. Existing CLI output identifies the source recording as `~/Downloads/新錄音 4.m4a`, lasting 5,365.738667 seconds, close to that endpoint. The user identifies these as the same recording; duration alone would not prove the match.

Treat the draft as a preferred example and an annotation starting point. Check it against the audio before calling it ground truth. In particular, verify omitted repetitions, English spellings, names, numbers, and short interjections. Preserve an unchanged source copy. Create a verbatim scoring reference separately from any edited, readable transcript. Keep private audio and reference text under ignored `artifacts/`, consistent with the existing project policy.

Repeated broad intervals cannot establish simultaneous speech or accurate word boundaries. They also make simple chronological concatenation ambiguous. Relabel voices with anonymous, stable IDs after listening before deriving speaker metrics. Do not turn the shared team label into a single speaker.

## Applying Hamel's skills

Hamel's [skills article](https://hamel.dev/blog/posts/evals-skills/index.html) identifies the official repository and recommends adapting the workflows to the product. I loaded the [evals-start instructions](https://github.com/ai-evals-course/evals-skills/blob/main/skills/evals-start/SKILL.md), which route an existing pipeline to [eval-audit](https://github.com/ai-evals-course/evals-skills/blob/main/skills/eval-audit/SKILL.md), and followed its six diagnostic areas below. The skills were read and applied for this investigation; they were not installed globally.

Hamel's [FAQ](https://hamel.dev/blog/posts/evals-faq/) recommends inspecting real outputs with a domain expert before choosing evaluators. For this product, review audio alongside the reference, ASR output, speaker activity, alignment, and final rendered transcript. Use open notes first. Group recurring problems afterward. Include random examples alongside difficult examples. The following audit is a starting point, not completed human error discovery.

## Audit findings ordered by product impact

### Labeled data. The preferred draft is not yet verified ground truth

Status is problem exists. The whole Markdown file includes unspoken material. Its transcript has coarse repeated timestamps and a shared speaker label. It cannot directly validate word timing or diarization.

Fix by checking a few complete passages against audio, writing an annotation policy, and marking each reference as draft, checked text, checked speakers, or checked timing. Score only the dimensions actually checked.

### Pipeline hygiene. The known recording cannot be an independent test

Status is problem exists. [The design](DESIGN.md) and [quality review](QUALITY.md) show that this recording drove the change from short speaker fragments to continuous ASR windows. All excerpts, channel variants, conversions, and noise variants of it belong to the same development group.

Fix by reserving different sessions and preferably different speakers before further tuning. Keep this recording as a useful regression case.

### Evaluator design. Current CER has a narrow, useful scope

Status is partial coverage. [evaluate-quality.py](../scripts/evaluate-quality.py) computes normalized character edit distance and reports substitutions, deletions, and insertions. It joins all segment text. It neither verifies matching source selections nor evaluates speaker attribution, punctuation, or timing. Its normalization removes punctuation and spaces, but does not convert Simplified Chinese to Traditional Chinese.

Fix by checking source, selection, output mode, and reference readiness before scoring. Keep CER for recognition. Add separate evaluations only for observed failures. Hamel's warning about generic generation-similarity scores does not make reference-based speech error rates inappropriate.

### Error analysis. Existing observations are useful but incomplete

Status is partial coverage. [QUALITY.md](QUALITY.md) records excessive short fragments and diagnostic before/after differences. Ignored local review artifacts also identify unexpected scripts and garbled technical terms. Fluent output and successful alignment do not prove correct words.

Fix by reviewing representative new runs with the audio. Treat window-boundary omissions, invented speech in silence, and speaker drift as hypotheses until observed and annotated. Use `error-discovery` for this next interactive review.

### Human review. An audio-backed review process is missing

Status is cannot determine. No reviewed annotation dataset or recorded adjudication policy was found in the inspected files. The supplied draft's production and checking process is unknown.

Fix by using a Taiwan Mandarin reviewer who knows the workplace terminology. Show synchronized audio and blind A/B transcripts. Let the reviewer mark uncertainty instead of guessing words or identity.

### Judge validation. There is no LLM judge to validate

Status is not applicable today. Do not add a holistic quality judge as the first evaluator. If a later readability judge is justified, use one failure criterion at a time and calibrate against human labels. The [validate-evaluator skill](https://github.com/ai-evals-course/evals-skills/blob/main/skills/validate-evaluator/SKILL.md) separates few-shot, development, and final test examples, and measures true-pass and true-fail detection separately. This judge split is distinct from the recording split used to compare ASR pipelines.

## Proposed scoring contract

Keep benchmark logic outside the production worker. A separate runner should invoke the CLI, save its JSON, and score it against checked local references. The current flow is audio conversion, independent diarization and continuous ASR, forced alignment, speaker assignment, then script conversion and formatting.

| Dimension | Proposed measurement | Required reference and limitation |
| --- | --- | --- |
| Recognition | CER with substitution, deletion, and insertion counts | Verbatim text for exactly the selected audio. Start with single-speaker or non-overlapping passages. |
| Mixed language | Supplementary mixed error rate using Han characters and English word tokens | Freeze tokenization, abbreviation, and number policy. English word errors must remain visible even when Chinese dominates. |
| Important content | Correct occurrences of annotated names, amounts, dates, and negations | Check against audio. Do not pass because a keyword appears somewhere in a long transcript. |
| Speaker activity | DER and its missed speech, false alarm, and confusion components | Checked time intervals and stable voices. Map anonymous labels once per recording, never independently per clip. |
| Who said what | Speaker-permutation text errors, with a documented token policy | Checked speaker-specific text. Keep overlapping-speech recognition separate from single-speaker text. |
| Timing | Median and tail boundary error on manually marked, correctly recognized anchors | Independent human timing. A second run of the same aligner is not timing ground truth. |
| Product output | Binary checks for valid JSON, bounds, exports, and preserved content | Existing schema and CLI tests cover part of this. They establish contract behavior, not recognition accuracy. |
| Readability | Blind human comparison of rendered transcripts | Judge legibility separately from factual preservation. A more readable omission still fails preservation. |
| Efficiency | End-to-end elapsed time divided by audio duration, peak memory, completion rate | Same hardware, input, cache state, model revisions, and options. |

[MeetEval](https://github.com/fgnt/meeteval) provides permutation-aware and time-constrained meeting transcription metrics, plus alignment visualization. Adapt tokenization explicitly for Chinese rather than applying English whitespace WER to Chinese paragraphs. Its tools require conversion of the CLI's JSON to their supported annotation formats.

[pyannote.metrics](https://pyannote.github.io/pyannote-metrics/reference.html) supports speaker metrics, evaluation regions, boundary collars, and overlap policies. For our proposed primary report, include overlap and use no collar. Add a secondary tolerance-based result with the precise library configuration recorded. Never compare results with different overlap or collar settings as though they were the same metric.

The worker's final text spans are not a complete raw diarization timeline. Decide whether the score targets raw activity or user-visible attribution, and capture the appropriate output. Measure unknown attribution on checked unambiguous speech; do not penalize appropriate uncertainty as if every span had a knowable single speaker.

Freeze two text views if needed. A lexical view converts reference and hypothesis with the same pinned Chinese conversion policy. A display view checks Taiwan Traditional Chinese output separately. Retain both originals. Do not silently normalize away mistakes in amounts, negation, or terminology. On empty-reference silence cases, CER is undefined; count inserted characters and hallucination events instead.

## Preventing overfitting

1. Assign splits by recording or conversation group before choosing excerpts. Keep all derived clips and acoustic variants together. Group related meeting series and speaker identities where metadata permits.
2. Use the known recording to debug and improve the pipeline. Keep public development splits for choosing normalization, thresholds, and parameters. Freeze public test cases for final comparison.
3. Reserve fresh Taiwan workplace recordings for the strongest generalization check. Public test data may have appeared in pretrained model training. A published test split does not prove model-level independence.
4. Freeze case IDs, hashes, channel choice, crop times, references, model revisions, CLI arguments, and scoring version before comparing candidates. Include language-auto and Chinese-hint conditions according to intended usage, with separate results.
5. Once final-test failures guide a change, treat those examples as development data and replace the final holdout. Do not repeatedly inspect a hidden set while claiming it remains untouched.
6. Report per-corpus and per-condition results, per-recording macro averages, and pooled edit counts with denominators. Bootstrap paired differences by recording, not by words or correlated excerpts. With very few independent recordings, report the uncertainty rather than claiming statistical confidence.
7. Add synthetic cases for explicit robustness questions after real-data review. A renamed transcript without newly recorded audio is not an ASR case. TTS and injected noise are useful controlled probes, but cannot establish real conversation quality. Keep them in separate reports.

These are proposed project controls. Do not pick a universal CER threshold or a weighted quality score before the reviewer establishes acceptable errors. Keep quantitative speech errors and binary product failures visible separately.

## Concrete starting cases and next work

The [public dataset shortlist](EVAL-DATASETS.md) provides verified acquisition routes, licenses, sizes, annotation limits, and a proposed sampling recipe. Start with ASCEND conversations, AliMeeting meetings, and an AMI English meeting. Taiwan Mandarin read speech is a useful additional bucket, but remains different from spontaneous workplace speech.

Use these local passages as proposed development review cases. Their approximate times come from the draft or existing quality probes; verify audio boundaries before extracting scoring references.

| Candidate | Approximate interval | Review purpose |
| --- | --- | --- |
| Opening | 00:00 to 01:21 | Establish source match, speaker changes, and disfluency policy. |
| Export workflow | 01:21 to 05:10 | Chinese-English switching, numbers, repeated phrases, and short responses. |
| Existing difficult probe | 20:48 to 21:48 | Technical names and a direct comparison with previous pipeline experiments. |
| Ending | 87:55 to 89:26 | Long-recording coverage and late speaker consistency. |

First check text on two to five minutes, then run the existing CER script on exactly matching baseline and candidate excerpts. Do not score the entire interview draft. Avoid selecting text merely by model segment start if the selection cuts a boundary segment. Re-run matching crops or define a checked complete-utterance selection.

Next review a mix of random and difficult examples across additional recordings. Build the failure vocabulary before implementing additional automated evaluators. A review interface should expose original audio time, reference status, anonymous voices, A/B text, and free notes. No LLM-produced label becomes human ground truth automatically.

The current script has quadratic edit-distance runtime, so begin with short excerpts rather than the full 89-minute concatenation. Future runner work should validate case metadata and use an appropriate efficient scorer. No production implementation change is needed to start annotation.

## Investigation record

The pstack Investigation steps were followed.

1. Route through the **how** skill. Completed with a read-only architectural explainer. The motivation-only **why** branch was skipped because this request concerns evaluation design and evidence.
2. Throughput checkpoint stays one line. `throughput checkpoint: n/a, read-only investigation`.
3. Produce the `how`-shaped output, or a recommendation with a tradeoffs table. Completed through the audit, scoring contract, and acquisition shortlist.
4. Apply the **unslop** skill to the reply. Applied with the technical-writing guidance.

The Prove It Works principle changed the conclusion. Inspection verified the actual draft structure and existing scoring code, so this report does not claim a reference-based accuracy improvement, ready corpus fixtures, or validated judges. Verification checked source links and document contents. No model tests were needed for this research-only change.
