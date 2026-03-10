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
CLAUDE_MD = SCRIPT_DIR / "CLAUDE.md"


# --------------------------------------------------------------------------- #
# プロンプトテンプレート
# --------------------------------------------------------------------------- #
PROMPT_TEMPLATE = """\
以下の会議の文字起こしテキストから、構造化された議事録を作成してください。

## 議事録作成ルール

- 文字起こしテキストの内容に忠実に従い、推測を含めない
- Whisperの書き起こし誤認識による不自然な表現は自然な日本語に修正してよいが、事実は変えない
- プロジェクト固有の用語は以下の「プロジェクト文脈」の用語集を参照して正しく表記する
- 必ず以下のフォーマットのみで出力すること。フォーマット外の説明・コメントは不要

## 出力フォーマット

# 議事録

## 決定事項

- （会議で確定した事項を箇条書きで記載。なければ「（なし）」）

## アクションアイテム

- （担当者・内容を明記したタスクを箇条書きで記載。なければ「（なし）」）

## 議事内容

（議論の流れを要旨としてまとめて記載）

---

## プロジェクト文脈

{claude_md_context}

---

## 文字起こしテキスト

{transcript}
"""


# --------------------------------------------------------------------------- #
# CLAUDE.md 読み込み（関連セクションのみ抽出）
# --------------------------------------------------------------------------- #
def load_claude_md_context() -> str:
    """CLAUDE.md からプロジェクト文脈の関連セクションのみ抽出する"""
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
) -> str:
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "chat_template_kwargs": {
            "enable_thinking": think,
            "clear_thinking": False,
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = base_url.rstrip("/") + "/chat/completions"

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
        token = delta.get("content") or ""
        if token:
            content_parts.append(token)
            print(".", end="", flush=True)
    print(" 完了", flush=True)

    content = "".join(content_parts)
    # インライン <think>...</think> が混入する場合に備えて除去
    return strip_think_blocks(content)


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
) -> str:
    """文字起こしファイルから議事録を生成してファイルに保存する"""
    print(f"[INFO] 文字起こしファイルを読み込み中: {transcript_path}")
    segments = parse_transcript(transcript_path)
    if not segments:
        raise ValueError(f"文字起こしセグメントが見つかりません: {transcript_path}")
    print(f"[INFO] {len(segments)} セグメントを検出")

    transcript_text = format_transcript(segments)
    claude_md_context = load_claude_md_context()
    prompt = PROMPT_TEMPLATE.format(
        claude_md_context=claude_md_context,
        transcript=transcript_text,
    )

    think_label = "有効" if think else "無効"
    print(f"[INFO] ローカルLLM（{model}）で議事録を生成中... （思考モード: {think_label}）")
    minutes_text = call_local_llm(prompt, model, base_url, api_key, timeout, think=think)

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
    print(f"[INFO] LLM URL     : {base_url}")

    try:
        output_path = generate_minutes(
            args.transcript, args.output, args.model, base_url, api_key, args.timeout,
            think=args.think,
        )
        print(f"[完了] {output_path}")
        return 0
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
