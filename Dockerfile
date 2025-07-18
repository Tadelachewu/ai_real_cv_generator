FROM python:3.11.9

ENV DEBIAN_FRONTEND=noninteractive

# Install WeasyPrint dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libcairo2 \
    pango1.0-tools \
    libpango1.0-dev \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-dev \
    libffi-dev \
    libxml2 \
    libxslt1.1 \
    libjpeg-dev \
    libssl-dev \
    curl \
    libgobject-2.0-0 \
    fonts-liberation \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . /app

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

CMD ["python", "bot.py"]
