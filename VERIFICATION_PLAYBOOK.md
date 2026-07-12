# Verification Playbook

Rules that convert judgment calls into checkable procedure. The three always-on rules below apply to every task. Project-specific rules accumulate in the second section — every recurring incident should end as a rule here, not as tribal memory.

## Always-On Rules (MANDATORY)

### 1. Mockup First (visual / geometry features)

Before implementing any feature with visual layout, geometry, or spatial arrangement: produce a throwaway mockup (HTML scratch file, wireframe, or ASCII sketch) using the real draw/layout logic where possible, get it approved, and only then treat its numbers as the spec. Do not write production code for an unapproved layout.

### 2. MEASURE Before Conclude (tuning / performance / geometry claims)

Before claiming an improvement or stating a quantitative fact (faster, smaller, aligned, within bounds): take an actual before-measurement and after-measurement with the same instrument, and report both numbers plus the delta. Impressions, extrapolations, and "should be" are not evidence.

### 3. Verify Sub-Agent Claims Against Source

Sub-agents return compressed summaries and are wrong often enough to matter. Before building on a sub-agent's factual claim (a value, a line number, a behavior), verify it against the source file or tool output yourself. Never chain a decision onto an unverified paraphrase.

### 4. Baseline-Move Check (behavioral contract changes)

After changing any default, startup, or behavioral contract: grep the unit AND e2e tests for assertions on the old baseline before merging. A green unit suite does not clear a moved baseline — e2e tests witness defaults that unit tests never touch. Treat any existing test you must edit or delete as a contract you must justify, never as collateral.

## Project-Specific Incident Rules

<!-- Grow this list: every time an incident recurs or a verification gap bites,
     add a rule here in the same session.
     Format per rule:
       ### <short imperative rule name>
       - Date: YYYY-MM-DD
       - Trigger: <when this rule applies>
       - Rule: <the procedure to follow>
       - Origin: <one line on the incident that created it>
-->

*(none yet)*
