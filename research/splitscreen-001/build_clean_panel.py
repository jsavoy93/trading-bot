"""SPLITSCREEN-001: write the split-repaired DEV panel copy (<= 2024-12-31).

Original panel is read-only; never modified. Writes
/root/research/splitscreen-001/panel_clean.parquet plus flagged_events.json.
"""
import hashlib, json, sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import repair as RP

SRC = Path("/root/.openclaw/worktrees/STRAT-027/experiments/strat_027/data/panel.parquet")
OUT = Path("/root/research/splitscreen-001")
CLEAN = OUT / "panel_clean.parquet"
CUTOFF = pd.Timestamp("2024-12-31")

def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def moves(df, years=("2021-01-01", "2024-12-31")):
    d = df[df.date.between(*years)].sort_values(["symbol", "date"])
    r = d.groupby("symbol", sort=False).close.transform(lambda s: s / s.shift(1))
    return {"gt90_up": int((r > 1.9).sum()), "gt90_down": int((r < 0.10).sum()),
            "gt40": int((r - 1).abs().gt(0.40).sum())}

def main():
    full = pd.read_parquet(SRC)
    all_cols = list(full.columns)
    src_rows, src_syms = len(full), int(full["symbol"].nunique())
    src_sha = sha(SRC)
    dev = full[full["date"] <= CUTOFF].copy()
    before = moves(dev)
    fixed, flags, dropped = RP.repair(dev)
    after = moves(fixed)
    assert fixed.date.max() <= CUTOFF
    fixed = fixed[all_cols]
    fixed.to_parquet(CLEAN, index=False)
    fl = sorted(flags, key=lambda f: -abs(np.log(f["ratio"])))
    out = {"src_panel": str(SRC), "src_sha256": src_sha, "src_rows": src_rows,
           "src_symbols": src_syms, "cutoff": str(CUTOFF.date()),
           "clean_rows": int(len(fixed)), "clean_symbols": int(fixed.symbol.nunique()),
           "clean_sha256": sha(CLEAN), "flagged_total": len(flags),
           "flagged_by_kind": {k: sum(1 for f in flags if f["kind"] == k) for k in ("P", "Q")},
           "flagged_2021_2024": sum(1 for f in flags if f["date"] >= "2021-01-01"),
           "flagged_pre2021": sum(1 for f in flags if f["date"] < "2021-01-01"),
           "dropped_count": len(dropped),
           "dropped": dict(sorted(dropped.items())),
           "moves_dev_before": before, "moves_dev_after": after,
           "flags": fl}
    (OUT / "flagged_events.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("src_sha256", "clean_rows", "clean_symbols",
          "clean_sha256", "flagged_total", "flagged_by_kind", "flagged_pre2021",
          "dropped_count", "moves_dev_before", "moves_dev_after")}))
    for f in fl[:15]:
        print(f"  {f['symbol']:7s} {f['date']} {f['kind']} ratio {f['ratio']:.4f} "
              f"({f['prev_close']:.2f}->{f['close']:.2f})")

if __name__ == "__main__":
    main()
