"""Integration tests: require the dockerised stack to be running.

Run them with::

    make up
    make test-int

These tests issue real HTTP requests against the host-mapped ports
and verify the end-to-end pipeline (api-gateway -> service-a ->
service-b + cpp-worker).
"""
