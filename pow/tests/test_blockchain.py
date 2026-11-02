from copy import deepcopy

import pytest
from blockchain import (
    Block,
    DIFFICULTY,
    GENESIS_PREV,
    genesis,
    mine,
    new_block,
    validate_chain,
)


def test_genesis_is_the_same_on_every_node():
    a, b = genesis(), genesis()
    assert a.hash == b.hash
    assert a.previous_hash == GENESIS_PREV


def test_mine_meets_the_fixed_difficulty():
    g = genesis()
    b = mine(new_block([g], [{"sender": "ana", "to": "beto", "amount": 1}]))
    assert b.hash.startswith("0" * DIFFICULTY)
    assert b.hash == b.compute_hash()
    assert b.previous_hash == g.hash


def test_tampering_changes_the_hash():
    b = mine(new_block([genesis()], [{"amount": 1}]))
    b.transactions = [{"amount": 999}]
    assert b.compute_hash() != b.hash


def test_validate_chain_rejects_a_tampered_chain():
    g = genesis()
    b = mine(new_block([g], [{"amount": 1}]))
    assert validate_chain([g, b])[0]
    b.transactions = [{"amount": 999}]
    assert not validate_chain([g, b])[0]


def test_validate_chain_accepts_valid_chain_without_mutating():
    g = genesis()
    b = mine(Block(1, g.hash, [{"amount": 1}], timestamp=1.0))
    chain = [g, b]
    before = deepcopy(chain)
    assert validate_chain(chain) == (True, "valid baseline chain")
    assert chain == before


@pytest.mark.parametrize("chain", [None, [], {}, "chain", [{}]])
def test_validate_chain_rejects_invalid_container(chain):
    assert not validate_chain(chain)[0]


@pytest.mark.parametrize("field,value", [
    ("index", True), ("index", 2), ("nonce", True), ("nonce", -1),
    ("timestamp", float("inf")), ("timestamp", float("nan")),
    ("timestamp", -1), ("timestamp", "yesterday"),
    ("transactions", {}), ("transactions", [1]),
    ("transactions", [{"amount": float("nan")}]),
    ("hash", "x" * 64), ("previous_hash", "Z" * 64),
])
def test_validate_chain_rejects_invalid_block_fields(field, value):
    g = genesis()
    b = mine(Block(1, g.hash, [{"amount": 1}], timestamp=1.0))
    setattr(b, field, value)
    assert not validate_chain([g, b])[0]


def test_validate_chain_rejects_remined_broken_link():
    g = genesis()
    b = mine(Block(1, "1" * 64, [{"amount": 1}], timestamp=1.0))
    assert b.hash == b.compute_hash() and b.hash.startswith("000")
    assert "broken previous_hash" in validate_chain([g, b])[1]


def test_validate_chain_rejects_alternative_genesis():
    other = mine(Block(0, GENESIS_PREV, [{"msg": "different network"}], timestamp=0.0))
    assert validate_chain([other]) == (False, "non-canonical genesis")


def test_validate_chain_rejects_hash_without_work():
    g = genesis()
    b = Block(1, g.hash, [{"amount": 1}], timestamp=1.0)
    while b.compute_hash().startswith("0" * DIFFICULTY):
        b.nonce += 1
    b.hash = b.compute_hash()
    assert "insufficient proof of work" in validate_chain([g, b])[1]
