# The agent's sandbox, Claude Code edition. The agent may do anything in here; its only way to the
# host is the tool server at host.docker.internal. Counterpart of ../Dockerfile (Codex).
FROM node:22-bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl python3 python3-venv sudo ripgrep \
 && rm -rf /var/lib/apt/lists/*

ARG CLAUDE_CODE_VERSION=2.1.310
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

# The base image's `node` user is uid 1000; use our own non-root user.
RUN useradd --create-home agent \
 && echo "agent ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/agent

COPY claude-settings.json /opt/harness/claude-settings.json
USER agent
WORKDIR /home/agent/work
# User-scope MCP config and onboarding flag. Credentials are NOT here: they come in as
# CLAUDE_CODE_OAUTH_TOKEN (docker run --env-file). Why each setting: docs/research/agent-tools.md
COPY --chown=agent:agent claude.json /home/agent/.claude.json
# MCP tools are always loaded, never deferred behind tool search.
ENV ENABLE_TOOL_SEARCH=false \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    DISABLE_AUTOUPDATER=1 \
    MCP_TIMEOUT=30000 \
    MCP_TOOL_TIMEOUT=300000
# Permissions are bypassed by the runner's flags (--dangerously-skip-permissions); the container is the sandbox.
ENTRYPOINT ["claude"]
