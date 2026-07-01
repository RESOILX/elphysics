#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qa_generator_type2.py — elphysics × AI Dataset Generator (Type 2: 電源解析・逆問題)

「R2 の両端電圧が分かっているとき、電源電圧 E を求めよ」という
逆問題の QA ペアを、Z3 SMT ソルバーで証明済みの厳密解付きで生成する。

Type 1（合成抵抗）が順問題なのに対し、Type 2 は制約から未知の電源値を
逆算する問題。elphysics の analysis モード（solve_dc_analysis + find）を使う。

使用方法:
  python elphysics/examples/qa_generator_type2.py
  python elphysics/examples/qa_generator_type2.py --count 500 --output big.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path

import elphysics
from elphysics import circuits as cir

G = "\033[92m"; Y = "\033[93m"; C = "\033[96m"
B = "\033[1m";  RST = "\033[0m"; DIM = "\033[2m"; RED = "\033[91m"

NICE_R = [2, 3, 4, 5, 6, 8, 10, 12, 15, 20]
NICE_I = [1, 2, 3]   # 直列電流 [A]。整数に固定して全値を厳密整数に保つ


@dataclass
class QAPair:
    id: str
    type: str        # divider2 / divider3
    difficulty: str  # easy / medium
    question: str
    answer: str
    reasoning: str
    circuit: dict
    e_exact: str
    e_float: float
    verified_by: str = "elphysics/z3-smt"


def frac_str(f: Fraction) -> str:
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def _series_divider(rs: list[int], current: int) -> tuple[dict, int]:
    """gnd →(E1)→ pos, 直列抵抗列 → gnd の分圧回路を作る。

    直列電流を整数に固定するため、末端抵抗の両端電圧 v_last = I×R_last も整数。
    その v_last を制約に与え、E1（voltage_v=null）を未知数として解く。
    戻り値: (回路 JSON, 末端抵抗の両端電圧 v_last[整数])。
    """
    v_last = current * rs[-1]            # 末端抵抗の電圧降下（整数）

    nodes = ["gnd", "pos"] + [f"n{k}" for k in range(len(rs) - 1)]
    comps = [cir.voltage_source("E1", "gnd", "pos", voltage_v=None)]
    chain = ["pos"] + [f"n{k}" for k in range(len(rs) - 1)] + ["gnd"]
    for k, r in enumerate(rs):
        comps.append(cir.resistor(f"R{k+1}", r, chain[k], chain[k + 1]))

    circuit = cir.analysis_circuit(
        comps, ref_node="gnd",
        constraints=[{"type": "voltage_across",
                      "component_id": f"R{len(rs)}", "voltage_v": v_last}],
        find=["E1"], nodes=nodes,
    )
    return circuit, v_last


def _reasoning(rs: list[int], v_last: int, e: Fraction) -> str:
    total = sum(rs)
    sum_expr = " + ".join(str(r) for r in rs)
    i = Fraction(v_last, rs[-1])
    return (f"① 末端抵抗 R{len(rs)}={rs[-1]}Ω の電流: I = V/R{len(rs)} = "
            f"{v_last}/{rs[-1]} = {frac_str(i)} A\n"
            f"② 直列なので全体に同じ電流。合成抵抗 ΣR = {sum_expr} = {total} Ω\n"
            f"③ 電源電圧: E = I × ΣR = {frac_str(i)} × {total} = {frac_str(e)} [V]")


def gen_divider(k: int, ctr: int) -> QAPair | None:
    rs = random.sample(NICE_R, k)
    current = random.choice(NICE_I)
    circuit, v_last = _series_divider(rs, current)

    sol = elphysics.solve_dc_analysis(circuit)
    if sol.get("status") != "ok" or "E1" not in sol.get("found", {}):
        return None
    e = sol["found"]["E1"]               # Z3 が逆算した厳密解

    comp_list = "、".join(f"R{i+1}={r}Ω" for i, r in enumerate(rs))
    q = (f"{comp_list} を直列に接続し、電源電圧 E[V] を加えたところ、"
         f"末端の R{k} の両端電圧が {v_last} V でした。電源電圧 E を求めてください。")
    a = f"電源電圧は {frac_str(e)} V です。"
    return QAPair(
        id=f"dc_e_divider{k}_{ctr:04d}", type=f"divider{k}",
        difficulty={2: "easy", 3: "medium"}[k],
        question=q, answer=a, reasoning=_reasoning(rs, v_last, e),
        circuit=circuit, e_exact=frac_str(e), e_float=float(e),
    )


def generate(n: int = 100, seed: int = 42) -> list[QAPair]:
    random.seed(seed)
    half = max(1, n // 2)
    schedule = [2] * half + [3] * (n - half)
    random.shuffle(schedule)

    ctrs = {2: 1, 3: 1}
    out: list[QAPair] = []
    _header()
    for i, k in enumerate(schedule):
        _progress(i + 1, len(schedule))
        qa = gen_divider(k, ctrs[k])
        if qa:
            out.append(qa)
            ctrs[k] += 1
    print()
    return out


W = 60

def _header() -> None:
    print(f"\n{B}{C}{'═' * W}{RST}")
    print(f"{B}{C}  elphysics × QA Generator  —  Type 2: 電源解析（逆問題）{RST}")
    print(f"{B}{C}{'═' * W}{RST}")
    print(f"  {DIM}Z3 SMT で未知電源 E を逆算し、証明済み QA を生成しています...{RST}\n")

def _progress(i: int, total: int, width: int = 42) -> None:
    filled = int(width * i / total)
    bar = f"{C}{'█' * filled}{DIM}{'░' * (width - filled)}{RST}"
    print(f"\r  [{bar}] {i}/{total} ({G}{int(100*i/total)}%{RST})", end="", flush=True)

def print_preview(pairs: list[QAPair], n: int = 3) -> None:
    print(f"\n{B}{'─' * W}{RST}\n{B}  サンプルプレビュー ({n} 件){RST}\n{B}{'─' * W}{RST}")
    for qa in random.sample(pairs, min(n, len(pairs))):
        print(f"\n{C}{B}[{qa.id}]{RST}  難易度: {Y}{qa.difficulty}{RST}  種類: {DIM}{qa.type}{RST}")
        print(f"  {B}Q:{RST} {qa.question}")
        print(f"  {G}A:{RST} {qa.answer}")
        for line in qa.reasoning.splitlines():
            print(f"  {DIM}    {line}{RST}")

def print_stats(pairs: list[QAPair]) -> None:
    by_type = Counter(p.type for p in pairs)
    print(f"\n{B}{'─' * W}{RST}\n{B}  統計{RST}\n{'─' * W}")
    print(f"  総数: {B}{G}{len(pairs)}{RST} 件   {DIM}verified by elphysics / Z3 SMT{RST}\n")
    for t in ("divider2", "divider3"):
        cnt = by_type.get(t, 0)
        print(f"    {t:<10} {G}{cnt:>4}{RST} 件  {C}{'▪' * cnt}{RST}")


def main() -> None:
    ap = argparse.ArgumentParser(description="elphysics QA Dataset Generator — Type 2")
    ap.add_argument("--count",   type=int, default=100,              metavar="N")
    ap.add_argument("--output",  type=str, default="qa_type2.jsonl", metavar="FILE")
    ap.add_argument("--seed",    type=int, default=42,               metavar="S")
    ap.add_argument("--preview", type=int, default=3,                metavar="K")
    args = ap.parse_args()

    pairs = generate(n=args.count, seed=args.seed)
    print_preview(pairs, n=args.preview)
    print_stats(pairs)

    out = Path(args.output)
    with open(out, "w", encoding="utf-8") as f:
        for qa in pairs:
            f.write(json.dumps(asdict(qa), ensure_ascii=False) + "\n")
    print(f"\n  {G}✓{RST} 保存完了: {B}{out.resolve()}{RST}  ({G}{len(pairs)}{RST} 件)\n")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
