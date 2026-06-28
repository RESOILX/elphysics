"""
schema_validation.py — 入力回路 JSON の構造検証 (JSON Schema)

電気的な検証 (unified_verifier / 各ルール) に渡す前段で、入力 JSON が
`schema/circuit.schema.json` の契約を満たすかを確認する。

【役割分担】
  - このモジュール  : 入力の「形」が正しいか (キー・型・列挙値) を検証する
  - unified_verifier: 形が正しい前提で「電気的に」正しいか (キルヒホッフ/定格等) を検証する

公開 API:
  - validate_circuit(circuit) -> list[str]
      スキーマ違反のメッセージ一覧を返す (空リスト = 妥当)。
  - is_valid(circuit) -> bool
  - load_schema() -> dict
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema" / "circuit.schema.json"


@lru_cache(maxsize=1)
def load_schema() -> dict:
    """同梱の circuit.schema.json を読み込む (初回のみ・以後キャッシュ)。"""
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def validate_circuit(circuit: dict) -> list[str]:
    """
    回路 JSON をスキーマ検証し、違反メッセージのリストを返す。

    Args:
        circuit: トップレベルキー付きの回路 JSON
                 (例: {"dc_circuit": {...}})

    Returns:
        スキーマ違反メッセージのリスト。妥当なら空リスト。

    Raises:
        RuntimeError: jsonschema 未インストール時 (依存に含まれるため通常は発生しない)。
    """
    try:
        import jsonschema
    except ImportError as e:  # pragma: no cover - 依存に含まれるため通常到達しない
        raise RuntimeError(
            "スキーマ検証には jsonschema が必要です。`pip install jsonschema` を実行してください。"
        ) from e

    # Draft 7 を使用: schema/circuit.schema.json がこの記法で書かれているため。
    # スキーマファイルを新しい Draft (2019-09 / 2020-12) の記法に書き換える場合は、
    # ここも対応するバリデータクラスに変更する必要がある。
    schema = load_schema()
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(circuit), key=lambda e: list(e.path))

    messages: list[str] = []
    for err in errors:
        loc = "/".join(str(p) for p in err.path) or "(root)"
        messages.append(f"{loc}: {err.message}")
    return messages


def is_valid(circuit: dict) -> bool:
    """回路 JSON がスキーマに適合するかを bool で返す。"""
    return not validate_circuit(circuit)
