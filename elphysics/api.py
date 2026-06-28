"""
api.py — 検証エンジンの Web API (FastAPI)

【役割】
  unified_verifier.py の検証ロジックを HTTP サービスとして公開する。
  回路 JSON を POST すると、検証結果 (ValidationResult リスト) と
  サマリーが JSON で返ってくる。

【設計思想：検証は"決定論的な正解"】
  この API が返す検証結果は決定論的 (同じ入力 → 必ず同じ結果)。
  LLM は一切関与せず、検証エンジンは「正解を返す部品」に徹する。

  LLM 等で回路を生成・修正するシステムから利用する場合は、
  「生成は LLM、検証はこの決定論エンジン」と役割を分離する構成を想定している:
    ① 生成側が回路 JSON を組み立てる            ← 生成 = LLM (非決定的)
    ② この API /verify で検証し正誤を判定する    ← 検証 = 決定論的
    ③ error があれば suggested_patch を適用し再検証

【エンドポイント】
  GET  /health        : 死活監視
  GET  /circuit-types : 対応している回路型の一覧
  POST /verify        : 回路 JSON を検証して結果を返す (本体)

【起動方法】
  uvicorn elphysics.api:app --reload --port 8000
  または:
  python -m elphysics.api
"""
from __future__ import annotations
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator
from typing import Literal

from elphysics.unified_verifier import (
    detect_circuit_type,
    summary as build_summary,
    verify,
    RULES_FOR_TYPE,
    KNOWN_KEYS,
)
from elphysics.schema_validation import validate_circuit

app = FastAPI(
    title="elphysics 検証エンジン API",
    description="直流回路 (dc) の形式検証サービス (Z3 SMT で キルヒホッフ・オームの法則を検証)",
    version="1.0.0",
)


# ── リクエスト / レスポンスのスキーマ ─────────────────────────────────

# 認識できるトップレベルキー (回路型判別に使う)。
# 回路型の定義元は unified_verifier に一本化している (H-4)。
_KNOWN_KEYS = KNOWN_KEYS

# ── リソース枯渇 (DoS) 対策の上限 ──
# 巨大な回路は Z3 ソルバーのメモリ・CPU を食い尽くしうる。
# 公開エンドポイントでは現実的な回路規模に上限を設けて拒否する。
_MAX_COMPONENTS = 2000   # 素子数の上限
_MAX_NODES      = 2000   # ノード数の上限


class VerifyRequest(BaseModel):
    """
    検証リクエスト。circuit にトップレベルキー付きの回路 JSON をそのまま渡す。
    例: {"circuit": {"dc_circuit": {...}}}
    """
    circuit: dict

    @field_validator("circuit")
    @classmethod
    def _check_structure(cls, v: dict) -> dict:
        """
        入力の構造を検証する (壊れた JSON・過大な入力を 422 で弾くため)。
        - 空の dict は不可
        - 認識できるトップレベルキーが少なくとも1つ必要
        - そのキーの中身は dict であること
        - 素子数・ノード数が上限以内であること (DoS 対策)
        """
        if not v:
            raise ValueError("circuit が空です。回路定義を含めてください。")
        known = _KNOWN_KEYS & set(v.keys())
        if not known:
            raise ValueError(
                f"認識できる回路キーがありません。"
                f"次のいずれかを含めてください: {sorted(_KNOWN_KEYS)}"
            )
        for key in known:
            inner = v[key]
            if not isinstance(inner, dict):
                raise ValueError(f"'{key}' の値はオブジェクト (dict) である必要があります。")

            # ── サイズ上限チェック (リソース枯渇対策) ──
            comps = inner.get("components")
            if isinstance(comps, list) and len(comps) > _MAX_COMPONENTS:
                raise ValueError(
                    f"素子数が上限 {_MAX_COMPONENTS} を超えています "
                    f"({len(comps)} 個)。回路を分割してください。"
                )
            nodes = inner.get("nodes")
            if isinstance(nodes, list) and len(nodes) > _MAX_NODES:
                raise ValueError(
                    f"ノード数が上限 {_MAX_NODES} を超えています "
                    f"({len(nodes)} 個)。回路を分割してください。"
                )
        return v


class ResultItem(BaseModel):
    """1件の検証結果。"""
    rule: str
    target: str
    status: Literal["ok", "warning", "error"]
    severity: Literal["none", "low", "medium", "high"]
    message: str
    reason: str
    suggested_patch: dict | list | None = None


class VerifyResponse(BaseModel):
    """検証レスポンス全体。"""
    circuit_type: str
    summary: dict
    results: list[ResultItem]


# ── エンドポイント ────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    """死活監視用。サービスが生きていれば status=ok を返す。"""
    return {"status": "ok", "service": "elphysics-verifier"}


@app.get("/circuit-types")
def circuit_types() -> dict:
    """
    対応している回路型と、それぞれに適用されるルール一覧を返す。
    クライアントがどの回路を投げられるか確認するためのもの。
    """
    return {
        "types": list(RULES_FOR_TYPE.keys()),
        "rules_for_type": RULES_FOR_TYPE,
    }


@app.post("/verify", response_model=VerifyResponse)
def verify_circuit(req: VerifyRequest) -> VerifyResponse:
    """
    回路 JSON を検証して結果を返す (このサービスの本体)。

    処理の流れ:
      1. 回路型を自動判別
      2. unified_verifier.verify() で全ルールを実行
      3. ValidationResult (dataclass) を JSON シリアライズ可能な形に変換
      4. サマリー (ok/warning/error 件数) を添えて返す
    """
    circuit = req.circuit

    # ① 回路型を判別 (レスポンスに含めてクライアントが把握できるように)
    #    VerifyRequest のバリデータで既知キーは保証済みだが、二重に防御する。
    circuit_type = detect_circuit_type(circuit)
    if circuit_type == "unknown":
        raise HTTPException(
            status_code=422,
            detail="認識できる回路型がありません (検証対象外の入力です)。",
        )

    # ②a スキーマ検証 (入力の「形」が正しいか)
    #     電気的検証に渡す前に JSON Schema で構造を弾く。
    schema_errors = validate_circuit(circuit)
    if schema_errors:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "入力回路がスキーマに適合しません。",
                "errors": schema_errors,
            },
        )

    # ②b 全ルールを実行 (決定論的・LLM 非関与)
    #    verify() は内部でルール例外を捕捉するが、想定外の例外は 500 で返す。
    try:
        results = verify(circuit)
    except Exception as e:  # noqa: BLE001 — 公開 API として stack を漏らさない
        raise HTTPException(status_code=500, detail=f"内部検証エラー: {e}") from e

    # タイムアウト検出: solver_timeout reason を持つ結果があれば 503 を返す
    if any(getattr(r, "reason", None) == "solver_timeout" for r in results):
        raise HTTPException(
            status_code=503,
            detail="Z3 ソルバーがタイムアウトしました。回路を簡略化して再度お試しください。",
        )

    # ③ dataclass → dict 変換 (suggested_patch は repr=False だが asdict には含まれる)
    items = [
        ResultItem(
            rule=r.rule,
            target=r.target,
            status=r.status,
            severity=r.severity,
            message=r.message,
            reason=r.reason,
            suggested_patch=r.suggested_patch,
        )
        for r in results
    ]

    # ④ サマリー集計
    s = build_summary(results)

    return VerifyResponse(
        circuit_type=circuit_type,
        summary=s,
        results=items,
    )


# ── 直接起動 (python -m elphysics.api) ───────────────────────────────

if __name__ == "__main__":
    import uvicorn

    # reload=False: スクリプト直接起動時はリロードなしで単純起動
    uvicorn.run(app, host="127.0.0.1", port=8000)
