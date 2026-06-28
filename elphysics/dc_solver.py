"""
dc_solver.py — Z3 による直流回路ソルバー。

モード1 (resistance): 合成抵抗を求める（terminals 指定、1V 印加）
モード2 (analysis) : 電圧源・制約付き回路の解析（ref_node + constraints 指定）

実行: python -m elphysics.dc_solver [dc_circuit.json]
"""
from __future__ import annotations

import json
import sys
from fractions import Fraction
from pathlib import Path

from z3 import Real, RealVal, Solver, sat, unknown

# Z3 ソルバーのタイムアウト (ミリ秒)。公開 API での計算時間を制限する。
_SOLVER_TIMEOUT_MS = 30_000


# ── 部品の判定 ────────────────────────────────────────────────────────

def _is_wire(comp: dict) -> bool:
    return comp.get("type") == "wire" or comp.get("resistance_ohm", None) == 0


# ── 導線によるノードのマージ（Union-Find）─────────────────────────────

class _UnionFind:
    """導線でつながったノードをまとめるための Union-Find。

    nodes に存在しないノード名で find/union を呼ぶと、
    KeyError の代わりに分かりやすい ValueError を投げる。
    """

    def __init__(self, nodes: list[str]):
        self.parent: dict[str, str] = {n: n for n in nodes}

    def find(self, x: str) -> str:
        if x not in self.parent:
            raise ValueError(
                f"ノード '{x}' は dc_circuit.nodes に存在しません。"
                f"既知のノード: {sorted(self.parent)}"
            )
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, x: str, y: str) -> None:
        self.parent[self.find(x)] = self.find(y)


def _merge_wires(circuit_inner: dict) -> _UnionFind:
    uf = _UnionFind(circuit_inner["nodes"])
    for comp in circuit_inner["components"]:
        if _is_wire(comp) and "from" in comp and "to" in comp:
            uf.union(comp["from"], comp["to"])
    return uf


# ── モード1: 合成抵抗 ──────────────────────────────────────────────────

def solve(circuit: dict) -> dict:
    """terminals 間に 1V を印加して合成抵抗を求める（モード1）。"""
    c = circuit["dc_circuit"]
    uf = _merge_wires(c)
    pos = uf.find(c["terminals"]["pos"])
    neg = uf.find(c["terminals"]["neg"])

    if pos == neg:
        return {
            "status": "ok", "mode": "resistance",
            "R_ab": Fraction(0), "R_ab_float": 0.0,
            "shorted_terminals": True,
            "node_voltages": {}, "branch_currents": {}, "total_current": None,
        }

    resistors = []
    shorted = []
    for comp in c["components"]:
        if _is_wire(comp):
            continue
        # 注意: voltage_source はこのモードでは無視される。
        # resistance モードは「抵抗のみの回路の合成抵抗」を求める前提のため、
        # 電圧源を含む回路を渡しても、エラーにはならず単に計算から除外される。
        # 電圧源を含む回路を解析したい場合は solve_analysis() を使うこと。
        if comp.get("type") == "voltage_source":
            continue
        f, t = uf.find(comp["from"]), uf.find(comp["to"])
        if f == t:
            shorted.append(comp["id"])
            continue
        resistors.append({
            "id": comp["id"], "R": comp["resistance_ohm"],
            "from": f, "to": t,
            "raw_from": comp["from"], "raw_to": comp["to"],
        })

    nodes = sorted({pos, neg}
                   | {r["from"] for r in resistors}
                   | {r["to"] for r in resistors})

    V = {n: Real(f"V_{n}") for n in nodes}
    I = {r["id"]: Real(f"I_{r['id']}") for r in resistors}

    s = Solver()
    s.set("timeout", _SOLVER_TIMEOUT_MS)
    s.add(V[pos] == 1)
    s.add(V[neg] == 0)

    for r in resistors:
        s.add(V[r["from"]] - V[r["to"]] == r["R"] * I[r["id"]])

    for node in nodes:
        if node in (pos, neg):
            continue
        kcl = RealVal(0)
        for r in resistors:
            if r["to"] == node:
                kcl = kcl + I[r["id"]]
            if r["from"] == node:
                kcl = kcl - I[r["id"]]
        s.add(kcl == 0)

    result = s.check()
    if result == unknown:
        return {"status": "timeout", "message": f"Z3 がタイムアウトしました ({_SOLVER_TIMEOUT_MS} ms 超過)"}
    if result != sat:
        return {"status": "unsat", "message": "制約を満たす解がありません"}

    m = s.model()

    def to_frac(z3_val) -> Fraction:
        return Fraction(z3_val.numerator_as_long(), z3_val.denominator_as_long())

    v_vals = {n: to_frac(m[V[n]]) for n in nodes}
    i_vals = {r["id"]: to_frac(m[I[r["id"]]]) for r in resistors}
    
    # pos端子から外部へ流れ出る電流の合計を求める。
    # from==pos の枝は pos から流れ出る方向なので +、
    # to==pos の枝は pos へ流れ込む方向なので - とする。
    # (i_total は「端子から見て、どれだけ電流を吸い込んでいるか」に相当し、
    #  この符号付けは下の R_ab = 1 / i_total の式と整合させるためのもの)

    i_total = Fraction(0)
    for r in resistors:
        if r["from"] == pos:
            i_total += i_vals[r["id"]]
        if r["to"] == pos:
            i_total -= i_vals[r["id"]]

    if i_total == 0:
        return {"status": "open", "message": "端子間に電流が流れません（開放回路）"}

    r_eq = Fraction(1) / i_total

    return {
        "status": "ok", "mode": "resistance",
        "R_ab": r_eq, "R_ab_float": float(r_eq),
        "shorted_terminals": False,
        "shorted_resistors": shorted,
        "node_voltages": v_vals,
        "branch_currents": i_vals,
        "total_current": i_total,
        "resistors": resistors,
        "merged": {raw: uf.find(raw) for raw in c["nodes"]},
    }


# ── モード2: 電圧源・制約付き解析 ────────────────────────────────────

def solve_analysis(circuit: dict) -> dict:
    """
    電圧源・追加制約を含む回路を解析するモード。

    dc_circuit.json の要素:
      ref_node   : 基準ノード（V=0）
      components : type="voltage_source" は voltage_v=null で未知電源
      constraints: [{"type":"voltage_across","component_id":"R6","voltage_v":1.8}]
      find       : ["E1"] — 値を求めたい voltage_source の id リスト
    """
    c = circuit["dc_circuit"]
    uf = _merge_wires(c)
    ref = uf.find(c["ref_node"])

    resistors = []
    v_sources = []
    for comp in c["components"]:
        if _is_wire(comp):
            continue
        f, t = uf.find(comp["from"]), uf.find(comp["to"])
        if f == t:
            continue
        if comp["type"] == "resistor":
            resistors.append({
                "id": comp["id"], "R": comp["resistance_ohm"],
                "from": f, "to": t,
                "raw_from": comp["from"], "raw_to": comp["to"],
            })
        elif comp["type"] == "voltage_source":
            v_sources.append({
                "id": comp["id"],
                "voltage_v": comp.get("voltage_v"),
                "from": f, "to": t,
                "raw_from": comp["from"], "raw_to": comp["to"],
            })

    all_nodes = ({ref}
                 | {r["from"] for r in resistors} | {r["to"] for r in resistors}
                 | {vs["from"] for vs in v_sources} | {vs["to"] for vs in v_sources})

    V = {n: Real(f"V_{n}") for n in all_nodes}
    I_r = {r["id"]: Real(f"I_{r['id']}") for r in resistors}
    I_vs = {vs["id"]: Real(f"I_{vs['id']}") for vs in v_sources}

    # 未知電源の変数
    E_vars: dict[str, object] = {}
    for vs in v_sources:
        if vs["voltage_v"] is None:
            E_vars[vs["id"]] = Real(f"E_{vs['id']}")

    s = Solver()
    s.set("timeout", _SOLVER_TIMEOUT_MS)

    # 基準ノード
    s.add(V[ref] == 0)

    # オームの法則
    for r in resistors:
        s.add(V[r["from"]] - V[r["to"]] == r["R"] * I_r[r["id"]])

    # 電圧源の制約（from→to の電圧上昇 = voltage_v）
    for vs in v_sources:
        if vs["voltage_v"] is not None:
            s.add(V[vs["to"]] - V[vs["from"]] == vs["voltage_v"])
        else:
            s.add(V[vs["to"]] - V[vs["from"]] == E_vars[vs["id"]])

    # 追加制約（voltage_across: component の from→to の電圧降下 = voltage_v）
    comp_map = {comp["id"]: comp for comp in c["components"]}
    for constraint in c.get("constraints", []):
        if constraint["type"] == "voltage_across":
            cid = constraint["component_id"]
            comp = comp_map[cid]
            f = uf.find(comp["from"])
            t = uf.find(comp["to"])
            s.add(V[f] - V[t] == constraint["voltage_v"])

    # キルヒホッフ（ref 以外の全ノード）
    for node in all_nodes:
        if node == ref:
            continue
        kcl = RealVal(0)
        for r in resistors:
            if r["to"] == node:
                kcl = kcl + I_r[r["id"]]
            if r["from"] == node:
                kcl = kcl - I_r[r["id"]]
        for vs in v_sources:
            if vs["to"] == node:
                kcl = kcl + I_vs[vs["id"]]
            if vs["from"] == node:
                kcl = kcl - I_vs[vs["id"]]
        s.add(kcl == 0)

    result = s.check()
    if result == unknown:
        return {"status": "timeout", "message": f"Z3 がタイムアウトしました ({_SOLVER_TIMEOUT_MS} ms 超過)"}
    if result != sat:
        return {"status": "unsat", "message": "制約を満たす解がありません"}

    m = s.model()

    def to_frac(z3_val) -> Fraction:
        return Fraction(z3_val.numerator_as_long(), z3_val.denominator_as_long())

    v_vals = {n: to_frac(m[V[n]]) for n in all_nodes}
    i_r_vals = {rid: to_frac(m[I_r[rid]]) for rid in I_r}
    i_vs_vals = {vid: to_frac(m[I_vs[vid]]) for vid in I_vs}

    found: dict[str, Fraction] = {}
    for eid, e_var in E_vars.items():
        found[eid] = to_frac(m[e_var])

    return {
        "status": "ok", "mode": "analysis",
        "node_voltages": v_vals,
        "branch_currents": i_r_vals,
        "source_currents": i_vs_vals,
        "found": found,
        "resistors": resistors,
        "v_sources": v_sources,
    }


# ── レポート出力 ──────────────────────────────────────────────────────

def print_report(circuit: dict, result: dict) -> None:
    c = circuit["dc_circuit"]
    print(f"問題: {c.get('question', c.get('description', ''))}")
    print("=" * 60)

    if result["status"] == "unsat":
        print(f"❌ {result.get('message', '解なし')}")
        return
    if result["status"] == "open":
        print(f"⚠️ {result.get('message', '開放回路')}")
        return

    if result["mode"] == "resistance":
        _print_resistance(c, result)
    elif result["mode"] == "analysis":
        _print_analysis(c, result)
    else:
        print(f"⚠️ 未知の mode: {result['mode']!r} — 表示できません")


def _print_resistance(c: dict, result: dict) -> None:
    if result.get("shorted_terminals"):
        print("端子a-b間が直結 → 合成抵抗 0Ω")
        return

    merged = result.get("merged", {})
    groups: dict[str, list[str]] = {}
    for raw, root in merged.items():
        groups.setdefault(root, []).append(raw)
    merged_groups = {root: ns for root, ns in groups.items() if len(ns) > 1}
    if merged_groups:
        print("[導線マージ]")
        for root, ns in merged_groups.items():
            print(f"  {{{', '.join(ns)}}} → {root}")
        print()

    if result.get("shorted_resistors"):
        print(f"[ショート済み抵抗] {', '.join(result['shorted_resistors'])}")
        print()

    print("[枝電流  (オームの法則)]")
    for r in result["resistors"]:
        V_diff = result["node_voltages"][r["from"]] - result["node_voltages"][r["to"]]
        I_r = result["branch_currents"][r["id"]]
        label = (f"{r['id']} ({r['R']}Ω, {r['raw_from']}→{r['raw_to']})"
                 if r["raw_from"] != r["from"] or r["raw_to"] != r["to"]
                 else f"{r['id']} ({r['R']}Ω)")
        print(f"  {label}: ΔV={V_diff} V,  I={I_r} A")

    print(f"\n[合成抵抗]")
    print(f"  端子電流: {result['total_current']} A")
    print(f"  R_ab = 1/{result['total_current']} = {result['R_ab']} Ω  "
          f"= {result['R_ab_float']:.4f} Ω")
    print(f"\n  答え: {result['R_ab']} Ω  ({result['R_ab_float']:.2f} Ω)")


def _print_analysis(c: dict, result: dict) -> None:
    vv = result["node_voltages"]
    ic = result["branch_currents"]

    print("[ノード電圧]")
    for node, v in sorted(vv.items()):
        print(f"  V_{node} = {v} V  ({float(v):.4f} V)")

    print("\n[枝電流  (オームの法則)]")
    for r in result["resistors"]:
        I_r = ic[r["id"]]
        V_diff = vv[r["from"]] - vv[r["to"]]
        print(f"  {r['id']} ({r['R']}Ω, {r['raw_from']}→{r['raw_to']}): "
              f"ΔV={V_diff} V,  I={I_r} A  ({float(I_r):.4f} A)")

    if result.get("source_currents"):
        print("\n[電源電流]")
        for vid, ival in result["source_currents"].items():
            print(f"  {vid}: I={ival} A  ({float(ival):.4f} A)")

    if result.get("found"):
        print("\n[求める値]")
        for vid, val in result["found"].items():
            print(f"  {vid} = {val} V  ({float(val):.4f} V)")

        found_items = list(result["found"].items())
        if len(found_items) == 1:
            vid, val = found_items[0]
            print(f"\n  答え: {vid} = {float(val):.2f} V")
        else:
            print("\n  答え:")
            for vid, val in found_items:
                print(f"    {vid} = {float(val):.2f} V")


# ── エントリポイント ──────────────────────────────────────────────────

def solve_auto(circuit: dict) -> dict:
    """モードを自動判定して solve または solve_analysis を実行する。"""
    if _is_analysis_mode(circuit):
        return solve_analysis(circuit)
    return solve(circuit)


def _is_analysis_mode(circuit: dict) -> bool:
    c = circuit["dc_circuit"]
    if "ref_node" in c:
        return True
    for comp in c.get("components", []):
        if comp.get("type") == "voltage_source":
            return True
    return False


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "examples" / "dc_circuit.json"
    with open(path, encoding="utf-8") as f:
        circuit = json.load(f)

    result = solve_auto(circuit)
    print_report(circuit, result)
