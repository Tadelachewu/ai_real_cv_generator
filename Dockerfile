FROM debian:bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system dependencies including additional font packages
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
    libjpeg62-turbo-dev \
    libssl-dev \
    curl \
    libglib2.0-0 \
    fonts-liberation \
    fonts-dejavu \
    fonts-freefont-ttf \
    git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set Python 3.11 as default
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt ./

# Pre-install build dependencies and handle specific package requirements
RUN pip3 install --upgrade pip setuptools wheel && \
    pip3 install --no-cache-dir \
    cffi==1.17.1 \
    pillow==11.3.0 \
    lxml==6.0.0 \
    && pip3 install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

CMD ["python3", "bot.py"]