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

https://github.com/user-attachments/assets/f63df4a1-a01a-42e4-b252-a64db8775990

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

## AI データセット生成

elphysics は **Z3 SMT による形式証明**を利用して、物理的に正確な QA データセットを自動生成できます。
LLM の生成と異なり、答えが数学的に保証されているため、ファインチューニングや評価ベンチマークの教師データとして利用できます。

### Type 1: 合成抵抗計算 QA

`elphysics/examples/qa_generator_type1.py` を使うと、直列・並列・混合回路の合成抵抗を問う QA ペアを生成できます。

```bash
# 100 件生成（デフォルト）
python elphysics/examples/qa_generator_type1.py

# 件数・出力先を指定
python elphysics/examples/qa_generator_type1.py --count 1000 --output dataset.jsonl
```

生成される JSONL の各レコード:

```json
{
  "id": "dc_r_mixed_ps_0001",
  "type": "mixed_ps",
  "difficulty": "medium",
  "question": "R1=6Ω、R2=12Ωの並列回路に直列にR3=4Ωを接続しました。合成抵抗を求めてください。",
  "answer": "合成抵抗は 8 Ω です。",
  "reasoning": "① 並列部分: 1/R_並 = 1/6 + 1/12 = 1/4 → R_並 = 4 Ω\n② 直列合計: R = 4 + 4 = 8 [Ω]",
  "circuit": { "dc_circuit": { "..." } },
  "r_ab_exact": "8",
  "r_ab_float": 8.0,
  "verified_by": "elphysics/z3-smt"
}
```

| フィールド | 説明 |
|-----------|------|
| `type` | `series` / `parallel` / `mixed_ps` / `mixed_sp` / `mixed_pp` |
| `difficulty` | `easy`（2素子）/ `medium`（3素子）/ `hard`（4素子） |
| `question` | 自然言語の問題文（4種テンプレートからランダム選択） |
| `answer` | 証明済みの答え（分数・近似値付き） |
| `reasoning` | Chain-of-Thought 形式の推論ステップ |
| `circuit` | 検証に使った回路 JSON（elphysics で再検証可能） |
| `verified_by` | `elphysics/z3-smt`（Z3 SMT ソルバーで証明済みを示す） |

```
生成例（--count 100 のデフォルト）:
  series    : 40 件（easy 20 / medium 20）
  parallel  : 40 件（easy 20 / medium 20）
  mixed_*   : 20 件（medium 12 / hard 8）
```

### Type 2: 電源解析（逆問題）QA

`elphysics/examples/qa_generator_type2.py` は、Type 1 の**逆問題**を生成します。
「ある抵抗の両端電圧が分かっているとき、未知の電源電圧 E を求めよ」という問題で、
elphysics の analysis モード（`solve_dc_analysis` + `find`）が Z3 で E を逆算します。

```bash
python elphysics/examples/qa_generator_type2.py --count 500 --output type2.jsonl
```

```json
{
  "id": "dc_e_divider3_0001",
  "type": "divider3",
  "difficulty": "medium",
  "question": "R1=15Ω、R2=5Ω、R3=10Ω を直列に接続し、電源電圧 E[V] を加えたところ、末端の R3 の両端電圧が 10 V でした。電源電圧 E を求めてください。",
  "answer": "電源電圧は 30 V です。",
  "reasoning": "① 末端抵抗 R3=10Ω の電流: I = V/R3 = 10/10 = 1 A\n② 直列なので全体に同じ電流。合成抵抗 ΣR = 15 + 5 + 10 = 30 Ω\n③ 電源電圧: E = I × ΣR = 1 × 30 = 30 [V]",
  "circuit": { "dc_circuit": { "..." } },
  "e_exact": "30",
  "e_float": 30.0,
  "verified_by": "elphysics/z3-smt"
}
```

順問題（Type 1）と逆問題（Type 2）を混ぜることで、双方向の推論を学習させるデータセットになります。

---

## 強化学習への応用

`elphysics/examples/rl_circuit_repair.py` は、**過電流を起こした回路を修復する**最小の強化学習環境です。
報酬が `elphysics.verify()` の**形式的判定**から与えられるため、報酬をごまかす（reward hacking）ことが原理的にできません
（＝ **検証可能報酬による強化学習 / RLVR**）。

```bash
python elphysics/examples/rl_circuit_repair.py --episodes 300
```

| 要素 | 内容 |
|------|------|
| 状態 | 現在の抵抗値 |
| 行動 | 抵抗を下げる / 据え置き / 上げる |
| 報酬 | `verify()` の結果（過電流なら罰、解消で報酬。過剰に上げないほど高得点） |
| 終了 | 過電流が解消したとき |

素の Python による表形式 Q 学習が、電源 10V・定格 1A の回路で「最小の安全抵抗 = 10Ω」を学習します。
注目点として、elphysics が返す `suggested_patch`（修正提案）が**エキスパートの模範解答**になり、
学習した方策と一致することを確認できます。

```
初期状態: Rt = 2Ω → I = 5.0A（過電流 5倍）
学習後の貪欲方策: 2Ω → 4Ω → 6Ω → 8Ω → 10Ω
suggested_patch（模範解答）: Rt → 10.0Ω  → 一致
```

---

## 進化的アルゴリズムへの応用

`elphysics/examples/ea_circuit_design.py` は、**合成抵抗が目標値になる回路を遺伝的アルゴリズムで設計**します。
適応度が `solve_dc()` の**厳密解（`Fraction`）**から計算されるため、浮動小数ノイズがなく再現性のある進化が回ります。

```bash
python elphysics/examples/ea_circuit_design.py --generations 40 --pop 60
```

| 要素 | 内容 |
|------|------|
| 遺伝子 | トポロジー（直列/並列/混合）+ 抵抗値（最大 5 素子） |
| 適応度 | `-|R_ab − 目標|`（厳密。完全一致で 0） |
| ハード制約 | Z3 が SAT（実行可能）でない個体は淘汰 |

elphysics が「**適応度 + 実行可能性オラクル**」として機能します。

```
目標: R_ab = 23/2 Ω (11.5000 Ω)
  世代  1  最良 R_ab = 54/5 Ω    誤差 0.7000
  世代  4  最良 R_ab = 196/17 Ω  誤差 0.0294
  世代 14  最良 R_ab = 23/2 Ω    ★ 厳密一致
発見した回路: 10Ω 直列 (2Ω ∥ 6Ω)   elphysics 検証: PASS
```

---

## パッケージ構成

```
elphysics/
 ├─ __init__.py          公開 API (verify, verify_file, validate_circuit, solve_dc*, ValidationResult ほか)
 ├─ unified_verifier.py  回路型を判別し対応ルールを実行する統合検証器
 ├─ dc_solver.py         直流回路ソルバー (Z3 形式証明)
 ├─ circuits.py          回路 JSON を組み立てる共有ビルダー (直列/並列/混合)
 ├─ rules/dc_analysis.py 検証ルール (run(circuit) で dict のリストを返す。
 │                        unified_verifier が ValidationResult に変換する)
 ├─ schema_validation.py 入力スキーマ検証
 ├─ api.py               FastAPI Web API
 ├─ schema/              入力回路 JSON の JSON Schema
 └─ examples/            サンプル回路 JSON + ユースケース実行スクリプト
     ├─ qa_generator_type1.py   データセット生成: 合成抵抗 QA
     ├─ qa_generator_type2.py   データセット生成: 電源解析（逆問題）QA
     ├─ rl_circuit_repair.py    強化学習: 検証可能報酬による回路修復
     └─ ea_circuit_design.py    進化的アルゴリズム: 目標合成抵抗の回路設計
```

---

## テスト

```bash
pytest
```

---

## ライセンス

MIT
