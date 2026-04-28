"""Host-side observability tooling.

Two tiny scripts that consume what every service emits:

* ``log_aggregator``   -> tails ``docker compose logs`` and re-emits
  one parsed structured-log line per record.
* ``metrics_collector`` -> scrapes ``/metrics`` from every service on a
  fixed interval and writes a rolling JSON snapshot to disk.
"""
