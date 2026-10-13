"""
Regenerate city_year_panel.parquet from the already-cleaned CSV.
Run from the replication/ directory:
    python regen_parquet.py
"""

from pathlib import Path
import pandas as pd

ROOT   = Path(__file__).parent
csv    = ROOT / "data" / "merge_panels" / "generated" / "city_year_panel.csv"
parquet = ROOT / "data" / "merge_panels" / "generated" / "city_year_panel.parquet"

df = pd.read_csv(csv, dtype={"city_id": str})
assert (df["year"] != 1999).all(), "1999 rows still present in CSV!"
df.to_parquet(parquet, index=False)

print(f"Written: {parquet.name}  ({len(df)} rows, min year {df['year'].min()})")
