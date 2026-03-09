# 富岳NEXT会議音声書き起こしシステム

HPCクラスター上で動作する、会議音声の自動書き起こしと議事録生成システムです。

## ワークフロー

```
オーディオ/ビデオ
    ↓
[trans.sh + whisper_vad.py]（SLURMジョブ）
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
│   ├── input/             # 元のオーディオ/ビデオファイル
│   └── transcribed/       # Whisper生成文字起こしテキスト（.md）
└── minutes/               # 最終議事録
    └── YYYY-MM-DD-timestamp-file-md-minutes.md
```

## 使い方

### 1. 音声書き起こし

オーディオファイルを`data/input/`ディレクトリに配置し、SLURMジョブを提出します：

```bash
bash scripts/trans.sh data/input/meeting.mp4
# または冒頭30秒をスキップする場合
bash scripts/trans.sh data/input/meeting.mp4 --skip 30
```

パーティションは自動選択されます（ai-l40s → qc-gh200 → ai-l40s）。
文字起こし結果は入力ファイルと同名の`.md`ファイルとして`data/transcribed/`に保存されます。

### 2. 議事録を生成

文字起こしファイルからClaude CLIを使って議事録を生成します：

```bash
python scripts/generate_minutes.py data/transcribed/meeting.md

# 出力ディレクトリを指定する場合
python scripts/generate_minutes.py data/transcribed/meeting.md --output output_dir
```

議事録は`minutes/YYYY/`ディレクトリに自動的に保存されます。

## プロジェクト背景

本システムは富岳NEXTプロジェクトの議事録生成に使用します。

**富岳NEXTは次世代スーパーコンピュータ開発プロジェクトで、シミュレーション性能とAI性能を両立したAI-HPCプラットフォームの構築を目指しています。**

理研、富士通、NVIDIAの三者連携で開発され、2030年頃の運用開始を計画しています。

**開発領域:**
1. アーキテクチャエリア - ハードウェアシステム設計
2. システムソフトウェアエリア - システムソフトウェアとエコシステム
3. アプリケーション開発エリア - アプリケーション開発とベンチマークフレームワーク

**主要技術:**
- MONAKA-X: 富士通次世代CPU（1.4nm、256コア/ソケット）
- NVIDIA GPU統合
- Benchparkベンチマークフレームワーク
- Spack/Rambleワークロード管理

詳細については[CLAUDE.md](CLAUDE.md)をご参照ください。
