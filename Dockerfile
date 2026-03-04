FROM ubuntu:24.04

# 시간대 설정 등 상호작용 방지
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# 우분투 패키지 업데이트 및 Python 3.12 설치
RUN apt-get update && apt-get install -y \
    python3.12 \
    python3.12-venv \
    python3-pip \
    python3.12-dev \
    build-essential \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# python을 python3.12로 연결
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 && \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1

WORKDIR /app

# 파이썬 패키지 설치를 위해 요구사항 파일 복사 및 설치
# Ubuntu 24.04부터는 PEP 668에 의해시스템 전역 패키지 설치 시 --break-system-packages 플래그가 필요합니다.
COPY requirements-openclaw-pi-langchain.txt /tmp/
RUN pip install --no-cache-dir --break-system-packages -r /tmp/requirements-openclaw-pi-langchain.txt
