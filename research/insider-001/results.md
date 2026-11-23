# INSIDER-001 results (full)

- generated: 2026-11-23T04:03:18Z  runtime: 17.2s
- months: 47  random draws: 1000
- panel: /tmp/panel_dev.parquet  (rows <= 2024-12-31 only)
- insider file: /root/.openclaw/worktrees/insider-001/research/insider-001/data/insider_purchases.parquet rows=53237 filings 2019-10-01 .. 2024-12-31

## Baseline

- n_months: 47
- mean_net: 0.01708229936906453
- ann_net: 0.22538667776710408
- max_drawdown: -0.2774257906660196
- sharpe: 0.8455531341538303
- worst3: [-0.17735783283068635, -0.15674685497199942, -0.12260030455028101]
- win_rate: 0.6808510638297872
- check: {'mean_net': 0.01708229936906453, 't': 1.673397861284941, 'mean_expected': 0.01708, 't_expected': 2.294, 'mean_ok': False, 't_ok': False, 'max_drawdown': -0.2774257906660196, 'max_drawdown_ref': -0.277}
- percentile_vs_random: 100.0

## V1_mom_tilt

- summary: n_months=47, mean_net=0.017082222072992433, ann_net=0.22538556024647893, max_drawdown=-0.27742874254885397, sharpe=0.845543062671164, worst3=[-0.17735783283068635, -0.15674685497199942, -0.12260030455028106], win_rate=0.6808510638297872, mean_excess=-7.729607209739104e-08, t_excess=-0.06088060419273499
- vs_baseline_mean: -7.729607209739104e-08
- vs_baseline_t: -0.06088060419273499
- percentile_vs_random: 100.0
- months_with_positions: 47
- names_per_month: 102.12765957446808
- insider_names_per_month: 7.957446808510638
- turnover: 0.6685733809089084
- bars: months_ge_min=True, mean_net_gt_0=True, mean_excess_gt_0=False, t_ge_bar=False, percentile_ge_bar=True, PASS=False

## V2_cluster

- summary: n_months=47, mean_net=0.004129747028591007, ann_net=0.05069822182400063, max_drawdown=-0.3743360927261863, sharpe=0.1887305762667607, worst3=[-0.13073534155382066, -0.11708693457302563, -0.10924494937450172], win_rate=0.5106382978723404, mean_excess=-0.003891528200756495, t_excess=-0.8579286292472712
- excess_vs_universe: -0.003891528200756495
- percentile_vs_random: 0.0
- months_with_positions: 47
- cash_months: 0
- names_per_month: 35.319148936170215
- turnover: 0.8337343315399437
- correlation_with_baseline_mom: -0.23691886259435951
- bars: months_ge_min=True, mean_net_gt_0=True, mean_excess_gt_0=False, t_ge_bar=False, percentile_ge_bar=False, PASS=False
