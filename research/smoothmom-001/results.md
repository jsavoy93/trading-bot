# SMOOTHMOM-001 results (DEV only)

window=2021-01..2024-11 n_months=47 n_random=1000 smoke=False runtime=146.7s

| portfolio | mean/mo | ann | t(excess) | p vs random | maxDD | Sharpe | win | turnover |
|---|---|---|---|---|---|---|---|---|
| baseline MOM | +1.708% | +22.54% | +2.294 | 100.00 | -27.74% | +0.846 | 68.1% | 0.186 |
| V1 double sort | +0.776% | +9.72% | +0.149 | 63.00 | -28.00% | +0.419 | 63.8% | 0.197 |
| V2 combined score | +1.049% | +13.34% | +0.592 | 92.40 | -27.85% | +0.538 | 70.2% | 0.189 |

V1 - baseline: mean -0.932%/mo  t -1.543
V2 - baseline: mean -0.659%/mo  t -1.008

bars: V1 a=False b=False c=False | V2 a=False b=False c=False (p >= 98.75, t >= 2.24, months >= 24)

worst3 baseline [-0.17735783283068635, -0.15674685497199942, -0.12260030455028101]
worst3 V1 [-0.1895280763725465, -0.18633039669054616, -0.0960695403920272]
worst3 V2 [-0.19428384656944206, -0.1937258150587186, -0.10935777507510733]

determinism sha256: `be398966bb07292ceb73a14ca9acb08bdcfbca2790b1ccc27eb5bace19ed7187`
