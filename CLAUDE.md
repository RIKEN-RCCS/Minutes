# CLAUDE.md

このファイルは、このリポジトリで作業する際にClaude Code (claude.ai/code)に役立てるためのガイドです。

## 概要

このリポジトリはHPCクラスタ上での自動会議音声書き起こしシステムを含んでいます。システムは会議の日本語オーディオファイルを、発言者の識別を含む構造化されたMarkdown文書に書き起こし、LLMを用いて議事録を生成します。

## ディレクトリ構成

```
Minutes/
├── CLAUDE.md              # ベースライン指示
├── README.md              # プロジェクト概要
├── scripts/               # スクリプト
│   ├── trans.sh           # SLURMバッチジョブ実行スクリプト
│   ├── whisper_vad.py     # 主書き起こしスクリプト（Whisper + PyAnnote + VAD）
│   └── generate_minutes.py # 議事録生成スクリプト（Claude CLI使用）
├── data/                  # 音声データ
│   └── input/             # オーディオ/ビデオファイルおよびWhisper出力（.mdファイル）
└── minutes/               # 最終会議記録
    └── YYYY-MM-DD-timestamp-file-md-minutes.md
```

## アーキテクチャ

音声処理パイプライン：
1. **音声抽出**: `ffmpeg`を使用してオーディオチャネルを抽出（モノラル、16kHz）
2. **ノイズ除去**（オプション）: DeepFilterNet3で背景ノイズを除去
3. **音声区間検出**: Silero VADで静音セグメントを検出
4. **話者識別**: PyAnnoteが話者を識別
5. **音声書き起こし**: Whisper large-v3（ローカルまたはリモート）が音声を書き起こす
6. **出力整形**: 話者セグメント化されたMarkdown出力を作成
7. **議事録生成**: Claude CLIが文字起こしを構造化された議事録に変換

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

文字起こしからClaude CLIを使用して構造化された議事録を生成します：

1. **決定事項** - 会議で決定された事項
2. **アクションアイテム** - 担当者が割り当てられたタスク
3. **議事内容** - 文字起こし内容に基づく議論の要旨

**ガイドライン:**
- 文字起こしテキストの内容に忠実に従う
- 事実を保持したまま不自然な表現を修正する
- 推測を含めない
- 確定した発言者名を使用する

## GitHubへのpush

このリポジトリはプライベートリポジトリのため、認証情報をURLに含めてpushする。

```bash
source ~/.secrets/github_tokens.sh && git push https://hikaru-inoue-cyber:${GITHUB_TOKEN}@github.com/RIKEN-RCCS/Minutes.git
```

- GitHubユーザー名: `hikaru-inoue-cyber`
- トークン: `~/.secrets/github_tokens.sh` の `GITHUB_TOKEN` 変数

## LLM（Claude CLI）

文字起こし結果から議事録を作成するにはClaude CLIを利用する。
`generate_minutes.py`が`claude -p`コマンドを呼び出す。
このCLAUDE.mdのプロジェクト背景・用語集はClaude CLIが自動で読み込むため、
プロンプトへの再記述は不要。

## プロジェクトの説明
<!-- プロジェクトの内容を docs/project.md に記載する、機密性の高い内容のため github へ登録しない -->
@docs/project.md
