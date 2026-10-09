FROM python:3.13-slim
WORKDIR /app
# ffmpeg cuts the one-line audio clips for tap mode and the phone hotline (/audio/clip/...)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
CMD ["./start.sh"]
