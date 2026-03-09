#!/usr/bin/env python3
"""
議事録生成スクリプト（Claude CLI使用）

Whisper生成の文字起こしテキストをClaude CLIに送信し、
構造化された議事録を生成します。

CLAUDE.mdのプロジェクト背景・用語集はClaude CLIが自動で読み込みます。

使い方:
  python generate_minutes.py data/transcribed/meeting.md
  python generate_minutes.py data/transcribed/meeting.md --output /path/to/minutes
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


PROMPT_TEMPLATE = """\
以下の会議の文字起こしテキストから、構造化された議事録を作成してください。

## 議事録作成ルール

- 文字起こしテキストの内容に忠実に従い、推測を含めない
- Whisperの書き起こし誤認識による不自然な表現は自然な日本語に修正してよいが、事実は変えない
- プロジェクト固有の用語はCLAUDE.mdの用語集を参照して正しく表記する
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

## 文字起こしテキスト

{transcript}
"""


def call_claude(prompt: str) -> str:
    """Claude CLIを呼び出して結果を返す"""
    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude CLIエラー:\n{result.stderr}")
    return result.stdout.strip()


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


def generate_minutes(transcript_path: str, output_dir: str) -> str:
    """文字起こしファイルから議事録を生成してファイルに保存する"""
    print(f"[INFO] 文字起こしファイルを読み込み中: {transcript_path}")
    segments = parse_transcript(transcript_path)
    if not segments:
        raise ValueError(f"文字起こしセグメントが見つかりません: {transcript_path}")
    print(f"[INFO] {len(segments)} セグメントを検出")

    transcript_text = format_transcript(segments)
    prompt = PROMPT_TEMPLATE.format(transcript=transcript_text)

    print("[INFO] Claude CLIで議事録を生成中...")
    minutes_text = call_claude(prompt)

    # 出力パスを生成: minutes/YYYY-MM-DD-HHMMSS-<basename>-minutes.md
    now = datetime.now()
    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    basename = Path(transcript_path).stem
    filename = now.strftime("%Y-%m-%d-%H%M%S") + f"-{basename}-minutes.md"
    output_path = output_dir_path / filename

    output_path.write_text(minutes_text, encoding="utf-8")
    print(f"[INFO] 議事録を保存しました: {output_path}")
    return str(output_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Claude CLIを使用して文字起こしから議事録を生成する"
    )
    parser.add_argument("transcript", help="文字起こし .md/.txt ファイルのパス")
    parser.add_argument(
        "--output", "-o",
        default="minutes",
        help="議事録の出力ディレクトリ（デフォルト: minutes）",
    )
    args = parser.parse_args()

    if not os.path.exists(args.transcript):
        print(f"[ERROR] ファイルが見つかりません: {args.transcript}", file=sys.stderr)
        return 1

    try:
        output_path = generate_minutes(args.transcript, args.output)
        print(f"[完了] {output_path}")
        return 0
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
