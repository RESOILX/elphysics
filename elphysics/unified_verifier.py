"""
unified_verifier.py — 回路検証オーケストレーター (DC)

回路 JSON を受け取り、型に応じた検証ルールを実行して
ValidationResult のリストを返す。

【対応する回路型と適用ルール】
  dc  (dc_circuit.json)
    → rules/dc_analysis : キルヒホッフ・オームの法則・電流定格チェック

【設計方針】
  - 回路型は JSON のトップレベルキーで自動判別する
  - 各ルールは ValidationResult 形式の dict リストを返す
  - このモジュールが全ルールを統一 ValidationResult に変換して返す
"""
from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from elphysics.schema_validation import validate_circuit

_HERE = Path(__file__).parent


# ── ValidationResult ──────────────────────────────────────────────────
# 全ルール共通の検証結果型。各 rules/*.run() は dict を返し、
# verify() がこの dataclass に変換して呼び出し側へ渡す。

@dataclass
class ValidationResult:
    rule: str
    target: str
    status: Literal["ok", "warning", "error"]
    message: str
    severity: Literal["none", "low", "medium", "high"]
    reason: str
    suggested_patch: dict | list | None = field(default=None, repr=False)

    def __str__(self) -> str:
        icon = {"error": "❌", "warning": "⚠️", "ok": "✅"}.get(self.status, "?")
        return f"{icon} [{self.rule} → {self.target}] {self.message}"


# ── 回路型の判別 ──────────────────────────────────────────────────────
# トップレベルキー → 回路型名 の単一の対応表 (Single Source of Truth)。
# detect_circuit_type / KNOWN_KEYS はこれを参照する。
_CIRCUIT_REGISTRY: dict[str, str] = {
    "dc_circuit": "dc",   # 直流回路 (合成抵抗 / 電源解析)
}

# 認識できるトップレベルキー一覧 (api の構造バリデーション等で参照)
KNOWN_KEYS = frozenset(_CIRCUIT_REGISTRY)


def detect_circuit_type(circuit: dict) -> str:
    """
    JSON のトップレベルキーから回路型を判別する。
    対応表 (_CIRCUIT_REGISTRY) に無いキーしか持たない場合は "unknown"。
    """
    for key, type_name in _CIRCUIT_REGISTRY.items():
        if key in circuit:
            return type_name
    return "unknown"


# ── ルール定義テーブル ────────────────────────────────────────────────
# 回路型 → 適用するルールモジュール名のリスト

RULES_FOR_TYPE: dict[str, list[str]] = {
    "dc": [
        "rules.dc_analysis",
    ],
}


# ── 統合検証エントリポイント ──────────────────────────────────────────

def verify(
    circuit: dict,
    *,
    extra_rules: list[str] | None = None,
) -> list[ValidationResult]:
    """
    回路 JSON を受け取り、型に応じた全ルールを実行して
    ValidationResult のリストを返す。

    Args:
        circuit: トップレベルキー付きの回路 JSON。
        extra_rules: 追加で実行するルールモジュール名のリスト
            (例: ["rules.my_rule"])。利用者が自前ルールを差し込む用途。

    回路型が unknown の場合はエラーの ValidationResult を1件返す。
    """
    
    # ── Step 0: スキーマ検証 (形が正しいか) ─────────────────────────
    schema_errors = validate_circuit(circuit)
    if schema_errors:
        return [ValidationResult(
            rule="schema_validation",
            target="circuit",
            status="error",
            message=msg,
            severity="high",
            reason="schema_violation",
        ) for msg in schema_errors]
    
    # ── Step 1: 回路型を判別 ─────────────────────────────────────────
    circuit_type = detect_circuit_type(circuit)

    if circuit_type == "unknown":
        return [ValidationResult(
            rule="unified_verifier",
            target="circuit",
            status="error",
            message="未知の回路型: トップレベルキーが dc_circuit ではありません",
            severity="high",
            reason="unknown_circuit_type",
        )]

    # ── Step 2: 対応するルールモジュールを読み込んで実行 ────────────
    rule_names = list(RULES_FOR_TYPE.get(circuit_type, []))
    if extra_rules:
        rule_names += list(extra_rules)
    all_results: list[ValidationResult] = []

    for rule_name in rule_names:
        try:
            # モジュールを動的にインポートして run() を呼ぶ
            mod = importlib.import_module(f"elphysics.{rule_name}")
            raw_results = mod.run(circuit)
            
            # ── Step 3: dict → ValidationResult に変換 ─────────────────
            for r in raw_results:
                all_results.append(ValidationResult(
                    rule=r["rule"],
                    target=r["target"],
                    status=r["status"],
                    message=r["message"],
                    severity=r["severity"],
                    reason=r["reason"],
                    suggested_patch=r.get("suggested_patch"),
                ))
        except Exception as e:
            all_results.append(ValidationResult(
                rule=rule_name,
                target="rule_error",
                status="error",
                message=f"ルール実行中に例外が発生: {e}",
                severity="high",
                reason="rule_exception",
            ))
            continue

    return all_results


# ── サマリー集計 ──────────────────────────────────────────────────────

def summary(results: list[ValidationResult]) -> dict:
    """検証結果の件数を集計して返す。"""
    return {
        "ok":      sum(1 for r in results if r.status == "ok"),
        "warning": sum(1 for r in results if r.status == "warning"),
        "error":   sum(1 for r in results if r.status == "error"),
    }


# ── ファイルから読んで検証するヘルパー ───────────────────────────────

def verify_file(path: str | Path) -> tuple[str, list[ValidationResult]]:
    """JSON ファイルパスを受け取り、verify() を実行して (回路型, 結果リスト) を返す。

    ファイルが存在しない場合や JSON として不正な場合は、
    例外を投げずに ("unknown", error の ValidationResult 1件) を返す。
    """
    path = Path(path)

    if not path.exists():
        return "unknown", [ValidationResult(
            rule="unified_verifier",
            target=str(path),
            status="error",
            message=f"ファイルが見つかりません: {path}",
            severity="high",
            reason="file_not_found",
        )]

    try:
        with open(path, encoding="utf-8") as f:
            circuit = json.load(f)
    except json.JSONDecodeError as e:
        return "unknown", [ValidationResult(
            rule="unified_verifier",
            target=str(path),
            status="error",
            message=f"JSON の形式が不正です: {e}",
            severity="high",
            reason="invalid_json",
        )]

    circuit_type = detect_circuit_type(circuit)
    return circuit_type, verify(circuit)

# ── CLI エントリポイント ──────────────────────────────────────────────

def cli_main(argv: list[str] | None = None) -> int:
    """
    コマンドラインから検証を実行する。
    `elphysics-verify [file.json ...]` と `python -m elphysics.unified_verifier`。
    引数を省略した場合は同梱サンプルをすべて検証する。
    error が1件以上あれば終了コード 1 を返す (CI で使えるように)。
    """
    args = list(sys.argv[1:] if argv is None else argv)

    targets = args if args else [
        str(_HERE / "examples" / "dc_circuit.json"),
        str(_HERE / "examples" / "dc_circuit_analysis.json"),
    ]

    total_errors = 0
    for target in targets:
        path = Path(target)
        if not path.exists():
            print(f"⚠️  ファイルが見つかりません: {path}")
            continue

        print(f"\n{'='*60}")
        print(f"検証対象: {path.name}")
        print(f"{'='*60}")

        circuit_type, results = verify_file(path)
        s = summary(results)
        total_errors += s["error"]
        print(f"回路型: {circuit_type}")
        print(f"結果: ✅ {s['ok']}件 OK  ⚠️  {s['warning']}件 WARNING  ❌ {s['error']}件 ERROR")
        print()

        for r in results:
            if r.status != "ok":
                print(f"  {r}")
                if r.suggested_patch:
                    if isinstance(r.suggested_patch, list):
                        for p in r.suggested_patch:
                            print(f"    💡 {json.dumps(p, ensure_ascii=False)}")
                    else:
                        print(f"    💡 {json.dumps(r.suggested_patch, ensure_ascii=False)}")

    return 1 if total_errors else 0


if __name__ == "__main__":
    raise SystemExit(cli_main())