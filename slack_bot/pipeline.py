"""
pipeline.py - Whisper文字起こし → LLM要約パイプライン

bot.py のバックグラウンドスレッドから呼び出される。
各ステップ完了時にSlackのスレッドに進捗を投稿する。

Whisper実行はSingularityコンテナ内で行う（trans.sh参照）。
generate_minutes_local.py はコンテナ外のPythonで実行する。
"""

import logging
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

from config import (
    AUDIO_SAVE_DIR,
    HUGGING_FACE_TOKEN,
    SLACK_BOT_TOKEN,
    VLLM_API_BASE,
    VLLM_MODEL,
)

logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"

# アーキテクチャに応じてSIFファイルパスを選択（trans.sh準拠）
_ARCH = platform.machine()
if _ARCH == "aarch64":
    SIF_FILE = Path("/lvs0/rccs-sdt/hikaru.inoue/cpu_aarch64/singularity/whisper.sif")
elif _ARCH == "x86_64":
    SIF_FILE = Path("/lvs0/rccs-sdt/hikaru.inoue/cpu_amd64/singularity/whisper.sif")
else:
    raise RuntimeError(f"Unsupported architecture: {_ARCH}")

# PyAnnoteモデルの永続キャッシュ
HF_HOME = Path.home() / ".cache" / "huggingface"


def _post(client, channel_id, thread_ts, text):
    client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=text)


def download_audio(client, channel_id, filename):
    """チャンネル内のファイルを検索してダウンロードし、保存パスを返す。"""
    response = client.files_list(channel=channel_id, types="all")
    files = response.get("files", [])
    logger.info(f"files_list: {len(files)} 件取得 (channel={channel_id})")
    for f in files:
        logger.info(f"  - name={f.get('name')!r} id={f.get('id')} created={f.get('created')}")
    matched = [f for f in files if f.get("name") == filename]
    if not matched:
        raise FileNotFoundError(f"`{filename}` がチャンネルに見つかりませんでした。")

    url = matched[0].get("url_private_download")
    if not url:
        raise RuntimeError(f"`{filename}` のダウンロードURLが取得できませんでした。")

    os.makedirs(AUDIO_SAVE_DIR, exist_ok=True)
    save_path = Path(AUDIO_SAVE_DIR) / filename

    dl = requests.get(
        url,
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        stream=True,
        timeout=300,
    )
    dl.raise_for_status()

    with open(save_path, "wb") as f:
        for chunk in dl.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)

    return save_path


def run_whisper(audio_path):
    """Singularityコンテナ内でffmpeg変換 + whisper_vad.py を実行する。

    trans.sh のコンテナ実行部分を再現:
      singularity run --nv whisper.sif sh run.sh
    コンテナ内run.shの処理:
      1. /.venv/bin/activate
      2. ffmpeg: 入力 → 16kHz mono WAV
      3. python3 whisper_vad.py wav_file transcript.md
    """
    transcript_path = audio_path.with_suffix(".md")

    # コンテナ内は FFmpeg 6.x 系のみ。torchcodec は FFmpeg 4.x 系の soname を要求する。
    # バインドマウント済みの /lvs0 配下に shim ディレクトリを作り LD_LIBRARY_PATH で解決する。
    # FFmpeg 4.x → 6.x の soname マッピング:
    #   libavutil.so.56    → .58
    #   libavcodec.so.58   → .60
    #   libavformat.so.58  → .60
    #   libavdevice.so.58  → .60
    #   libavfilter.so.7   → .9
    #   libswscale.so.5    → .7
    #   libswresample.so.3 → .4
    _FFMPEG_SHIMS = {
        "libavutil.so.56":    "/usr/lib/aarch64-linux-gnu/libavutil.so.58",
        "libavcodec.so.58":   "/usr/lib/aarch64-linux-gnu/libavcodec.so.60",
        "libavformat.so.58":  "/usr/lib/aarch64-linux-gnu/libavformat.so.60",
        "libavdevice.so.58":  "/usr/lib/aarch64-linux-gnu/libavdevice.so.60",
        "libavfilter.so.7":   "/usr/lib/aarch64-linux-gnu/libavfilter.so.9",
        "libswscale.so.5":    "/usr/lib/aarch64-linux-gnu/libswscale.so.7",
        "libswresample.so.3": "/usr/lib/aarch64-linux-gnu/libswresample.so.4",
    }
    lib_shim_dir = Path(AUDIO_SAVE_DIR) / "lib_shim"
    lib_shim_dir.mkdir(parents=True, exist_ok=True)
    for name, target in _FFMPEG_SHIMS.items():
        symlink = lib_shim_dir / name
        if not symlink.is_symlink():
            symlink.symlink_to(target)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".sh", dir=AUDIO_SAVE_DIR, delete=False
    ) as f:
        run_sh = Path(f.name)
        wav_path = audio_path.with_suffix(".wav")
        f.write(f"""\
. /.venv/bin/activate
# trans.sh と同様に hf_tokens.sh からトークンを読み込む（.env設定が優先）
[ -f ~/.secrets/hf_tokens.sh ] && . ~/.secrets/hf_tokens.sh
export HUGGING_FACE_TOKEN="${{HUGGING_FACE_TOKEN:-{HUGGING_FACE_TOKEN}}}"
export HF_HOME="{HF_HOME}"
export LD_LIBRARY_PATH="{lib_shim_dir}${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"
# GB10統合メモリ環境でCUDA割り当てを柔軟にする
export PYTORCH_CUDA_ALLOC_CONF=backend:cudaMallocAsync,expandable_segments:True

ffmpeg -y -i {audio_path} -ac 1 -ar 16000 -vn -af "highpass=f=1000" -sample_fmt s16 {wav_path}
python3 {SCRIPT_DIR}/whisper_vad.py {wav_path} {transcript_path}
""")

    env = os.environ.copy()
    env["SINGULARITY_BIND"] = "/lvs0"

    try:
        result = subprocess.run(
            ["singularity", "run", "--nv", str(SIF_FILE), "sh", str(run_sh)],
            env=env,
            capture_output=True,
            text=True,
            timeout=7200,  # 2時間でタイムアウト
        )
        if result.returncode != 0:
            logger.error("=== STDOUT ===\n%s", result.stdout[-3000:])
            logger.error("=== STDERR ===\n%s", result.stderr[-3000:])
            raise RuntimeError(
                f"Whisperエラー (exit={result.returncode}):\n"
                f"*STDERR末尾:*\n```{result.stderr[-1500:]}```\n"
                f"*STDOUT末尾:*\n```{result.stdout[-1000:]}```"
            )
        logger.info(result.stdout[-500:])
    finally:
        run_sh.unlink(missing_ok=True)
        wav_path = audio_path.with_suffix(".wav")
        wav_path.unlink(missing_ok=True)

    return transcript_path


def run_minutes(transcript_path):
    """generate_minutes_local.py をコンテナ外のPythonで実行し、議事録パスを返す。"""
    minutes_dir = Path(AUDIO_SAVE_DIR) / "minutes"
    minutes_dir.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "generate_minutes_local.py"),
         str(transcript_path),
         "--model", VLLM_MODEL,
         "--url", VLLM_API_BASE,
         "--output", str(minutes_dir),
         "--multi-stage", "--chunk-minutes", "10",
         "--max-tokens", "16384"],
        capture_output=True,
        text=True,
        timeout=7200,  # 2時間でタイムアウト
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"要約エラー (exit={result.returncode}):\n"
            f"```{result.stderr[-2000:]}```"
        )

    # stdout から "[完了] /path/to/file" を探す
    for line in result.stdout.splitlines():
        if line.startswith("[完了]"):
            minutes_path = Path(line.split(None, 1)[1].strip())
            if minutes_path.exists():
                return minutes_path

    raise RuntimeError("議事録ファイルのパスが取得できませんでした。\n"
                       f"stdout: {result.stdout[-500:]}")


def run_pipeline(client, channel_id, filename, thread_ts):
    """ダウンロード → 文字起こし → 要約 → Slack投稿 の全体パイプライン。"""
    audio_path = None
    transcript_path = None
    try:
        # 1. ダウンロード
        audio_path = download_audio(client, channel_id, filename)
        file_size_mb = audio_path.stat().st_size / (1024 * 1024)
        _post(client, channel_id, thread_ts,
              f"ダウンロード完了: `{filename}` ({file_size_mb:.1f} MB)\n"
              f"文字起こしを開始します...")

        # 2. Whisper文字起こし（Singularityコンテナ内）
        transcript_path = run_whisper(audio_path)
        _post(client, channel_id, thread_ts,
              f"文字起こし完了: `{transcript_path.name}`\n"
              f"要約を開始します（数十分かかる場合があります）...")

        # 3. LLM要約
        minutes_path = run_minutes(transcript_path)

        # 議事録ファイルをスレッドにアップロード
        client.files_upload_v2(
            channel=channel_id,
            thread_ts=thread_ts,
            file=str(minutes_path),
            filename=minutes_path.name,
            title=minutes_path.stem,
            initial_comment="要約完了しました。",
        )

    except Exception as e:
        logger.exception("Pipeline failed")
        _post(client, channel_id, thread_ts, f"エラーが発生しました:\n{e}")
    else:
        # 正常完了時のみ削除
        if audio_path:
            audio_path.unlink(missing_ok=True)
        if transcript_path:
            transcript_path.unlink(missing_ok=True)
