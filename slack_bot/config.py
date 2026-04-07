import os
from dotenv import load_dotenv

load_dotenv()

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]
AUDIO_SAVE_DIR = os.environ.get("AUDIO_SAVE_DIR", "/tmp/whisper_audio")
VLLM_API_BASE = os.environ.get("VLLM_API_BASE", "http://localhost:8000/v1")
VLLM_MODEL = os.environ.get("VLLM_MODEL", "Qwen3-Swallow-32B")
HUGGING_FACE_TOKEN = os.environ.get("HUGGING_FACE_TOKEN", "")
