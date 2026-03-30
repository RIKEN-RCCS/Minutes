#!/usr/bin/env python3
"""
generate_minutes_local.py

Whisper生成の文字起こしMarkdownからローカルLLMを使って議事録を生成する。

generate_minutes.py のローカルLLM版。
Claude CLIが自動で読み込むCLAUDE.mdのプロジェクト文脈を明示的にプロンプトに埋め込む。

使い方:
  python generate_minutes_local.py TRANSCRIPT_FILE --model MODEL [options]

Options:
  --model MODEL       使用するモデル名（必須）
  --think             思考モードを有効化（デフォルト: 無効）
  --output DIR        議事録の出力ディレクトリ（デフォルト: minutes）
  --url URL           ローカルLLMのURL（RIVAULT_URL 環境変数でも可）
  --token TOKEN       APIトークン（RIVAULT_TOKEN 環境変数でも可）
  --timeout SEC       LLM呼び出しタイムアウト秒数（デフォルト: 600）

認証情報の読み込み順序:
  1. --url / --token 引数
  2. RIVAULT_URL / RIVAULT_TOKEN 環境変数
  3. ~/.secrets/rivault_tokens.sh の内容をパース
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests


# --------------------------------------------------------------------------- #
# パス解決
# --------------------------------------------------------------------------- #
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
PROJECT_MD = REPO_ROOT / "docs" / "project.md"


# --------------------------------------------------------------------------- #
# プロンプトテンプレート
# --------------------------------------------------------------------------- #
PROMPT_TEMPLATE = """\
以下の会議の文字起こしテキストから、2ステップで議事録を作成してください。

---

## ステップ1: 議題スキャン

文字起こしの全セグメントを先頭から末尾まで確認し、大きな話題の転換点を記録してください。
細かい話題は関連する上位議題にまとめつつ、**必ず6〜8個の議題ブロック**に整理してください。ブロックが5個以下になる場合は大きなトピックをさらに細分化してください。

[議題スキャン]
- [HH:MM:SS] （議題名）
- [HH:MM:SS] （議題名）
（末尾まで）
[/議題スキャン]

---

## ステップ2: 議事録の作成

ステップ1でリストアップした議題ブロックを漏れなく反映した議事録を、以下のルールとフォーマットで作成してください。

### 話者ラベルの実名対応

文字起こし中の SPEAKER_XX は音声認識による仮ラベルです。
文字起こしテキスト中で他の参加者から「○○さん」と名前を呼ばれているか、自己紹介が確認できる場合のみ実名を使用してください。
それ以外は SPEAKER_XX のまま記載し、メンバーリストからの憶測による割り当ては行わないでください。
**SPEAKER_XX と実名を並記することは禁止**（例：「小林 千草 (SPEAKER_04)」は不可）。

入力テキスト（チャンク抽出結果）中に「ナルセさん」「ダルセさん」「道頌さん」等の非公式な呼称や音声認識誤りが含まれている場合、メンバーリストで照合して正式名称に置き換えること（例:「ナルセさん」→「成瀬 彰」）。照合できない場合は出力に含めないこと。

### 議事録作成ルール

- 文字起こしテキストの内容に忠実に従い、推測・創作を含めない
- Whisperの書き起こし誤認識による不自然な表現は自然な日本語に修正してよいが、事実は変えない
- プロジェクト固有の用語は「プロジェクト文脈」の用語集を参照して正しく表記する
- 出力に含まれる人名はすべて「プロジェクト文脈」のメンバーリストに記載された正式名称を使用すること
- 日付・期限は文字起こしに出てくる表現のみ使うこと（「今週末」「月曜日」「26日」等）。文字起こしに月・年の記載がない場合に「3月」「2026年」等を補完することは厳禁。数字だけの日付（「26日」）はそのまま「26日」と記載し、「3月26日」に拡張しないこと
- 出力は `# 議事録` で始め、`## 議事内容` の最後の `### ` セクション本文の最後の文で終えること。「以上が」「以上、」「上記が」等の締めくくり文は書かないこと

### 出力フォーマット（この構造を厳守すること）

以下の骨格の順番・見出し記号を変えずに出力してください。

```
# 議事録

## 決定事項

- （決定事項1） [出典: 30字以内]
- （決定事項2） [出典: 30字以内]

## アクションアイテム

| 担当者 | タスク内容 | 期限 |
|---|---|---|
| （名前） | （タスク） | （期限または「未定」） |

## 議事内容

### （議題ブロック1のタイトル）

（300〜600字の議論内容）

### （議題ブロック2のタイトル）

（300〜600字の議論内容）
```

各セクションの記載ルール:

**## 決定事項**: 参加者間で同意が成立した事項（「そうしましょう」「〜で進める」「〜にします」等）を箇条書き。なければ「（なし）」。

**## アクションアイテム**: 文字起こし全体から、個人・グループへのタスク依頼をすべて漏れなく抽出して表に記載。抽出対象: 「〜をお願いします」「〜してください」「〜を確認します」「〜を連絡します」「〜を進めます」「〜をやっておきます」「〜に依頼します」等。担当者が「各担当者」「みなさん」等の場合もそのまま記載。期限は文字起こしの表現のまま。不明は「（未定）」。なければ「（なし）」。

**## 議事内容**: ステップ1の議題ブロック（**必ず6節以上、目標6〜8節**）ごとに `### ` 見出しで節を立てること。節が5節以下になる場合は大きな議題をサブトピックに分割して節を増やすこと。各節に**必ず300字以上**（目標400〜600字）で以下を記述（300字未満の場合は議論の詳細を補完すること）:
- 誰が（確認できた実名またはSPEAKER_XX）何を提案・報告・質問したか
- どのような意見・懸念・数値・条件が挙げられたか
- どのような結論・方針・継続課題になったか
- 固有名詞・数値・日付・バージョン番号は正確に保持

ステップ1の全ブロックを記述し終えたら出力を終了してください。

---

## プロジェクト文脈

{claude_md_context}

---

## 文字起こしテキスト

{transcript}
"""


# --------------------------------------------------------------------------- #
# プロジェクト文脈の読み込み
# --------------------------------------------------------------------------- #
def load_claude_md_context() -> str:
    """docs/project.md からプロジェクト文脈を読み込む。
    存在しない場合は CLAUDE.md 内の関連セクションにフォールバックする。"""
    # まず docs/project.md を読み込む
    if PROJECT_MD.exists():
        content = PROJECT_MD.read_text(encoding="utf-8")
        sections = []
        capture = False
        for line in content.splitlines():
            if re.match(r"^###\s+(ステークホルダー|主なプロジェクト参加者|プロジェクト固有の用語|会議の種類)", line):
                capture = True
            if capture:
                sections.append(line)
        return "\n".join(sections) if sections else content

    # フォールバック: CLAUDE.md から抽出
    if not CLAUDE_MD.exists():
        return ""
    content = CLAUDE_MD.read_text(encoding="utf-8")
    sections = []
    capture = False
    for line in content.splitlines():
        if re.match(r"^###\s+(ステークホルダー|主なプロジェクト参加者|プロジェクト固有の用語|会議の種類)", line):
            capture = True
        elif re.match(r"^---", line) and capture:
            capture = False
        if capture:
            sections.append(line)
    return "\n".join(sections) if sections else content[:3000]


# --------------------------------------------------------------------------- #
# 認証情報の読み込み
# --------------------------------------------------------------------------- #
def load_rivault_tokens() -> tuple[str, str]:
    """RIVAULT_URL と RIVAULT_TOKEN を返す。読み込み順: 環境変数 → トークンファイル"""
    url = os.environ.get("RIVAULT_URL")
    token = os.environ.get("RIVAULT_TOKEN")

    if url and token:
        return url, token

    token_file = Path.home() / ".secrets" / "rivault_tokens.sh"
    if token_file.exists():
        content = token_file.read_text(encoding="utf-8")
        for line in content.splitlines():
            m = re.match(r'^\s*export\s+RIVAULT_URL=["\']?(.*?)["\']?\s*$', line)
            if m:
                url = url or m.group(1).strip()
            m = re.match(r'^\s*export\s+RIVAULT_TOKEN=["\']?(.*?)["\']?\s*$', line)
            if m:
                token = token or m.group(1).strip()

    if not url:
        print("ERROR: RIVAULT_URL が設定されていません。", file=sys.stderr)
        print("  環境変数 RIVAULT_URL を設定するか --url で指定してください。", file=sys.stderr)
        sys.exit(1)
    if not token:
        print("ERROR: RIVAULT_TOKEN が設定されていません。", file=sys.stderr)
        print("  環境変数 RIVAULT_TOKEN を設定するか --token で指定してください。", file=sys.stderr)
        sys.exit(1)

    return url, token


# --------------------------------------------------------------------------- #
# 文字起こし解析（generate_minutes.py と同一）
# --------------------------------------------------------------------------- #
def parse_transcript(file_path: str) -> list[dict]:
    """文字起こしファイルを解析して発言セグメントのリストを返す"""
    content = Path(file_path).read_text(encoding="utf-8")

    pattern = re.compile(
        r"####\s*\[([0-9:]+)\s*-\s*([0-9:]+)\]\s+(SPEAKER_\d+)\n(.*?)(?=\n####|\Z)",
        re.DOTALL,
    )

    segments = []
    for m in pattern.finditer(content):
        start_str, end_str, speaker, text = m.groups()
        text = text.strip()
        if not text or text in ("...", "…"):
            continue
        segments.append({
            "speaker": speaker,
            "start": _parse_timestamp(start_str.strip()),
            "end": _parse_timestamp(end_str.strip()),
            "text": text,
        })
    return segments


def _parse_timestamp(time_str: str) -> int:
    """HH:MM:SS形式のタイムスタンプを秒数に変換する"""
    parts = time_str.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + int(s)
    return 0


def format_transcript(segments: list[dict]) -> str:
    """セグメントリストをLLMへの入力テキストに整形する"""
    lines = []
    for seg in segments:
        h, rem = divmod(seg["start"], 3600)
        m, s = divmod(rem, 60)
        timestamp = f"{h:02d}:{m:02d}:{s:02d}"
        lines.append(f"[{timestamp}] {seg['speaker']}: {seg['text']}")
    return "\n\n".join(lines)


def chunk_transcript(segments: list[dict], chunk_duration_sec: int = 1800) -> list[list[dict]]:
    """セグメントリストを時間ウィンドウごとに分割する"""
    if not segments:
        return []
    start_time = segments[0]["start"]
    chunks: list[list[dict]] = []
    current: list[dict] = []
    for seg in segments:
        if seg["start"] >= start_time + chunk_duration_sec * (len(chunks) + 1):
            if current:
                chunks.append(current)
                current = []
        current.append(seg)
    if current:
        chunks.append(current)
    return chunks


# --------------------------------------------------------------------------- #
# チャンク抽出プロンプト（マルチステージ Stage 2 用）
# --------------------------------------------------------------------------- #
CHUNK_EXTRACTION_TEMPLATE = """\
以下は会議の一部分（第{chunk_idx}/{total_chunks}部、時刻 {time_range}）の文字起こしです。
「プロジェクト文脈」のメンバーリストと用語集を参照しながら、この部分から以下を箇条書きで簡潔に抽出してください。

## 抽出ルール

### 日付・期限
- 文字起こしに出てくる表現のまま記載すること
- 「26日」は「26日」のまま記載し、「3月26日」「2月26日」等に拡張しないこと

### 人名の処理（厳守）
- 文字起こし中で「○○さん」「○○くん」等と呼ばれた名前は、**必ずプロジェクト文脈のメンバーリストで照合**すること
  - 照合できた場合: メンバーリストの正式名称を使用（例: 「ナルセさん」→「成瀬 彰」、「竹本さん」→「竹本 祐介」）
  - 照合できない場合: その名前を出力に含めず「SPEAKER_XX」で代替すること
- 発言者の帰属は「○○さんが〜しました」等の明示的な文脈がある場合のみ記載し、推測で帰属させないこと
- 発言者が不明確な場合は「SPEAKER_XX が〜」または帰属なしで内容のみ記載すること

### その他
- 文字起こしの内容にのみ基づいて抽出すること（推測・創作禁止）

## 主要な議論ポイント
（この部分で扱われた主なトピックを箇条書きで5〜8点。各50字以内）
- ...

## 仮の決定事項
（参加者間で合意が成立した事項のみ。なければ「なし」）
- ...

## 仮のアクションアイテム
（「〜をお願いします」「〜してください」「〜を確認します」「〜に依頼します」等、担当者が明示されたタスクをすべて抽出）
- [担当者] タスク内容（期限：xx日）

## 話者確認
（「○○さん」等で実名確認できた SPEAKER_XX のみ。なければ「なし」）
- SPEAKER_XX = 名前

---

## プロジェクト文脈

{claude_md_context}

---

## 文字起こし（{time_range}）

{chunk_text}
"""


def extract_from_chunk(
    chunk_text: str,
    chunk_idx: int,
    total_chunks: int,
    time_range: str,
    claude_md_context: str,
    model: str,
    base_url: str,
    api_key: str,
    timeout: int,
    no_stream: bool = False,
) -> str:
    """1チャンクから事実を抽出する（Stage 2）"""
    prompt = CHUNK_EXTRACTION_TEMPLATE.format(
        chunk_idx=chunk_idx,
        total_chunks=total_chunks,
        time_range=time_range,
        claude_md_context=claude_md_context,
        chunk_text=chunk_text,
    )
    system = (
        "あなたは会議議事録の補助AIです。"
        "必ず日本語のみで回答してください。英語での説明・分析・推論は不要です。"
        "指定された出力フォーマットに従い、箇条書きで簡潔に出力してください。"
    )
    result = call_local_llm(
        prompt, model, base_url, api_key, timeout,
        think=False, max_tokens=1024, no_stream=no_stream, system=system,
    )
    return result


# --------------------------------------------------------------------------- #
# ローカルLLM 呼び出し（requests ライブラリ使用・ストリーミング）
# --------------------------------------------------------------------------- #
def strip_think_blocks(text: str) -> str:
    """<think>...</think> ブロックを除去して議事録本文のみを返す"""
    return re.sub(r"<think>[\s\S]*?</think>\s*", "", text).strip()


def call_local_llm(
    prompt: str,
    model: str,
    base_url: str,
    api_key: str,
    timeout: int = 600,
    think: bool = False,
    max_tokens: int = 8192,
    no_stream: bool = False,
    system: str = "",
) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.8,
    }
    # thinking モードは Qwen/ELYZA 系モデルの vLLM 拡張パラメータのみ送信
    if think:
        payload["chat_template_kwargs"] = {
            "enable_thinking": True,
            "clear_thinking": False,
        }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = base_url.rstrip("/") + "/chat/completions"

    if no_stream:
        # 非ストリーミング（LiteLLM プロキシ経由で streaming が動作しない場合等）
        payload["stream"] = False
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        content = msg.get("content") or msg.get("reasoning_content") or ""
        print(f"[INFO] 生成トークン数（strip前）: {len(content)} chars, think={think}")
        stripped = strip_think_blocks(content)
        print(f"[INFO] 生成トークン数（strip後）: {len(stripped)} chars")
        return stripped

    # ストリーミング（デフォルト）
    payload["stream"] = True
    resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=timeout)
    resp.raise_for_status()

    content_parts: list[str] = []
    print("[INFO] 生成中 ", end="", flush=True)
    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if not line.startswith("data: "):
            continue
        data_str = line[len("data: "):]
        if data_str.strip() == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        choices = chunk.get("choices", [])
        if not choices:
            continue
        delta = choices[0].get("delta", {})
        token = delta.get("content") or delta.get("reasoning_content") or ""
        if token:
            content_parts.append(token)
            print(".", end="", flush=True)
    print(" 完了", flush=True)

    content = "".join(content_parts)
    print(f"[INFO] 生成トークン数（strip前）: {len(content)} chars, think={think}")
    stripped = strip_think_blocks(content)
    print(f"[INFO] 生成トークン数（strip後）: {len(stripped)} chars")
    return stripped


# --------------------------------------------------------------------------- #
# 議事録生成
# --------------------------------------------------------------------------- #
def generate_minutes(
    transcript_path: str,
    output_dir: str,
    model: str,
    base_url: str,
    api_key: str,
    timeout: int,
    think: bool = False,
    max_tokens: int = 8192,
    multi_stage: bool = False,
    chunk_minutes: int = 30,
    no_stream: bool = False,
) -> str:
    """文字起こしファイルから議事録を生成してファイルに保存する"""
    print(f"[INFO] 文字起こしファイルを読み込み中: {transcript_path}")
    segments = parse_transcript(transcript_path)
    if not segments:
        raise ValueError(f"文字起こしセグメントが見つかりません: {transcript_path}")
    print(f"[INFO] {len(segments)} セグメントを検出")

    claude_md_context = load_claude_md_context()

    if multi_stage:
        # ------------------------------------------------------------------ #
        # マルチステージ: 分割→抽出→統合
        # ------------------------------------------------------------------ #
        chunk_duration_sec = chunk_minutes * 60
        chunks = chunk_transcript(segments, chunk_duration_sec)
        total = len(chunks)
        print(f"[INFO] マルチステージモード: {total} チャンクに分割（各約 {chunk_minutes} 分）")

        extractions: list[str] = []
        for i, chunk_segs in enumerate(chunks, 1):
            chunk_text = format_transcript(chunk_segs)
            h0, r0 = divmod(chunk_segs[0]["start"], 3600)
            m0, s0 = divmod(r0, 60)
            h1, r1 = divmod(chunk_segs[-1]["end"], 3600)
            m1, s1 = divmod(r1, 60)
            time_range = f"{h0:02d}:{m0:02d}:{s0:02d}〜{h1:02d}:{m1:02d}:{s1:02d}"
            print(f"[INFO] チャンク {i}/{total} を抽出中... ({time_range})")
            extraction = extract_from_chunk(
                chunk_text, i, total, time_range,
                claude_md_context, model, base_url, api_key, timeout,
                no_stream=no_stream,
            )
            extractions.append(f"=== 第{i}部（{time_range}）===\n{extraction}")
            print(f"[INFO] チャンク {i}/{total} 抽出完了（{len(extraction)} 字）")

        combined = "\n\n".join(extractions)
        print(f"[INFO] 全チャンク抽出完了。統合テキスト: {len(combined)} 字")
        print(f"[INFO] ローカルLLM（{model}）で議事録を統合生成中...")
        prompt = PROMPT_TEMPLATE.format(
            claude_md_context=claude_md_context,
            transcript=combined,
        )
        minutes_text = call_local_llm(
            prompt, model, base_url, api_key, timeout,
            think=think, max_tokens=max_tokens, no_stream=no_stream,
        )
    else:
        # ------------------------------------------------------------------ #
        # 単一パス（従来の動作）
        # ------------------------------------------------------------------ #
        transcript_text = format_transcript(segments)
        prompt = PROMPT_TEMPLATE.format(
            claude_md_context=claude_md_context,
            transcript=transcript_text,
        )
        think_label = "有効" if think else "無効"
        print(f"[INFO] ローカルLLM（{model}）で議事録を生成中... （思考モード: {think_label}）")
        minutes_text = call_local_llm(
            prompt, model, base_url, api_key, timeout,
            think=think, max_tokens=max_tokens, no_stream=no_stream,
        )

    # ステップ1スクラッチパッドを除去: "# 議事録\n" (単独行) 以降のみを保持
    for marker in ("# 議事録\n\n", "# 議事録\n"):
        idx = minutes_text.find(marker)
        if idx >= 0:
            if idx > 0:
                print(f"[INFO] スクラッチパッド除去: 先頭 {idx} 文字を削除")
                minutes_text = minutes_text[idx:]
            break
    # モデルが "# 議事録" を重複出力する場合に除去
    minutes_text = re.sub(r'^(# 議事録\s*\n+){2,}', '# 議事録\n\n', minutes_text)
    # 末尾の締めくくりコメントを除去（「以上」「以下」「上記」で始まる行以降）
    minutes_text = re.sub(r'\n+(?:以上|以下|上記)[^\n]*$', '', minutes_text.rstrip())
    # 絶対年号を除去: 「2025年3月26日」→「3月26日」（文字起こし中の相対日付が年付きに拡張された場合）
    minutes_text = re.sub(r'\d{4}年(\d{1,2}月\d{1,2}日)', r'\1', minutes_text)
    # 「（推測）」「（不明）」等の不確かさ注記を除去
    minutes_text = re.sub(r'（推測）|（不明）|（確認要）|（未確認）', '', minutes_text)
    # llama.cpp / Ollama 等でチャットテンプレートの区切りトークンが漏出する場合に除去
    minutes_text = re.sub(r'<\|(?:user|assistant|system|endoftext)\|>.*', '', minutes_text, flags=re.DOTALL).rstrip()

    # 出力パスを生成: {output_dir}/YYYY-MM-DD-HHMMSS-<basename>-minutes.md
    now = datetime.now()
    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    basename = Path(transcript_path).stem
    filename = now.strftime("%Y-%m-%d-%H%M%S") + f"-{basename}-minutes.md"
    output_path = output_dir_path / filename

    output_path.write_text(minutes_text, encoding="utf-8")
    print(f"[INFO] 議事録を保存しました: {output_path}")
    return str(output_path)


# --------------------------------------------------------------------------- #
# メイン
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(
        description="ローカルLLMを使用して文字起こしから議事録を生成する"
    )
    parser.add_argument("transcript", help="文字起こし .md/.txt ファイルのパス")
    parser.add_argument("--model", required=True, help="使用するモデル名")
    parser.add_argument(
        "--output", "-o",
        default="minutes",
        help="議事録の出力ディレクトリ（デフォルト: minutes）",
    )
    parser.add_argument("--think", action="store_true", help="思考モードを有効化（デフォルト: 無効）")
    parser.add_argument("--url", default=None, help="ローカルLLMのURL（RIVAULT_URL 環境変数でも可）")
    parser.add_argument("--token", default=None, help="APIトークン（RIVAULT_TOKEN 環境変数でも可）")
    parser.add_argument("--timeout", type=int, default=600, help="LLM呼び出しタイムアウト秒数（デフォルト: 600）")
    parser.add_argument("--max-tokens", type=int, default=8192, help="最大出力トークン数（デフォルト: 8192）")
    parser.add_argument("--multi-stage", action="store_true", help="マルチステージ（分割→抽出→統合）モードを有効化")
    parser.add_argument("--chunk-minutes", type=int, default=30, help="マルチステージ時のチャンクサイズ（分単位、デフォルト: 30）")
    parser.add_argument("--no-stream", action="store_true", help="ストリーミングを無効化（LiteLLM プロキシ経由等で streaming が動作しない場合に使用）")
    args = parser.parse_args()

    if not os.path.exists(args.transcript):
        print(f"[ERROR] ファイルが見つかりません: {args.transcript}", file=sys.stderr)
        return 1

    # 認証情報の読み込み（引数 > 環境変数 > トークンファイル）
    if args.url:
        os.environ["RIVAULT_URL"] = args.url
    if args.token:
        os.environ["RIVAULT_TOKEN"] = args.token
    base_url, api_key = load_rivault_tokens()

    print(f"[INFO] モデル      : {args.model}")
    print(f"[INFO] 思考モード  : {'有効' if args.think else '無効'}")
    print(f"[INFO] max_tokens  : {args.max_tokens}")
    print(f"[INFO] マルチステージ: {'有効' if args.multi_stage else '無効'}")
    if args.multi_stage:
        print(f"[INFO] チャンク    : {args.chunk_minutes} 分")
    print(f"[INFO] ストリーミング: {'無効' if args.no_stream else '有効'}")
    print(f"[INFO] LLM URL     : {base_url}")

    try:
        output_path = generate_minutes(
            args.transcript, args.output, args.model, base_url, api_key, args.timeout,
            think=args.think, max_tokens=args.max_tokens,
            multi_stage=args.multi_stage, chunk_minutes=args.chunk_minutes,
            no_stream=args.no_stream,
        )
        print(f"[完了] {output_path}")
        return 0
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
