# NemoClaw OpenClaw Agent — pinned build from upstream source
#
# Installs OpenClaw CLI from npm registry.
# Pinned to NemoClaw v0.0.28 / OpenClaw v2026.4.9.
#
# Based on: github.com/NVIDIA/NemoClaw/Dockerfile.base @ v0.0.28
# Simplified: no NemoClaw plugin, no blueprint, no exec-approvals patch.
# The agent runs standalone with its gateway API on port 18789.

FROM node:22-slim

ARG OPENCLAW_VERSION=2026.4.9

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        curl \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# OpenClaw expects ~/.openclaw — create under /home/node for the node user
RUN mkdir -p /home/node/.openclaw /home/node/.openclaw-data \
    && chown -R node:node /home/node

# hadolint ignore=DL3016
RUN npm install -g "openclaw@${OPENCLAW_VERSION}" \
    && openclaw --version

USER node
WORKDIR /home/node

EXPOSE 18789
CMD ["openclaw", "gateway", "run", "--port", "18789", "--allow-unconfigured"]
