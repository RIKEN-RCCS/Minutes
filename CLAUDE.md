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

## LLM（Claude CLI）

文字起こし結果から議事録を作成するにはClaude CLIを利用する。
`generate_minutes.py`が`claude -p`コマンドを呼び出す。
このCLAUDE.mdのプロジェクト背景・用語集はClaude CLIが自動で読み込むため、
プロンプトへの再記述は不要。

## 富岳NEXTプロジェクトについて

### プロジェクト概要

1. 富岳NEXTに求められる役割
近年、シミュレーションやデータサイエンスの進展に加え、生成AIの急速な普及により計算リソースの需要が急増しています。また、AIやシミュレーション、自動実験、リアルタイムデータを組み合わせた新しい科学研究の重要性が高まるなど、必要とされる計算リソースは一層多様化しています。こうした背景を踏まえ、新たなフラッグシップスーパーコンピュータとして、「富岳NEXT」には、従来のスーパーコンピュータが追求してきたシミュレーション性能をさらに強化するとともに、AIにおいても世界最高水準の性能を達成し、両者が密に連携して処理を行うことができる「AI-HPCプラットフォーム」となることが求められています。

2. 富岳NEXT開発体制
AIおよびシミュレーションの両面で世界最高水準の性能を達成するため、富岳NEXTの開発は、理研を中核とし、高性能ARMベースCPUで世界をリードする富士通のCPU・システム化技術、AI/HPC向けGPUで世界トップシェアを誇るNVIDIAのGPU技術およびグローバルエコシステムを活用した三者連携のもとで進めます。
また、システムソフトウェアを含めたソフトウェア開発は、三者連携による取り組みに加え、国際連携によるオープンな体制で実施する計画です。これらの取り組みにより、AI性能（FP8）で世界発のゼタ（Zetta、10¹⁸）スケールを達成する競争力のあるシステムを構築し、グローバルマーケットへの展開を通じた世界的エコシステムの形成を目指します。

3. 富岳NEXT開発方針
富岳NEXTの開発においては、「次世代計算基盤に関する報告書 最終まとめ」や「次世代計算基盤に関する調査研究」の研究結果、さらに「富岳」の開発と運用による経験と教訓を踏まえた検討結果を基に、開発方針として、「Made with Japan」、「技術革新」、「持続性/継続性」を掲げ、推進していきます。
これらの開発方針を基盤に、次世代AI-HPCプラットフォームによる計算可能領域の拡張と「AI for Science」による新しい科学の創出、先端AI技術・計算基盤における日本の主権確保、さらに半導体や計算リソースのロードマップに基づく持続的研究開発を進めることで、世界的な「富岳NEXT」エコシステムを築き上げ、日本の半導体産業と情報基盤のさらなる強化を目指します。

4. 運用方針
富岳NEXTは、2030年頃の稼働開始を目標に理研神戸地区隣接地に整備し、「富岳」からのシステム移行時においても計算リソースが利用不可となる期間を極力生じさせない利用環境を整え、世界最高水準の計算性能・リソースを安定的に提供し続けることを目指しています。また、量子コンピュータとの連携により新たな計算領域を拡大しつつ、最新の冷却技術の導入や再生可能エネルギーの活用を促進するシステム運用技術等を組み合わせることで、省エネルギー化、低炭素化を追求します。これらの取り組みに加え、AIによる運用および利用者支援をさらに進化させることで、計算基盤の持続性と効率性を確保し、誰もが利用しやすい研究環境を提供する計画です。

5. 研究開発テーマ

5.1. アーキテクチャエリア
富岳NEXTの開発において、半導体製造技術やパッケージング技術、およびメモリ技術の動向を調査しながら、CPUや加速機構のマイクロアーキテクチャやメモリサブシステム、計算ノードアーキテクチャ、Scale-upやScale-outの相互接続網、全体システムなどの、主にハードウェア設計に関する研究開発を行う。特に、開発アーキテクチャに対するアプリケーション性能モデリングの研究を行いつつ、アプリケーションの特性を考慮して、ハードウェアシステムとアプリケーションの協調設計を実施する。

5.2. システムソフトウェアエリア
富岳NEXTの目標性能達成や高いユーザビリティ実現に向け、ハードウェアの潜在能力を最大限に引き出すシステムソフトウェアを開発する。開発成果はオープンソースとして公開し、国際的なOSSコミュニティとの連携を通じて継続的な開発とエコシステム形成を進める。また、開発環境、数値計算・通信基盤、AIソフトを有機的に統合し、次世代のHPC-AI融合基盤を創出する。

5.3. アプリケーション開発エリア
富岳NEXTを代表とする次世代計算基盤の協調設計を想定したHPCアプリケーション研究開発、開発支援、性能評価のためのベンチマークからなる、一連の研究開発を行う。また、シミュレーションとAI技術の高度な融合によるHPCアプリケーションの高速化・機能の拡張と強化を支える基盤技術の創出を目指す。これらの活動を効果的かつ効率的に行うための各種フレームワークの開発と公開を行う。

### ステークホルダー

* 理化学研究所
- 松岡 聡 Satoshi Matsuoka matsu@acm.org: 計算科学研究センター センター長
- 近藤 正章 Masaaki Kondo masaaki.kondo@riken.jp: 次世代計算基盤開発本部 部門長、最終意思決定者
- 佐野 健太郎 Kentaro Sano kentaro.sano@riken.jp: 次世代計算基盤開発部門 次世代計算基盤システム開発ユニット ユニットリーダー、アーキテクチャエリア責任者、マイクロ・ノードアーキテクチャWGリーダー
- 佐藤 賢斗 Kento Sato kento.sato@riken.jp: 次世代計算基盤開発部門 先進的計算基盤技術開発ユニット ユニットリーダー、システムソフトウェアエリア責任者
- 青木 保道 Yasumichi Aoki yasumichi.aoki@riken.jp: 次世代計算基盤開発部門 次世代計算基盤アプリケーション開発ユニット ユニットリーダー、アプリケーション開発エリア責任者、HPCアプリケーションWGリーダー、富岳NEXTのアプリケーションに関する意思決定者
- 山本 啓二 Keiji Yamamoto keiji.yamamoto@riken.jp: 次世代計算基盤部門 次世代計算基盤運用技術ユニット ユニットリーダー、運用技術エリア責任者
- 嶋田 庸嗣 Yoji Shimada yshima@riken.jp: 次世代計算基盤部門 マネジメント室 研究員

* 富士通株式会社
- 新庄 直樹 Naoki Shinjo shinjo@fujitsu.com: 富士通側責任者

* NVIDIA
- Dan Ernst dane@nvidia.com: NVIDIA側のアーキテクチャ責任者
- Heidi Poxon hpoxon@nvidia.com: NVIDIA側のアプリケーション責任者

### 主なプロジェクト参加者

* 理化学研究所
- 庄司 文由 Fumiyoshi Shoji shoji@riken.jp: 次世代計算基盤開発本部 副部門長
- 安里 彰 Akira Asato akira.asato@riken.jp: アーキテクチャエリア エリアマネージャー
- 上野 知洋 Tomohiro Ueno tomohiro.ueno@riken.jp: アーキテクチャエリア システム・ネットワークWGリーダー
- Jens Domke jens.domke@riken.jp: アーキテクチャエリア コデザインWGリーダー
- 村井 均 Hitoshi Murai h-murai@riken.jp: システムソフトウェアエリア プログラミング環境WGリーダー
- 今村 俊幸 Toshiyuki Imamura imamura.toshiyuki@riken.jp: システムソフトウェアエリア 数値計算ライブラリ・ミドルウェアWGリーダー
- 中村 宜文 Yoshifumi Nakamura nakamura@riken.jp: システムソフトウェアエリア 通信ライブラリWGリーダー、アプリケーションエリア ベンチマークWGサブリーダー
- Mohamed Wahib mohamed.attia@riken.jp: システムソフトウェアエリア AIソフトウェアWGリーダー
- William Dawson william.dawson@riken.jp: アプリケーション開発エリアサブリーダー、HPCアプリケーションWGサブリーダー、SubWG2オーガナイザー
- 井上 晃 Hikaru Inoue hikaru.inoue@riken.jp: アプリケーション開発エリア エリアマネージャー
- 西澤 誠也 Seiya Nishizawa s-nishizawa@riken.jp: アプリケーション開発エリア HPCアプリケーションWGサブリーダー
- 小林 千草 Chigusa Kobayashi ckobayashi@riken.jp: アプリケーション開発エリア ベンチマークWGリーダー
- 伊東 真吾 Shingo Ito shingo.ito@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG1オーガナイザー
- 藤田 航平 Kohei Fujita fujita@eri.u-tokyo.ac.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG4オーガナイザー
- 大西 順也 Junya Onishi junya.onishi@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG5オーガナイザー
- 金森 逸作 Issaku Kanamori kanamori-i@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG6オーガナイザー兼リエゾン
- 鈴木 厚 Atsushi Suzuki atsushi.suzuki.aj@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG8オーガナイザー
- 幸城 秀彦 Hidehiko Kohshiro hidehiko.kohshiro@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG2メンバー
- 足立 幸穂 Sachiho Adachi sachiho.adachi@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG3メンバー
- 河合 佑太 Yuta Kawai yuta.kawai@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG3メンバー
- 田中 福治 Fukuharu Tanaka fukuharu.tanaka@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG7メンバー
- James Taylor james.taylor@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG3メンバー
- 垂水 勇太 Yuta Tarumi yuta.tarumi@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG3メンバー
- Tristan Hascoet tristan.hascoet@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG3メンバー
- 滝脇 知也 Tomoya Takiwaki takiwaki.tomoya@nao.ac.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG6リエゾン
- 寺山 慧 Kei Terayama terayama@yokohama-cu.ac.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG1リエゾン
- 高橋 大介 Daisuke Takahashi daisuke@cs.tsukuba.ac.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG8リエゾン
- 山口 弘純 Hirozumi Yamaguchi hirozumi.yamaguchi@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG7オーガナイザー兼リエゾン
- 下川辺 隆史 Takashi Shimokawabe shimokawabe@cc.u-tokyo.ac.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG7アドバイザー
- 岩下 武史 Takeshi Iwashita iwashita@i.kyoto-u.ac.jp: アプリケーション開発エリア HPCアプリケーションWG ブロック2アドバイザー
- 深沢 圭一郎 Keiichiro Fukazawa fukazawa@chikyu.ac.jp: アプリケーション開発エリア HPCアプリケーションWG ブロック1アドバイザー
- 山地 洋平 Youhei Yamaji YAMAJI.Youhei@nims.go.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG2リエゾン
- 小玉 知央 Chihiro Kodama kodamac@jamstec.go.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG3リエゾン
- 高木 亮治 Ryoji Takaki takaki.ryoji@jaxa.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG5リエゾン
- 加藤 千幸 Chisachi Kato kato.chisachi24@nihon-u.ac.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG5アドバイザー
- 中島 研吾 Kengo Nakajima nakajima@cc.u-tokyo.ac.jp: アプリケーション開発エリア HPCアプリケーションWG ブロック1アドバイザー
- 富田 浩文 Hirofumi Tomita htomita@riken.jp: アプリケーション開発エリア HPCアプリケーションWG ブロック2アドバイザー
- 横田 理央 Rio Yokota rio.yokota@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG8メンバー
- 似鳥 啓吾 Keigo Nitadori keigo@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG6メンバー
- 曽田 繁利 Shigetoshi Sota sotas@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG2メンバー
- 大塚 雄一 Yuichi Otsuka otsukay@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG2メンバー
- 安藤 和人 Kazuto Ando kazuto.ando@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG5メンバー
- 山浦 剛 Tsuyoshi Yamaura tyamaura@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG3メンバー
- 黒田 明義 Akiyoshi Kuroda kro@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG8メンバー
- 石附 茂 Shigeru Ishizuki shigeru.ishizuki@riken.jp: アプリケーション開発エリア HPCアプリケーションWG SubWG4,7メンバー
- 足立 朋美 Tomomi Adachi tomomi.adachi@riken.jp: 次世代計算基盤部門 マネジメント室
- 西 直樹 Naoki Nishi naoki.nishi@riken.jp: 次世代計算基盤部門 マネジメント室
- 西田 拓展 Takuhiro Nishida takuhiro.nishida@riken.jp: 次世代計算基盤部門 マネジメント室

* 富士通株式会社
- 福本 尚人 Naoto Fukumoto fukumoto.naoto@fujitsu.com: アプリケーションエリア担当技術者

* NVIDIA
- 成瀬 彰 Akira Naruse anaruse@nvidia.com: アプリケーションエリア担当技術者
- 永田 聡美 Satomi Nagata snagata@nvidia.com: アプリケーションエリア担当営業 シニアマネージャ
- 竹本 祐介 Yusuke Takemoto ytakemoto@nvidia.com: アプリケーションエリア担当営業 カスタマープログラムマネージャ

### プロジェクト固有の用語

**システム・ハードウェア関連**
- 富岳NEXT: 次世代スーパーコンピュータ開発プロジェクト
- MONAKA-X（富士通製次世代CPU、1.4nmプロセス、256コア/ソケット）
- NVLink-C2C（CPU-GPU間の広帯域コヒーレント接続）
- Scale-upネットワーク / Scale-outネットワーク（ノード内GPU接続 vs ノード間接続の区別）
- NVL4 / NVL72（Scale-upドメインサイズの選択肢）
- 3Dチップレット（MONAKA-Xのアーキテクチャ）
- SVE2 / SME2（ARMv9命令セットの拡張）

**プロジェクト・組織関連**
- Made with Japan（国際連携開発コンセプト）
- Genesis Mission（DOEとの国際協力枠組み）
- lighthouse challenge（Genesis Missionの26の科学技術目標）
- 4者連携（ANL・NVIDIA・富士通・理研によるMOU）
- HPSF（国際HPCソフトウェア組織、ベンダー中立な開発体制）
- JAMセッション（最先端AIツールを科学技術に応用する合同セッション）
- RiVault（理研R-CCS製LLM）
- RIKEN TRIP-AGIS（理研のAI4S関連プロジェクト）
- JHPC-quantum（量子-HPC連携プロジェクト）
- DBO方式（Design Build Operate、新計算機棟の建設方式）

**アプリケーション・ソフトウェア関連**
- Benchpark（DOE/MEXT共同開発のCI/CD/CBベンチマークフレームワーク）
- Ozakiスキーム（高精度行列演算を低精度演算器で実現する手法）
- バーチャル富岳（ソフトウェア検証環境）
- Tadashi（コード生成・最適化AIツール）
- GENESIS（分子動力学シミュレーション）
- SALMON（Scalable Ab-initio Light-Matter simulator for Optics and Nanoscience）
- SCALE-LETKF（Coupled weather simulation and data assimilation application）
- E-Wave（地震シミュレーションアプリ）
- FrontFlow/Blue（CFD）
- LQCD-DWF-HMC（Hybrid Monte-Carlo algorithm of domain wall fermions in Lattice QCD）
- FFVHC-ACE（次世代流体解析ソルバー）
- UWABAMI+INAZUMA（一般相対論的輻射磁気流体コード）
- Spack（HPCソフトウェアのパッケージ管理ツール）
- Ramble（Spackと連携して動作するHPCワークロード管理・実験自動化ツール）

**性能・アーキテクチャ概念**
- ゼタ（Zetta）スケール（FLOPSスケールの表現、AI性能目標）
- AI4S / AI for Science（科学へのAI応用の総称）
- Big Simulation / Big Data / AI Scientist（HPC-AI融合の3類型）
- テストベッド Phase-1〜4（段階的GPU環境整備計画）
- 尾崎スキーム（Ozakiスキームの別表記）

**データセンター関連**
- 温水冷却（冷凍機不要化による省エネ手法）
- 冷却電力比率10%目標（「富岳」の35%から削減）
