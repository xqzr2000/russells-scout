# syntax=docker/dockerfile:1
#
# One Dockerfile, several stages:
#   base  - Python + Chromium + crawl4ai. Shared by every environment.
#   dev   - base + git, sudo, a "vscode" user and test tools. Used by the devcontainer
#           (Codespaces and local VS Code).
#   runtime - slim Python + Postgres driver + our code, no browser. The `scout` service
#             on the box (compose profile "scout"): `scout db ...` now, the WorkBC
#             sweep next. Built separately from base, so it stays small.
#
# Same base image family as upstream crawl4ai's own Dockerfile (Debian bookworm, Python 3.12).

FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Put the virtualenv first on PATH so `python`, `pip`, `scout` all use it.
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    # Install Playwright's browser in a shared folder (not root's home) so a
    # non-root user can launch it.
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN python -m venv /opt/venv

# Install third-party dependencies first, from the version pins in requirements.txt.
# This layer is cached until requirements.txt changes, so rebuilds are fast.
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# Chromium + the Debian system libraries it needs (fonts, NSS, GTK bits, ...).
# We do NOT run `crawl4ai-setup`: it also downloads Patchright browsers
# (undetected mode) that this project doesn't use.
RUN python -m playwright install --with-deps chromium \
    && chmod -R a+rx /ms-playwright


FROM base AS dev

ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=$USER_UID

RUN apt-get update \
    && apt-get install -y --no-install-recommends git sudo curl ca-certificates openssh-client less \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID -m -s /bin/bash $USERNAME \
    && echo "$USERNAME ALL=(root) NOPASSWD:ALL" > /etc/sudoers.d/$USERNAME \
    && chmod 0440 /etc/sudoers.d/$USERNAME

COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install -r /tmp/requirements-dev.txt \
    # Let the dev user `pip install -e .` (and anything else) into the venv.
    && chown -R $USERNAME:$USERNAME /opt/venv

USER $USERNAME


FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    SCOUT_MIGRATIONS_DIR=/app/migrations

RUN python -m venv /opt/venv

COPY requirements-runtime.txt /tmp/requirements-runtime.txt
RUN pip install -r /tmp/requirements-runtime.txt

# Our package without its browser dependencies (crawl4ai stays out of this image).
COPY pyproject.toml /app/
COPY src /app/src
COPY migrations /app/migrations
RUN pip install --no-deps /app \
    && useradd --uid 10002 --no-create-home --shell /usr/sbin/nologin scout

USER 10002
WORKDIR /app
ENTRYPOINT ["scout"]
CMD ["--help"]
