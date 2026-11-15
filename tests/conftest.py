"""Keep the suite offline. A test that opens a socket fails immediately."""

import socket

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise RuntimeError("network is disabled in tests")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    for name in (
        "GEMINI_API_KEY",
        "TRUST_PROXY",
        "LIVE_CALLS_PER_IP_PER_HOUR",
        "LIVE_CALLS_PER_DAY",
    ):
        monkeypatch.delenv(name, raising=False)
