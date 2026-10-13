"""
Remove synthetic 1999 test data from the replication panel files.
Run once from the replication/ directory:
    python strip_1999.py
"""

from pathlib import Path
import pandas as pd

ROOT = Path(__file__).parent

files = [
    (ROOT / "data" / "merge_panels" / "generated" / "city_year_panel.csv",     "csv"),
    (ROOT / "data" / "merge_panels" / "generated" / "city_year_panel.parquet",  "parquet"),
]

for path, fmt in files:
    if not path.exists():
        print(f"SKIP (not found): {path.name}")
        continue

    if fmt == "csv":
        df = pd.read_csv(path, dtype={"city_id": str})
    else:
        df = pd.read_parquet(path)

    before = len(df)
    df = df[df["year"] != 1999].copy()
    after = len(df)

    if fmt == "csv":
        df.to_csv(path, index=False)
    else:
        df.to_parquet(path, index=False)

    print(f"{path.name}: removed {before - after} rows ({before} -> {after})")

print("Done.")
