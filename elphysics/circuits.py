"""
circuits.py — 回路 JSON を組み立てる共有ビルダー。

データセット生成・強化学習・進化的アルゴリズムなど、複数のユースケースが
同じトポロジー生成コードを重複実装しないための土台。

  >>> from elphysics import circuits as cir
  >>> c = cir.series([3, 6])                 # 直列 3Ω + 6Ω
  >>> c = cir.parallel([6, 12])              # 並列 6Ω ∥ 12Ω
  >>> c = cir.mixed_ps([6, 12], 4)           # (6∥12) 直列 4Ω

いずれも `{"dc_circuit": {...}}` 形式を返し、そのまま
`elphysics.verify()` / `elphysics.solve_dc()` に渡せる。
"""
from __future__ import annotations

# ── 部品 ──────────────────────────────────────────────────────────────

def resistor(cid: str, ohm, frm: str, to: str, *, rated_current_a=None) -> dict:
    """抵抗を 1 つ生成する。`rated_current_a` を渡すと定格電流チェックの対象になる。"""
    comp = {"id": cid, "type": "resistor", "resistance_ohm": ohm, "from": frm, "to": to}
    if rated_current_a is not None:
        comp["rated_current_a"] = rated_current_a
    return comp


def voltage_source(cid: str, frm: str, to: str, *, voltage_v=None) -> dict:
    """電圧源を 1 つ生成する。`voltage_v=None` で未知電源（analysis モードで求める）。"""
    return {"id": cid, "type": "voltage_source", "voltage_v": voltage_v,
            "from": frm, "to": to}


# ── ノード集合の導出 ──────────────────────────────────────────────────

def _nodes_of(components: list[dict], *extra: str) -> list[str]:
    """components と追加ノードから、出現順を保った一意なノード列を作る。"""
    seen: dict[str, None] = {}
    for n in extra:
        seen.setdefault(n, None)
    for comp in components:
        seen.setdefault(comp["from"], None)
        seen.setdefault(comp["to"], None)
    return list(seen)


# ── 回路（トップレベル）──────────────────────────────────────────────

def resistance_circuit(components: list[dict], pos: str, neg: str,
                       nodes: list[str] | None = None) -> dict:
    """合成抵抗モードの回路 JSON を組み立てる（terminals 指定）。"""
    return {"dc_circuit": {
        "nodes": nodes or _nodes_of(components, pos, neg),
        "components": components,
        "terminals": {"pos": pos, "neg": neg},
    }}


def analysis_circuit(components: list[dict], ref_node: str,
                     constraints: list[dict] | None = None,
                     find: list[str] | None = None,
                     nodes: list[str] | None = None) -> dict:
    """電源解析モードの回路 JSON を組み立てる（ref_node + constraints + find）。"""
    inner: dict = {
        "nodes": nodes or _nodes_of(components, ref_node),
        "components": components,
        "ref_node": ref_node,
    }
    if constraints is not None:
        inner["constraints"] = constraints
    if find is not None:
        inner["find"] = find
    return {"dc_circuit": inner}


# ── 定番トポロジー（合成抵抗モード）──────────────────────────────────

def series(resistances: list[int]) -> dict:
    """直列接続。pos → n0 → … → neg。"""
    n = len(resistances)
    nodes = ["pos"] + [f"n{i}" for i in range(n - 1)] + ["neg"]
    comps = [resistor(f"R{i+1}", r, nodes[i], nodes[i + 1])
             for i, r in enumerate(resistances)]
    return resistance_circuit(comps, "pos", "neg", nodes)


def parallel(resistances: list[int]) -> dict:
    """並列接続。すべて pos → neg。"""
    comps = [resistor(f"R{i+1}", r, "pos", "neg")
             for i, r in enumerate(resistances)]
    return resistance_circuit(comps, "pos", "neg", ["pos", "neg"])


def mixed_ps(r_par: list[int], r_ser: int) -> dict:
    """並列グループ → 直列 1 本。(∥r_par) と直列 r_ser。"""
    comps = [resistor(f"R{i+1}", r, "pos", "mid") for i, r in enumerate(r_par)]
    comps.append(resistor(f"R{len(r_par)+1}", r_ser, "mid", "neg"))
    return resistance_circuit(comps, "pos", "neg", ["pos", "mid", "neg"])


def mixed_sp(r_ser: int, r_par: list[int]) -> dict:
    """直列 1 本 → 並列グループ。r_ser と直列に (∥r_par)。"""
    comps = [resistor("R1", r_ser, "pos", "mid")]
    comps += [resistor(f"R{i+2}", r, "mid", "neg") for i, r in enumerate(r_par)]
    return resistance_circuit(comps, "pos", "neg", ["pos", "mid", "neg"])


def mixed_pp(r_par1: list[int], r_par2: list[int]) -> dict:
    """並列グループ → 並列グループ（直列接続）。(∥r_par1) と直列 (∥r_par2)。"""
    comps = [resistor(f"R{i+1}", r, "pos", "mid") for i, r in enumerate(r_par1)]
    comps += [resistor(f"R{len(r_par1)+i+1}", r, "mid", "neg")
              for i, r in enumerate(r_par2)]
    return resistance_circuit(comps, "pos", "neg", ["pos", "mid", "neg"])
