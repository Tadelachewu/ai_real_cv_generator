# Use the official Python 3.11 slim image
FROM python:3.11.9-slim

# Set working directory
WORKDIR /app

# Copy project files into the container
COPY . /app

# Upgrade pip and install dependencies
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Set the command to run the bot
CMD ["python", "bot.py"]
