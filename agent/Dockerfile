# syntax=docker/dockerfile:1
#
# Project Scout agent image: Claude Code (headless) talking to DeepSeek's
# Anthropic-compatible API. See docs/architecture-plan.md §5.
#
# Hardening lives in compose.yaml (read-only root, no capabilities, tmpfs home,
# no Docker socket). This image only provides the tools and a non-root user.

FROM node:22-bookworm-slim

# Pinned on purpose: no self-updating, upgrades are deliberate (plan §5.1).
ARG CLAUDE_CODE_VERSION=2.1.310
ARG AGENT_UID=10001
ARG AGENT_GID=10001

# Tools the agent needs to write, test and commit scouting code.
# jq is also used by the locked PreToolUse guard hook.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git jq ripgrep procps less \
        python3 python3-venv python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force \
    && claude --version

# Non-root agent user. No sudo is installed, so there is nothing to escalate with.
RUN groupadd --gid "${AGENT_GID}" agent \
    && useradd --uid "${AGENT_UID}" --gid "${AGENT_GID}" --home-dir /home/agent \
        --create-home --shell /bin/bash agent \
    && mkdir -p /workspace \
    && chown "${AGENT_UID}:${AGENT_GID}" /workspace

ENV HOME=/home/agent \
    DISABLE_AUTOUPDATER=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

USER ${AGENT_UID}:${AGENT_GID}
WORKDIR /workspace

# Sessions are started explicitly (agent/run-session.sh); nothing runs by default.
CMD ["claude", "--version"]
