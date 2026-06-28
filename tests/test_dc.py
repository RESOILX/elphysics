"""
test_dc.py — 直流回路 (DC) の検証テスト

- dc_solver: 合成抵抗 (直列/並列) と電源解析モード
- verify(): 同梱サンプルが error なしで通る / 過電流を検出する
- schema_validation: 妥当な入力は通し、壊れた入力は弾く
- API: /health, /circuit-types, /verify (正常系・異常系)
"""
import json
from pathlib import Path

import pytest

import elphysics
from elphysics import (
    verify, solve_dc, solve_dc_analysis, validate_circuit, is_valid,
)

_EXAMPLES = Path(__file__).resolve().parent.parent / "elphysics" / "examples"


# ── 回路ビルダー (テスト内ローカル) ──────────────────────────────────

def dc_series(resistances):
    nodes, comps, prev = ["a"], [], "a"
    for i, r in enumerate(resistances):
        nxt = "b" if i == len(resistances) - 1 else f"n{i}"
        comps.append({"id": f"R{i+1}", "type": "resistor",
                      "resistance_ohm": r, "from": prev, "to": nxt})
        if nxt not in nodes:
            nodes.append(nxt)
        prev = nxt
    return {"dc_circuit": {"nodes": nodes, "components": comps,
                           "terminals": {"pos": "a", "neg": "b"}}}


def dc_parallel(resistances):
    comps = [{"id": f"R{i+1}", "type": "resistor", "resistance_ohm": r,
              "from": "a", "to": "b"} for i, r in enumerate(resistances)]
    return {"dc_circuit": {"nodes": ["a", "b"], "components": comps,
                           "terminals": {"pos": "a", "neg": "b"}}}


# ── 合成抵抗モード ────────────────────────────────────────────────────

def test_series_resistance():
    # 直列 3Ω + 6Ω = 9Ω
    res = solve_dc(dc_series([3, 6]))
    assert res["status"] == "ok"
    assert abs(res["R_ab_float"] - 9.0) < 1e-9


def test_parallel_resistance():
    # 並列 3Ω // 6Ω = 2Ω
    res = solve_dc(dc_parallel([3, 6]))
    assert res["status"] == "ok"
    assert abs(res["R_ab_float"] - 2.0) < 1e-9


def test_overcurrent_detected():
    # R に定格を付け、1V 印加時の電流が定格を超えたら error
    circuit = {"dc_circuit": {
        "nodes": ["a", "b"],
        "components": [{"id": "R1", "type": "resistor", "resistance_ohm": 1,
                        "from": "a", "to": "b", "rated_current_a": 0.5}],
        "terminals": {"pos": "a", "neg": "b"},
    }}
    results = verify(circuit)
    assert any(r.status == "error" and r.reason == "over_current" for r in results)


# ── 電源解析モード ────────────────────────────────────────────────────

def test_analysis_unknown_emf():
    circuit = json.loads(
        (_EXAMPLES / "dc_circuit_analysis.json").read_text(encoding="utf-8"))
    res = solve_dc_analysis(circuit)
    assert res["status"] == "ok"
    # R2=3Ω に 4V → I=4/3A、R1=6Ω で 8V 降下 → E1 = 12V
    assert abs(float(res["found"]["E1"]) - 12.0) < 1e-9


# ── 同梱サンプル ──────────────────────────────────────────────────────

@pytest.mark.parametrize("filename", ["dc_circuit.json", "dc_circuit_analysis.json"])
def test_examples_verify_without_error(filename):
    circuit = json.loads((_EXAMPLES / filename).read_text(encoding="utf-8"))
    assert [r for r in verify(circuit) if r.status == "error"] == []


# ── スキーマ検証 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("filename", ["dc_circuit.json", "dc_circuit_analysis.json"])
def test_examples_conform_to_schema(filename):
    circuit = json.loads((_EXAMPLES / filename).read_text(encoding="utf-8"))
    assert validate_circuit(circuit) == []


def test_builder_circuits_conform():
    assert is_valid(dc_series([3, 1, 2]))
    assert is_valid(dc_parallel([6, 3]))


@pytest.mark.parametrize("bad", [
    {"banana_circuit": {}},                                   # 未知の型キー
    {"dc_circuit": {"nodes": ["a"], "components": []}},        # terminals も ref_node も無い
    {"ac_circuit": {"source": {}, "nodes": [], "components": []}},  # DC 版は dc_circuit 以外不可
])
def test_malformed_rejected(bad):
    assert validate_circuit(bad)


# ── API ───────────────────────────────────────────────────────────────

def test_api_health(api_client):
    r = api_client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_api_circuit_types_dc_only(api_client):
    r = api_client.get("/circuit-types")
    assert r.status_code == 200
    assert r.json()["types"] == ["dc"]


def test_api_verify_dc(api_client):
    circuit = json.loads((_EXAMPLES / "dc_circuit.json").read_text(encoding="utf-8"))
    r = api_client.post("/verify", json={"circuit": circuit})
    assert r.status_code == 200
    body = r.json()
    assert body["circuit_type"] == "dc"
    assert body["summary"]["error"] == 0


def test_api_rejects_unknown_type(api_client):
    r = api_client.post("/verify", json={"circuit": {"ac_circuit": {}}})
    assert r.status_code == 422


def test_api_rejects_schema_violation(api_client):
    # dc_circuit だが terminals も ref_node も無い → schema 違反 → 422
    bad = {"dc_circuit": {"nodes": ["a", "b"], "components": []}}
    r = api_client.post("/verify", json={"circuit": bad})
    assert r.status_code == 422
