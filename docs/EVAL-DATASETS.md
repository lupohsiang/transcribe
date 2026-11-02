# Public audio and reference datasets

Research checked on 2026-10-29. This is a shortlist and a proposed sampling recipe. No corpus audio was downloaded and no cases were added by this research.

Start with ASCEND for a small conversational addition, AliMeeting for Mandarin meetings, and one AMI meeting for an English sanity check. Keep Taiwan Mandarin as a separate coverage requirement. Common Voice provides a Taiwan locale but read speech. The Sinica conversational route has application and payment barriers.

The ranking weighs relevance, reference quality, and acquisition cost. It is a recommendation, not a model leaderboard.

| Rank | Dataset | Best use | Acquisition constraint |
| --- | --- | --- | --- |
| 1 | ASCEND | Spontaneous Mandarin-English switches | Small public test shard |
| 2 | AliMeeting | Mandarin meetings and overlap | Smallest complete split is 3.42 GB |
| 3 | AMI | English meeting sanity check | Individual meeting audio is available |
| 4 | Common Voice zh-TW | Taiwan Mandarin pronunciation and Traditional Chinese | Account, terms acceptance, scripted speech |
| 5 | AISHELL-4 | Additional Mandarin meeting rooms and speakers | Test archive is 5.2 GB |
| 6 | FLEURS cmn_hans_cn | Clean Mandarin transcription sanity check | Read speech, China locale |

## 1. ASCEND

HKUST's corpus has 10.62 hours of spontaneous Mandarin-English conversation recorded in Hong Kong. It has 9,869 training, 1,130 validation, and 1,315 test utterances. Records include audio, transcription, language, speaker, session, and topic. The example audio is 16 kHz. The data license is CC BY-SA 4.0. [Official dataset card](https://huggingface.co/datasets/CAiRE/ASCEND).

The [authors' repository](https://github.com/HLTCHKUST/ASCEND) points to that dataset. Its MIT code license does not replace the data license. The [official file tree](https://huggingface.co/datasets/CAiRE/ASCEND/tree/main/main) exposes `test-00000-of-00001.parquet`, 106 MB, separately from training data. There is no visible access-request gate. The card documents `load_dataset("CAiRE/ASCEND")`. Select `split="test"` for evaluation.

Recommended handling is to export individual utterances with their `transcription`, then filter `language == "mixed"`. Inspect channel count during export. These are short conversational clips, so keep full-meeting behavior as a separate test. Hong Kong collection does not establish Taiwan accent coverage.

## 2. AliMeeting

The corpus contains real Mandarin meetings with 2 to 4 participants. Each discussion lasts 15 to 30 minutes. It includes eight-channel far-field arrays, participant headset recordings, and transcriptions. The official split is 104.75 training hours, 4 Eval hours, and 10 Test hours. The license is CC BY-SA 4.0. [OpenSLR 119](https://www.openslr.org/119/).

The same page links directly to [Eval_Ali.tar.gz](https://speech-lab-share-data.oss-cn-shanghai.aliyuncs.com/AliMeeting/openlr/Eval_Ali.tar.gz), 3.42 GB, with eight meetings and both recording conditions. Test is 8.90 GB. No application gate is shown. Start with Eval while developing the evaluation procedure, then freeze an untouched Test selection.

Proposed handling is to retain the source annotations and select one fixed array channel for a mono baseline. Keep the matching headset condition as a paired acoustic comparison. Record the chosen channel explicitly. Do not treat each microphone as an independent case, or report a selected channel as a beamformed result.

## 3. AMI

The [official download chooser](https://groups.inf.ed.ac.uk/ami/download/) offers individual meetings and media streams. Manual annotations include orthographic transcription in NXT format. The manual annotation ZIP is 22 MB. Signals and transcription are CC BY 4.0. A headset mix is one WAV, averaging about 30 MB per meeting. This avoids downloading the entire corpus.

One concrete entrypoint is the [ES2004a audio directory](https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/ES2004a/audio/). It lists `ES2004a.Mix-Headset.wav`, 32 MB, individual headsets, and separate array channels. Obtain the [manual annotations ZIP](https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip) and match the meeting ID.

Use the existing headset mix for an English meeting sanity check. Reconstruct the reference from timed word annotations across speakers and retain overlap information. NXT-to-text conversion is still required. This entrypoint does not prescribe a train or test split. Label ES2004a as a local sanity case unless a published partition is explicitly adopted. No login gate is shown.

## 4. Common Voice Scripted Speech, Chinese (Taiwan)

Use the [official Mozilla Data Collective listing](https://mozilladatacollective.com/datasets/cmn2g7eaj01fio10769r1m96n). Mozilla releases Common Voice under CC0. [Common Voice terms](https://commonvoice.mozilla.org/dav/terms). Access requires an account with verified email, credentials for API use, and acceptance of dataset terms through the website. The API returns a temporary download URL. [Official SDK guide](https://community.mozilladatacollective.com/using-the-mdc-python-sdk-library-to-download-datasets/), [API documentation](https://mozilladatacollective.com/api-reference/docs).

The listing rendered only a loading shell in this inspection, so its current archive size, split counts, and release-specific restrictions were not independently verified. After authorized download, inspect `test.tsv`, the matching clips, reference `sentence`, locale, and optional accent fields. Confirm the actual split schema and channels before selection. Treat it as scripted pronunciation coverage, not spontaneous meeting coverage.

Do not substitute an unofficial mirror automatically. Mozilla says its community datasets are distributed through MDC and ties distribution to consent management. [Mozilla distribution FAQ](https://community.mozilladatacollective.com/faq-can-i-get-the-common-voice-or-other-mdc-datasets-from-other-platforms-like-github-or-hugging-face/).

## 5. AISHELL-4

AISHELL-4 provides 120 hours across 211 Mandarin meetings with 4 to 8 speakers. It uses an eight-channel circular microphone array and includes accurate transcriptions and speaker voice activity. The license is CC BY-SA 4.0. [OpenSLR 111](https://www.openslr.org/111/).

Use that page's `test.tar.gz` link, 5.2 GB, through its EU or China mirrors. Training archives are split by room size. No application gate is shown. This is a useful second meeting source after AliMeeting, but a larger initial download.

Proposed handling is to select a fixed array channel, preserve source timing and speaker annotations, and choose clips with and without overlap. Record room and channel metadata when available. Verify archive annotation structure after download rather than assuming that it matches AliMeeting.

## 6. FLEURS

Google's release lists `cmn_hans_cn`, a Mandarin China configuration. It does not list a Taiwan Mandarin configuration. It also lists `yue_hant_hk`, which is Cantonese. Audio is 16 kHz, with normalized `transcription` and `raw_transcription`. FLEURS focuses on read speech and uses CC BY 4.0. [Official dataset metadata and card](https://huggingface.co/datasets/google/fleurs/raw/main/README.md).

For a concrete route, the [Mandarin audio directory](https://huggingface.co/datasets/google/fleurs/tree/main/data/cmn_hans_cn/audio) offers `dev.tar.gz`, 217 MB, and `test.tar.gz`, 525 MB. Match them to `dev.tsv` or `test.tsv` in the [parent directory](https://huggingface.co/datasets/google/fleurs/tree/main/data/cmn_hans_cn). Alternatively, the card documents streaming through `load_dataset`; use configuration `cmn_hans_cn` and split `test`. No access-request gate is shown.

Keep this as a clean speech control. It cannot close the Taiwan spontaneous-speech gap. Inspect waveform channels before local export.

## Taiwan conversational gap

Sinica MCDC8 contains eight conversations with audio and manually checked timed Praat transcripts. Its distributor requires institutional identification, signed agreements, and payment. The phone-aligned subset has 3.5 hours from 16 people, WAV and TextGrid files, and separate application terms. These are relevant candidates but are not anonymous ready-to-download fixtures. COSPRO likewise requires an academic research application. [Official ACLCLP application information](https://www.aclclp.org.tw/use_mat_c.php).

## Proposed initial selection

This recipe is proposed, not downloaded or scored.

- Select 20 ASCEND test utterances across speakers and topics. Include 12 mixed, 4 Mandarin, and 4 English clips. Include fillers and mid-sentence switches after listening.
- Select four 60 to 90 second AliMeeting Eval excerpts across at least two meetings. Include two low-overlap and two overlapping excerpts. Pair the same time ranges across far-field and headset conditions.
- Select two 60 to 90 second AMI excerpts from ES2004a. Include a speaker change and an overlap segment. Keep the complete meeting as a later long-audio check.
- Reserve 12 Common Voice zh-TW test clips across speakers once official access is available. Keep this bucket visibly pending until acquired.
- Add four AISHELL-4 excerpts and eight FLEURS test clips only after the first three sources work. Report each source separately.

Freeze source version, split, record ID, time range, channel, reference text, normalization policy, and file hash for every accepted case. Keep raw references alongside any Traditional Chinese conversion. Score Mandarin character errors, English word errors, and mixed-language errors separately. A public test split does not prove that the audio was absent from a pretrained model's training data. Treat this suite as a regression and comparison set unless model provenance establishes independence.
