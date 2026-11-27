# SMOOTHMOM-001 — PREREGISTRATION ERRATUM (2026-11-23)

Status: **committed before any SMOOTHMOM-001 result exists.** This erratum does NOT
edit the frozen `PREREGISTRATION.md`; it records three text defects found in a review
of the frozen file against the script, and the reading that is authoritative.

No parameter, variant, leg size, bar, benchmark, window, or seed changes here. The
frozen formula in §4, the two variant definitions in §5, the pass bars in §7 and the
random-draw spec in §7(c) stand exactly as frozen.

## 1. §5 internal contradiction — direction of the second score component

§5 V2 says: "score = average of (MOM 12-1 percentile rank) and ((-ID) percentile
rank), both percentiles in [0, 1] ...; ID is sign-adjusted so smooth winners rank high
and smooth losers rank low."

Read literally, `pct_rank(-ID)` is NOT direction-aligned: with
`ID = sign(cum 12-1) x (%neg - %pos)`, smooth losers (steady decline: negative
cum 12-1, few big down days but many small up days) have very negative ID, so `-ID`
ranks them **high** — the second component would then contradict §5's own parenthetical
("smooth winners rank high and smooth losers rank low") and V1's construction (V1
correctly shorts the LOWEST-ID = smoothest names of the bottom MOM quintile).

**Resolution (authoritative reading of §5 V2):** the second score component is
direction-aligned, i.e. computed on `sign(MOM) x (-ID)` over the eligible names —
equivalently, up to monotone transforms, on the asymmetry `%pos - %neg` (smooth losers
have %pos > %neg ⇒ positive ⇒ high; spiky winners have %pos > %neg too but spiky
*losers* are the ones that rank low). Lower ID always means smoother, and the component
ranks smooth winners high and smooth losers low, exactly as §5's parenthetical and V1
require.

The remaining V2 mechanics are unchanged: percentile ranks in [0, 1] over the month's
eligible names, equal 0.5 weights, long = top decile by score, short = bottom decile by
score, equal weight inside legs, frozen stable tie-breaks.

## 2. §4 prose erratum — winner/loser polarities stated backwards

§4 currently says: "ID is positive for smooth winners (few big up days, many small down
days) and negative for spiky winners; the sign flip makes smooth losers (steady decline)
have positive ID too."

That sentence has the polarities swapped. With
`ID = sign(cum 12-1) x (%neg - %pos)`:

- Smooth **winners** (many small up days, few big up days): %neg < %pos ⇒ the
  parenthesised term is negative; the positive sign factor turns ID **negative**.
- Spiky **winners** (a few big jumps): %neg > %pos ⇒ ID **positive**.
- Smooth **losers** (steady decline, many small up days): %neg < %pos, sign factor
  negative ⇒ ID **positive**.
- Spiky **losers** (a few big drops): %neg > %pos, sign factor negative ⇒ ID
  **negative**.

So: ID is negative for smooth winners and positive for spiky winners; smooth losers
end up positive and spiky losers negative. The §4 formula, the rule "**lower ID =
smoother**", and both §5 variant definitions are unaffected and remain authoritative —
"lowest ID" always selects the smoothest names, winners or losers, which is what V1
does and what the corrected V2 component must do (§1 above).

## 3. §6 tolerance note — the 1e-12 tolerance is unsatisfiable

§6 says "mean net within 1e-12 of 0.017082". The frozen constant is rounded; the true
DEV baseline mean is 0.01708229936... (prints as 1.708%/mo). A 1e-12 tolerance against
the rounded constant can never pass.

**Resolution:** the script enforces, and the study uses:

- mean net: |mean - 0.017082| <= 5e-5
- t(excess): |t - 2.294| <= 5e-3
- max drawdown: |maxDD - (-0.277)| <= 5e-3

These are the tolerances documented in the code (§6's "tight tolerances, loud failure"
intent preserved). The t tolerance and maxDD tolerance are unchanged from §6; only the
rounded-constant mean tolerance is widened to 5e-5 for this reason.
