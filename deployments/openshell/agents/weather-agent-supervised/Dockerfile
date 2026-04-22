# Weather agent running inside OpenShell supervisor
# Supervisor applies Landlock + seccomp, then execs the weather agent
FROM ghcr.io/nvidia/openshell/supervisor:latest AS supervisor

FROM ghcr.io/kagenti/agent-examples/weather_service:latest

# Copy supervisor binary from supervisor image
COPY --from=supervisor /usr/local/bin/openshell-sandbox /usr/local/bin/openshell-sandbox

# Create sandbox user and install agent deps into system python
USER root
RUN apt-get update -qq && apt-get install -y -qq iproute2 >/dev/null 2>&1 || true && \
    groupadd -f sandbox && \
    id sandbox >/dev/null 2>&1 || useradd -r -g sandbox -d /home/sandbox -m -s /bin/bash sandbox && \
    mkdir -p /home/sandbox /.cache /tmp /var/log && \
    chown -R sandbox:sandbox /home/sandbox /.cache /tmp && \
    /app/.venv/bin/pip install --target /usr/local/lib/python3.13/site-packages \
        uvicorn httpx a2a-sdk starlette opentelemetry-api opentelemetry-sdk \
        opentelemetry-exporter-otlp-proto-http opentelemetry-instrumentation-httpx \
        langchain-core langgraph 2>/dev/null || \
    pip install --break-system-packages \
        uvicorn httpx a2a-sdk starlette opentelemetry-api opentelemetry-sdk \
        opentelemetry-exporter-otlp-proto-http opentelemetry-instrumentation-httpx \
        langchain-core langgraph 2>/dev/null || true

# Copy the agent entry point for system python
RUN cp -r /app/src/weather_service /usr/local/lib/python3.*/dist-packages/ 2>/dev/null || \
    cp -r /app/src/weather_service /usr/lib/python3/dist-packages/ 2>/dev/null || true

# Supervisor is the entrypoint — it applies isolation then execs the agent
ENTRYPOINT ["/usr/local/bin/openshell-sandbox"]
