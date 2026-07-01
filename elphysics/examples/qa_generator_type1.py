#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qa_generator_type1.py — elphysics × AI Dataset Generator (Type 1: 合成抵抗計算)

Z3 SMT ソルバーで証明済みの合成抵抗 QA ペアを自動生成する。

使用方法:
  python elphysics/examples/qa_generator_type1.py
  python elphysics/examples/qa_generator_type1.py --count 500 --output big.jsonl
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

# ── ANSI ──────────────────────────────────────────────────────────────
G = "\033[92m"; Y = "\033[93m"; C = "\033[96m"
B = "\033[1m";  RST = "\033[0m"; DIM = "\033[2m"
RED = "\033[91m"

NICE_R = [2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 24, 30]


# ── データモデル ──────────────────────────────────────────────────────
@dataclass
class QAPair:
    id: str
    type: str        # series / parallel / mixed_ps / mixed_sp / mixed_pp
    difficulty: str  # easy / medium / hard
    question: str
    answer: str
    reasoning: str
    circuit: dict
    r_ab_exact: str  # 有理数表現 e.g. "3/2"
    r_ab_float: float
    verified_by: str = "elphysics/z3-smt"


# ── ユーティリティ ────────────────────────────────────────────────────
def frac_str(f: Fraction) -> str:
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"

def r_label(f: Fraction) -> str:
    if f.denominator == 1:
        return f"{f.numerator} Ω"
    return f"{frac_str(f)} Ω（≈ {float(f):.2f} Ω）"

def comp_list(ids_rs: list[tuple[str, int]]) -> str:
    return "、".join(f"{i}={r}Ω" for i, r in ids_rs)

def par_inv(rs: list[int]) -> Fraction:
    return sum(Fraction(1, r) for r in rs)


# ── 回路生成 ──────────────────────────────────────────────────────────
def _circuit(nodes: list[str], comps: list[dict]) -> dict:
    return {"dc_circuit": {
        "nodes": nodes, "components": comps,
        "terminals": {"pos": nodes[0], "neg": nodes[-1]},
    }}

def make_series(rs: list[int]) -> dict:
    nodes = ["pos"] + [f"n{i}" for i in range(len(rs) - 1)] + ["neg"]
    comps = [{"id": f"R{i+1}", "type": "resistor", "resistance_ohm": r,
               "from": nodes[i], "to": nodes[i+1]} for i, r in enumerate(rs)]
    return _circuit(nodes, comps)

def make_parallel(rs: list[int]) -> dict:
    comps = [{"id": f"R{i+1}", "type": "resistor", "resistance_ohm": r,
               "from": "pos", "to": "neg"} for i, r in enumerate(rs)]
    return _circuit(["pos", "neg"], comps)

def make_mixed_ps(r_par: list[int], r_ser: int) -> dict:
    """並列グループ → 直列1本"""
    comps = [{"id": f"R{i+1}", "type": "resistor", "resistance_ohm": r,
               "from": "pos", "to": "mid"} for i, r in enumerate(r_par)]
    comps.append({"id": f"R{len(r_par)+1}", "type": "resistor",
                  "resistance_ohm": r_ser, "from": "mid", "to": "neg"})
    return _circuit(["pos", "mid", "neg"], comps)

def make_mixed_sp(r_ser: int, r_par: list[int]) -> dict:
    """直列1本 → 並列グループ"""
    comps = [{"id": "R1", "type": "resistor", "resistance_ohm": r_ser,
               "from": "pos", "to": "mid"}]
    for i, r in enumerate(r_par):
        comps.append({"id": f"R{i+2}", "type": "resistor", "resistance_ohm": r,
                      "from": "mid", "to": "neg"})
    return _circuit(["pos", "mid", "neg"], comps)

def make_mixed_pp(r_par1: list[int], r_par2: list[int]) -> dict:
    """並列グループ → 並列グループ（直列）"""
    comps = []
    for i, r in enumerate(r_par1):
        comps.append({"id": f"R{i+1}", "type": "resistor", "resistance_ohm": r,
                      "from": "pos", "to": "mid"})
    for i, r in enumerate(r_par2):
        comps.append({"id": f"R{len(r_par1)+i+1}", "type": "resistor",
                      "resistance_ohm": r, "from": "mid", "to": "neg"})
    return _circuit(["pos", "mid", "neg"], comps)


# ── 推論テキスト ──────────────────────────────────────────────────────
def reasoning_series(rs: list[int], r_ab: Fraction) -> str:
    expr = " + ".join(str(r) for r in rs)
    return f"R = {expr} = {frac_str(r_ab)} [Ω]"

def reasoning_parallel(rs: list[int], r_ab: Fraction) -> str:
    inv = par_inv(rs)
    terms = " + ".join(f"1/{r}" for r in rs)
    return (f"1/R = {terms} = {inv}\n"
            f"R = 1 / ({inv}) = {frac_str(r_ab)} [Ω]")

def reasoning_ps(r_par: list[int], r_ser: int, r_ab: Fraction) -> str:
    rp = Fraction(1) / par_inv(r_par)
    inv_expr = " + ".join(f"1/{r}" for r in r_par)
    return (f"① 並列部分: 1/R_並 = {inv_expr} = {par_inv(r_par)} → R_並 = {frac_str(rp)} Ω\n"
            f"② 直列合計: R = {frac_str(rp)} + {r_ser} = {frac_str(r_ab)} [Ω]")

def reasoning_sp(r_ser: int, r_par: list[int], r_ab: Fraction) -> str:
    rp = Fraction(1) / par_inv(r_par)
    inv_expr = " + ".join(f"1/{r}" for r in r_par)
    return (f"① 並列部分: 1/R_並 = {inv_expr} = {par_inv(r_par)} → R_並 = {frac_str(rp)} Ω\n"
            f"② 直列合計: R = {r_ser} + {frac_str(rp)} = {frac_str(r_ab)} [Ω]")

def reasoning_pp(r1: list[int], r2: list[int], r_ab: Fraction) -> str:
    rp1 = Fraction(1) / par_inv(r1)
    rp2 = Fraction(1) / par_inv(r2)
    i1 = " + ".join(f"1/{r}" for r in r1)
    i2 = " + ".join(f"1/{r}" for r in r2)
    return (f"① グループA: 1/R_A = {i1} → R_A = {frac_str(rp1)} Ω\n"
            f"② グループB: 1/R_B = {i2} → R_B = {frac_str(rp2)} Ω\n"
            f"③ 直列合計:  R = {frac_str(rp1)} + {frac_str(rp2)} = {frac_str(r_ab)} [Ω]")


# ── 問題文・回答テンプレート ──────────────────────────────────────────
_Q_SERIES = [
    lambda cl: f"{cl}が直列に接続されています。合成抵抗を求めてください。",
    lambda cl: f"次の直列回路の端子間合成抵抗[Ω]を求めよ。{cl}",
    lambda cl: f"直列回路: {cl}。端子間の合成抵抗はいくらですか？",
    lambda cl: f"{cl}を直列につないだとき、合成抵抗を求めてください。",
]
_Q_PARALLEL = [
    lambda cl: f"{cl}が並列に接続されています。合成抵抗を求めてください。",
    lambda cl: f"次の並列回路の端子間合成抵抗[Ω]を求めよ。{cl}",
    lambda cl: f"並列回路: {cl}。合成抵抗はいくつですか？",
    lambda cl: f"{cl}を並列接続したとき、端子間の合成抵抗を求めてください。",
]
_Q_PS = [
    lambda pc, si, sr: f"{pc}の並列回路に直列に{si}={sr}Ωを接続しました。合成抵抗を求めてください。",
    lambda pc, si, sr: f"並列接続した{pc}に{si}={sr}Ωを直列につなぎました。端子間合成抵抗はいくらですか？",
]
_Q_SP = [
    lambda si, sr, pc: f"{si}={sr}Ωの後に{pc}の並列回路を直列接続しました。合成抵抗を求めてください。",
    lambda si, sr, pc: f"{si}={sr}Ωと{pc}の並列グループを直列につないだとき、合成抵抗[Ω]を求めよ。",
]
_Q_PP = [
    lambda p1, p2: f"並列グループA（{p1}）と並列グループB（{p2}）を直列に接続した回路の合成抵抗を求めてください。",
    lambda p1, p2: f"2つの並列回路を直列につなぎました。\n  グループ1: {p1}\n  グループ2: {p2}\n合成抵抗を求めてください。",
]
_A = [
    lambda r: f"合成抵抗は {r} です。",
    lambda r: f"端子間の合成抵抗は {r} になります。",
    lambda r: f"この回路の合成抵抗は {r} です。",
    lambda r: f"合成抵抗を求めると {r} となります。",
]

def _q(templates, *args):
    return random.choice(templates)(*args)

def _a(r_ab: Fraction) -> str:
    return random.choice(_A)(r_label(r_ab))


# ── 個別生成関数 ──────────────────────────────────────────────────────
def _solve(circuit: dict) -> Fraction | None:
    sol = elphysics.solve_dc(circuit)
    return sol["R_ab"] if sol.get("status") == "ok" else None

def gen_series(n: int, ctr: int) -> QAPair | None:
    rs = random.sample(NICE_R, n)
    cir = make_series(rs)
    r_ab = _solve(cir)
    if r_ab is None:
        return None
    ids_rs = [(f"R{i+1}", r) for i, r in enumerate(rs)]
    return QAPair(
        id=f"dc_r_series_{ctr:04d}", type="series",
        difficulty={2: "easy", 3: "medium", 4: "hard"}[n],
        question=_q(_Q_SERIES, comp_list(ids_rs)),
        answer=_a(r_ab), reasoning=reasoning_series(rs, r_ab),
        circuit=cir, r_ab_exact=frac_str(r_ab), r_ab_float=float(r_ab),
    )

def gen_parallel(n: int, ctr: int) -> QAPair | None:
    rs = random.sample(NICE_R, n)
    cir = make_parallel(rs)
    r_ab = _solve(cir)
    if r_ab is None:
        return None
    ids_rs = [(f"R{i+1}", r) for i, r in enumerate(rs)]
    return QAPair(
        id=f"dc_r_parallel_{ctr:04d}", type="parallel",
        difficulty={2: "easy", 3: "medium", 4: "hard"}[n],
        question=_q(_Q_PARALLEL, comp_list(ids_rs)),
        answer=_a(r_ab), reasoning=reasoning_parallel(rs, r_ab),
        circuit=cir, r_ab_exact=frac_str(r_ab), r_ab_float=float(r_ab),
    )

def gen_mixed_ps(ctr: int) -> QAPair | None:
    r_par = random.sample(NICE_R, 2)
    r_ser = random.choice([r for r in NICE_R if r not in r_par])
    cir = make_mixed_ps(r_par, r_ser)
    r_ab = _solve(cir)
    if r_ab is None:
        return None
    par_ids = [(f"R{i+1}", r) for i, r in enumerate(r_par)]
    ser_id = f"R{len(r_par)+1}"
    return QAPair(
        id=f"dc_r_mixed_ps_{ctr:04d}", type="mixed_ps", difficulty="medium",
        question=_q(_Q_PS, comp_list(par_ids), ser_id, r_ser),
        answer=_a(r_ab), reasoning=reasoning_ps(r_par, r_ser, r_ab),
        circuit=cir, r_ab_exact=frac_str(r_ab), r_ab_float=float(r_ab),
    )

def gen_mixed_sp(ctr: int) -> QAPair | None:
    r_par = random.sample(NICE_R, 2)
    r_ser = random.choice([r for r in NICE_R if r not in r_par])
    cir = make_mixed_sp(r_ser, r_par)
    r_ab = _solve(cir)
    if r_ab is None:
        return None
    par_ids = [(f"R{i+2}", r) for i, r in enumerate(r_par)]
    return QAPair(
        id=f"dc_r_mixed_sp_{ctr:04d}", type="mixed_sp", difficulty="medium",
        question=_q(_Q_SP, "R1", r_ser, comp_list(par_ids)),
        answer=_a(r_ab), reasoning=reasoning_sp(r_ser, r_par, r_ab),
        circuit=cir, r_ab_exact=frac_str(r_ab), r_ab_float=float(r_ab),
    )

def gen_mixed_pp(ctr: int) -> QAPair | None:
    all_r = random.sample(NICE_R, 4)
    r1, r2 = all_r[:2], all_r[2:]
    cir = make_mixed_pp(r1, r2)
    r_ab = _solve(cir)
    if r_ab is None:
        return None
    p1_ids = [("R1", r1[0]), ("R2", r1[1])]
    p2_ids = [("R3", r2[0]), ("R4", r2[1])]
    return QAPair(
        id=f"dc_r_mixed_pp_{ctr:04d}", type="mixed_pp", difficulty="hard",
        question=_q(_Q_PP, comp_list(p1_ids), comp_list(p2_ids)),
        answer=_a(r_ab), reasoning=reasoning_pp(r1, r2, r_ab),
        circuit=cir, r_ab_exact=frac_str(r_ab), r_ab_float=float(r_ab),
    )


# ── スケジューラー ────────────────────────────────────────────────────
def generate(n: int = 100, seed: int = 42) -> list[QAPair]:
    random.seed(seed)
    per = max(1, n // 5)
    rem = n - per * 4
    schedule: list[tuple[str, int | None]] = (
        [("series",    2)] * per +
        [("series",    3)] * per +
        [("parallel",  2)] * per +
        [("parallel",  3)] * per +
        [("mixed_ps", None)] * (rem // 3) +
        [("mixed_sp", None)] * (rem // 3) +
        [("mixed_pp", None)] * (rem - 2 * (rem // 3))
    )
    random.shuffle(schedule)

    ctrs = {k: 1 for k in ("series", "parallel", "mixed_ps", "mixed_sp", "mixed_pp")}
    results: list[QAPair] = []

    _print_header()
    for i, (typ, arg) in enumerate(schedule):
        _progress(i + 1, len(schedule))
        qa: QAPair | None = None
        if   typ == "series":    qa = gen_series(arg, ctrs["series"])
        elif typ == "parallel":  qa = gen_parallel(arg, ctrs["parallel"])
        elif typ == "mixed_ps":  qa = gen_mixed_ps(ctrs["mixed_ps"])
        elif typ == "mixed_sp":  qa = gen_mixed_sp(ctrs["mixed_sp"])
        elif typ == "mixed_pp":  qa = gen_mixed_pp(ctrs["mixed_pp"])
        if qa:
            results.append(qa)
            ctrs[typ] += 1

    print()
    return results


# ── 表示 ──────────────────────────────────────────────────────────────
W = 58

def _print_header() -> None:
    print(f"\n{B}{C}{'═' * W}{RST}")
    print(f"{B}{C}  elphysics × QA Generator  —  Type 1: 合成抵抗計算{RST}")
    print(f"{B}{C}{'═' * W}{RST}")
    print(f"  {DIM}Z3 SMT ソルバーで証明済みの QA ペアを生成しています...{RST}\n")

def _progress(i: int, total: int, width: int = 42) -> None:
    filled = int(width * i / total)
    bar = f"{C}{'█' * filled}{DIM}{'░' * (width - filled)}{RST}"
    pct = int(100 * i / total)
    print(f"\r  [{bar}] {i}/{total} ({G}{pct}%{RST})", end="", flush=True)

def print_preview(pairs: list[QAPair], n: int = 3) -> None:
    print(f"\n{B}{'─' * W}{RST}")
    print(f"{B}  サンプルプレビュー ({n} 件){RST}")
    print(f"{B}{'─' * W}{RST}")
    for qa in random.sample(pairs, min(n, len(pairs))):
        print(f"\n{C}{B}[{qa.id}]{RST}  難易度: {Y}{qa.difficulty}{RST}  種類: {DIM}{qa.type}{RST}")
        print(f"  {B}Q:{RST} {qa.question}")
        print(f"  {G}A:{RST} {qa.answer}")
        for line in qa.reasoning.splitlines():
            print(f"  {DIM}    {line}{RST}")

def print_stats(pairs: list[QAPair]) -> None:
    by_type = Counter(p.type for p in pairs)
    by_diff = Counter(p.difficulty for p in pairs)
    print(f"\n{B}{'─' * W}{RST}")
    print(f"{B}  統計{RST}")
    print(f"{'─' * W}")
    print(f"  総数: {B}{G}{len(pairs)}{RST} 件   {DIM}verified by elphysics / Z3 SMT{RST}\n")
    print(f"  種類別:")
    for t in ("series", "parallel", "mixed_ps", "mixed_sp", "mixed_pp"):
        cnt = by_type.get(t, 0)
        print(f"    {t:<12} {G}{cnt:>4}{RST} 件  {C}{'▪' * cnt}{RST}")
    print(f"\n  難易度別:")
    for d, col in [("easy", G), ("medium", Y), ("hard", RED)]:
        cnt = by_diff.get(d, 0)
        print(f"    {d:<10} {col}{cnt:>4}{RST} 件  {col}{'▪' * cnt}{RST}")


# ── エントリポイント ──────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="elphysics QA Dataset Generator — Type 1")
    ap.add_argument("--count",   type=int, default=100,              metavar="N")
    ap.add_argument("--output",  type=str, default="qa_type1.jsonl", metavar="FILE")
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
