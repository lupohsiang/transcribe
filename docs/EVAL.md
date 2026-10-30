# Local speech evaluation

The evaluation runner invokes the production CLI on local audio, validates each saved transcript against its manifest selection, then scores the saved JSON. It does not alter the transcription worker. Evaluation artifacts and downloaded media live under ignored `artifacts/eval-data/`.

The starter recipe exports 12 ASCEND test utterances, six mixed-language, three Chinese, and three English. It chooses the earliest source IDs with at least two seconds of audio, then records all 12 in one heldout group because the source speakers recur across sessions. ASCEND references are source-labeled, not independently human checked. The sample uses the original Chinese script and automatic language detection. ASCEND is licensed CC BY-SA 4.0; see the [official ASCEND dataset card](https://huggingface.co/datasets/CAiRE/ASCEND).

The generated five-second zero-PCM case checks the CLI and silence-hallucination reporting contract. It is a synthetic fixture and cannot qualify as a speech-accuracy reference. The optional private importer extracts complete 00:00 to 01:21 transcript blocks only after the `**逐字稿**` marker. It preserves the printed names in provenance, excludes them from reference text, and marks the case draft, development, and used for tuning. The copied audio and reference remain local.

## Prepare cases

On a clean checkout, download the pinned public test shard and create an isolated environment for the Parquet-only exporter. The source file is pinned to a Hugging Face revision and verified against its SHA-256 before use. The official dataset card describes the ASCEND corpus and its data license.

```sh
mkdir -p artifacts/eval-data/downloads
curl --fail --location \
  'https://huggingface.co/datasets/CAiRE/ASCEND/resolve/737e9800ae31be9932ba8464c80366559bd28424/main/test-00000-of-00001.parquet' \
  --output artifacts/eval-data/downloads/ascend-test.parquet
echo 'a4c81d2b5ed6124f052089a695972808c16e0ce0c365ec9773c5d1a8fcf043a7  artifacts/eval-data/downloads/ascend-test.parquet' | shasum -a 256 --check
uv venv --python 3.12 artifacts/eval-data/.venv
uv pip install --python artifacts/eval-data/.venv/bin/python pyarrow==23.0.1
```

The source URL, revision, checksum, and license attribution are also recorded in [eval/lock.json](../eval/lock.json).

```sh
artifacts/eval-data/.venv/bin/python scripts/prepare-eval.py starter \
  --parquet artifacts/eval-data/downloads/ascend-test.parquet \
  --output-dir artifacts/eval-data
```

This writes `artifacts/eval-data/manifest.jsonl`, audio clips, and UTF-8 references. Preparation refuses existing output files; choose a fresh directory when rebuilding.

Import the private draft only when those local source files are available.

```sh
python3 scripts/prepare-eval.py private-draft \
  --source-markdown '/path/to/AI 工作訪談草稿 商品組 8_24.md' \
  --audio '/path/to/新錄音 4.m4a' \
  --output-dir artifacts/eval-data
```

This writes `manifest.private.jsonl`, combining the starter cases and private draft. The private reference is compared with Traditional Chinese CLI output. The draft is not ground truth and cannot enter the release gate.

## Validate, run, and score

```sh
python3 scripts/evaluate-suite.py validate artifacts/eval-data/manifest.private.jsonl
python3 scripts/evaluate-suite.py run artifacts/eval-data/manifest.private.jsonl \
  --out artifacts/eval-data/run-baseline-1
python3 scripts/evaluate-suite.py score artifacts/eval-data/manifest.private.jsonl \
  --outputs artifacts/eval-data/run-baseline-1 \
  --report artifacts/eval-data/run-baseline-1-report.json
```

`run` uses `bun src/cli.ts run` with the case's audio path, exact source-coordinate selection, language hint, and Chinese script. Every run gets a new output directory. It saves one transcript JSON, a stderr log, and a sidecar per case. The sidecar binds the manifest case and audio hashes, output hash, CLI arguments, transcript model pins, elapsed time, source code fingerprint, warnings, and run status. A timeout sends SIGINT to the isolated CLI process group first, then escalates to SIGTERM and SIGKILL if needed. User interruption follows the same cleanup path.

For a filtered run, use the same filter when scoring it. For example, add `--split heldout` to both commands. `--case CASE_ID` is also available. Validation always checks the whole manifest before applying filters. Scoring saved JSON without sidecars is allowed for description, but it cannot qualify for the release gate. Missing outputs and failed run sidecars fail scoring rather than silently producing a complete result.

The score report separates cases by corpus, split, reference status, and reference language. CER applies NFKC, case folding, and retains Unicode letters and numbers while ignoring spaces and punctuation. Mixed-language error rate uses one token per Han character and word tokens for contiguous non-Han letters and numbers. English WER is shown only for English references. Segment texts are joined with spaces so English word boundaries remain intact. Empty references have undefined CER and report inserted characters and nonempty transcript events. Unknown-speaker and overlap counts are diagnostics; they are not diarization accuracy scores.

Source-labeled ASCEND agreement remains distinct from the private draft's provisional agreement. A checked reference can qualify only when it is speech from a heldout case with a successful bound run record, and checked references require reviewer metadata. The generated silence case is excluded. No accuracy thresholds are configured, so `release_gate.ready` remains false. `--require-checked` fails if no eligible checked heldout speech reference is available; it does not create or imply a universal CER threshold.

The pins and license records for ASCEND and the downloaded optional AMI files are in [eval/lock.json](../eval/lock.json). AMI annotation conversion is deferred; no AMI cases are included. Case selection details and fixed commands are in [eval/recipe.json](../eval/recipe.json).
