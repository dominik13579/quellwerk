import csv
import json
import statistics
from pathlib import Path

root = Path(__file__).resolve().parent
path = root / "results" / "human_review.csv"
rows = list(csv.DictReader(path.open(encoding="utf-8")))
required = ["citation_precision_human_0_to_1", "groundedness_human_0_to_1", "answer_correct_0_or_1"]
reviewed = [r for r in rows if all(r.get(k, "").strip() for k in required)]
if not reviewed:
    raise SystemExit("No completed reviews. Fill the three numeric columns in human_review.csv first.")
metrics = {
    "reviewed_answers": len(reviewed),
    "citation_precision_human": statistics.mean(float(r[required[0]]) for r in reviewed),
    "groundedness_human": statistics.mean(float(r[required[1]]) for r in reviewed),
    "answer_accuracy_human": statistics.mean(float(r[required[2]]) for r in reviewed),
}
(root / "results" / "human_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
print(json.dumps(metrics, indent=2))
