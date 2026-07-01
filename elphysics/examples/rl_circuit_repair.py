#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rl_circuit_repair.py — elphysics × 強化学習（検証可能報酬による回路修復）

過電流を起こしている壊れた回路を、エージェントが抵抗値を調整して修復する
最小の強化学習環境。報酬は elphysics.verify() の「形式的判定」から与えられる。

  ・報酬が Z3 SMT の形式証明に基づくため、reward hacking されない（RLVR）
  ・elphysics の suggested_patch がそのまま「エキスパートの模範解答」になり、
    学習した方策と比較できる

依存は Python 標準ライブラリのみ（Q 学習を素の Python で実装）。

使用方法:
  python elphysics/examples/rl_circuit_repair.py
  python elphysics/examples/rl_circuit_repair.py --episodes 400
"""
from __future__ import annotations

import argparse
import random
import sys

import elphysics
from elphysics import circuits as cir

G = "\033[92m"; Y = "\033[93m"; C = "\033[96m"
B = "\033[1m";  RST = "\033[0m"; DIM = "\033[2m"; RED = "\033[91m"

# 候補となる抵抗値（行動で選べる離散集合）
CANDIDATES = [2, 4, 6, 8, 10, 12, 15, 20]
SOURCE_V = 10          # 電源電圧 [V]
RATED_A = 1.0          # 修復対象抵抗の定格電流 [A]  → 安全には R ≥ V/定格 = 10Ω 必要


# ── 環境 ──────────────────────────────────────────────────────────────
class CircuitRepairEnv:
    """1 本の抵抗を調整して過電流を解消する修復 MDP。

    状態  : 現在の抵抗値のインデックス（CANDIDATES 上）
    行動  : 0=下げる / 1=そのまま / 2=上げる
    報酬  : elphysics.verify() の結果から算出（過電流なら罰、解消で報酬）
    終了  : 過電流が解消したとき、または最大ステップ到達
    """

    def __init__(self, start_idx: int = 0, max_steps: int = 12):
        self.max_steps = max_steps
        self.start_idx = start_idx
        self.reset()

    def _build(self, r_ohm: int) -> dict:
        return cir.analysis_circuit(
            [cir.voltage_source("E1", "gnd", "a", voltage_v=SOURCE_V),
             cir.resistor("Rt", r_ohm, "a", "gnd", rated_current_a=RATED_A)],
            ref_node="gnd",
        )

    def _feasible(self, r_ohm: int) -> bool:
        """過電流 (over_current) の指摘が無ければ実行可能。"""
        results = elphysics.verify(self._build(r_ohm))
        return not any(r.reason == "over_current" for r in results)

    def reset(self) -> int:
        self.idx = self.start_idx
        self.steps = 0
        return self.idx

    def step(self, action: int) -> tuple[int, float, bool]:
        self.steps += 1
        if action == 0:
            self.idx = max(0, self.idx - 1)
        elif action == 2:
            self.idx = min(len(CANDIDATES) - 1, self.idx + 1)
        # action == 1 は据え置き

        r = CANDIDATES[self.idx]
        feasible = self._feasible(r)
        done = feasible or self.steps >= self.max_steps

        if feasible:
            # 実行可能な中で「抵抗を上げすぎない」ほど高報酬 → 最小の実行可能値が最適
            reward = 10.0 - 0.2 * r
        else:
            reward = -1.0        # まだ過電流（ステップコスト）
        return self.idx, reward, done


# ── 表形式 Q 学習 ─────────────────────────────────────────────────────
def train(env: CircuitRepairEnv, episodes: int, seed: int = 0) -> dict:
    random.seed(seed)
    n_states, n_actions = len(CANDIDATES), 3
    Q = [[0.0] * n_actions for _ in range(n_states)]
    alpha, gamma = 0.5, 0.9
    returns: list[float] = []

    for ep in range(episodes):
        eps = max(0.05, 1.0 - ep / (episodes * 0.7))   # 徐々に探索を減らす
        s = env.reset()
        total = 0.0
        done = False
        while not done:
            a = random.randrange(n_actions) if random.random() < eps else _argmax(Q[s])
            s2, r, done = env.step(a)
            best_next = 0.0 if done else max(Q[s2])
            Q[s][a] += alpha * (r + gamma * best_next - Q[s][a])
            s, total = s2, total + r
        returns.append(total)
        if (ep + 1) % max(1, episodes // 10) == 0:
            avg = sum(returns[-max(1, episodes // 10):]) / max(1, episodes // 10)
            _progress(ep + 1, episodes, avg)
    print()
    return {"Q": Q, "returns": returns}


def _argmax(row: list[float]) -> int:
    best, bi = row[0], 0
    for i, v in enumerate(row):
        if v > best:
            best, bi = v, i
    return bi


def greedy_rollout(env: CircuitRepairEnv, Q: list[list[float]]) -> list[int]:
    s = env.reset()
    path = [CANDIDATES[s]]
    done = False
    while not done:
        s, _, done = env.step(_argmax(Q[s]))
        path.append(CANDIDATES[s])
    return path


# ── 表示 ──────────────────────────────────────────────────────────────
W = 62

def _progress(ep: int, total: int, avg: float) -> None:
    width = 34
    filled = int(width * ep / total)
    bar = f"{C}{'█' * filled}{DIM}{'░' * (width - filled)}{RST}"
    print(f"\r  学習 [{bar}] {ep}/{total}  直近平均リターン {G}{avg:+.2f}{RST}",
          end="", flush=True)

def _header() -> None:
    print(f"\n{B}{C}{'═' * W}{RST}")
    print(f"{B}{C}  elphysics × 強化学習  —  検証可能報酬による回路修復{RST}")
    print(f"{B}{C}{'═' * W}{RST}")
    print(f"  電源 {SOURCE_V}V / 定格 {RATED_A}A → 安全には R ≥ "
          f"{int(SOURCE_V/RATED_A)}Ω が必要")
    print(f"  {DIM}報酬は elphysics.verify() の形式判定から与えられる（RLVR）{RST}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="elphysics RL — circuit repair env")
    ap.add_argument("--episodes", type=int, default=300, metavar="N")
    ap.add_argument("--seed",     type=int, default=0,   metavar="S")
    args = ap.parse_args()

    _header()
    env = CircuitRepairEnv(start_idx=0)

    start_r = CANDIDATES[0]
    print(f"  初期状態: Rt = {RED}{start_r}Ω{RST}  "
          f"→ I = {SOURCE_V}/{start_r} = {RED}{SOURCE_V/start_r:.1f}A{RST} "
          f"({RED}過電流 {SOURCE_V/start_r/RATED_A:.0f}倍{RST})\n")

    out = train(env, episodes=args.episodes, seed=args.seed)

    path = greedy_rollout(env, out["Q"])
    print(f"\n  {B}学習後の貪欲方策の修復手順:{RST}")
    print("    " + f" {DIM}→{RST} ".join(f"{r}Ω" for r in path))
    solved = path[-1]
    print(f"  {G}✓{RST} 到達: Rt = {G}{solved}Ω{RST}  "
          f"→ I = {SOURCE_V/solved:.2f}A ≤ 定格 {RATED_A}A\n")

    # elphysics の suggested_patch（オラクルの模範解答）と比較
    broken = env._build(start_r)
    patch = next((r.suggested_patch for r in elphysics.verify(broken)
                  if r.reason == "over_current" and r.suggested_patch), None)
    if patch:
        oracle_r = patch["updates"]["resistance_ohm"]
        print(f"  {B}elphysics の suggested_patch（模範解答）:{RST} "
              f"Rt → {C}{oracle_r}Ω{RST}")
        verdict = "一致" if solved >= oracle_r else "未達"
        print(f"  学習方策の最小実行可能値 {solved}Ω vs オラクル {oracle_r}Ω "
              f"→ {G if solved >= oracle_r else Y}{verdict}{RST}\n")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
