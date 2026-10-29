<h1 align="center">transcribe</h1>

<p align="center">
  <a href="README.md">English</a> · 繁體中文
</p>

transcribe 是在 Apple Silicon Mac 上執行的本機命令列工具，可將音訊轉成逐字稿，並區分不同說話者。

將錄音輸出為方便閱讀的文字、供代理程式與腳本使用的 JSON，或 SRT 字幕。CLI 使用 Bun 與 Effect，透過 MLX 執行 Qwen3-ASR 語音辨識、Qwen3-ForcedAligner 時間對齊與 Nemotron 說話者分段。

初次準備時會下載相依套件與模型檔案。轉寫時使用本機模型快照，並啟用 Hugging Face 與 Transformers 的離線模式。說話者標籤用來區分同一次執行中的聲音，不會辨識人物姓名。

## 開始使用

需要搭載 Apple Silicon、執行 macOS 14 以上版本的 Mac。原生執行檔已內含 Bun；另需安裝 uv 與 FFmpeg。不需要 GitHub 帳號，即可安裝最新發行版本：

```bash
brew install uv ffmpeg
curl -fsSL https://raw.githubusercontent.com/lupohsiang/transcribe/main/install.sh -o install-transcribe.sh && sh install-transcribe.sh
export PATH="$HOME/.local/bin:$PATH"
transcribe setup
transcribe doctor
transcribe run "$HOME/Downloads/新錄音 4.m4a" --format text --output transcript.txt
```

安裝腳本會驗證 SHA-256 校驗碼，安裝至 `~/.local/share/transcribe/releases`，並在 `~/.local/bin` 建立指令連結。需要時請將 PATH 設定加入 shell 設定檔。再次執行安裝腳本即可升級，升級後請執行 `transcribe setup`。MLX Python 環境與模型由 `setup` 另外準備，不包含在執行檔內。curl、指定版本及原生建置方式請參閱[安裝與發行說明](docs/install.md)。

若要從原始碼執行，請另外安裝 Bun，並在此專案目錄執行：

```bash
brew install uv ffmpeg
bun install --frozen-lockfile
bun src/cli.ts setup
bun src/cli.ts doctor
```

`setup` 會安裝固定版本的 Python 執行環境，並下載指定版本的模型。第一次轉寫前請先執行。`doctor` 用來檢查相依工具與模型檔案是否已備妥。

將錄音轉成文字檔：

```bash
bun src/cli.ts run "recording.m4a" --format text --output transcript.txt
```

第一次試用時，可以先轉寫兩分鐘：

```bash
bun src/cli.ts run "recording.m4a" --duration 120 --format text
```

預設輸出格式為 JSON。需要字幕時，使用 `--format srt`。進度訊息寫入 stderr；逐字稿則寫入 stdout，或 `--output` 指定的檔案。若輸出檔案已存在，必須加上 `--force` 才會覆寫。

所有格式中的中文逐字稿預設輸出為臺灣正體字，使用本機 OpenCC 在語音辨識後轉換。若要保留模型原文，請加上 `--chinese-script original`。此設定不會翻譯其他語言，也不會更改說話者標籤與時間戳記。

更多範例、選項、設定與疑難排解，請參閱[命令列使用指南](docs/cli.zh-tw.md)。

## 文件

- [命令列使用指南](docs/cli.zh-tw.md)：輸出格式、擷取部分錄音、自動化與設定。
- [架構設計（英文）](docs/DESIGN.md)：模型選擇與轉寫流程。
- [驗證紀錄（英文）](docs/VERIFICATION.md)：實際錄音的測試結果與準確度限制。
- [模型版本設定](model-pins.json)：使用的模型儲存庫與固定版本。

語音辨識每次處理最多 30 秒的連續音訊，不會因說話者切換而切成零碎片段。對齊模型會先估計詞語或中文字元的時間，再指派說話者。即使說話者模型未偵測到活動，辨識出的文字仍會保留，並標示為未知說話者。最多支援八個說話者標籤，聲音重疊時不會歸給單一說話者。重要逐字稿請對照原始錄音校對。

目前的對齊轉寫支援中文、粵語、英語、德語、西班牙語、法語、義大利語、葡萄牙語與俄語。日語及韓語所需的斷詞相依套件未包含在此鎖定版本中，其他 ASR 語言也尚不支援此流程。

若從尚未使用對齊模型的舊版升級，請重新執行 `setup`。設計、限制與參考逐字稿的評估方式，請參閱[轉寫品質說明（英文）](docs/QUALITY.md)。

## 開發

安裝 JavaScript 相依套件並執行檢查：

```bash
bun install --frozen-lockfile
bun run check
bun run test
bun run build
bun run build:native
```

測試涵蓋 CLI 行為、搭配協定測試替身的 FFmpeg 轉檔、資料結構驗證、輸出檔案保護、取消執行，以及說話者區間的切分。實際模型推論另行驗證。

`build` 產生透過 Bun 執行的 `dist/cli.js`；`build:native` 在 `dist/release` 產生 Apple Silicon 原生執行檔、Python 支援檔、安裝腳本與校驗碼。請使用完整發行壓縮檔，單獨搬移執行檔會缺少 worker。
