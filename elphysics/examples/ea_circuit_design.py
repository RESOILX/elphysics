#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ea_circuit_design.py — elphysics × 進化的アルゴリズム（目標合成抵抗の回路設計）

「合成抵抗が目標値になる回路」を遺伝的アルゴリズムで設計する。
適応度（fitness）は elphysics.solve_dc() の厳密解（Fraction）から計算するため、
浮動小数ノイズがなく再現性のある進化が回る。

  ・適応度 = -|R_ab − 目標|（厳密・完全一致で 0）
  ・実行可能性（SAT）を満たさない個体はハード制約で淘汰
  ・elphysics が「適応度 + 実行可能性オラクル」として機能する

依存は Python 標準ライブラリのみ（GA を素の Python で実装）。

使用方法:
  python elphysics/examples/ea_circuit_design.py
  python elphysics/examples/ea_circuit_design.py --generations 60 --pop 80
"""
from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from fractions import Fraction

import elphysics
from elphysics import circuits as cir

G = "\033[92m"; Y = "\033[93m"; C = "\033[96m"
B = "\033[1m";  RST = "\033[0m"; DIM = "\033[2m"; RED = "\033[91m"

NICE_R = [2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 24, 30]
GENOME_LEN = 5   # 遺伝子の抵抗値スロット数（探索空間を大きく保つ）

# テンプレート名 → (使う抵抗の本数, values からの回路生成関数)。
# 最大 5 素子まで許すことで探索空間を ~10^6 規模にし、進化が実際に効くようにする。
TEMPLATES = {
    "series3":  (3, lambda v: cir.series(v[:3])),
    "series4":  (4, lambda v: cir.series(v[:4])),
    "series5":  (5, lambda v: cir.series(v[:5])),
    "parallel3":(3, lambda v: cir.parallel(v[:3])),
    "parallel4":(4, lambda v: cir.parallel(v[:4])),
    "mixed_ps": (3, lambda v: cir.mixed_ps(v[:2], v[2])),
    "mixed_sp": (3, lambda v: cir.mixed_sp(v[0], v[1:3])),
    "mixed_pp": (4, lambda v: cir.mixed_pp(v[:2], v[2:4])),
}
TEMPLATE_NAMES = list(TEMPLATES)


@dataclass
class Genome:
    template: str
    values: list[int]   # 長さ GENOME_LEN。テンプレートの本数だけ使う

    def circuit(self) -> dict:
        _, build = TEMPLATES[self.template]
        return build(self.values)


def random_genome() -> Genome:
    return Genome(random.choice(TEMPLATE_NAMES),
                  [random.choice(NICE_R) for _ in range(GENOME_LEN)])


def r_ab(genome: Genome) -> Fraction | None:
    """厳密な合成抵抗。解けない（実行不能）なら None。"""
    sol = elphysics.solve_dc(genome.circuit())
    return sol["R_ab"] if sol.get("status") == "ok" else None


def fitness(genome: Genome, target: Fraction) -> float:
    r = r_ab(genome)
    if r is None or r <= 0:
        return float("-inf")            # 実行不能はハード淘汰
    return -abs(float(r) - float(target))


# ── 遺伝操作 ──────────────────────────────────────────────────────────
def crossover(a: Genome, b: Genome) -> Genome:
    template = random.choice([a.template, b.template])
    values = [random.choice([a.values[i], b.values[i]]) for i in range(GENOME_LEN)]
    return Genome(template, values)


def mutate(g: Genome, rate: float) -> Genome:
    template = g.template
    values = list(g.values)
    if random.random() < rate:
        template = random.choice(TEMPLATE_NAMES)
    for i in range(GENOME_LEN):
        if random.random() < rate:
            values[i] = random.choice(NICE_R)
    return Genome(template, values)


def tournament(pop: list[Genome], scored: dict[int, float], k: int = 3) -> Genome:
    picks = random.sample(range(len(pop)), k)
    best = max(picks, key=lambda i: scored[i])
    return pop[best]


def evolve(target: Fraction, pop_size: int, generations: int,
           mut_rate: float = 0.25) -> Genome:
    # 注意: ここでは random.seed() しない。呼び出し側で 1 度だけ種を設定することで、
    # 目標を作った seed_genome が初期集団にそのまま紛れ込む（タダ当たり）のを防ぐ。
    pop = [random_genome() for _ in range(pop_size)]
    best_overall: Genome | None = None
    best_fit = float("-inf")

    _header(target)
    for gen in range(generations):
        scored = {i: fitness(g, target) for i, g in enumerate(pop)}
        gi = max(scored, key=scored.get)
        if scored[gi] > best_fit:
            best_fit, best_overall = scored[gi], pop[gi]

        _report(gen, generations, best_overall, best_fit, target)
        if best_fit == 0.0:             # 厳密一致に到達
            break

        # エリート保存 + 選択・交叉・突然変異
        elite = pop[gi]
        nxt = [elite]
        while len(nxt) < pop_size:
            child = crossover(tournament(pop, scored), tournament(pop, scored))
            nxt.append(mutate(child, mut_rate))
        pop = nxt

    print()
    return best_overall


# ── 表示 ──────────────────────────────────────────────────────────────
W = 62

def _header(target: Fraction) -> None:
    print(f"\n{B}{C}{'═' * W}{RST}")
    print(f"{B}{C}  elphysics × 進化的アルゴリズム  —  目標合成抵抗の回路設計{RST}")
    print(f"{B}{C}{'═' * W}{RST}")
    tgt = target.numerator if target.denominator == 1 else f"{target.numerator}/{target.denominator}"
    print(f"  目標: R_ab = {B}{Y}{tgt} Ω{RST}  ({float(target):.4f} Ω)")
    print(f"  {DIM}適応度は Z3 SMT の厳密解から計算（浮動小数ノイズなし）{RST}\n")

def _report(gen: int, total: int, best: Genome, fit: float, target: Fraction) -> None:
    r = r_ab(best)
    rs = "?" if r is None else (str(r.numerator) if r.denominator == 1
                                else f"{r.numerator}/{r.denominator}")
    hit = f"{G}★ 厳密一致{RST}" if fit == 0.0 else f"誤差 {abs(fit):.4f}"
    print(f"\r  世代 {gen+1:>3}/{total}  最良 R_ab = {C}{rs:>7}{RST} Ω   {hit}      ",
          end="", flush=True)


def _describe(g: Genome) -> str:
    arity, _ = TEMPLATES[g.template]
    v = g.values[:arity]
    if g.template.startswith("series"):
        return "直列 " + " + ".join(f"{x}Ω" for x in v)
    if g.template.startswith("parallel"):
        return "並列 " + " ∥ ".join(f"{x}Ω" for x in v)
    if g.template == "mixed_ps":
        return f"({v[0]}Ω ∥ {v[1]}Ω) 直列 {v[2]}Ω"
    if g.template == "mixed_sp":
        return f"{v[0]}Ω 直列 ({v[1]}Ω ∥ {v[2]}Ω)"
    if g.template == "mixed_pp":
        return f"({v[0]}Ω ∥ {v[1]}Ω) 直列 ({v[2]}Ω ∥ {v[3]}Ω)"
    return g.template


def main() -> None:
    ap = argparse.ArgumentParser(description="elphysics EA — circuit design")
    ap.add_argument("--generations", type=int, default=40, metavar="N")
    ap.add_argument("--pop",         type=int, default=60, metavar="N")
    ap.add_argument("--seed",        type=int, default=0,  metavar="S")
    args = ap.parse_args()

    # 目標値はランダムな回路を 1 つ作って厳密解から決める（到達可能性を保証）
    random.seed(args.seed)
    seed_genome = random_genome()
    while r_ab(seed_genome) is None:
        seed_genome = random_genome()
    target = r_ab(seed_genome)

    best = evolve(target, pop_size=args.pop, generations=args.generations)

    r = r_ab(best)
    print(f"\n  {B}発見した回路:{RST} {C}{_describe(best)}{RST}")
    print(f"  合成抵抗 R_ab = {G}{r}{RST} Ω  (目標 {target} Ω)")

    # elphysics で最終検証（設計結果が物理的に妥当であることの形式証明）
    results = elphysics.verify(best.circuit())
    ok = all(r.status != "error" for r in results)
    exact = (r == target)
    print(f"  elphysics 検証: {G if ok else RED}{'PASS' if ok else 'FAIL'}{RST}"
          f"   厳密一致: {G+'YES' if exact else Y+'NO'}{RST}\n")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
