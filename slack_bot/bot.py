import logging
import os
import signal
import sys
import threading
import time

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from config import SLACK_APP_TOKEN, SLACK_BOT_TOKEN
from pipeline import run_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# バックグラウンド実行時に SIGTTIN で停止しないよう stdin を閉じる
sys.stdin = open(os.devnull, "r")

app = App(token=SLACK_BOT_TOKEN)

# 同時実行中のジョブを管理するセマフォ（同時に何件でも受け付けるが、状況把握のため追跡）
_active_jobs: dict[str, str] = {}  # thread_ts → filename
_jobs_lock = threading.Lock()


@app.command("/delete")
def handle_delete(ack, body, client):
    filename = body.get("text", "").strip()
    channel_id = body["channel_id"]

    if not filename:
        ack("使い方: `/delete <ファイル名>`")
        return

    # Bold書式のアスタリスクを除去（例: *foo.md* → foo.md）
    filename = filename.strip("*")
    # 拡張子がなければ .md を付加
    if "." not in filename:
        filename += ".md"

    ack(f"`{filename}` を検索して削除します...")

    response = client.files_list(channel=channel_id, types="all")
    files = response.get("files", [])
    matched = [f for f in files if f.get("name") == filename]

    if not matched:
        client.chat_postMessage(
            channel=channel_id,
            text=f"`{filename}` がこのチャンネルに見つかりませんでした。",
        )
        return

    file_id = matched[0]["id"]
    try:
        client.files_delete(file=file_id)
        client.chat_postMessage(
            channel=channel_id,
            text=f"`{filename}` を削除しました。",
        )
    except Exception as e:
        logger.error("ファイル削除に失敗: %s", e)
        client.chat_postMessage(
            channel=channel_id,
            text=f"`{filename}` の削除に失敗しました: {e}",
        )


@app.command("/transcribe")
def handle_transcribe(ack, body, client):
    filename = body.get("text", "").strip()
    channel_id = body["channel_id"]

    if not filename:
        ack("使い方: `/transcribe <ファイル名>`")
        return

    # 実行中のジョブがあれば受け付けない
    with _jobs_lock:
        if _active_jobs:
            from datetime import datetime
            running = "\n".join(
                f"• `{fname}` (開始: {datetime.fromtimestamp(float(ts)).strftime('%H:%M:%S')})"
                for ts, (fname, ch) in _active_jobs.items()
                if ts != "pending"
            )
            ack(f"現在処理中のジョブがあるため受け付けられません。\n\n"
                f"*実行中:*\n{running}\n\n"
                f"完了後に改めて実行してください。")
            return

        # ジョブ登録（スロット確保）してからACK
        _active_jobs["pending"] = (filename, channel_id)

    ack(f"`{filename}` の処理を開始します。完了したらこのスレッドに返信します。")

    try:
        post = client.chat_postMessage(
            channel=channel_id,
            text=f"`{filename}` のダウンロードを開始します...",
        )
        thread_ts = post["ts"]
    except Exception as e:
        logger.error("チャンネルへのメッセージ投稿に失敗: %s", e)
        with _jobs_lock:
            _active_jobs.pop("pending", None)
        return

    # pending → 正式なthread_tsキーに差し替え
    with _jobs_lock:
        _active_jobs.pop("pending", None)
        _active_jobs[thread_ts] = (filename, channel_id)

    def run_and_cleanup():
        try:
            run_pipeline(client, channel_id, filename, thread_ts)
        finally:
            with _jobs_lock:
                _active_jobs.pop(thread_ts, None)

    threading.Thread(target=run_and_cleanup, daemon=True).start()


def _start_with_retry(max_retries: int = 5, base_delay: float = 10.0):
    """接続失敗時に指数バックオフでリトライする。"""
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    delay = base_delay
    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Socket Mode 接続を開始します (試行 %d/%d)", attempt, max_retries)
            handler.start()
            return  # 正常終了（通常はここに来ない）
        except KeyboardInterrupt:
            logger.info("シャットダウンします")
            return
        except Exception as e:
            if attempt >= max_retries:
                logger.error("接続に %d 回失敗しました。終了します: %s", max_retries, e)
                raise
            logger.warning("接続失敗 (試行 %d/%d): %s — %.0f 秒後にリトライします",
                           attempt, max_retries, e, delay)
            time.sleep(delay)
            delay = min(delay * 2, 300)  # 最大5分


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    _start_with_retry()
