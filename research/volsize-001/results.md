# VOLSIZE-001 results (DEV 2021-01..2024-12)

n_months=47  n_random=1000  smoke=False  runtime=74.1s

| portfolio | mean/mo | ann | t | maxDD | Sharpe | win | p vs random |
|---|---|---|---|---|---|---|---|
| baseline MOM | +1.708% | +22.54% | +2.294 | -27.74% | +0.846 | 68.1% | 100.00 |
| V1 inv-vol | +1.448% | +18.82% | -0.573 | -25.77% | +0.750 | 68.1% | 33.80 |
| V2 vol-target | +1.918% | +25.60% | +0.622 | -29.60% | +0.890 | 68.1% | 74.10 |

bar(a) threshold p >= 98.75 and t >= 2.24; V1 bar(a)=False V2 bar(a)=False
V1 bar(b)=False V2 bar(b)=False  V1 bar(c)=False V2 bar(c)=False

V1 - baseline: mean -0.261%/mo  t -1.044
V2 - baseline: mean +0.210%/mo  t +0.572

determinism sha256: `092a83a80e3e65ce3859cad7659bf5fbdce210a9e9ef42513a0232d3e8e5c35a`
