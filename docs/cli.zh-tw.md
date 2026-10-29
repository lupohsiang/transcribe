# 命令列使用指南

若使用[原生發行版本](install.md)，請將以下範例中的 `bun src/cli.ts` 換成 `transcribe`；只有從原始碼執行時才需要 Bun。

[專案介紹](../README.zh-tw.md) · [English](cli.md)

請在原始碼專案目錄中執行指令。以下範例使用 `bun src/cli.ts`；執行 `bun run build` 後，也能改用 `bun dist/cli.js`，但仍須保留完整專案。

## 轉寫錄音

```bash
bun src/cli.ts run "recording.m4a" --format text
bun src/cli.ts run "recording.m4a" --format text --output transcript.txt
bun src/cli.ts run "recording.m4a" --format srt --output subtitles.srt
```

每次執行 `run` 都會重新進行模型推論。改選輸出格式時，不會沿用先前產生的 JSON。轉寫完成後，CLI 才會以原子方式建立輸出檔案。若檔案已存在，需加上 `--force` 才能覆寫；CLI 會拒絕覆寫原始音訊。

路徑含有空白時，請用引號包住。FFmpeg 會將輸入轉為暫存的單聲道 16 kHz PCM 音訊，不會修改原始錄音。

## 擷取部分錄音

從第 60 秒開始，轉寫接下來的兩分鐘：

```bash
bun src/cli.ts run "recording.m4a" --start 60 --duration 120 --format text
```

`--duration` 表示要處理的秒數，不是結束時間。輸出時間戳記仍對應原始錄音的絕對時間。JSON 會記錄完整錄音長度及選取範圍。說話者標籤只適用於這次選取的範圍；不同次執行中的相同標籤，不代表同一個人。

需要時可指定語言提示：

```bash
bun src/cli.ts run "recording.m4a" --language Chinese --format text
```

此對齊流程支援中文、粵語、英語、德語、西班牙語、法語、義大利語、葡萄牙語及俄語。此鎖定版本不包含日語及韓語所需的斷詞套件，其他 ASR 語言也尚不支援。未指定時，ASR 模型會自動判斷語言；若偵測到不支援的語言，執行會停止。`--language` 控制語音辨識的語言，不是翻譯或繁簡體設定。

## 繁體中文輸出

JSON、文字與 SRT 中的中文逐字稿，預設都會轉成臺灣正體字。語音辨識完成後，程式會使用內建 OpenCC 字典，在本機套用 `cn` 到 `tw` 的轉換。此設定轉換字形，不啟用臺灣詞彙替換；不會翻譯英文，也不會更改時間戳記、說話者標籤、檔案路徑或警告訊息。

若要保留模型的原始文字：

```bash
bun src/cli.ts run "recording.m4a" --chinese-script original --format text
```

也可使用 `--chinese-script zh-TW` 明確指定預設值。繁簡轉換不會修正語音辨識錯誤，也不代表文字或說話者辨識已經過人工校對。

## 搭配代理程式與腳本

預設輸出為 JSON。關閉進度訊息，並將 stdout 存成檔案：

```bash
bun src/cli.ts run "recording.m4a" --quiet > transcript.json
bun src/cli.ts schema > transcript.schema.json
```

`--json` 等同於 `--format json`。`--output -` 也會寫入 stdout。Shell 重新導向會遵循 shell 本身的覆寫規則；需要 CLI 的覆寫保護時，請改用 `--output transcript.json`。

錯誤訊息會以 JSON 物件寫入 stderr。成功時結束碼為 0；失敗時為非零。輸入錯誤使用 2、取消執行使用 130、逾時使用 124。未加上 `--quiet` 時，stderr 也會包含進度與警告訊息，請勿將整段 stderr 當成單一 JSON 文件解析。

Ctrl-C、SIGTERM 與 `--timeout` 都會停止目前的子行程群組，並清除暫存音訊。預設逾時時間為六小時。整次執行完成前，不會寫出逐字稿；目前也無法接續中斷的工作。

## 指令與選項

| 指令 | 用途 |
| --- | --- |
| `run <file>` | 轉寫音訊並標記說話者。 |
| `setup` | 安裝鎖定版本的 Python 環境，並下載指定版本的模型。 |
| `doctor [--json]` | 檢查相依工具與模型快照是否可用。 |
| `models [--json]` | 顯示已安裝模型的 ID、版本與可用狀態。 |
| `schema` | 輸出逐字稿的 JSON Schema。 |

`run` 可使用的選項：

| 選項 | 預設值 | 用途 |
| --- | --- | --- |
| `-f, --format <format>` | `json` | 選擇 `json`、`text` 或 `srt`。 |
| `--json` | | 選擇 JSON 輸出。 |
| `-o, --output <path>` | stdout | 將結果寫入檔案；`-` 代表 stdout。 |
| `--force` | 關閉 | 覆寫既有輸出檔案。 |
| `--start <seconds>` | `0` | 從原始錄音的指定秒數開始。 |
| `--duration <seconds>` | 剩餘錄音 | 要轉寫的音訊長度。 |
| `--language <name>` | 自動判斷 | 提供 ASR 語言提示，例如 `Chinese` 或 `English`。 |
| `--chinese-script <script>` | `zh-TW` | 選擇臺灣正體字，或以 `original` 保留模型原文。 |
| `--timeout <seconds>` | `21600` | 限制整次執行的時間。 |
| `-q, --quiet` | 關閉 | 不顯示進度與 worker 警告。 |
| `-h, --help` | | 顯示指令說明。 |

`setup` 也支援 `--quiet`。執行 `bun src/cli.ts --version` 可查看 CLI 版本。

## 逐字稿格式與限制

第 1 版 JSON 包含 `schemaVersion`、`source`、`models`、`timing`、`segments` 與 `warnings`。每個片段包含 `start`、`end`、`text`、`speaker` 及 `activeSpeakers`。時間單位為原始錄音的秒數。`source.selection` 記錄選取範圍的起點與終點。

只有一個聲音時，`speaker` 會包含該說話者的標籤。聲音重疊時，`speaker` 為 `null`，`activeSpeakers` 則列出偵測到的說話者。不會將聲音分離成各自獨立的音軌。無法指派說話者時，`speaker` 為 `null`，`activeSpeakers` 則為空陣列；文字與 SRT 輸出會顯示 `[unknown speaker]`。

Effect Schema 會驗證時間邊界、片段順序、區間不重疊，以及說話者欄位的一致性。匯出的 JSON Schema 描述欄位結構與簡單限制；跨欄位的關聯檢查會在執行時進行。

Nemotron 會以連續的說話者狀態處理整段選取錄音。ASR 會獨立辨識完整選取範圍，每次處理最多 30 秒的連續音訊。Qwen3-ForcedAligner 先估計詞語或中文字元的時間，再依獨立的說話者活動時間線指派標籤。新逐字稿使用 `timing: "forced-aligned"`，並包含 `models.alignment`。舊的 `timing: "speaker-turn"` 逐字稿仍可解析，且保留原有時間意義。這些時間是模型估計值，不代表已驗證的逐字準確度。固定視窗邊界仍可能切開句子。

最多支援八個說話者標籤。長錄音中的標籤可能漂移，也可能將同一個人的聲音分成多個標籤。未辨識出文字的視窗會列入 JSON 警告。零長度對齊項目的文字會併入相鄰的有效時間項目，不會直接丟棄。若對齊結果無效或無法對應原文，執行會在輸出檔案發布前停止。辨識使用語言提示或偵測結果，對齊則需要使用模型支援的語言。CLI 不會辨識人物身分、跨錄音比對聲音，或依說話者匯出獨立音訊檔案。重要逐字稿請對照原始錄音校對。

## 設定

macOS 預設狀態目錄為 `~/Library/Application Support/transcribe`。

| 環境變數 | 預設值 | 用途 |
| --- | --- | --- |
| `TRANSCRIBE_HOME` | 上述狀態目錄 | 存放 `models.lock.json` 的目錄。 |
| `HF_HOME` | `$TRANSCRIBE_HOME/huggingface` | 初次設定時使用的 Hugging Face 快取目錄。 |

模型清單會記錄快照的絕對路徑。若移動或刪除快照，請使用選定的設定重新執行 `setup`。初次設定與轉寫時，請一致地設定環境變數。Python 相依套件仍會安裝在專案的 `.venv` 中。

`bun install --frozen-lockfile` 會安裝 JavaScript 相依套件。`setup` 使用 `uv sync --frozen` 及 `model-pins.json` 中指定的版本。這些準備步驟需要網路。模型推論使用本機快照與離線模式旗標，並非透過作業系統封鎖網路連線。

## 疑難排解

先執行 `bun src/cli.ts doctor`。若缺少工具，請先安裝；若缺少 Python 環境或模型快照，請執行 `bun src/cli.ts setup`。需要查看指令語法時，使用 `bun src/cli.ts run --help`。

若輸出檔案已存在，請改用其他路徑，或加上 `--force`。若執行逾時，可先縮短選取範圍，或增加 `--timeout`。較短範圍會產生自己的說話者標籤，無法接續或延伸上一次執行的說話者身分。
