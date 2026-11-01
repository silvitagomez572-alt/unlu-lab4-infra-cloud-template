"""HTTP node of the mini PoW blockchain: the BASELINE you extend into your TF.

It runs as ONE node and already answers the contract the course uses
(port 8090, the same paths the chart, the ServiceMonitor and the docs expect):
  - GET  /healthz   -> {"status": "ok"}
  - GET  /metrics   -> Prometheus, with pow_block_height
  - GET  /chain     -> the local chain, as JSON
  - GET  /tx        -> the pending transactions
  - POST /tx        -> {"sender": "ana", "to": "beto", "amount": 1}
  - POST /mine      -> mine one block with the pending transactions

The chain is a JSON file under DATA_DIR (default /data): kill the container,
keep the disk, keep the chain.

TODO(TF), none of this is implemented. It is your Trabajo Final:
  - Peers: PEERS (comma-separated URLs, the chart fills it in) is read below
    but not used. Add GET /peers and POST /peers/sync: fetch the peers'
    chains, validate them (blockchain.validate_chain) and keep the best one
    (blockchain.choose_best_chain). Should a new block also be pushed to the
    peers instead of waiting for a sync?
  - Adjustable difficulty (blockchain.DIFFICULTY is a constant).
  - Mempool rules: duplicates, signatures and balances are not checked.
    Pending transactions remain in memory and do not survive a restart.
  - Cross-process concurrency: the Lab 8 lock protects one Python process.
    Keep one worker and one writer per DATA_DIR/PVC.
  - More metrics for your dashboard (peers, mining time, ...).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from blockchain import Block, genesis, mine, new_block, validate_chain
from fastapi import FastAPI, HTTPException, Request
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse, Response

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CHAIN_FILE = DATA_DIR / "chain.json"
PORT = int(os.environ.get("PORT", "8090"))
# TODO(TF): use them (GET /peers, POST /peers/sync). Read here so the chart's
# PEERS value already reaches the process.
PEERS = [p.strip() for p in os.environ.get("PEERS", "").split(",") if p.strip()]

BLOCK_HEIGHT = Gauge("pow_block_height", "Height of the local chain (last block index)")

# One writer per Python process. Distributed consensus remains TODO(TF).
STATE_LOCK = threading.Lock()
LOGGER = logging.getLogger(__name__)
pending_tx: list[dict[str, Any]] = []


class Tx(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    sender: str = Field(min_length=1, max_length=128)
    to: str = Field(min_length=1, max_length=128)
    amount: float = Field(gt=0, allow_inf_nan=False)


class ChainStateError(RuntimeError):
    """Unreadable or invalid state; never overwrite it with a fresh genesis."""


def load_chain() -> list[Block]:
    if CHAIN_FILE.exists():
        try:
            raw = json.loads(CHAIN_FILE.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("chain must be a list")
            chain = [Block.from_dict(b) for b in raw]
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise ChainStateError("cannot read chain") from exc
        ok, reason = validate_chain(chain)
        if not ok:
            raise ChainStateError(reason)
        return chain
    chain = [genesis()]
    save_chain(chain)
    return chain


def save_chain(chain: list[Block]) -> None:
    ok, reason = validate_chain(chain)
    if not ok:
        raise ChainStateError(reason)
    temporary = None
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=DATA_DIR, prefix=".chain-", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump([b.to_dict() for b in chain], handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, CHAIN_FILE)
    except (OSError, ValueError, TypeError) as exc:
        raise ChainStateError("cannot persist chain") from exc
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("could not remove temporary chain file")


@asynccontextmanager
async def lifespan(_: FastAPI):
    BLOCK_HEIGHT.set(load_chain()[-1].index)
    yield


app = FastAPI(title="pow-node-baseline", version="0.1.0", lifespan=lifespan)


@app.exception_handler(ChainStateError)
async def state_error(_: Request, exc: ChainStateError) -> JSONResponse:
    LOGGER.error("chain state unavailable: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "chain state unavailable"})


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/chain")
def get_chain() -> list[dict[str, Any]]:
    with STATE_LOCK:
        return [b.to_dict() for b in load_chain()]


@app.get("/tx")
def get_pending_tx() -> list[dict[str, Any]]:
    with STATE_LOCK:
        return list(pending_tx)


@app.post("/tx", status_code=201)
def add_tx(tx: Tx) -> dict[str, Any]:
    entry = tx.model_dump() | {"received_at": time.time()}
    with STATE_LOCK:
        pending_tx.append(entry)
    return entry


@app.post("/mine")
def mine_block() -> dict[str, Any]:
    if not STATE_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="node state is busy; retry later")
    try:
        chain = load_chain()
        started = time.perf_counter()
        block = mine(new_block(chain, list(pending_tx) or [{"msg": "empty block"}]))
        elapsed_seconds = time.perf_counter() - started
        chain.append(block)
        save_chain(chain)
        # A failed persistence operation must not discard the pending batch.
        pending_tx.clear()
        BLOCK_HEIGHT.set(block.index)
        LOGGER.info("mined block index=%s elapsed_seconds=%.6f", block.index, elapsed_seconds)
        return {"mined": block.to_dict(), "elapsed_seconds": elapsed_seconds}
    finally:
        STATE_LOCK.release()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
