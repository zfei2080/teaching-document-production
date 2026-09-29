"""
讲义自动化生成系统 - 配置文件

默认直连阿里云百炼并固定使用 qwen-vl-max 做结构化抽题。
"""

import os
import logging

# --- API 配置 ---
# 默认直连阿里云百炼；如需改回其它兼容 OpenAI 的服务，可通过环境变量覆盖
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("DASHSCOPE_API_KEY", "")
OPENAI_BASE_URL = os.environ.get(
    "OPENAI_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1"
)

_LOCAL_PROXY_PREFIXES = (
    "http://127.0.0.1:8765/v1",
    "http://localhost:8765/v1",
)

def check_api_key():
    """检查 API Key 是否已设置；本地代理模式下允许使用占位 key。"""
    if OPENAI_BASE_URL.startswith(_LOCAL_PROXY_PREFIXES):
        return
    if not OPENAI_API_KEY:
        raise ValueError("API Key 未设置。请设置 OPENAI_API_KEY 或 DASHSCOPE_API_KEY。")

# --- 模型参数 ---
MODEL_NAME = os.environ.get("LECTURE_MODEL", "qwen-vl-max")
MAX_TOKENS = int(os.environ.get("LECTURE_MAX_TOKENS", "8192"))
TEMPERATURE = float(os.environ.get("LECTURE_TEMPERATURE", "0.3"))

# --- 重试配置 ---
MAX_RETRIES = 3
INITIAL_BACKOFF = 2  # 秒

# --- 输出配置 ---
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
OUTPUT_FORMAT = os.environ.get("LECTURE_OUTPUT_FORMAT", "docx")  # "docx" | "md"

# --- 模板配置 ---
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

# --- 批量处理配置 ---
DELETE_SOURCE_AFTER_PROCESS = (
    os.environ.get("LECTURE_DELETE_SOURCE", "false").lower() == "true"
)
SOURCE_DIR = os.environ.get("LECTURE_SOURCE_DIR", "")

# --- 日志配置 ---
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_LEVEL = logging.INFO

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)

# 确保输出目录存在
os.makedirs(OUTPUT_DIR, exist_ok=True)
