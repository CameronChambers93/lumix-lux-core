FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# --- Claude Code (native installer; auto-updates). Kept early so project layers below don't reinstall it.
USER vscode
RUN curl -fsSL https://claude.ai/install.sh | bash && mkdir -p /home/vscode/.claude
USER root
ENV PATH=/home/vscode/.local/bin:$PATH \
    CLAUDE_CONFIG_DIR=/home/vscode/.claude

# --- Core: Python 3.12 (Python runtime, fakecam) and Node 22 (JS runtime tests). No other dependencies.
# The base image's Yarn apt repo has an expired key and breaks apt-get update.
RUN rm -f /etc/apt/sources.list.d/yarn.list \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get update && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*
