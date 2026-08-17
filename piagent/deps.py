# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import ipaddress
import importlib
import importlib.util
import json
import math
import mimetypes
import os
import queue
import re
import shlex
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Optional, Protocol, Sequence

# LangChain may import transformers for tokenizer utilities. PiAgent does not
# require Torch, so skip transformers' optional Torch probe unless users opt in.
os.environ.setdefault("USE_TORCH", "0")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

from dotenv import load_dotenv
from pydantic import Field, create_model

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain.tools import tool
from langgraph.config import get_stream_writer

# 스크립트 실행 시 현재 디렉토리 또는 상위 디렉토리의 .env 파일을 찾아 환경 변수로 동적 할당합니다.
# override=True 로 설정하여, 도커 시동 시 잡혀있던 환경변수보다 수정된 .env 값이 우선하도록 합니다.
load_dotenv(override=True)


def _now_ts() -> float:
    return time.time()


DEFAULT_BLOCKED_PATHS = (
    ".env",
    ".git/**",
    ".openclaw/memory/**",
    "secrets/**",
    "private/**",
    "node_modules/**",
)

WORK_NOTE_SECTIONS = (
    "Title",
    "Current State",
    "Task Spec",
    "Critical Files",
    "Decisions",
    "Commands",
    "Errors",
    "Verification",
    "Worklog",
)


def _split_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _sanitize_user_id(raw: Optional[str]) -> Optional[str]:
    text = str(raw or "").strip()
    if not text:
        return None
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", text).strip("._-")
    return safe or None

__all__ = [name for name in globals() if not name.startswith("__")]
