# Transparent Event Bridge (Python PoC)

A proof-of-concept that lets an existing Kagenti agent join an **asynchronous, Kafka-backed
event backbone with zero agent code changes**. It converts an agent's HTTP traffic to and
from [CloudEvents](https://cloudevents.io/) and can run Claude jobs off the event stream.

This implements the design from
[#1274 (Transparent Event Bridge Proxy)](https://github.com/kagenti/kagenti/issues/1274),
[#2044 (PoC)](https://github.com/kagenti/kagenti/issues/2044),
[#2045 (AuthBridge convergence)](https://github.com/kagenti/kagenti/issues/2045), and
[#1460 (identity for event-driven agents)](https://github.com/kagenti/kagenti/issues/1460)
— in **Python**, with a **minimal broker** on top of a **local Kafka** and **binary
CloudEvents transport**.

Files:

| File | Role |
|------|------|
| `rossocortex-container/rossocortex.py` | Budget/Auth proxy; **spawns** the bridge when the feature flag is on |
| `rossocortex-container/eventing_bridge.py` | The event bridge itself (egress proxy, broker, dispatcher, Claude worker) |
| `test-eventing-bridge.py` | End-to-end smoke test |

---

## High-level architecture

The bridge runs **four roles in one process**, all sharing one local Kafka topic
(`kagenti.events` by default):

```
                 ┌──────────────────────── eventing_bridge.py ─────────────────────────┐
                 │                                                                       │
  agent HTTP     │  (1) Egress proxy          (2) Broker ingress                        │
  (HTTP_PROXY) ──┼─►  :8090  ──wrap as──►      :8091  ──produce──►┐                      │
                 │      CloudEvent (binary)      (binary CE in)    │                     │
                 │                                                 ▼                     │
                 │                                        ┌──────────────────┐          │
                 │                                        │   Kafka topic    │  ◄─── durable, async
                 │                                        │  kagenti.events  │       buffer
                 │                                        └──────────────────┘          │
                 │                                            │            │             │
                 │           (3) Trigger dispatcher ◄─────────┘            └──────► (4) Claude worker
                 │               consume + filter                              consume type ==
                 │               deliver to K_SINK (HTTP POST)                 dev.kagenti.claude.request
                 │                    │                                             │  run `claude -p`
                 │                    ▼                                             ▼  publish result
                 │              agent A2A endpoint                          new dev.kagenti.claude.response
                 │              http://localhost:8080/a2a                   event back to the topic
                 └───────────────────────────────────────────────────────────────────┘
```

1. **Egress forward proxy** (`:8090`) — the agent points `HTTP_PROXY` here. Each intercepted
   request body is wrapped in a **binary-mode CloudEvent** and POSTed to the broker ingress.
   The agent gets an immediate `202` — the call is now asynchronous.
2. **Broker ingress** (`:8091`) — accepts binary CloudEvents over HTTP and **produces** them
   to the Kafka topic. This is the minimal stand-in for a Knative/Kafka broker ingress.
   Special case: an event of type `dev.kagenti.claude.request` is treated as a Claude job
   (see below).
3. **Trigger dispatcher** — a Kafka consumer that reads the topic, optionally filters by a
   `ce-type` prefix (a "Trigger"), and delivers each event's payload to the agent's local
   A2A endpoint (`K_SINK`) as a plain HTTP POST. The agent has no idea the request arrived
   via eventing.
4. **Claude worker** — a second Kafka consumer (separate consumer group) that picks up
   `dev.kagenti.claude.request` events and runs them through `claude -p`, publishing the
   output as a `dev.kagenti.claude.response` event back to the topic.

---

## Feature flag — enabling eventing in rossocortex

The bridge is **off by default** and gated behind a single environment variable, matching the
repo's "feature flags, disabled by default" rule and the design in #1460/#2045:

```
KAGENTI_FEATURE_EVENTING=true
```

`rossocortex.py` reads it once at startup:

```python
EVENTING_ENABLED = os.environ.get("KAGENTI_FEATURE_EVENTING", "").lower() == "true"
```

When **true**, `rossocortex.py` spawns `eventing_bridge.py` as a subprocess (mirroring how it
already spawns the AuthBridge proxy) and tears it down on exit. When **unset/false**,
rossocortex behaves exactly as before — no eventing code paths run, no ports bound, no Kafka
connections. Because `rossoctlx.py` launches rossocortex with an inherited environment, you
enable eventing simply by exporting the flag before starting:

```bash
KAGENTI_FEATURE_EVENTING=true rossoctlx start --upstream https://your-litellm...
# or run the proxy directly:
KAGENTI_FEATURE_EVENTING=true ./scripts/rossocortex-container/rossocortex.py --upstream ...
```

---

## How CloudEvents are used

- **Binary content mode** everywhere. On the HTTP hops, CloudEvent attributes travel as
  `ce-*` headers (`ce-specversion`, `ce-type`, `ce-source`, `ce-id`, `ce-subject`, …) with the
  original payload as the HTTP body. On the Kafka hop, the same attributes travel as Kafka
  record headers. This is the efficient, Kafka-friendly mode the issues call for.
- **SDK**: the official [`cloudevents`](https://pypi.org/project/cloudevents/) Python SDK
  (pinned `>=1.11,<2`). Egress uses `cloudevents.conversion.to_binary`; broker ingress parses
  incoming events with `cloudevents.http.from_http`; the Kafka hop uses
  `cloudevents.kafka.to_binary` / `from_binary`.
- **Attributes set on egress**: `type = dev.kagenti.http.<method>` (e.g. `dev.kagenti.http.post`),
  `source = rossocortex/eventing-bridge`, `subject = <host><path>`, plus `targethost` and
  `httpmethod` extension attributes for routing/audit.
- **Causation**: a Claude response event carries `causationid` = the request event's `id`, so
  request→response can be correlated for audit (per the #1274 design). This is also where a
  SPIFFE-derived `source` would slot in for the identity work in #1460.

---

## The async part — events wait in Kafka until the bridge is ready

The whole point of routing through Kafka (rather than a direct HTTP call) is **temporal
decoupling**:

- The egress proxy returns `202` immediately after the event is enqueued at the broker
  ingress — the agent does **not** hold a connection open waiting for the result. A
  long-running job (Claude, research, etc.) can take minutes with no held socket.
- Kafka **durably retains** every event on the topic. If the consumer side (dispatcher or
  Claude worker) is slow, restarting, or temporarily down, events **accumulate on the topic**
  and are processed once the consumer is back. Nothing is lost mid-flight.
- Both consumers use **consumer groups** (`kagenti-eventing-bridge` for the dispatcher,
  `kagenti-eventing-bridge-claude` for the Claude worker). Kafka tracks each group's committed
  offset, so a restarted consumer resumes from where it left off — pending events "wait in the
  topic" until it is ready to process them.
- The bridge's consumer loops also **survive a late or absent broker**: if Kafka isn't up yet,
  they log and retry the subscription every few seconds instead of crashing.

> First-run note: consumers start with `auto_offset_reset="latest"`, so the *very first* time a
> group subscribes it reads only events produced *after* that point. Once the group exists,
> its committed offset is what guarantees "produce now, process later." For a demo where you
> want to replay everything already on the topic, set the consumer group to read from
> `earliest` (that's what the test does with a throwaway group).

Because a Claude job can be triggered **either** by POSTing a CloudEvent to the broker ingress
**or** by producing one to the Kafka topic, you can queue work onto the topic while the bridge
is stopped and it will be picked up and run by the Claude worker when the bridge starts.

---

## Running it

### 1. Start a local Kafka (single node, KRaft — no ZooKeeper)

```bash
docker run -d --name kafka -p 9092:9092 apache/kafka:3.8.0
```

### 2. Start the bridge

Standalone (for local hacking / the test):

```bash
KAGENTI_FEATURE_EVENTING=true ./scripts/rossocortex-container/eventing_bridge.py &
```

or via rossocortex (spawned automatically when the flag is on):

```bash
KAGENTI_FEATURE_EVENTING=true ./scripts/rossocortex-container/rossocortex.py --upstream ...
```

The scripts are PEP-723 `uv` scripts — `uv` fetches `cloudevents`, `kafka-python`, and `httpx`
on first run; no virtualenv setup needed.

### 3. Exercise it

Send an HTTP call *through the egress proxy* — it becomes a CloudEvent on the topic:

```bash
curl -x http://localhost:8090 -XPOST http://any-host/task -d '{"hello":"world"}'
```

Run a Claude job by POSTing a binary CloudEvent to the broker ingress (output is published
back to the topic as `dev.kagenti.claude.response`):

```bash
curl -XPOST http://localhost:8091 \
  -H 'ce-specversion: 1.0' -H 'ce-type: dev.kagenti.claude.request' \
  -H 'ce-source: demo/client' -H 'ce-id: 1' \
  -H 'Content-Type: text/plain' \
  -d 'Summarize the CloudEvents spec in one sentence.'
```

---

## Configuration

All configurable via environment variables (defaults shown):

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAGENTI_FEATURE_EVENTING` | *(off)* | Gate checked by `rossocortex.py` before spawning the bridge |
| `KAGENTI_KAFKA_BROKER` | `localhost:9092` | Kafka bootstrap servers |
| `KAGENTI_EVENTING_TOPIC` | `kagenti.events` | Kafka topic |
| `KAGENTI_EVENTING_EGRESS_PORT` | `8090` | Egress forward-proxy port (`HTTP_PROXY`) |
| `KAGENTI_EVENTING_BROKER_PORT` | `8091` | Broker ingress port |
| `KAGENTI_EVENTING_BROKER_INGRESS` | `http://localhost:8091` | Where egress publishes events |
| `KAGENTI_EVENTING_TYPE_PREFIX` | `dev.kagenti.http` | `ce-type` prefix for egress events |
| `KAGENTI_EVENTING_TRIGGER_FILTER` | `""` (all) | Dispatcher only delivers `ce-type`s with this prefix |
| `K_SINK` | `http://localhost:8080/a2a` | Agent A2A endpoint for ingress delivery |
| `CE_SOURCE` | `rossocortex/eventing-bridge` | CloudEvent `source` |
| `KAGENTI_EVENTING_CLAUDE_INPUT_TYPE` | `dev.kagenti.claude.request` | Event type that triggers a Claude job |
| `KAGENTI_EVENTING_CLAUDE_OUTPUT_TYPE` | `dev.kagenti.claude.response` | Event type for the Claude output |
| `KAGENTI_EVENTING_CLAUDE_BIN` | `claude` | Claude executable |
| `KAGENTI_EVENTING_CLAUDE_TIMEOUT` | `300` | Claude timeout (seconds) |

---

## Testing — `test-eventing-bridge.py`

A self-contained `uv` smoke test that verifies the egress → broker → Kafka path end to end.
Run it after Kafka and the bridge are up:

```bash
./scripts/test-eventing-bridge.py
```

What it does, step by step:

1. **Waits for Kafka.** Does a fast TCP probe of `localhost:9092`, then confirms the port
   actually speaks Kafka with a metadata (`topics()`) fetch. If Kafka is down it prints the
   start command (`docker run -d --name kafka -p 9092:9092 apache/kafka:3.8.0`) and polls until
   it becomes ready (up to `TEST_READY_TIMEOUT`, default 90s).
2. **Waits for the bridge.** Hits the egress `:/healthz` and broker `:/` health endpoints. If
   down, prints the start command
   (`KAGENTI_FEATURE_EVENTING=true ./scripts/rossocortex-container/eventing_bridge.py &`) and
   polls until ready.
3. **Runs the test** — the Python equivalent of
   `curl -x http://localhost:8090 -XPOST http://any-host/task -d '{"hello":"world"}'`:
   - Subscribes a throwaway Kafka consumer group (from `earliest`) so it can't miss the event.
   - Sends the POST **through the egress proxy** using `httpx(proxy=...)`, tagging the payload
     with a unique nonce.
   - Asserts the egress proxy returns `202 accepted`.
   - **Consumes from the topic** and confirms the wrapped CloudEvent arrived — matching on the
     nonce and asserting `ce-type == dev.kagenti.http.post`. This proves the full
     egress → CloudEvent → broker → Kafka pipeline works, not just that the HTTP call returned.
4. **Prints cleanup commands.**

Ports and broker are overridable via the same env vars (e.g.
`KAGENTI_EVENTING_EGRESS_PORT`, `KAGENTI_EVENTING_BROKER_PORT`, `KAGENTI_KAFKA_BROKER`).

---

## Cleanup

```bash
pkill -f eventing_bridge.py        # stop the eventing bridge
docker rm -f kafka                 # stop and remove the local Kafka container
```
