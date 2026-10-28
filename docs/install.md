# Installation and releases / 安裝與發行

Native releases support Apple Silicon and macOS 14+. Bun is compiled into the CLI with [`bun build --compile`](https://bun.com/docs/bundler/executables). Python/MLX, FFmpeg, and model weights remain external dependencies. `transcribe setup` creates a pinned `.venv` next to the release executable and downloads the pinned models. No source checkout or Bun installation is required to use a release.

原生版本支援 Apple Silicon 與 macOS 14 以上版本，不需要安裝 Bun 或下載原始碼。Python/MLX、FFmpeg 與模型仍為外部相依項目；請執行 `transcribe setup` 準備。

## Private repository / 私人儲存庫

Install Homebrew's `gh`, `uv`, and `ffmpeg`, then authenticate with a GitHub account that can access this repository:

```bash
brew install gh uv ffmpeg
gh auth login
gh api -H 'Accept: application/vnd.github.raw+json' repos/lupohsiang/transcribe/contents/install.sh > install-transcribe.sh && sh install-transcribe.sh
export PATH="$HOME/.local/bin:$PATH"
transcribe setup
transcribe doctor
```

The installer downloads private release assets through `gh release download`. [GitHub requires authentication for private release assets](https://docs.github.com/en/rest/releases/assets#get-a-release-asset). An anonymous curl command cannot download this private repository. Do not put access tokens into URLs.

私人版本透過 `gh release download` 下載，需要有權限的 GitHub 帳號。匿名 curl 無法下載私人儲存庫；請勿將存取權杖放入網址。

## Public curl command / 公開版本的 curl 指令

If the repository is made public later, this command will work without GitHub authentication:

```bash
curl -fsSL https://raw.githubusercontent.com/lupohsiang/transcribe/main/install.sh -o install-transcribe.sh && sh install-transcribe.sh
```

目前仍為私人儲存庫，以上公開下載指令尚不適用。

## Versions and paths / 版本與路徑

The default is the latest published release. To select a release after downloading the install script:

```bash
TRANSCRIBE_VERSION=v0.1.0 sh install-transcribe.sh
```

Repeat installation to upgrade, followed by `transcribe setup`. Archives are verified against the release's `SHA256SUMS` before extraction. This checks download integrity; it is not a separate publisher signature. Installs are kept under `~/.local/share/transcribe/releases/<archive-sha256>`, with a symlink at `~/.local/bin/transcribe`. Existing unrelated executables are preserved. Earlier releases remain available; keep them if you want to switch back. Model files and the model manifest are shared under `~/Library/Application Support/transcribe` by default.

再次安裝即可升級，之後請執行 `transcribe setup`。安裝前會驗證校驗碼；校驗碼用來確認下載完整性，並非獨立的發行者簽章。舊版會保留，模型與模型設定檔則共用預設資料目錄。

| Variable | Purpose / 用途 |
| --- | --- |
| `TRANSCRIBE_VERSION` | Release tag, e.g. `v0.1.0` / 指定發行標籤 |
| `TRANSCRIBE_INSTALL_ROOT` | Absolute release directory / 發行檔案的絕對路徑 |
| `TRANSCRIBE_BIN_DIR` | Absolute command directory / 指令連結的絕對路徑 |
| `TRANSCRIBE_HOME` | Model data and manifest directory / 模型資料與設定檔目錄 |
| `HF_HOME` | Optional Hugging Face cache directory / 自訂模型快取 |

Remove the command symlink and release directories to uninstall. Remove the model data directory separately if you also want to reclaim model storage. No shell profile is modified automatically.

## Building and publishing / 建置與發行

```bash
bun install --frozen-lockfile
bun run check
bun run test
bun run build:native
```

The build produces these files:

```text
dist/release/transcribe-darwin-arm64.tar.gz
dist/release/SHA256SUMS
dist/release/install.sh
```

The archive contains `transcribe`, `python/worker.py`, `pyproject.toml`, `uv.lock`, and `model-pins.json`. Keep these files together. The executable resolves its supporting files from its real location, including when launched through the installed symlink from another directory.

Test installation directly from the local build, without contacting GitHub:

```bash
TRANSCRIBE_ARCHIVE="$PWD/dist/release/transcribe-darwin-arm64.tar.gz" \
TRANSCRIBE_CHECKSUMS="$PWD/dist/release/SHA256SUMS" \
TRANSCRIBE_INSTALL_ROOT="$PWD/dist/test-install/releases" \
TRANSCRIBE_BIN_DIR="$PWD/dist/test-install/bin" sh install.sh
./dist/test-install/bin/transcribe --help
```

To publish, commit and push the changes, then push a tag matching `package.json`'s version. The tag workflow checks the code, cross-compiles for Apple Silicon, and publishes the three release assets. It does not run MLX inference on its Linux runner; real model inference is verified locally on an Apple Silicon Mac.

發行前請提交並推送變更，再推送與 `package.json` 版本相符的標籤。工作流程會檢查程式、交叉編譯並上傳三個發行檔案；實際 MLX 推論另在 Apple Silicon Mac 驗證。
