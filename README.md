# elphysics

> **⚠️ 試験公開中 (Alpha)**  
> このパッケージは現在アルファ版です。API・入力フォーマットは予告なく変更される可能性があります。本番環境での利用はご自身の判断でお願いします。

**直流回路の形式検証エンジン**

回路設計 JSON を受け取り、物理法則（キルヒホッフの電流則・オームの法則）に照らして数学的に検証するライブラリ。

> A formal verification engine for DC electrical circuits. Give it a circuit; it proves whether Kirchhoff's current law and Ohm's law can hold simultaneously.

```
dc_circuit JSON  ──→  elphysics.verify()  ──→  ValidationResult のリスト
                      (Z3 SMT 形式証明)        (ok / warning / error + 修正提案)
```

検証は**決定論的**です。同じ入力には必ず同じ結果を返し、判定に LLM は一切関与しません。

---

## デモ

https://github.com/user-attachments/assets/REPLACE_WITH_ASSET_ID

LLM が elphysics の `/verify` API を呼び出し、`suggested_patch` を受け取って回路を修正・再検証するまでの流れです。

---

## なぜ DC からなのか

オームの法則・キルヒホッフの法則という**確立した基本原理だけ**で完結する直流回路に範囲を絞り、
検証エンジンとしての正しさを担保することを最優先にしています。交流・三相・変圧器など他の回路型は
本リポジトリには含めていません（今後別途追加予定）。

---

## 検証できること

| モード | 入力 | 検証内容 |
|--------|------|----------|
| 合成抵抗 | `terminals` (pos/neg) | 端子間の合成抵抗、ノード電圧・枝電流、各素子の電流定格 (`rated_current_a`) |
| 電源解析 | `ref_node` + `constraints` | 制約から未知電源 (`voltage_v: null` + `find`) を逆算 |

いずれも Z3 SMT ソルバーで「キルヒホッフ・オームの法則を同時に満たす解が存在するか」を**形式証明**します
（UNSAT = 回路の矛盾＝設計ミスの検出）。

---

## インストール

**要件**: Python 3.10 以上

GitHub から直接:
```bash
pip install "git+https://github.com/RESOILX/elphysics"
pip install "elphysics[api] @ git+https://github.com/RESOILX/elphysics"   # Web API も使う場合
```

開発・テスト:
```bash
git clone https://github.com/RESOILX/elphysics
cd elphysics
pip install -e ".[dev]"
```

依存は **z3-solver** と **jsonschema** のみ（API を使う場合は fastapi/uvicorn）。

---

## クイックスタート

### Python ライブラリとして

```python
import elphysics

circuit = {
    "dc_circuit": {
        "nodes": ["a", "b"],
        "components": [
            {"id": "R1", "type": "resistor", "resistance_ohm": 3, "from": "a", "to": "b"},
            {"id": "R2", "type": "resistor", "resistance_ohm": 6, "from": "a", "to": "b"},
        ],
        "terminals": {"pos": "a", "neg": "b"},
    }
}

for r in elphysics.verify(circuit):
    print(r.status, r.message)
```

入力 JSON の構造は同梱の JSON Schema (`elphysics/schema/circuit.schema.json`) で検証できます。
（`verify()` は内部でもスキーマ検証を行うため、通常はこちらを個別に呼ぶ必要はありません。）

```python
errors = elphysics.validate_circuit(circuit)   # スキーマ違反メッセージのリスト (空なら妥当)
```

個別のソルバーを直接呼ぶこともできますが、**回路がどちらのモードかに応じて使い分ける必要があります**。

```python
elphysics.solve_dc(circuit)            # 合成抵抗モード (terminals 指定の回路用)
elphysics.solve_dc_analysis(circuit)   # 電源解析モード (ref_node 指定の回路用)
```

モードが分からない場合は、自動判別する `elphysics.verify()` の利用を推奨します。

### カスタムルールの追加

`verify()` の `extra_rules` 引数で、独自の検証ルールを差し込めます。

```python
results = elphysics.verify(circuit, extra_rules=["rules.my_custom_rule"])
```

ルールモジュールは `run(circuit) -> list[dict]` という関数を実装してください
（`dict` は `rule` / `target` / `status` / `severity` / `message` / `reason` キーを持つ必要があります）。

### CLI として

```bash
elphysics-verify path/to/dc_circuit.json
# 引数なしで同梱サンプルをすべて検証
elphysics-verify
```

### Web API として

```bash
uvicorn elphysics.api:app --port 8000
```
```bash
curl -X POST http://127.0.0.1:8000/verify \
  -H "Content-Type: application/json" \
  -d '{
    "circuit": {
      "dc_circuit": {
        "nodes": ["a", "b"],
        "components": [
          {"id": "R1", "type": "resistor", "resistance_ohm": 3, "from": "a", "to": "b"},
          {"id": "R2", "type": "resistor", "resistance_ohm": 6, "from": "a", "to": "b"}
        ],
        "terminals": {"pos": "a", "neg": "b"}
      }
    }
  }'
```

---

## 入力フォーマット

### 合成抵抗モード

```json
{
  "dc_circuit": {
    "nodes": ["a", "b"],
    "components": [
      {"id": "R1", "type": "resistor", "resistance_ohm": 3, "from": "a", "to": "b"}
    ],
    "terminals": {"pos": "a", "neg": "b"}
  }
}
```

`type` は `resistor` / `wire`（導線・抵抗0）/ `voltage_source`。`rated_current_a` を付けると
その素子に流れる電流が定格を超えていないかも検証します。

### 電源解析モード

```json
{
  "dc_circuit": {
    "nodes": ["gnd", "a", "b"],
    "components": [
      {"id": "E1", "type": "voltage_source", "voltage_v": null, "from": "gnd", "to": "a"},
      {"id": "R1", "type": "resistor", "resistance_ohm": 6, "from": "a", "to": "b"},
      {"id": "R2", "type": "resistor", "resistance_ohm": 3, "from": "b", "to": "gnd"}
    ],
    "ref_node": "gnd",
    "constraints": [{"type": "voltage_across", "component_id": "R2", "voltage_v": 4}],
    "find": ["E1"]
  }
}
```

`voltage_v: null` の電源を未知数とし、`constraints` を満たす値を `find` で求めます。

---

## 検証結果の契約 (`ValidationResult`)

| フィールド | 型 | 説明 |
|-----------|----|------|
| `rule` | str | ルール名 (`dc_analysis`) |
| `target` | str | 対象 (素子 ID / `R_ab` / `circuit` など) |
| `status` | str | `ok` / `warning` / `error` |
| `severity` | str | `none` / `low` / `medium` / `high` |
| `message` | str | 人間向けの説明文 |
| `reason` | str | 機械可読な理由コード (例: `kcl_ohm_satisfied`, `over_current`) |
| `suggested_patch` | dict / None | 修正提案 (例: 抵抗値の更新) |

---

## パッケージ構成

```
elphysics/
 ├─ __init__.py          公開 API (verify, verify_file, validate_circuit, solve_dc*, ValidationResult ほか)
 ├─ unified_verifier.py  回路型を判別し対応ルールを実行する統合検証器
 ├─ dc_solver.py         直流回路ソルバー (Z3 形式証明)
 ├─ rules/dc_analysis.py 検証ルール (run(circuit) で dict のリストを返す。
 │                        unified_verifier が ValidationResult に変換する)
 ├─ schema_validation.py 入力スキーマ検証
 ├─ api.py               FastAPI Web API
 ├─ schema/              入力回路 JSON の JSON Schema
 └─ examples/            サンプル回路 JSON
```

---

## テスト

```bash
pytest
```

---

## ライセンス

MIT