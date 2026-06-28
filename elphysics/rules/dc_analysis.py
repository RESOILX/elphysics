"""
rules/dc_analysis.py — 直流回路の電気的検証ルール

dc_solver.py が Z3 で解いた結果を ValidationResult 形式に変換する。

【検証の流れ】
  1. dc_circuit.json の型を判別 (抵抗計算モード / 解析モード)
  2. dc_solver でノード電圧・枝電流を解く
  3. 解の存在確認 (UNSAT → 設計ミス検出)
  4. キルヒホッフ・オームの法則の充足を確認
  5. 各素子の定格電流チェック (各素子に rated_current_a が設定されている場合)
  6. 未知電源値の妥当性チェック (analysis モードのみ)

【設計哲学】
  dc_solver は Z3 SMT ソルバーを使っているため、
  SAT = 「キルヒホッフ・オームの法則を同時に満たす解が存在する」という形式証明になる。
  UNSAT = 回路の制約が矛盾 → 設計ミス。
"""
from __future__ import annotations

from elphysics.dc_solver import solve_auto, _is_analysis_mode

RULE_ID = "dc_analysis"


def run(circuit: dict) -> list[dict]:
    """
    dc_circuit キーを持つ JSON を受け取り、検証結果リストを返す。
    dc_circuit 以外のトップレベルキーの場合は空リストで無視する。
    """
    # ── 型チェック：このルールは dc_circuit 専用 ────────────────────
    if "dc_circuit" not in circuit:
        return []  # 対象外の回路型はスキップ

    # ── どちらのモードで解くか判別 ──────────────────────────────────
    # analysis モード: ref_node + voltage_source を含む回路 (E を求めるなど)
    # resistance モード: terminals を指定して合成抵抗を求める標準形式
    analysis_mode = _is_analysis_mode(circuit)

    try:
        result = solve_auto(circuit)
    except Exception as e:
        return [_r(
            status="error", severity="high",
            target="circuit",
            message=f"dc_solver 実行エラー: {e}",
            reason="solver_exception",
        )]

    results = []

    # ── 1. 解の存在確認 ─────────────────────────────────────────────
    if result["status"] == "timeout":
        return [_r(
            status="error", severity="high",
            target="circuit",
            message=result["message"],
            reason="solver_timeout",
        )]

    # Z3 が UNSAT を返した場合: キルヒホッフとオームの法則が同時に満たせない
    # → 回路トポロジーまたは制約の矛盾を意味する設計ミス
    if result["status"] == "unsat":
        results.append(_r(
            status="error", severity="high",
            target="circuit",
            message="Z3 が UNSAT: キルヒホッフ・オームの法則を同時に満たす解が存在しない（回路の矛盾）",
            reason="unsatisfiable_constraints",
        ))
        return results  # 以降のチェックは無意味なので即返す

    # 開放回路検出: 端子間に経路がない
    if result["status"] == "open":
        results.append(_r(
            status="error", severity="high",
            target="circuit",
            message="開放回路: 端子間に電流経路がありません",
            reason="open_circuit",
        ))
        return results

    # ── 2. キルヒホッフ・オームの法則の充足確認 (SAT = 形式的に OK) ──────────
    # Z3 が SAT を返した = 全制約を同時に満足する解が存在することの証明
    results.append(_r(
        status="ok", severity="none",
        target="circuit",
        message="キルヒホッフ・オームの法則を全ノードで同時充足 (Z3 SAT 証明済み)",
        reason="kcl_ohm_satisfied",
    ))

    # ── 3. 合成抵抗が正か (resistance モードのみ) ────────────────────
    # 合成抵抗が 0 = 端子間が短絡、負 = 有り得ない値
    if not analysis_mode and "R_ab" in result:
        r_ab = float(result["R_ab"])
        if result.get("shorted_terminals"):
            results.append(_r(
                status="error", severity="high",
                target="R_ab",
                message="端子間が直接短絡 → 合成抵抗 0Ω (過電流の危険)",
                reason="shorted_terminals",
            ))
        elif r_ab <= 0:
            results.append(_r(
                status="error", severity="high",
                target="R_ab",
                message=f"合成抵抗が正しくありません: {r_ab:.4f} Ω",
                reason="negative_resistance",
            ))
        else:
            results.append(_r(
                status="ok", severity="none",
                target="R_ab",
                message=f"合成抵抗 = {result['R_ab']} Ω ({r_ab:.4f} Ω) — 正常",
                reason="resistance_ok",
            ))

    # ── 4. 各素子の定格電流チェック ─────────────────────────────────
    # circuit JSON の各 component に "rated_current_a" が設定されていれば
    # 実際の電流と比較して過電流を検出する
    branch_currents = result.get("branch_currents", {})
    c = circuit["dc_circuit"]
    for comp in c.get("components", []):
        rated_a = comp.get("rated_current_a")  # 定格電流 (オプション)
        if rated_a is None:
            continue  # 定格未指定はチェックしない

        actual_a = abs(float(branch_currents.get(comp["id"], 0)))
        if actual_a > float(rated_a):
            # 定格超過 → ERROR + suggested_patch で抵抗値を増やす提案
            # 必要な最小抵抗 = 電圧降下 / 定格電流
            v_drop = abs(float(
                result["node_voltages"].get(comp["from"], 0) -
                result["node_voltages"].get(comp["to"], 0)
            )) if "node_voltages" in result else None
            patch = None
            if v_drop and comp.get("type") == "resistor":
                new_r = round(v_drop / float(rated_a), 2)
                patch = {
                    "action": "update_component",
                    "component_id": comp["id"],
                    "updates": {"resistance_ohm": new_r},
                }
            results.append(_r(
                status="error", severity="high",
                target=comp["id"],
                message=(f"{comp['id']} 電流 {actual_a:.4f} A > 定格 {rated_a} A "
                         f"({actual_a/float(rated_a)*100:.0f}% 過負荷)"),
                reason="over_current",
                suggested_patch=patch,
            ))
        else:
            # 定格内 → OK + 余裕率を表示
            margin = (1 - actual_a / float(rated_a)) * 100
            results.append(_r(
                status="ok", severity="none",
                target=comp["id"],
                message=(f"{comp['id']} 電流 {actual_a:.4f} A ≤ 定格 {rated_a} A "
                         f"(余裕 {margin:.0f}%)"),
                reason="current_ok",
            ))

    # ── 5. 未知電源値の妥当性チェック (analysis モードのみ) ──────────
    # "find" に指定した電源 (voltage_v: null) が解けたか確認する
    if analysis_mode and "found" in result:
        for vid, val in result["found"].items():
            v = float(val)
            if v <= 0:
                # 電源が負または 0 → 極性か回路トポロジーの問題
                results.append(_r(
                    status="warning", severity="medium",
                    target=vid,
                    message=f"{vid} の解が {v:.4f} V (負または 0) — 極性を確認してください",
                    reason="source_voltage_nonpositive",
                ))
            else:
                results.append(_r(
                    status="ok", severity="none",
                    target=vid,
                    message=f"{vid} = {val} V ({v:.4f} V) — 正常に解けました",
                    reason="source_found",
                ))

    return results


# ── ヘルパー ──────────────────────────────────────────────────────────

def _r(*, status, severity, target, message, reason, suggested_patch=None) -> dict:
    """ValidationResult 形式の dict を生成する。"""
    return {
        "rule": RULE_ID,
        "target": target,
        "status": status,
        "severity": severity,
        "message": message,
        "reason": reason,
        "suggested_patch": suggested_patch,
    }
