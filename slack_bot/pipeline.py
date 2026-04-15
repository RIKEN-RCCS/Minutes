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
import threading
import time
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


def download_audio(client, channel_id, filename,
                   max_retries: int = 5, retry_delay: float = 5.0):
    """チャンネル内のファイルを検索してダウンロードし、保存パスを返す。

    アップロード直後は files_list に反映されない場合があるため、
    ファイルが見つからない間は max_retries 回まで retry_delay 秒待ってリトライする。
    """
    matched = []
    for attempt in range(1, max_retries + 1):
        response = client.files_list(channel=channel_id, types="all")
        files = response.get("files", [])
        logger.info(f"files_list (試行 {attempt}/{max_retries}): {len(files)} 件取得 (channel={channel_id})")
        for f in files:
            logger.info(f"  - name={f.get('name')!r} id={f.get('id')} created={f.get('created')}")
        matched = [f for f in files if f.get("name") == filename]
        if matched:
            break
        if attempt < max_retries:
            logger.warning(f"`{filename}` が見つかりません。{retry_delay:.0f} 秒後にリトライします... ({attempt}/{max_retries})")
            time.sleep(retry_delay)

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

    # stdout と stderr をマージしてリアルタイムにログ出力する
    output_lines = []
    try:
        proc = subprocess.Popen(
            ["singularity", "run", "--nv", str(SIF_FILE), "sh", str(run_sh)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for line in proc.stdout:
            line = line.rstrip()
            logger.info("[whisper] %s", line)
            output_lines.append(line)
        proc.wait(timeout=7200)

        if proc.returncode != 0:
            tail = "\n".join(output_lines[-50:])
            raise RuntimeError(
                f"Whisperエラー (exit={proc.returncode}):\n"
                f"```{tail[-2000:]}```"
            )
    finally:
        run_sh.unlink(missing_ok=True)
        wav_path = audio_path.with_suffix(".wav")
        wav_path.unlink(missing_ok=True)

    return transcript_path


def run_minutes(transcript_path, client, channel_id, thread_ts):
    """generate_minutes_local.py をコンテナ外のPythonで実行し、議事録パスを返す。

    stdout をリアルタイムにログ出力しつつ [INFO] 行をパースして
    Stage 進捗を Slack スレッドに投稿する。
    """
    minutes_dir = Path(AUDIO_SAVE_DIR) / "minutes"
    minutes_dir.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT_DIR / "generate_minutes_local.py"),
         str(transcript_path),
         "--model", VLLM_MODEL,
         "--url", VLLM_API_BASE,
         "--output", str(minutes_dir),
         "--multi-stage", "--chunk-minutes", "10",
         "--max-tokens", "4096"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # stderr は別スレッドで読み、logger.warning に流す
    stderr_lines = []

    def _read_stderr():
        for line in proc.stderr:
            line = line.rstrip()
            logger.warning("[minutes] %s", line)
            stderr_lines.append(line)

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()

    # stdout をリアルタイムに処理
    minutes_path = None
    total_chunks = None
    posted_milestones = set()  # 投稿済みの進捗マイルストーン（%）

    for raw_line in proc.stdout:
        line = raw_line.rstrip()
        logger.info("[minutes] %s", line)

        # Stage 1 開始: チャンク数を把握
        if "チャンクに分割" in line:
            # "[INFO] マルチステージモード: N チャンクに分割（各約 M 分）"
            try:
                total_chunks = int(line.split(":")[1].strip().split()[0])
            except Exception:
                pass
            _post(client, channel_id, thread_ts,
                  f"Stage 1 開始: 文字起こしを {total_chunks} チャンクに分割して抽出中...")

        # Stage 1 チャンク完了: 25% / 50% / 75% / 100% でSlack投稿
        elif "抽出完了" in line and total_chunks:
            # "[INFO] チャンク N/total 抽出完了（M 字）"
            try:
                frac = line.split("チャンク")[1].split("抽出完了")[0].strip()
                current = int(frac.split("/")[0])
                pct = current * 100 // total_chunks
                milestone = (pct // 25) * 25  # 25, 50, 75, 100
                if milestone > 0 and milestone not in posted_milestones:
                    posted_milestones.add(milestone)
                    _post(client, channel_id, thread_ts,
                          f"Stage 1: チャンク抽出 {milestone}% 完了 ({current}/{total_chunks})")
            except Exception:
                pass

        # Stage 2 開始
        elif "議事録を統合生成中" in line:
            _post(client, channel_id, thread_ts,
                  "Stage 2: 議事内容を統合生成中...")

        # Stage 3 開始
        elif "決定事項・アクションアイテムを生成中" in line:
            _post(client, channel_id, thread_ts,
                  "Stage 3: 決定事項・アクションアイテムを抽出中...")

        # 完了行からパスを取得
        elif line.startswith("[完了]"):
            try:
                minutes_path = Path(line.split(None, 1)[1].strip())
            except Exception:
                pass

    proc.wait(timeout=7200)
    stderr_thread.join()

    if proc.returncode != 0:
        tail = "\n".join(stderr_lines[-30:])
        raise RuntimeError(
            f"要約エラー (exit={proc.returncode}):\n"
            f"```{tail[-2000:]}```"
        )

    if minutes_path is None or not minutes_path.exists():
        raise RuntimeError("議事録ファイルのパスが取得できませんでした。\n"
                           f"stderr: {chr(10).join(stderr_lines[-10:])}")

    return minutes_path


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
        minutes_path = run_minutes(transcript_path, client, channel_id, thread_ts)

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
