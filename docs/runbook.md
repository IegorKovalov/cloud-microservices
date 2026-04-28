# Runbook

Practical, copy-pasteable steps for running, exercising, and extending the
framework.

## 1. Prerequisites

- Docker Engine 24+ with the `docker compose` plugin
- Python 3.11+ (only required for host-side scripts and tests)
- `make`, `curl`, `jq` (optional but pleasant)

Clone the repository and copy the example env file:

```bash
git clone https://github.com/IegorKovalov/cloud-microservices.git
cd cloud-microservices
cp .env.example .env       # already provided in the repo; safe to overwrite
```

## 2. Bring the stack up

```bash
make up
make ps        # confirm all five containers are healthy
make health    # one-shot health probe against every /health endpoint
```

Tear it down with `make down`. To rebuild from a clean slate:

```bash
make down
make rebuild   # docker compose build --no-cache
make up
```

## 3. Run the test suites

Set up the host-side venv once:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Then:

```bash
make test-unit       # pure-python tests, no docker required
make test-int        # end-to-end tests; requires `make up`
make test            # both
```

Unit tests live in `tests/unit/` and cover `shared/`, `orchestration/`,
and `monitoring/`. Integration tests in `tests/integration/` automatically
**skip** when the api-gateway is not reachable on `localhost:8000`, so a
fresh checkout's `pytest tests/` always succeeds.

## 4. Hit the API directly

```bash
# Pipeline (default flavour: asyncio.gather fan-out)
curl -sS -X POST http://localhost:8000/process \
  -H 'content-type: application/json' \
  -d '{"items":[1,2,3,4,5],"operation":"square_sum"}' | jq

# Same input via the asyncio.Queue worker pool
curl -sS -X POST http://localhost:8000/process/queue \
  -H 'content-type: application/json' \
  -d '{"items":[1,2,3,4,5],"operation":"sum"}' | jq

# Same input via the ThreadPoolExecutor variant
curl -sS -X POST http://localhost:8000/process/threadpool \
  -H 'content-type: application/json' \
  -d '{"items":[1,2,3,4,5],"operation":"sum"}' | jq

# Aggregated /system/health from the gateway
curl -sS http://localhost:8000/system/health | jq

# Direct cpp-worker
curl -sS -X POST http://localhost:8003/square_sum \
  -H 'content-type: application/json' \
  -d '{"items":[1,2,3,4,5]}' | jq

# Direct service-b storage
curl -sS -X POST http://localhost:8002/store \
  -H 'content-type: application/json' \
  -d '{"key":"alpha","value":3.14}' | jq
curl -sS http://localhost:8002/store | jq
```

## 5. Inject faults and observe recovery

In one terminal:

```bash
source .venv/bin/activate
python -m orchestration.recovery
# Logs every health probe, escalates after HEALTH_FAILURE_THRESHOLD failures.
```

In another terminal, kill `service-a`:

```bash
make fault-inject
# Equivalent to:
curl -sS -X POST http://localhost:8004/inject/kill \
  -H 'content-type: application/json' -d '{"target":"service-a"}'
```

Within ~`HEALTH_POLL_INTERVAL_SECONDS * HEALTH_FAILURE_THRESHOLD`
seconds, the recovery process logs:

```
{"level":"warning","message":"health_check_failed","target":"service-a","consecutive_failures":1,...}
{"level":"warning","message":"health_check_failed","target":"service-a","consecutive_failures":2,...}
{"level":"warning","message":"health_check_failed","target":"service-a","consecutive_failures":3,...}
{"level":"info","message":"recovery_event","target":"service-a","success":true,"duration_seconds":0.4,...}
```

Confirm with `make health`.

### Latency / 5xx variants

```bash
# Make /slow take 1.5s
curl -sS -X POST http://localhost:8004/inject/latency \
  -H 'content-type: application/json' \
  -d '{"target":"self","duration_ms":1500}'
time curl -sS http://localhost:8004/slow

# Make /broken always return 500
curl -sS -X POST http://localhost:8004/inject/error \
  -H 'content-type: application/json' \
  -d '{"target":"self","error_rate":1.0}'
curl -i http://localhost:8004/broken

# Reset
curl -sS -X POST http://localhost:8004/inject/error \
  -H 'content-type: application/json' \
  -d '{"target":"self","error_rate":0.0}'
```

## 6. Observability tools

```bash
# Tail and re-emit normalised structured logs from every container
make aggregate

# Scrape /metrics from every service into metrics/metrics.json (+ history)
make collect

# One-shot snapshot from the host
make metrics
```

## 7. Adding a new service

1. **Pick a name and port.** Add `MY_SERVICE_PORT` and `MY_SERVICE_HOST`
   entries to `.env` and `.env.example`.

2. **Create the service directory.** Mirror the layout of `service-b`:
   ```
   services/my-service/
   ├── Dockerfile
   ├── requirements.txt
   └── app/
       ├── __init__.py
       └── main.py
   ```

3. **Use the shared FastAPI factory.** In `app/main.py`:
   ```python
   from shared.fastapi_app import create_service_app

   app, metrics = create_service_app("my-service")
   # add domain routes...
   ```
   You automatically get `GET /health`, `GET /metrics`, structured logging,
   and request-timing middleware.

4. **Wire the Dockerfile.** Use `services/service-b/Dockerfile` as a
   template; remember to `COPY shared/ /app/shared/` so the service has
   the shared models in its image.

5. **Register it in `docker-compose.yml`.** Add a service block with a
   healthcheck (use the same `python -c ...urlopen` one-liner that the
   other services use), expose the port, and join the `mesh` network.

6. **Add it to the orchestrator.** Append a `ServiceTarget(...)` to
   `orchestration/__init__.py:default_targets()`.

7. **Add it to the fault-injector allow-list.** Update `_ALLOWED_TARGETS`
   in `services/fault-injector/app/main.py` so chaos demos can target
   the new container.

8. **Test it.** Add a unit test for any new domain logic and an
   integration test under `tests/integration/test_end_to_end.py` that
   asserts on the new endpoints.

## 8. Useful environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `API_GATEWAY_PORT` | 8000 | Host port mapped to the gateway. |
| `SERVICE_A_PORT` | 8001 | service-a host port. |
| `SERVICE_B_PORT` | 8002 | service-b host port. |
| `CPP_WORKER_PORT` | 8003 | cpp-worker host port. |
| `FAULT_INJECTOR_PORT` | 8004 | fault-injector host port. |
| `LOG_LEVEL` | `INFO` | structlog level for every service. |
| `HEALTH_POLL_INTERVAL_SECONDS` | 5 | Recovery loop poll interval. |
| `HEALTH_FAILURE_THRESHOLD` | 3 | Consecutive failures before recovery fires. |
| `RECOVERY_BACKOFF_SECONDS` | 2 | Cooldown after a recovery attempt. |
| `METRICS_OUTPUT_PATH` | `./metrics/metrics.json` | metrics_collector output. |
| `METRICS_SCRAPE_INTERVAL_SECONDS` | 10 | metrics_collector tick interval. |
| `CPP_WORKER_THREADS` | (`hw_concurrency`) | C++ thread pool size. |
| `FANOUT_CONCURRENCY` | 16 | service-a `asyncio.gather` semaphore size. |
| `QUEUE_WORKERS` | 4 | service-a `asyncio.Queue` consumer count. |
| `THREADPOOL_WORKERS` | 4 | service-a thread pool size. |

## 9. IDE setup for `cpp-worker` (one-time)

Inside Docker, the C++ build pulls `cpp-httplib` and `nlohmann/json` via CMake
`FetchContent`. On your local filesystem those headers don't exist until you
ask CMake for them, which means clangd (Cursor / VS Code) shows red squiggles
on `<httplib.h>` and everything that depends on it.

Fix it once with:

```bash
make cpp-prep
```

This runs `cmake -B services/cpp-worker/build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`,
which fetches the dependencies onto disk and emits
`services/cpp-worker/build/compile_commands.json`. The committed
`services/cpp-worker/.clangd` already points clangd at that path, so reloading
your editor window clears all the false errors.

Run `make cpp-clean` if you want to wipe the local CMake build tree
(it does not affect the Docker image).

## 10. Troubleshooting

- **`make up` hangs on `cpp-worker` build.** First-time builds fetch
  `cpp-httplib` and `nlohmann/json` via CMake's `FetchContent`; this can
  take a couple of minutes on a cold cache. Subsequent rebuilds reuse
  Docker's layer cache.
- **Ports 8000-8004 already in use.** Override them in `.env`; both
  `docker-compose.yml` and the host-side Make targets read from there.
- **`fault-injector` returns 500 on `/inject/kill`.** Make sure
  `/var/run/docker.sock` exists on your host (it does on Linux and on
  Docker Desktop for Mac/Windows). On rootless Docker setups, point the
  socket at `$XDG_RUNTIME_DIR/docker.sock`.
- **Integration tests skip.** That's by design — they only run when the
  stack is up. `make up && make test-int`.
