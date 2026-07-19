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

# Railway injects these as build ARGs only; a Dockerfile build must promote
# them to ENV itself to have them visible at container runtime (Nixpacks
# does this promotion automatically, which is easy to miss when switching
# away from it). main.py logs these so it's possible to tell, from the
# summary email alone, exactly which commit produced a given run, rather
# than assuming a fresh push has actually reached the running container.
ARG RAILWAY_GIT_COMMIT_SHA
ARG RAILWAY_GIT_COMMIT_MESSAGE
ARG RAILWAY_DEPLOYMENT_ID
ENV RAILWAY_GIT_COMMIT_SHA=$RAILWAY_GIT_COMMIT_SHA
ENV RAILWAY_GIT_COMMIT_MESSAGE=$RAILWAY_GIT_COMMIT_MESSAGE
ENV RAILWAY_DEPLOYMENT_ID=$RAILWAY_DEPLOYMENT_ID

# Verify ffprobe actually landed where we expect, at build time rather than
# waiting to discover it's missing on the first real run.
RUN ffprobe -version

CMD ["python", "main.py"]
