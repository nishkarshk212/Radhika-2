FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update -y \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        unzip \
        git \
        ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 24 — yt-dlp 2026.x requires Node >= 23.5 to solve YouTube's
# n-signature challenge. Without it every download fails with
# "Sign in to confirm you're not a bot".
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Verify a compatible Node.js runtime is present (required by yt-dlp 2026.x)
RUN node --version
RUN curl -fsSL https://deno.land/install.sh | sh \
    && cp /root/.deno/bin/deno /usr/local/bin/deno

# Verify deno is accessible
RUN deno --version

# Install uv (fast Python package manager)
RUN curl -Ls https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Copy dependency spec and install Python deps first (layer caching)
COPY pyproject.toml ./
RUN uv sync --no-dev
ENV PATH="/app/.venv/bin:${PATH}"

# Copy the rest of the project
COPY . .

# Create necessary runtime directories
RUN mkdir -p downloads cache ishu/cookies

CMD ["bash", "start"]
