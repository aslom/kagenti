# NemoClaw Hermes Agent — pinned build from upstream source
#
# Installs Hermes Agent (Nous Research) from GitHub release tarball.
# Pinned to NemoClaw v0.0.28 / Hermes v2026.4.13.
#
# Based on: github.com/NVIDIA/NemoClaw/agents/hermes/Dockerfile.base @ v0.0.28
# Simplified: no NemoClaw plugin, no blueprint, no OpenShell supervisor.
# The agent runs standalone with an OpenAI-compatible API on port 8642.

FROM python:3.12-slim

ARG HERMES_VERSION=v2026.4.13

# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r agent && useradd -r -g agent -d /sandbox -s /bin/bash agent \
    && mkdir -p /sandbox/.hermes /sandbox/.hermes-data/memories \
        /sandbox/.hermes-data/sessions /sandbox/.hermes-data/skills \
        /sandbox/.hermes-data/plugins /sandbox/.hermes-data/workspace \
        /sandbox/.hermes-data/logs /sandbox/.hermes-data/cache \
    && chown -R agent:agent /sandbox

# hadolint ignore=DL3013
RUN pip install --no-cache-dir \
        "hermes-agent @ https://github.com/NousResearch/hermes-agent/archive/refs/tags/${HERMES_VERSION}.tar.gz" \
        "pyyaml==6.0.3" \
    && hermes --version

USER agent
ENV HOME=/sandbox
WORKDIR /sandbox

EXPOSE 8642
ENV HERMES_PORT=8642
CMD ["hermes", "gateway", "run"]
