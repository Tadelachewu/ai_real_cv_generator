FROM debian:bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    build-essential \
    libcairo2-dev \
    libpango1.0-dev \
    libgdk-pixbuf-2.0-dev \
    libffi-dev \
    libxml2-dev \
    libxslt1-dev \
    libjpeg62-turbo-dev \
    libssl-dev \
    fonts-liberation \
    fonts-dejavu \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create and activate virtual environment
RUN python3.11 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Copy requirements first
COPY requirements.txt .

# Install dependencies in virtualenv
# NOTE: setuptools must stay < 81. python-telegram-bot 13.x depends on
# APScheduler 3.6.3, which imports `pkg_resources` (removed in setuptools 81+).
RUN pip install --upgrade pip wheel "setuptools<81" && \
    pip install --no-cache-dir -r requirements.txt && \
    python -c "import pkg_resources, telegram.ext, weasyprint; print('dependency check OK')"

COPY . .

CMD ["python", "bot.py"]