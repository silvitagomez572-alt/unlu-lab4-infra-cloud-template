import importlib
import json
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    already_imported = "node" in sys.modules
    import node as node_module

    if already_imported:
        importlib.reload(node_module)
    with TestClient(node_module.app) as test_client:
        yield test_client


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_chain_starts_with_genesis(client):
    chain = client.get("/chain").json()
    assert len(chain) == 1
    assert chain[0]["index"] == 0


def test_tx_and_mine(client):
    r = client.post("/tx", json={"sender": "ana", "to": "beto", "amount": 1})
    assert r.status_code == 201
    assert len(client.get("/tx").json()) == 1

    body = client.post("/mine").json()
    assert body["mined"]["index"] == 1
    assert body["mined"]["transactions"][0]["sender"] == "ana"
    assert body["elapsed_seconds"] >= 0
    assert "elapsed_seconds" not in body["mined"]
    assert client.get("/tx").json() == []
    assert len(client.get("/chain").json()) == 2


def test_chain_can_be_reloaded_from_disk(client, tmp_path):
    client.post("/mine")
    assert (tmp_path / "chain.json").exists()
    import node

    assert len(node.load_chain()) == 2


def test_metrics_reports_block_height(client):
    client.post("/mine")
    assert "pow_block_height 1.0" in client.get("/metrics").text


@pytest.mark.parametrize("transaction", [
    {"sender": "ana", "to": "beto", "amount": 0},
    {"sender": "ana", "to": "beto", "amount": -1},
    {"sender": "ana", "to": "beto", "amount": "nan"},
    {"sender": "ana", "to": "beto", "amount": "inf"},
    {"sender": "  ", "to": "beto", "amount": 1},
    {"sender": "ana", "to": "", "amount": 1},
])
def test_invalid_transaction_is_not_queued(client, transaction):
    assert client.post("/tx", json=transaction).status_code == 422
    assert client.get("/tx").json() == []


def test_mine_busy_does_not_change_state(client):
    import node
    original = node.CHAIN_FILE.read_bytes()
    # Simulate an occupied writer lock; no concurrent requests or worker suite.
    node.STATE_LOCK.acquire()
    try:
        assert client.post("/mine").status_code == 409
    finally:
        node.STATE_LOCK.release()
    assert node.CHAIN_FILE.read_bytes() == original


def test_mine_write_failure_preserves_chain_and_pending(client, monkeypatch):
    import node
    client.post("/tx", json={"sender": "ana", "to": "beto", "amount": 1})
    original = node.CHAIN_FILE.read_bytes()

    def fail_replace(*_):
        raise OSError("simulated disk error")

    with monkeypatch.context() as patch:
        patch.setattr(node.os, "replace", fail_replace)
        assert client.post("/mine").status_code == 503
    assert node.CHAIN_FILE.read_bytes() == original
    assert len(client.get("/tx").json()) == 1
    assert "pow_block_height 0.0" in client.get("/metrics").text
    assert not list(node.DATA_DIR.glob(".chain-*.tmp"))
    assert client.post("/mine").status_code == 200
    assert client.get("/tx").json() == []
    assert len(client.get("/chain").json()) == 2


@pytest.mark.parametrize("corruption", ["bad_json", "empty_chain", "tampered_hash"])
def test_corrupt_chain_is_not_overwritten(client, corruption):
    import node
    client.post("/tx", json={"sender": "ana", "to": "beto", "amount": 1})
    if corruption == "bad_json":
        damaged = b"{broken"
    elif corruption == "empty_chain":
        damaged = b"[]"
    else:
        raw = json.loads(node.CHAIN_FILE.read_text())
        raw[0]["hash"] = "0" * 64
        damaged = json.dumps(raw).encode()
    node.CHAIN_FILE.write_bytes(damaged)
    assert client.get("/chain").status_code == 503
    assert client.post("/mine").status_code == 503
    assert node.CHAIN_FILE.read_bytes() == damaged
    assert len(client.get("/tx").json()) == 1


@pytest.mark.skip(reason="TODO(TF): add POST /peers/sync and enable this test")
def test_peers_sync_exists(client):
    assert client.post("/peers/sync").status_code == 200
