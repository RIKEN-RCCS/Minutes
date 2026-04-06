# CLAUDE.md

このファイルは、このリポジトリで作業する際にClaude Code (claude.ai/code)に役立てるためのガイドです。

## 概要

このリポジトリはHPCクラスタ上での自動会議音声書き起こしシステムを含んでいます。システムは会議の日本語オーディオファイルを、発言者の識別を含む構造化されたMarkdown文書に書き起こし、LLMを用いて議事録を生成します。

## ディレクトリ構成

```
Minutes/
├── CLAUDE.md              # ベースライン指示
├── README.md              # プロジェクト概要
├── docs/
│   └── project.md         # プロジェクト説明・参加者一覧・用語集（gitignore）
├── scripts/               # スクリプト
│   ├── trans.sh           # SLURMバッチジョブ実行スクリプト
│   ├── whisper_vad.py     # 主書き起こしスクリプト（Whisper + PyAnnote + VAD）
│   ├── generate_minutes.py         # 議事録生成スクリプト（Claude CLI使用）
│   ├── generate_minutes_local.py   # 議事録生成スクリプト（ローカルLLM使用）
│   └── local.sh           # generate_minutes_local.py の実行コマンド（gitignore）
├── data/                  # 音声データ
│   └── input/             # オーディオ/ビデオファイルおよびWhisper出力（.mdファイル）
└── minutes/               # 最終会議記録
    ├── YYYY-MM-DD-timestamp-file-md-minutes.md
    └── YYYY-MM-DD-timestamp-file-combined.txt  # Stage 1 キャッシュ（再実行用）
```

## アーキテクチャ

音声処理パイプライン：
1. **音声抽出**: `ffmpeg`を使用してオーディオチャネルを抽出（モノラル、16kHz）
2. **ノイズ除去**（オプション）: DeepFilterNet3で背景ノイズを除去
3. **音声区間検出**: Silero VADで静音セグメントを検出
4. **話者識別**: PyAnnoteが話者を識別
5. **音声書き起こし**: Whisper large-v3（ローカルまたはリモート）が音声を書き起こす
6. **出力整形**: 話者セグメント化されたMarkdown出力を作成
7. **議事録生成**: LLM（Claude CLI またはローカル LLM）が文字起こしを構造化された議事録に変換

## ワークフロー

### 会議記録生成パイプライン

1. **入力**: `data/input/`ディレクトリにオーディオ/ビデオファイルを配置
2. **書き起こし・議事録生成**: SLURMジョブを実行（書き起こし完了後、自動で議事録も生成）
   ```bash
   bash scripts/trans.sh data/input/meeting.mp4 [--skip 30]
   ```
   ログインノードから実行。パーティションを自動選択（ai-l40s優先、次いでqc-gh200）。
   - 書き起こし出力: `data/input/meeting.md`（入力ファイルと同じディレクトリに同名で生成）
   - 議事録出力: `minutes/YYYY-MM-DD-timestamp-file-md-minutes.md`

### 使い方

#### SLURMクラスターで書き起こしを実行

**ログインノードから**実行すること（SLURMジョブ内からは不可）：
```bash
bash scripts/trans.sh file1.mp4 file2.mp4 [--skip 30]
```

- デフォルト: ファイル全体を処理
- `--skip N`: ファイル冒頭のN秒をスキップ
- ファイルを単一のジョブで順次処理
- パーティションを自動選択（`sinfo`でidle/mixノードを確認）:
  - `ai-l40s` 空きあり → `--gpus=1` 付きで投入
  - `ai-l40s` 空きなし・`qc-gh200` 空きあり → GPU指定なしで投入
  - 両方混雑 → `ai-l40s` にデフォルト投入

#### Pythonスクリプトを直接実行

```bash
python scripts/whisper_vad.py input.wav output.md [--local] [--denoise]
```

#### 議事録を生成

```bash
python scripts/generate_minutes.py data/transcribed/meeting.md
```

### 書き起こし形式

Whisper生成テキスト（発言者セグメントあり）：
```markdown
# Transcription

#### [00:00:00 - 00:00:30] SPEAKER_00
会議の内容...

#### [00:00:35 - 00:01:00] SPEAKER_01
返答内容...
```

### パラメータ設定（scripts/whisper_vad.py内）

```python
CHUNK_LENGTH = 30  # 秒（Whisperの最大チャンク）
INITIAL_PROMPT = "以下は富岳NEXT開発プロジェクトの日本語の会議録です..."  # システムプロンプト
MODEL_LOCAL = "./whisper-large-v3-ja-final"  # ローカルファインチューニング済み日本語モデル
```

## 議事録フォーマット

文字起こしからLLMを使用して構造化された議事録を生成します：

1. **決定事項** - 会議で決定された事項
2. **アクションアイテム** - 担当者が割り当てられたタスク
3. **議事内容** - 文字起こし内容に基づく議論の要旨

**ガイドライン:**
- 文字起こしテキストの内容に忠実に従う
- 事実を保持したまま不自然な表現を修正する
- 推測を含めない
- 確定した発言者名を使用する

## LLM による議事録生成

議事録生成には以下の2つの方法がある。

### Claude CLI（generate_minutes.py）

`generate_minutes.py`が`claude -p`コマンドを呼び出す。
このCLAUDE.mdのプロジェクト背景・用語集はClaude CLIが自動で読み込むため、
プロンプトへの再記述は不要。

### ローカル LLM（generate_minutes_local.py）

vLLM サーバー上のローカル LLM を使う方法。外部サービス不要でクローズド環境でも動作する。
gemma-4-26B-A4B-it（MoE）を用いると Claude Sonnet 相当の品質が得られることを確認済み。
詳細は「ローカルLLM議事録生成」セクションを参照。

## ローカルLLM議事録生成（generate_minutes_local.py）

vLLMサーバー上のローカルLLMを使って議事録を生成するスクリプト。
`scripts/local.sh` に現在の実行コマンドを記載している。

### 3ステージパイプライン（--multi-stage）

```
Stage 1: チャンク抽出（extract_from_chunk × N）
  文字起こしを --chunk-minutes 分ごとに分割し、各チャンクから事実を抽出する。
  結果は minutes/*-combined.txt にキャッシュ保存される。

Stage 2: 議事内容生成（PROMPT_TEMPLATE）
  全チャンク要約を統合し、6-8 の節からなる議事内容（## 議事内容）を生成する。

Stage 3: 決定事項・アクションアイテム抽出（DECISIONS_TEMPLATE）
  同じチャンク要約から ## 決定事項 と ## アクションアイテム を抽出する。
```

出力順序: `## 決定事項` → `## アクションアイテム` → `## 議事内容`

### 主なオプション

| オプション | 説明 |
|---|---|
| `--model MODEL` | vLLM で起動しているモデル名 |
| `--url URL` | vLLM エンドポイント（例: `http://ng-dgx-s-00:8000/v1`）|
| `--multi-stage` | 3ステージパイプラインを有効化 |
| `--chunk-minutes N` | Stage 1 のチャンク長（分）。デフォルト 30、推奨 10 |
| `--think` | reasoning モード（Qwen3-Swallow等の thinking モデル用）|
| `--no-chat-template-kwargs` | `chat_template_kwargs` を送信しない（Qwen3-Swallow では必須）|
| `--max-tokens N` | 最大生成トークン数。thinking モデルは 16384 推奨 |
| `--temperature F` | サンプリング温度（デフォルト: think時 0.6、通常時 0.8）|
| `--from-combined FILE` | Stage 1 をスキップし、キャッシュから Stage 2+3 を実行 |

### vLLMサーバーと現在の推奨設定

- **エンドポイント**: `http://ng-dgx-s-00:8000/v1`
- **現在のモデル**: `google/gemma-4-26B-A4B-it`（MoE、約25分/会議）
  - `--think --temperature 1.0 --max-tokens 16384` が必要（`--no-chat-template-kwargs` は不要）
  - vLLM 起動時に `--reasoning-parser gemma4` が必要
- **RiVault**（代替）: `http://llm.ai.r-ccs.riken.jp:11434/v1`、トークンは `~/.secrets/rivault_tokens.sh`

### 試したモデルの評価まとめ

| モデル | 結果 | 備考 |
|---|---|---|
| **Nvidia Nemotron-3-Super-120B** | 未評価 | マルチステージ未実装時点での単一パス検証のみ。Lost in the Middle で会議後半が欠落。`--think` は推論が終了しなくなるため非推奨。検証環境は既になく追加評価不可 |
| **GLM-4.7-Flash**（RiVault） | △ 動作するが品質低め | `--no-chat-template-kwargs` なし時代。chat template トークン（`<|user|>` 等）が出力に混入する問題あり。後処理でフィルタ追加 |
| **Qwen3-Swallow-32B-RL-v0.2**（dense） | ○ 高品質 | 常時 reasoning モード。`--no-chat-template-kwargs --think` が必要。streaming 時に `content` が空になる問題 → no_stream リトライで解決。速度が遅い（10チャンクで約60分）|
| **Kimi-K2-Thinking**（RiVault） | ✗ 実用不可 | RiVault の 60 秒 gateway timeout により長文プロンプトで 504 エラー。thinking が `content` 内の `<think>` タグに入る（`reasoning_content` ではない）など挙動が異なる |
| **Qwen3-Swallow-30B-A3B-RL-v0.2**（MoE） | ○ 高品質・高速 | MoE により dense 比約2倍速（約20分/会議）。`--think --no-chat-template-kwargs --max-tokens 16384` |
| **gemma-4-26B-A4B-it**（MoE） | ◎ 現在の推奨 | Qwen3-Swallow より高品質。約25分/会議。`--think --temperature 1.0 --max-tokens 16384`（`--no-chat-template-kwargs` 不要）。vLLM に `--reasoning-parser gemma4` が必要。`-it` 版でないとチャットテンプレートがなく動作しない |

**マルチステージパイプライン導入の経緯**: Nemotron・GLM での単一パス生成では会議後半の欠落や品質の不安定さが課題だった。文字起こしを10分チャンクに分割して段階的に処理する方式（`--multi-stage --chunk-minutes 10`）で品質が大幅に改善した。

### 設計上の注意点

- Qwen3-Swallow は常時 reasoning モード。vLLM の `--reasoning-parser qwen3` が thinking を `reasoning_content` に分離するため、streaming 時に `content` が空になることがある → 自動で `no_stream=True` リトライ
- `strip_think_blocks()`: `</think>` が max_tokens 内に収まらない場合は空文字を返してリトライを促す
- Stage 3 の `decisions_max_tokens` は `--max-tokens` の値を使用（固定 4096 では thinking に使い切られる）
- チャンク要約（combined）は話者帰属情報を除去した散文のため、アクションアイテムの担当者特定精度はやや低い

## プロジェクトの説明
<!-- プロジェクトの内容を docs/project.md に記載する、機密性の高い内容のため github へ登録しない -->
@docs/project.md
