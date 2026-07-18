#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["cloudevents>=1.11,<2", "kafka-python>=2.0", "httpx>=0.27"]
# ///
"""Smoke test for the transparent eventing bridge (scripts/rossocortex-container/eventing_bridge.py).

What it does:
  1. Checks a local Kafka is reachable; if not, prints the docker command and waits
     until it is ready.
  2. Checks the eventing bridge is running (egress + broker ingress health); if not,
     prints the start command and waits until it is ready.
  3. Runs a Python test equivalent to:
         curl -x http://localhost:8090 -XPOST http://any-host/task -d '{"hello":"world"}'
     i.e. sends an HTTP request THROUGH the egress forward proxy, then verifies the
     wrapped CloudEvent actually landed on the Kafka topic.
  4. Prints cleanup commands.

Run:
    ./scripts/test-eventing-bridge.py
"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx
from cloudevents.kafka import KafkaMessage
from cloudevents.kafka import from_binary as ce_from_kafka
from kafka import KafkaConsumer, KafkaProducer

KAFKA_BROKER = os.environ.get("KAGENTI_KAFKA_BROKER", "localhost:9092")
KAFKA_TOPIC = os.environ.get("KAGENTI_EVENTING_TOPIC", "kagenti.events")
EGRESS_PORT = int(os.environ.get("KAGENTI_EVENTING_EGRESS_PORT", "8090"))
BROKER_PORT = int(os.environ.get("KAGENTI_EVENTING_BROKER_PORT", "8091"))
EGRESS_URL = f"http://localhost:{EGRESS_PORT}"
BROKER_URL = f"http://localhost:{BROKER_PORT}"

KAFKA_START_CMD = "docker run -d --name kafka -p 9092:9092 apache/kafka:3.8.0"
BRIDGE_START_CMD = "KAGENTI_FEATURE_EVENTING=true ./scripts/rossocortex-container/eventing_bridge.py &"

READY_TIMEOUT = int(os.environ.get("TEST_READY_TIMEOUT", "90"))


# --------------------------------------------------------------------------- #
# Pretty output helpers
# --------------------------------------------------------------------------- #
def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def ok(msg: str) -> None:
    print(_c("32", "[ OK ]"), msg)


def info(msg: str) -> None:
    print(_c("36", "[INFO]"), msg)


def fail(msg: str) -> None:
    print(_c("31", "[FAIL]"), msg)


# --------------------------------------------------------------------------- #
# Readiness checks
# --------------------------------------------------------------------------- #
def _port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    import socket
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def kafka_ready() -> bool:
    host, _, port = KAFKA_BROKER.partition(":")
    # Fast negative: if nothing is listening, Kafka clearly isn't up yet.
    if not _port_open(host or "localhost", int(port or "9092")):
        return False
    # Confirm the port actually speaks Kafka by doing a metadata fetch.
    try:
        c = KafkaConsumer(bootstrap_servers=KAFKA_BROKER, request_timeout_ms=3000, consumer_timeout_ms=1000)
        topics = c.topics()  # returns a (possibly empty) set if the broker is reachable
        c.close()
        return topics is not None
    except Exception:
        return False


def bridge_ready() -> bool:
    try:
        e = httpx.get(f"{EGRESS_URL}/healthz", timeout=2)
        b = httpx.get(f"{BROKER_URL}/", timeout=2)
        return e.status_code == 200 and b.status_code == 200
    except Exception:
        return False


def wait_until(check, what: str, start_cmd: str) -> bool:
    """Return True once `check()` passes; otherwise print how to start it and poll."""
    if check():
        ok(f"{what} is ready")
        return True
    fail(f"{what} is not running.")
    print(f"       Start it with:\n\n           {start_cmd}\n")
    info(f"waiting up to {READY_TIMEOUT}s for {what} to become ready...")
    deadline = time.time() + READY_TIMEOUT
    while time.time() < deadline:
        time.sleep(2)
        if check():
            ok(f"{what} is ready")
            return True
        print("       .", end="", flush=True)
    print()
    fail(f"{what} still not ready after {READY_TIMEOUT}s. Aborting.")
    return False


# --------------------------------------------------------------------------- #
# Test: egress proxy -> CloudEvent -> broker -> Kafka topic
# --------------------------------------------------------------------------- #
def test_egress_to_kafka() -> bool:
    # Unique marker so we can find OUR event among any existing topic records.
    nonce = f"test-{os.getpid()}-{int(time.time())}"
    payload = {"hello": "world", "nonce": nonce}

    # Subscribe first (fresh group, from earliest) so we cannot miss the record.
    consumer = KafkaConsumer(
        KAFKA_TOPIC,
        bootstrap_servers=KAFKA_BROKER,
        group_id=f"eventing-smoketest-{nonce}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        consumer_timeout_ms=1000,
    )

    # Equivalent to: curl -x http://localhost:8090 -XPOST http://any-host/task -d '{"hello":"world"}'
    info(f"POST http://any-host/task through egress proxy {EGRESS_URL} (nonce={nonce})")
    with httpx.Client(proxy=EGRESS_URL, timeout=15) as client:
        resp = client.post(
            "http://any-host/task",
            content=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )

    if resp.status_code != 202:
        fail(f"egress proxy returned {resp.status_code} (expected 202): {resp.text}")
        consumer.close()
        return False
    ok(f"egress proxy accepted the request -> 202 {resp.text.strip()}")

    # Verify the wrapped CloudEvent reached the Kafka topic.
    info(f"consuming from Kafka topic '{KAFKA_TOPIC}' to confirm the event landed...")
    deadline = time.time() + 20
    found = None
    while time.time() < deadline and found is None:
        for record in consumer:
            try:
                event = ce_from_kafka(
                    KafkaMessage(
                        headers={k: v for k, v in (record.headers or [])},
                        key=record.key.decode() if record.key else None,
                        value=record.value,
                    )
                )
            except Exception:
                continue
            data = event.data
            if isinstance(data, (bytes, bytearray)):
                try:
                    data = json.loads(data)
                except Exception:
                    data = {}
            if isinstance(data, dict) and data.get("nonce") == nonce:
                found = event
                break
    consumer.close()

    if found is None:
        fail(f"event with nonce {nonce} never appeared on topic '{KAFKA_TOPIC}'")
        return False

    ce_type = str(found["type"])
    ok(f"event found on topic: type={ce_type} source={found['source']} subject={found.get('subject')}")
    if ce_type != "dev.kagenti.http.post":
        fail(f"unexpected event type: {ce_type} (expected dev.kagenti.http.post)")
        return False
    ok("event type is correct (dev.kagenti.http.post) — egress → broker → Kafka path works")
    return True


# --------------------------------------------------------------------------- #
def cleanup_hint() -> None:
    print()
    print(_c("1", "Cleanup:"))
    print("    pkill -f eventing_bridge.py        # stop the eventing bridge")
    print("    docker rm -f kafka                 # stop and remove the local Kafka container")


def main() -> int:
    print(_c("1", "== Eventing bridge smoke test ==\n"))

    if not wait_until(kafka_ready, "Kafka", KAFKA_START_CMD):
        cleanup_hint()
        return 1
    if not wait_until(bridge_ready, "Eventing bridge", BRIDGE_START_CMD):
        cleanup_hint()
        return 1

    print()
    passed = test_egress_to_kafka()
    print()
    if passed:
        ok("ALL TESTS PASSED")
    else:
        fail("TESTS FAILED")
    cleanup_hint()
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
