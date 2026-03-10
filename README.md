# 富岳NEXT会議音声書き起こしシステム

HPCクラスター上で動作する、会議音声の自動書き起こしと議事録生成システムです。

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

オーディオファイルを`data/input/`ディレクトリに配置し、ログインノードから実行します：

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
