FROM debian:bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    build-essential \
    libcairo2-dev \
    libpango1.0-dev \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-dev \
    libffi-dev \
    libxml2-dev \
    libxslt1-dev \
    libjpeg-dev \
    libssl-dev \
    curl \
    libglib2.0-0 \
    fonts-liberation \
    git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip3 install --upgrade pip && \
    pip3 install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python3", "bot.py"]