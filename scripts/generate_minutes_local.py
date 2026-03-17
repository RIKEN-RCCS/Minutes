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
以下の会議の文字起こしテキストから、構造化された議事録を作成してください。
文字起こしテキストを冒頭から末尾まで通読してから書き始め、すべてのセクションを完全に書き終えてください。

## 話者ラベルの実名対応

文字起こし中の SPEAKER_XX は音声認識による仮ラベルです。
文字起こしテキスト中で他の参加者から「○○さん」と名前を呼ばれているか、自己紹介が確認できる場合のみ実名を使用してください。
それ以外は SPEAKER_XX のまま記載し、メンバーリストからの憶測による割り当ては行わないでください。

## 議事録作成ルール

- 文字起こしテキストの内容に忠実に従い、推測・創作を含めない
- Whisperの書き起こし誤認識による不自然な表現は自然な日本語に修正してよいが、事実は変えない
- プロジェクト固有の用語は「プロジェクト文脈」の用語集を参照して正しく表記する
- 出力に含まれるすべての人名は「プロジェクト文脈」のメンバーリストに存在する名前のみ使用すること。リストに存在しない名前（例：「道頌さん」「ダルセさん」等）は音声認識の誤認識であるため、絶対に出力に含めないこと
- 必ず以下のフォーマットのみで出力すること。フォーマット外の説明・コメントは不要

## 出力フォーマット

### セクション1: 決定事項

まず「# 議事録」「## 決定事項」の見出しを書いてください。
次に、会議中に合意・確認された事項を箇条書きで列挙してください。
決定事項とは、「はい、わかりました」「そうしましょう」「〜にします」「〜で進める」など、参加者間で同意が成立した方針・ルール・日程・役割分担等のことです。
各項目は「- 決定した内容 [出典: 議論の要約]」の形式で書いてください。出典は文字起こしを直接引用せず、必ず自分の言葉で30字以内に要約してください。
なければ「（なし）」と書いてください。

### セクション2: アクションアイテム

「## アクションアイテム」の見出しを書き、担当者が割り当てられたすべてのタスクを表形式で記載してください。
「〜をお願いする」「〜してください」「〜を確認する」「〜を進める」など、明示的に担当者が指名されたタスクを抽出してください。
会議の全体（インプット管理、NVIDIAスケジュール、AI性能目標の担当確認、報告書作成、エフォート・出張申請など）を通じたタスクを漏れなく記載してください。
表のヘッダー行は「| 担当者 | タスク内容 | 期限 |」です。
なければ「（なし）」と書いてください。

### セクション3: 議事内容

「## 議事内容」の見出しを書き、議題ごとに節を立てて記述してください。
各節は以下の形式（アスタリスク2つで挟んだ太字）の見出しで始め、続けてその議題の議論内容を150〜400字で記述してください。
`###` などのMarkdown見出し記号や、アスタリスク1つ（`*イタリック*`）は使わないこと。

  **ベンチマーク用インプットリポジトリの展開方針**

  （議論内容）
記述内容：誰が何を提案・説明し、どのような意見が交わされ、どのような結論・方針になったか。固有名詞・数値・日付は正確に保持すること。
一つの節に複数の異なる話題を詰め込まず、話題転換ごとに新しい節を立ててください。
この会議には少なくとも以下の議題があります：インプット管理方針、NVIDIAとのベンチマークスケジュール、AI性能目標の推定担当、報告書作成の進捗、エフォート・出張申請について。
文字起こしの冒頭から終わりまで、すべての議題を網羅してください（途中で省略しないこと）。

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
    max_tokens: int = 32768,
) -> str:
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "max_tokens": max_tokens,
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
        token = delta.get("content") or delta.get("reasoning_content") or ""
        if token:
            content_parts.append(token)
            print(".", end="", flush=True)
    print(" 完了", flush=True)

    content = "".join(content_parts)
    print(f"[DEBUG] 生成トークン数（strip前）: {len(content)} chars, think={think}")
    stripped = strip_think_blocks(content)
    print(f"[DEBUG] 生成トークン数（strip後）: {len(stripped)} chars")
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
    max_tokens: int = 32768,
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
    minutes_text = call_local_llm(prompt, model, base_url, api_key, timeout, think=think, max_tokens=max_tokens)

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
    parser.add_argument("--max-tokens", type=int, default=32768, help="最大出力トークン数（デフォルト: 32768）")
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
    print(f"[INFO] LLM URL     : {base_url}")

    try:
        output_path = generate_minutes(
            args.transcript, args.output, args.model, base_url, api_key, args.timeout,
            think=args.think, max_tokens=args.max_tokens,
        )
        print(f"[完了] {output_path}")
        return 0
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
