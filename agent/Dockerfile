# The agent's sandbox. The agent may do anything in here; its only way to the host
# is the tool server at host.docker.internal.
FROM node:22-bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl python3 python3-venv \
 && rm -rf /var/lib/apt/lists/*

ARG CODEX_VERSION=0.164.0
RUN npm install -g @openai/codex@${CODEX_VERSION}

RUN useradd --create-home agent
USER agent
WORKDIR /home/agent/work
COPY --chown=agent:agent codex-config.toml /home/agent/.codex/config.toml
