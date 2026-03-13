# 会議音声書き起こしシステム

会議音声の自動書き起こしと議事録生成システムです。  
音声データからの文字起こしに必要なライブラリ等はSingularityでコンテナ化されており、R-CCS CloudのGPU(L40S,GH200)で自動的に実行されます。文字起こしされたデータはClaude CLIで要約されます。  
実行にはR-CCS CloudとClaude.aiのアカウントが必要です。

## ワークフロー

```
オーディオ/ビデオ
    ↓
[whisper_vad.py]（SLURMジョブ）
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
├── CLAUDE.md              # Claude Codeプロジェクト説明
├── scripts/
│   ├── trans.sh           # SLURMバッチ書き起こしスクリプト
│   ├── whisper_vad.py     # 主書き起こしスクリプト（Whisper + PyAnnote + VAD）
│   └── generate_minutes.py # 議事録生成スクリプト（Claude CLI使用）
├── data/
│   └── input/             # オーディオ/ビデオファイルおよびWhisper生成文字起こしテキスト（.md）
└── minutes/               # 最終議事録
    └── YYYY-MM-DD-timestamp-file-md-minutes.md
```

## 使い方

* docs/project.mdを用意し、プロジェクトに関する概要、参加者、固有の用語などをMarkdown形式で記述してください。Claude CLIで文字起こしテキストを要約する際に効果を発揮します。

* オーディオファイルを`data/input/`ディレクトリに配置し、ログインノードから実行します：

```bash
bash scripts/trans.sh data/input/meeting.mp4
# または冒頭30秒をスキップする場合
bash scripts/trans.sh data/input/meeting.mp4 --skip 30
```

**ログインノードから実行してください**（SLURMジョブ内からは実行不可）。
書き起こし完了後、自動で議事録も生成されます。

`sinfo`でパーティションの空き状況を確認し、自動選択します：
- `ai-l40s` 空きあり → `--gpus=1` 付きで投入
- `ai-l40s` 空きなし・`qc-gh200` 空きあり → GPU指定なしで投入
- 両方混雑 → `ai-l40s` にデフォルト投入

**出力:**
- 書き起こし: `data/input/meeting.md`（入力ファイルと同名）
- 議事録: `minutes/YYYY-MM-DD-timestamp-meeting-md-minutes.md`
