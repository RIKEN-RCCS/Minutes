# 会議音声書き起こしシステム

会議音声の自動書き起こしと議事録生成システムです。

## 必要条件

本システムの利用には以下が必要です。

- AIモデルの実行に必要な NVIDIA GPU
- 音声データからの文字起こし、要約に必要なライブラリ等を格納した Singularity コンテナ
- 議事録生成に必要な LLM（[Claude CLI](https://claude.ai/code) またはローカル LLM）
- プロジェクト情報を記述した Markdown 形式のファイル（`docs/project.md`）

RIKEN R-CCS Cloud のユーザーであれば、以下の資源を利用できます。

- Singularity コンテナのレシピは [RIKEN-RCCS GitHub](https://github.com/RIKEN-RCCS/singularity_defpack/tree/main/gpu_nvidia/whisper) で公開されています。
- `trans.sh` が GPU を搭載した計算ノード（L40S、GH200）を自動選択してジョブを投入します。

## ワークフロー

```
オーディオ/ビデオ
    ↓
[whisper_vad.py]（SLURM ジョブ）
    ↓
文字起こしテキスト（発言者識別付き）
    ↓
[generate_minutes.py]（Claude CLI）
  または
[generate_minutes_local.py]（ローカル LLM・vLLM）
    ↓
構造化議事録（決定事項 / アクションアイテム / 議事内容）
```

## ディレクトリ構成

```
Minutes/
├── README.md              # 本ファイル
├── CLAUDE.md              # Claude Code プロジェクト説明
├── docs/
│   └── project.md         # プロジェクト情報（概要・参加者・用語集）
├── scripts/
│   ├── trans.sh           # SLURM バッチ書き起こしスクリプト
│   ├── whisper_vad.py     # 主書き起こしスクリプト（Whisper + PyAnnote + VAD）
│   ├── generate_minutes.py      # 議事録生成スクリプト（Claude CLI 使用）
│   └── generate_minutes_local.py # 議事録生成スクリプト（ローカル LLM 使用）
├── data/
│   └── input/             # 入力オーディオ/ビデオファイルおよび文字起こしテキスト（.md）
└── minutes/               # 生成された議事録
    ├── YYYY-MM-DD-timestamp-file-md-minutes.md
    └── YYYY-MM-DD-timestamp-file-combined.txt  # Stage 1 キャッシュ（再実行用）
```

## 使い方

### 1. プロジェクト情報ファイルの準備

`docs/project.md` にプロジェクトの概要・参加者・固有の用語などを Markdown 形式で記述します。
議事録生成 LLM のコンテキストとして活用されます。以下のセクションを含めると効果的です。

```markdown
### プロジェクト概要
### ステークホルダー
### 主なプロジェクト参加者
### プロジェクト固有の用語
### 内部・外部の境界
### 会議の種類と頻度
```

### 2. 書き起こしと議事録生成の実行

> **注意：** `trans.sh` は **R-CCS Cloud のログインノードから実行**してください（SLURM ジョブ内からは実行できません）。

音声/動画ファイルを `data/input/` に置き、R-CCS Cloud のログインノードから以下を実行します。

```bash
bash scripts/trans.sh data/input/meeting.mp4
# 冒頭 30 秒をスキップする場合
bash scripts/trans.sh data/input/meeting.mp4 --skip 30
# ローカル LLM エンドポイントを指定する場合
bash scripts/trans.sh data/input/meeting.mp4 --url http://ng-dgx-s-00:8000/v1
```

書き起こし完了後、`generate_minutes_local.py`（`google/gemma-4-26B-A4B-it`）で自動的に議事録が生成されます。
`--url` を省略した場合は `http://localhost:8000/v1`（または環境変数 `GENERATE_MINUTES_URL`）を使用します。

`sinfo` でパーティションの空き状況を確認し、以下の優先順位でジョブを投入します。

- `ai-l40s` に空きあり → `--gpus=1` 付きで投入
- `ai-l40s` が満杯・`qc-gh200` に空きあり → GPU 指定なしで投入
- 両方混雑 → `ai-l40s` にデフォルト投入

**出力ファイル：**

- 文字起こし: `data/input/meeting.md`（入力ファイルと同名、拡張子を `.md` に変換）
- 議事録: `minutes/YYYY-MM-DD-timestamp-meeting-md-minutes.md`

## ローカル LLM による議事録生成（generate_minutes_local.py）

OpenAI 互換 API を持つローカル LLM（vLLM）で議事録を生成するスクリプトです。gemma-4-26B-A4B-it（MoE）を用いると Claude Sonnet 相当の品質が得られます。

### 3ステージパイプライン

単純な単一パス生成では会議後半が欠落しやすい問題（Lost in the Middle）を解決するため、以下の3ステージ方式を採用しています。

```
Stage 1: 文字起こしを 10 分チャンクに分割して各チャンクから事実を抽出
           → minutes/*-combined.txt にキャッシュ保存
Stage 2: 全チャンク要約を統合して議事内容（6-8 節）を生成
Stage 3: 同じチャンク要約から決定事項・アクションアイテムを抽出
```

### 試したモデルの評価

| モデル | 評価 | 所要時間 | 備考 |
|---|---|---|---|
| Nvidia Nemotron-3-Super-120B | 未評価 | マルチステージ未実装時点での単一パス検証のみ。Lost in the Middle で後半欠落。検証環境なく追加評価不可 |
| GLM-4.7-Flash（RiVault） | △ | 速い | chat template トークンが出力に混入。後処理でフィルタ追加で対応 |
| Qwen3-Swallow-32B-RL-v0.2（dense） | ○ | 約60分 | 高品質。常時 reasoning モード。`--think --no-chat-template-kwargs` が必要 |
| Kimi-K2-Thinking（RiVault） | ✗ | − | RiVault の 60 秒 gateway timeout により長文生成で 504 エラー。実用不可 |
| Qwen3-Swallow-30B-A3B-RL-v0.2（MoE） | ○ | 約20分 | 高品質・高速。dense 比約2倍速。`--think --no-chat-template-kwargs` が必要 |
| **gemma-4-26B-A4B-it（MoE）** | **◎** | **約25分** | **現在の推奨。** Qwen3-Swallow より高品質。`--think --temperature 1.0` で動作。vLLM に `--reasoning-parser gemma4` が必要 |

### Claude CLI との比較

| 観点 | Claude CLI | ローカル LLM（gemma-4-26B-A4B-it） |
|---|---|---|
| プロジェクト文脈の活用 | `CLAUDE.md` を自動読み込み、プロンプト不要 | `docs/project.md` をプロンプトに埋め込んで対応 |
| 議事内容の品質 | 高品質 | 同等レベル（Claude Sonnet 相当） |
| 固有名詞の正確性 | ほぼ正確 | 目視確認を推奨 |
| 実行環境 | Claude CLI が必要 | vLLM サーバーがあれば動作 |
| 所要時間 | 数分 | 約25分 |

どちらも実用的な品質で議事録を生成できます。外部サービスに依存しないクローズド環境での運用には `generate_minutes_local.py`（vLLM + gemma-4）が適しています。
