"""
elphysics — 電気回路の形式検証エンジン (DC 回路)

回路設計 JSON を受け取り、物理法則 (キルヒホッフの電流則・オームの法則) に
照らして数学的に検証する。検証は決定論的で、同じ入力には必ず同じ結果を返す。

【対応回路型】
  - dc : 直流回路 (合成抵抗 / 電源解析)。Z3 SMT ソルバーで キルヒホッフ・オームの法則を
         同時に満たす解の存在を形式証明する。

【基本的な使い方】
  >>> import elphysics
  >>> circuit = {"dc_circuit": {...}}
  >>> results = elphysics.verify(circuit)
  >>> for r in results:
  ...     print(r.status, r.message)

  個別ソルバーを直接呼ぶこともできる:
  (回路の種類に応じて使い分ける必要がある点に注意):
  >>> elphysics.solve_dc(circuit)           # 合成抵抗モード (terminals 指定)
  >>> elphysics.solve_dc_analysis(circuit)  # 電源解析モード (ref_node 指定)
"""
from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 統合検証 (推奨エントリポイント) ──
from elphysics.unified_verifier import (
    ValidationResult,
    detect_circuit_type,
    summary,
    verify,
    verify_file,
)

# ── 入力スキーマ検証 ──
from elphysics.schema_validation import validate_circuit, is_valid, load_schema

# ── 個別ソルバー (低レベル API) ──
from elphysics.dc_solver import solve as solve_dc, solve_analysis as solve_dc_analysis

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # 統合検証
    "verify",
    "verify_file",
    "detect_circuit_type",
    "summary",
    "ValidationResult",
    # 入力スキーマ検証
    "validate_circuit",
    "is_valid",
    "load_schema",
    # 個別ソルバー
    "solve_dc",
    "solve_dc_analysis",
]
