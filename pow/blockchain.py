"""Mini proof-of-work blockchain: the BASELINE you extend into your TF.

What is already here (read it once, it is short):
  - Block: the data of one block and its sha256 hash.
  - genesis(): the first block, the same on every node.
  - mine(): the simplest PoW loop there is, with a FIXED difficulty.
  - new_block(): the next block on top of a chain.

What is NOT here: everything marked TODO(TF). That is your Trabajo Final:
a PoW blockchain of your own, with several nodes, running in the cloud.
Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field

GENESIS_PREV = "0" * 64

# Leading hex zeros a block hash needs. FIXED on purpose: 3 zeros = about
# 4096 attempts, a few milliseconds on a laptop.
# TODO(TF): adjustable difficulty. Decide where it comes from (config, the
# chain itself, a retarget rule from the time between blocks...) and make
# every node agree on it.
DIFFICULTY = 3


@dataclass
class Block:
    index: int
    previous_hash: str
    transactions: list[dict]
    timestamp: float = field(default_factory=time.time)
    nonce: int = 0
    hash: str = ""

    def header(self) -> str:
        """Everything the hash covers except the nonce, as canonical JSON."""
        payload = {
            "index": self.index,
            "previous_hash": self.previous_hash,
            "transactions": self.transactions,
            "timestamp": self.timestamp,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def compute_hash(self, nonce: int | None = None) -> str:
        n = self.nonce if nonce is None else nonce
        return hashlib.sha256(f"{self.header()}{n}".encode()).hexdigest()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Block":
        return cls(**data)


def mine(block: Block) -> Block:
    """Try nonces 0, 1, 2, ... until the hash starts with DIFFICULTY zeros."""
    target = "0" * DIFFICULTY
    while True:
        h = block.compute_hash()
        if h.startswith(target):
            block.hash = h
            return block
        block.nonce += 1


def genesis() -> Block:
    """Block 0. Fixed timestamp, so every node computes the same genesis."""
    return mine(Block(0, GENESIS_PREV, [{"msg": "genesis infra-cloud"}], timestamp=0.0))


def new_block(chain: list[Block], transactions: list[dict]) -> Block:
    last = chain[-1]
    return Block(last.index + 1, last.hash, transactions)


def validate_chain(chain: list[Block]) -> tuple[bool, str]:
    """Validate the baseline's canonical genesis, links and fixed-difficulty PoW.

    This is structural validation, not balances, signatures or consensus.
    The network difficulty remains DIFFICULTY, never a value supplied by a peer.
    """
    if not isinstance(chain, list) or not chain:
        return False, "chain must be a non-empty list"
    for position, block in enumerate(chain):
        if not isinstance(block, Block):
            return False, f"block {position}: expected Block"
        if type(block.index) is not int or block.index != position:
            return False, f"block {position}: invalid index"
        if type(block.nonce) is not int or block.nonce < 0:
            return False, f"block {position}: invalid nonce"
        if type(block.timestamp) not in (int, float):
            return False, f"block {position}: invalid timestamp"
        try:
            if not math.isfinite(block.timestamp) or block.timestamp < 0:
                return False, f"block {position}: invalid timestamp"
        except OverflowError:
            return False, f"block {position}: invalid timestamp"
        if not isinstance(block.transactions, list) or not all(
            isinstance(tx, dict) for tx in block.transactions
        ):
            return False, f"block {position}: invalid transactions"
        for value in (block.hash, block.previous_hash):
            if not isinstance(value, str) or len(value) != 64 or any(
                char not in "0123456789abcdef" for char in value
            ):
                return False, f"block {position}: invalid hash format"
        try:
            calculated = block.compute_hash()
        except (TypeError, ValueError, OverflowError, RecursionError):
            return False, f"block {position}: invalid canonical JSON"
        if calculated != block.hash:
            return False, f"block {position}: hash mismatch"
        if not block.hash.startswith("0" * DIFFICULTY):
            return False, f"block {position}: insufficient proof of work"
        expected_previous = GENESIS_PREV if position == 0 else chain[position - 1].hash
        if block.previous_hash != expected_previous:
            return False, f"block {position}: broken previous_hash link"
    if chain[0].to_dict() != genesis().to_dict():
        return False, "non-canonical genesis"
    return True, "valid baseline chain"


def choose_best_chain(candidates: list[list[Block]]) -> list[Block] | None:
    """TODO(TF): consensus. Among the VALID candidates (yours + your peers'),
    pick the one every node should keep: the longest valid chain (or the one
    with the most accumulated work). What happens on a tie?
    """
    raise NotImplementedError("TODO(TF): longest-valid-chain consensus")
