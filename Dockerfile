FROM python:3.12-slim

# ffmpeg (for ffprobe validation, see pipeline/validate.py) is not included
# in the slim base image. Debian's default repos have it, no extra repo
# config needed (unlike Ubuntu's universe/multiverse).
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
