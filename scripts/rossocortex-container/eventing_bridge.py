#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["cloudevents>=1.11,<2", "kafka-python>=2.0", "httpx>=0.27"]
# ///
"""Transparent Event Bridge (Python PoC) — kagenti issues #1274 / #2044 / #2045 / #1460.

Lets an existing agent join an async event backbone with **no code changes**. It is
spawned by rossocortex.py only when KAGENTI_FEATURE_EVENTING=true (off by default),
mirroring the AuthBridge subprocess lifecycle.

Four roles run in one process (a "minimal broker" backed by a local Kafka):

  1. Egress forward proxy  (HTTP  :8090)
     Agent points HTTP_PROXY here. Each intercepted request body is wrapped in a
     CloudEvent (binary content mode, ce-* headers) and POSTed to the broker ingress.
     The agent gets an immediate 202 — the call is now async.

  2. Broker ingress        (HTTP  :8091)
     Accepts binary CloudEvents and produces them to a Kafka topic. This is the
     minimal stand-in for the Knative/Kafka broker ingress.
     Special case: an event of the hardcoded CLAUDE_INPUT_TYPE
     (dev.kagenti.claude.request) is treated as a Claude job — its payload is run
     through `claude -p "<payload>"` and the output is published back to the topic
     as a new CLAUDE_OUTPUT_TYPE (dev.kagenti.claude.response) event.

  3. Trigger dispatcher    (Kafka consumer thread)
     Consumes the topic, optionally filters by ce-type prefix (a "Trigger"), and
     delivers each event's data to the agent's local A2A sink as an HTTP POST. The
     agent has no idea the request arrived via eventing. (Skips CLAUDE_INPUT_TYPE
     events — those are handled by the claude worker below.)

  4. Claude worker         (Kafka consumer thread, separate consumer group)
     Consumes CLAUDE_INPUT_TYPE events straight off the topic and runs them through
     the exact same `claude -p "<payload>"` path as the HTTP broker-ingress branch,
     publishing the CLAUDE_OUTPUT_TYPE result back to the topic. So a claude job can
     be triggered either by POSTing a CloudEvent to the broker ingress OR by
     producing one to the Kafka topic.

Transport is CloudEvents **binary content mode** end to end (ce-* HTTP headers on the
broker-ingress hop, ce_* Kafka headers on the Kafka hop) — per the issue design.

Config (all env vars, with defaults):
    KAGENTI_FEATURE_EVENTING          gate checked by rossocortex.py before spawning us
    KAGENTI_KAFKA_BROKER              Kafka bootstrap servers   (localhost:9092)
    KAGENTI_EVENTING_TOPIC            Kafka topic               (kagenti.events)
    KAGENTI_EVENTING_EGRESS_PORT      egress proxy listen port  (8090)
    KAGENTI_EVENTING_BROKER_PORT      broker ingress port       (8091)
    KAGENTI_EVENTING_BROKER_INGRESS   broker ingress URL        (http://localhost:<BROKER_PORT>)
    KAGENTI_EVENTING_TYPE_PREFIX      ce-type prefix for egress (dev.kagenti.http)
    KAGENTI_EVENTING_TRIGGER_FILTER   only deliver ce-types with this prefix ("" = all)
    K_SINK                            agent A2A endpoint         (http://localhost:8080/a2a)
    CE_SOURCE                         CloudEvent source          (rossocortex/eventing-bridge)
    KAGENTI_EVENTING_CLAUDE_INPUT_TYPE   event type that triggers claude (dev.kagenti.claude.request)
    KAGENTI_EVENTING_CLAUDE_OUTPUT_TYPE  event type for claude output   (dev.kagenti.claude.response)
    KAGENTI_EVENTING_CLAUDE_BIN          claude executable              (claude)
    KAGENTI_EVENTING_CLAUDE_TIMEOUT      claude timeout in seconds      (300)

Local Kafka (single node, KRaft — no ZooKeeper):
    docker run -d --name kafka -p 9092:9092 apache/kafka:3.8.0

Quick smoke test (with the bridge running):
    # egress: wrap an HTTP call into an event -> broker -> Kafka -> delivered to K_SINK
    curl -x http://localhost:8090 -XPOST http://any-host/task -d '{"hello":"world"}'

    # claude job: POST a binary-mode CloudEvent whose payload runs through `claude -p`,
    # with the output re-published to the topic as a dev.kagenti.claude.response event
    curl -XPOST http://localhost:8091 \
      -H 'ce-specversion: 1.0' -H 'ce-type: dev.kagenti.claude.request' \
      -H 'ce-source: demo/client' -H 'ce-id: 1' \
      -H 'Content-Type: text/plain' \
      -d 'Summarize the CloudEvents spec in one sentence.'
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import httpx
from cloudevents.conversion import to_binary
from cloudevents.http import CloudEvent, from_http
from cloudevents.kafka import KafkaMessage
from cloudevents.kafka import from_binary as ce_from_kafka
from cloudevents.kafka import to_binary as ce_to_kafka
from kafka import KafkaConsumer, KafkaProducer

KAFKA_BROKER = os.environ.get("KAGENTI_KAFKA_BROKER", "localhost:9092")
KAFKA_TOPIC = os.environ.get("KAGENTI_EVENTING_TOPIC", "kagenti.events")
EGRESS_PORT = int(os.environ.get("KAGENTI_EVENTING_EGRESS_PORT", "8090"))
BROKER_PORT = int(os.environ.get("KAGENTI_EVENTING_BROKER_PORT", "8091"))
BROKER_INGRESS = os.environ.get("KAGENTI_EVENTING_BROKER_INGRESS", f"http://localhost:{BROKER_PORT}")
TYPE_PREFIX = os.environ.get("KAGENTI_EVENTING_TYPE_PREFIX", "dev.kagenti.http")
TRIGGER_FILTER = os.environ.get("KAGENTI_EVENTING_TRIGGER_FILTER", "")
A2A_SINK = os.environ.get("K_SINK", "http://localhost:8080/a2a")
CE_SOURCE = os.environ.get("CE_SOURCE", "rossocortex/eventing-bridge")

# Claude request handling: a CloudEvent of the (hardcoded) input type carries a
# payload that is run through `claude -p "<payload>"`; the output is published as
# a new event of the output type back to the Kafka topic.
CLAUDE_INPUT_TYPE = os.environ.get("KAGENTI_EVENTING_CLAUDE_INPUT_TYPE", "dev.kagenti.claude.request")
CLAUDE_OUTPUT_TYPE = os.environ.get("KAGENTI_EVENTING_CLAUDE_OUTPUT_TYPE", "dev.kagenti.claude.response")
CLAUDE_BIN = os.environ.get("KAGENTI_EVENTING_CLAUDE_BIN", "claude")
CLAUDE_TIMEOUT = int(os.environ.get("KAGENTI_EVENTING_CLAUDE_TIMEOUT", "300"))

CONSUMER_GROUP = "kagenti-eventing-bridge"
CLAUDE_WORKER_GROUP = "kagenti-eventing-bridge-claude"
_METHODS = ("POST", "PUT", "PATCH", "GET", "DELETE")


def _log(msg: str) -> None:
    sys.stderr.write(f"[eventing-bridge] {msg}\n")
    sys.stderr.flush()


# --------------------------------------------------------------------------- #
# Kafka producer — shared, lazily (re)connected so we survive a late broker.
# --------------------------------------------------------------------------- #
_producer: KafkaProducer | None = None
_producer_lock = threading.Lock()


def _get_producer() -> KafkaProducer | None:
    global _producer
    with _producer_lock:
        if _producer is not None:
            return _producer
        try:
            _producer = KafkaProducer(bootstrap_servers=KAFKA_BROKER, acks="all")
            _log(f"connected producer to Kafka at {KAFKA_BROKER}")
        except Exception as e:  # noqa: BLE001 - broker may not be up yet; naming varies across kafka-python versions
            _log(f"WARNING: cannot reach Kafka at {KAFKA_BROKER} ({e}); producing will fail until it is up")
            _producer = None
        return _producer


def _produce(event: CloudEvent) -> None:
    """Marshal a CloudEvent to a Kafka record (binary mode) and publish it."""
    producer = _get_producer()
    if producer is None:
        raise RuntimeError(f"Kafka broker unavailable at {KAFKA_BROKER}")
    msg: KafkaMessage = ce_to_kafka(event)
    key = msg.key.encode() if isinstance(msg.key, str) else msg.key
    headers = [(k, v if isinstance(v, bytes) else str(v).encode()) for k, v in msg.headers.items()]
    producer.send(KAFKA_TOPIC, value=msg.value, key=key, headers=headers)
    producer.flush()


# --------------------------------------------------------------------------- #
# Claude request handler: on the hardcoded input event type, run `claude -p`
# with the event payload and publish the output as a new event type to Kafka.
# --------------------------------------------------------------------------- #
def _extract_prompt(event: CloudEvent) -> str:
    """Pull the prompt string out of a CloudEvent payload.

    Accepts raw text/bytes, or a JSON object with a
    "payload"/"prompt"/"input"/"text"/"message" field (falling back to the whole
    JSON document if none of those are present)."""
    data = event.data
    if isinstance(data, (bytes, bytearray)):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError:
            return ""
    if isinstance(data, str):
        text = data.strip()
        if text[:1] in ("{", "["):
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return text
        else:
            return text
    if isinstance(data, dict):
        for key in ("payload", "prompt", "input", "text", "message"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val
        return json.dumps(data)
    return str(data) if data is not None else ""


def _run_claude(prompt: str) -> str:
    """Invoke `claude -p "<prompt>"` and return its stdout (raises on failure)."""
    _log(f"invoking {CLAUDE_BIN} -p (prompt {len(prompt)} chars)")
    result = subprocess.run(
        [CLAUDE_BIN, "-p", prompt],
        capture_output=True,
        text=True,
        timeout=CLAUDE_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{CLAUDE_BIN} exited {result.returncode}: {result.stderr.strip()[:500]}")
    return result.stdout


def _handle_claude_request(event: CloudEvent) -> CloudEvent:
    """Run the event payload through Claude and publish the result as a new event."""
    prompt = _extract_prompt(event)
    if not prompt:
        raise ValueError("claude request event carried an empty payload")
    output = _run_claude(prompt)
    response = CloudEvent(
        {
            "type": CLAUDE_OUTPUT_TYPE,
            "source": CE_SOURCE,
            "subject": event.get("subject") or "claude/response",
            "datacontenttype": "text/plain",
            # link the response to its request for audit/causation (issue #1274)
            "causationid": str(event["id"]),
        },
        output,
    )
    _produce(response)
    _log(f"claude output {len(output)}B -> event {CLAUDE_OUTPUT_TYPE} (causationid={event['id']})")
    return response


# --------------------------------------------------------------------------- #
# Role 1 — egress forward proxy: HTTP request -> CloudEvent -> broker ingress.
# --------------------------------------------------------------------------- #
class EgressProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet; we log our own lines
        pass

    def _dispatch(self, method: str) -> None:
        if self.path.endswith("/healthz"):
            self._reply(200, b'{"status":"ok"}')
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""

        # self.path is an absolute URI when the client used us as HTTP_PROXY.
        parsed = urlparse(self.path)
        target_host = parsed.netloc or self.headers.get("Host", "unknown")
        target_path = parsed.path or "/"
        content_type = self.headers.get("Content-Type", "application/json")

        attributes = {
            "type": f"{TYPE_PREFIX}.{method.lower()}",
            "source": CE_SOURCE,
            "subject": f"{target_host}{target_path}",
            "datacontenttype": content_type,
            # ext attrs must be lowercase alnum; carry routing hints for triggers/audit
            "targethost": target_host,
            "httpmethod": method,
        }
        event = CloudEvent(attributes, body)

        try:
            headers, ce_body = to_binary(event)
            resp = httpx.post(BROKER_INGRESS, headers=headers, content=ce_body, timeout=30)
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001 - surface any publish failure to the caller
            _log(f"egress publish failed for {method} {self.path}: {e}")
            self._reply(502, b'{"error":"eventing bridge: publish to broker failed"}')
            return

        _log(f"egress {method} {target_host}{target_path} -> event {event['type']} ({len(body)}B)")
        self._reply(202, b'{"status":"accepted","mode":"eventing"}')

    def _reply(self, status: int, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _bind_methods(handler_cls) -> None:
    """Wire do_POST/do_GET/... to the handler's _dispatch."""
    for m in _METHODS:
        setattr(handler_cls, f"do_{m}", lambda self, _m=m: self._dispatch(_m))


_bind_methods(EgressProxyHandler)


# --------------------------------------------------------------------------- #
# Role 2 — minimal broker ingress: binary CloudEvent -> Kafka topic.
# --------------------------------------------------------------------------- #
class BrokerIngressHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):  # health only
        payload = b'{"status":"ok","topic":"' + KAFKA_TOPIC.encode() + b'"}'
        self._reply(200, payload)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""

        # Parse the incoming binary-mode CloudEvent (ce-* headers + body).
        try:
            event = from_http(dict(self.headers), body)
        except Exception as e:  # noqa: BLE001
            _log(f"broker ingress: invalid CloudEvent: {e}")
            self._reply(400, b'{"error":"broker ingress: invalid CloudEvent"}')
            return

        # Hardcoded input type -> run `claude -p <payload>` and emit a response event.
        if str(event["type"]) == CLAUDE_INPUT_TYPE:
            try:
                _handle_claude_request(event)
            except Exception as e:  # noqa: BLE001
                _log(f"claude request failed: {e}")
                self._reply(500, b'{"error":"broker ingress: claude invocation failed"}')
                return
            payload = b'{"status":"processed","output_type":"' + CLAUDE_OUTPUT_TYPE.encode() + b'"}'
            self._reply(202, payload)
            return

        # Otherwise behave as a plain broker ingress: enqueue the event as-is.
        try:
            _produce(event)
        except Exception as e:  # noqa: BLE001
            _log(f"broker ingress failed: {e}")
            self._reply(500, b'{"error":"broker ingress: could not enqueue event"}')
            return
        _log(f"broker enqueued event {event['type']} -> topic {KAFKA_TOPIC}")
        self._reply(202, b'{"status":"enqueued"}')

    def _reply(self, status: int, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


# --------------------------------------------------------------------------- #
# Kafka consumers — a shared poll loop drives two independent consumer groups:
#   Role 3  — trigger dispatcher: deliver events to the agent A2A sink over HTTP.
#   Role 4  — claude worker: run CLAUDE_INPUT_TYPE events pulled from the topic
#             through `claude -p` (identical logic to the HTTP broker-ingress
#             path) and publish the CLAUDE_OUTPUT_TYPE result back to the topic.
# --------------------------------------------------------------------------- #
_stop = threading.Event()


def _record_to_event(record) -> CloudEvent:
    """Reconstruct a CloudEvent from a Kafka record (binary mode)."""
    headers = {k: v for k, v in (record.headers or [])}
    key = record.key.decode() if record.key else None
    return ce_from_kafka(KafkaMessage(headers=headers, key=key, value=record.value))


def _consume_loop(name: str, group_id: str, handler) -> None:
    """Poll KAFKA_TOPIC under `group_id`, passing each record to `handler`.

    Survives a late/absent broker by retrying the subscription. consumer_timeout_ms
    makes the iterator yield periodically so we can observe the stop signal."""
    consumer: KafkaConsumer | None = None
    while not _stop.is_set():
        if consumer is None:
            try:
                consumer = KafkaConsumer(
                    KAFKA_TOPIC,
                    bootstrap_servers=KAFKA_BROKER,
                    group_id=group_id,
                    auto_offset_reset="latest",
                    enable_auto_commit=True,
                    consumer_timeout_ms=1000,
                )
                _log(f"{name} subscribed to {KAFKA_TOPIC} (group={group_id})")
            except Exception as e:  # noqa: BLE001 - broker may not be up yet
                _log(f"{name}: cannot reach Kafka at {KAFKA_BROKER} ({e}), retrying in 3s")
                _stop.wait(3)
                continue

        try:
            for record in consumer:  # yields at most until consumer_timeout_ms with no data
                if _stop.is_set():
                    break
                handler(record)
        except Exception as e:  # noqa: BLE001 - keep the loop alive across transient errors
            _log(f"{name} error: {e}")
            _stop.wait(1)

    if consumer is not None:
        consumer.close()


def _dispatcher_loop() -> None:
    _log(f"dispatcher filter={TRIGGER_FILTER or '*'} -> sink {A2A_SINK}")
    _consume_loop("dispatcher", CONSUMER_GROUP, _deliver)


def _claude_worker_loop() -> None:
    _log(f"claude worker handling type={CLAUDE_INPUT_TYPE}")
    _consume_loop("claude-worker", CLAUDE_WORKER_GROUP, _process_claude_record)


def _process_claude_record(record) -> None:
    """Kafka path for claude jobs: same logic as the HTTP broker-ingress branch."""
    try:
        event = _record_to_event(record)
    except Exception as e:  # noqa: BLE001
        _log(f"claude worker: undecodable record dropped: {e}")
        return
    if str(event["type"]) != CLAUDE_INPUT_TYPE:
        return  # only claude requests are ours; everything else is ignored here
    _log(f"claude worker: consumed {CLAUDE_INPUT_TYPE} (id={event.get('id')}) from {KAFKA_TOPIC}")
    try:
        _handle_claude_request(event)
    except Exception as e:  # noqa: BLE001
        _log(f"claude worker: claude invocation failed for {event.get('id')}: {e}")


def _deliver(record) -> None:
    try:
        event = _record_to_event(record)
    except Exception as e:  # noqa: BLE001
        _log(f"dispatcher: undecodable record dropped: {e}")
        return

    ce_type = str(event["type"])
    if ce_type == CLAUDE_INPUT_TYPE:
        return  # claude work-requests are handled by the claude worker, not delivered
    if TRIGGER_FILTER and not ce_type.startswith(TRIGGER_FILTER):
        return  # this Trigger does not match

    data = event.data
    if isinstance(data, str):
        content = data.encode()
    elif isinstance(data, (bytes, bytearray)):
        content = bytes(data)
    elif data is None:
        content = b""
    else:  # dict/list -> JSON
        content = json.dumps(data).encode()

    out_headers = {
        "Content-Type": event.get("datacontenttype") or "application/json",
        "Ce-Type": ce_type,
        "Ce-Source": str(event["source"]),
        "Ce-Id": str(event["id"]),
    }
    try:
        resp = httpx.post(A2A_SINK, content=content, headers=out_headers, timeout=120)
        _log(f"delivered event {ce_type} -> {A2A_SINK} [{resp.status_code}]")
    except Exception as e:  # noqa: BLE001
        _log(f"delivery to {A2A_SINK} failed for {ce_type}: {e}")


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
def main() -> None:
    _log("Transparent Event Bridge starting")
    _log(f"  egress proxy    : http://0.0.0.0:{EGRESS_PORT}  (set HTTP_PROXY here)")
    _log(f"  broker ingress  : http://0.0.0.0:{BROKER_PORT}")
    _log(f"  kafka broker    : {KAFKA_BROKER}  topic={KAFKA_TOPIC}")
    _log(f"  a2a sink        : {A2A_SINK}")
    _log(f"  claude worker   : type={CLAUDE_INPUT_TYPE} -> `{CLAUDE_BIN} -p` -> {CLAUDE_OUTPUT_TYPE}")

    broker_server = ThreadingHTTPServer(("0.0.0.0", BROKER_PORT), BrokerIngressHandler)
    egress_server = ThreadingHTTPServer(("0.0.0.0", EGRESS_PORT), EgressProxyHandler)

    threads = [
        threading.Thread(target=broker_server.serve_forever, daemon=True, name="broker-ingress"),
        threading.Thread(target=_dispatcher_loop, daemon=True, name="dispatcher"),
        threading.Thread(target=_claude_worker_loop, daemon=True, name="claude-worker"),
    ]
    for t in threads:
        t.start()

    def _shutdown(*_):
        _log("shutting down")
        _stop.set()
        broker_server.shutdown()
        egress_server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        egress_server.serve_forever()  # blocks in the main thread
    except KeyboardInterrupt:
        _shutdown()

    if _producer is not None:
        _producer.flush()
        _producer.close()
    time.sleep(0.2)


if __name__ == "__main__":
    main()
