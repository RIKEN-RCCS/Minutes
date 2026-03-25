# 会議音声書き起こしシステム

会議音声の自動書き起こしと議事録生成システムです。

## 必要条件

本システムの利用には以下が必要です。

- 音声データからの文字起こしに必要なライブラリ等を格納した Singularity コンテナ
- Speech-to-Text モデルの実行に必要な NVIDIA GPU
- 文字起こしデータの要約に必要な [Claude.ai](https://claude.ai) のアカウント
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
    ↓
構造化議事録
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
    └── YYYY-MM-DD-timestamp-file-md-minutes.md
```

## 使い方

### 1. プロジェクト情報ファイルの準備

`docs/project.md` にプロジェクトの概要・参加者・固有の用語などを Markdown 形式で記述します。
Claude CLI による要約の品質向上に活用されます。以下のセクションを含めると効果的です。

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
```

書き起こし完了後、自動で議事録も生成されます。

`sinfo` でパーティションの空き状況を確認し、以下の優先順位でジョブを投入します。

- `ai-l40s` に空きあり → `--gpus=1` 付きで投入
- `ai-l40s` が満杯・`qc-gh200` に空きあり → GPU 指定なしで投入
- 両方混雑 → `ai-l40s` にデフォルト投入

**出力ファイル：**

- 文字起こし: `data/input/meeting.md`（入力ファイルと同名、拡張子を `.md` に変換）
- 議事録: `minutes/YYYY-MM-DD-timestamp-meeting-md-minutes.md`

## [参考] ローカル LLM による議事録生成

Claude CLI が使えない環境向けに、OpenAI 互換 API を持つローカル LLM で議事録を生成するスクリプトも用意しています。

```bash
python3 scripts/generate_minutes_local.py data/input/meeting.md \
    --url http://hostname:port/v1 \
    --model model-name \
    --token YOUR_TOKEN
```

### オープンモデルの限界と注意事項

`generate_minutes_local.py` は Nemotron 3 Super 120B（1M トークンコンテキスト）での動作検証を行いましたが、Claude Sonnet 4.6 と比較して以下の点で品質差があります。

**① 長文コンテキストの不均一な処理（Lost in the Middle）**

コンテキストウィンドウが 1M トークンであっても、入力全体を均等に参照して出力を生成できるわけではありません。
入力の冒頭・末尾には注意が向きやすい一方、中間部分（この場合は会議後半の議題）が出力から欠落しやすい傾向があります。
これはコンテキスト長の制限とは別の問題で、LLM の注意機構の特性に起因します。

**② instruction following の精度**

「出典は 30 字以内で要約すること」「`**太字**` を使うこと」などの細かいフォーマット指示が守られないケースが多く、試行ごとに出力が不安定でした。
Claude Sonnet 4.6 はプロンプトなしでも `CLAUDE.md` を読み込むだけで高品質な議事録を生成できましたが、オープンモデルでは同等の品質を得るために大幅なプロンプトエンジニアリングが必要でした。

**③ 出力の早期打ち切り**

入力を「受け取れる」ことと、それをもとに「密度の高い長い出力を生成する」ことは別の能力です。
Nemotron は途中で「十分書いた」と判断して出力を打ち切る傾向があり、`--think` モード（推論モード）を有効にすると推論が際限なく続いて終了しなくなる問題も確認されています。
プロンプトに「すべてのセクションを書き終えてから終了すること」と明示することである程度改善できます。

**④ 固有名詞・音声認識ノイズへの対処**

音声認識の誤認識（例：「道頌さん」「ダルセさん」等の存在しない名前）をメンバーリストと照合して排除する、という論理的な判断がうまく機能しませんでした。
プロンプトで明示的に禁止しても完全には防げず、生成された議事録の固有名詞は目視確認が必要です。

**⑤ Claude Sonnet 4.6 との総合比較**

| 観点 | Claude Sonnet 4.6 | Nemotron 3 Super 120B |
|------|-------------------|-----------------------|
| プロジェクト文脈の活用 | `CLAUDE.md` を自動読み込み、プロンプト不要 | `docs/project.md` を明示的に埋め込む必要あり |
| 議事内容の網羅性 | 会議全体を漏れなく整理 | プロンプトで議題を明示しないと後半が欠落 |
| フォーマット遵守 | 指示なしで適切な形式を選択 | 細かい指定が守られないことが多い |
| 固有名詞の正確性 | ほぼ正確 | 誤認識ノイズの混入あり |
| 出力の安定性 | 安定 | 試行ごとにばらつきあり |
| think モード | 高品質かつ適切な時間で完了 | 終了しなくなるケースあり（非推奨） |

オープンモデルでも実用レベルに近い品質は得られますが、生成結果の確認・修正コストを考慮すると、**議事録生成用途では Claude CLI（`generate_minutes.py`）の利用を推奨**します。
