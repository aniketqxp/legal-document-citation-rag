"""Build a retrieval eval set from CUAD ground truth.

Reads ``CUAD_v1/master_clauses.csv`` and emits a JSONL eval set where each line
is one question:

    {
      "contract_id":   "<base filename, matches the txt file and the indexed doc>",
      "txt_filename":   "<file in CUAD_v1/full_contract_txt>",
      "category":       "Governing Law",
      "question":       "Which jurisdiction's law governs this agreement?",
      "gold_spans":     ["This Agreement ... governed by the laws of ...", ...]
    }

Only contracts whose full text exists in ``full_contract_txt`` are used (so the
corpus builder can index them), and only the curated categories in
``categories.py`` that are present in the CSV and non-empty for that contract.

This module is pure standard library on purpose: it must run on a bare Python
(e.g. the host) with no application or third-party dependencies installed.

Usage
─────
    python -m evaluation.dataset                 # 12 contracts, <=6 questions each
    python -m evaluation.dataset --contracts 20 --per-contract 8 --seed 7
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import random
from pathlib import Path

from evaluation.categories import QUESTION_TEMPLATES

# evaluation/dataset.py -> evaluation -> backend -> <repo root>
REPO_ROOT = Path(__file__).resolve().parents[2]
CUAD_DIR = REPO_ROOT / "CUAD_v1"
MASTER_CSV = CUAD_DIR / "master_clauses.csv"
TXT_DIR = CUAD_DIR / "full_contract_txt"
DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_OUT = DATA_DIR / "eval_set.jsonl"

# CUAD clause text can be very long; lift the csv field-size ceiling.
csv.field_size_limit(10_000_000)


def parse_gold_spans(cell: str | None) -> list[str]:
    """Parse one ``master_clauses.csv`` category cell into a list of spans.

    Cells are stored as Python list literals, e.g. ``"['span one', 'span two']"``.
    Falls back to treating the raw cell as a single span if it is not a literal.
    """
    text = (cell or "").strip()
    if not text or text in ("[]", "['']", '[""]'):
        return []
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return [text]
    if isinstance(value, list):
        return [s.strip() for s in value if isinstance(s, str) and s.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _txt_filename_for(master_filename: str) -> str | None:
    """Map a ``master_clauses.csv`` Filename (``*.pdf``) to its full-text file."""
    base = Path(master_filename).stem
    candidate = TXT_DIR / f"{base}.txt"
    return candidate.name if candidate.is_file() else None


def build_eval_set(
    *, n_contracts: int, per_contract: int, seed: int
) -> list[dict]:
    """Build the eval set as a list of question records (see module docstring)."""
    if not MASTER_CSV.is_file():
        raise FileNotFoundError(
            f"CUAD master_clauses.csv not found at {MASTER_CSV}. "
            "The CUAD_v1 dataset must be present locally to build the eval set."
        )

    with MASTER_CSV.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        rows = list(reader)

    # Only ask about categories that both exist in this CSV and we have a
    # question for — tolerates CUAD revisions that rename/drop columns.
    usable_categories = [c for c in QUESTION_TEMPLATES if c in header]

    # Keep only contracts whose full text we can actually index.
    indexable = [
        (row, txt)
        for row in rows
        if (txt := _txt_filename_for(row.get("Filename", "")))
    ]
    random.Random(seed).shuffle(indexable)

    eval_set: list[dict] = []
    contracts_used = 0
    for row, txt_filename in indexable:
        if contracts_used >= n_contracts:
            break

        contract_id = Path(row["Filename"]).stem
        questions_for_contract: list[dict] = []
        for category in usable_categories:
            if len(questions_for_contract) >= per_contract:
                break
            gold_spans = parse_gold_spans(row.get(category))
            if not gold_spans:
                continue
            questions_for_contract.append(
                {
                    "contract_id": contract_id,
                    "txt_filename": txt_filename,
                    "category": category,
                    "question": QUESTION_TEMPLATES[category],
                    "gold_spans": gold_spans,
                }
            )

        # Only count a contract once it actually contributes a question.
        if questions_for_contract:
            eval_set.extend(questions_for_contract)
            contracts_used += 1

    return eval_set


def write_jsonl(records: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a CUAD retrieval eval set.")
    parser.add_argument("--contracts", type=int, default=12, help="distinct contracts")
    parser.add_argument(
        "--per-contract", type=int, default=6, help="max questions per contract"
    )
    parser.add_argument("--seed", type=int, default=13, help="contract sampling seed")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output JSONL")
    args = parser.parse_args()

    records = build_eval_set(
        n_contracts=args.contracts,
        per_contract=args.per_contract,
        seed=args.seed,
    )
    write_jsonl(records, args.out)

    n_contracts = len({r["contract_id"] for r in records})
    by_category: dict[str, int] = {}
    for r in records:
        by_category[r["category"]] = by_category.get(r["category"], 0) + 1

    print(f"Wrote {len(records)} questions, {n_contracts} contracts -> {args.out}")
    print("Questions per category:")
    for category, count in sorted(by_category.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>3}  {category}")


if __name__ == "__main__":
    main()
