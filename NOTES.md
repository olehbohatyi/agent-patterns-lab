# Phase 0: Baseline agent (CLI-driven loop)

## Architecture
A Python script calls `claude -p` in a loop (subprocess). It decides on its own
whether to continue or stop, based on the actual pytest output.

## Evidence (real run)
Attempt 1: deliberately given a contradictory instruction (is_prime(1) = True).
The test failed: assert True is False, where True = is_prime(1)
The agent read this output itself, generated a prompt containing the error
text, and rewrote solution.py.
Attempt 2: 31 passed. The agent stopped on its own.

## Conclusion
The "run → check → fix based on the real error" loop works without human
involvement. Baseline for comparison with Phase 5 (evidence-based
stop conditions with more complex tasks and attempt history).

# Phase 1: Linear vs Loop agent comparison

| Task                          | Linear (1 attempt) | Loop (up to 3 attempts) |
|--------------------------------|---------------------|--------------------------|
| reverse a string               |     PASSED                |        PASSED                  |
| palindrome check                |      FAILED               |       PASSED                   |
| sum of digits (negative nums)   |      PASSED               |       PASSED                   |
| second largest (duplicates)     |      PASSED               |       PASSED                   |
| count vowels (case-insensitive) |      PASSED               |       PASSED                  |

## Conclusion
Linear agent: 4/5 (80%) tasks passed on the first attempt.
Loop agent: 5/5 (100%) tasks passed.

The difference showed up on the "palindrome check" task — the linear 
agent didn't account for ignoring spaces/case on the first attempt 
and had no mechanism to fix it. The loop agent received the actual 
pytest error output, understood the issue, and rewrote the code 
on the second attempt.

This confirms the core hypothesis: an agent loop with evidence-based 
verification (real test output, not the model's self-assessment) 
delivers a measurable reliability improvement even on simple tasks.

## Phase 2: Context-selection bug misdiagnosed as a model limitation

The diamond agent failed the palindrome task with an identical `NameError`
on all 3 retries — a tell that the bug was in `test_solution.py` (never
rewritten by the loop), not `solution.py` (rewritten each attempt). Cause:
`test_solution.py` had no `from solution import is_palindrome` line.

First instinct was to patch the prompt with "include the import line" —
treating it as a model capability gap. Actual cause: the test-generation
prompt only said the function "is already implemented in solution.py" but
never showed its content, so the model was guessing the function name from
a text description. Each `claude -p` call is a blank slate with no
filesystem access.

Fix: pass `solution.py`'s content directly into the test-generation prompt.
Applied to `agent_diamond.py`, then backported to `agent_loop.py`/
`agent_linear.py` (same latent bug — it's what caused Phase 1's linear
"palindrome check" failure above).

Takeaway: a "Select" failure in context engineering, not a model
limitation — worth checking what the prompt actually handed the model
before concluding it "can't do" something.

## Phase 2: Diamond pattern — PASS/FAIL calibration check

### Setup
Same diamond agent (4 parallel reviewers + asymmetric aggregator), run on
two contrasting tasks to check whether the verifier can actually say both
PASS and FAIL, not just rubber-stamp everything.

### Run 1 — palindrome check (PASS)
3 of 4 reviewers (security, performance, style) found no issues.
TEST_COVERAGE flagged a real gap: punctuation isn't stripped, so
"A man, a plan, a canal: Panama" returns False. The aggregator classified
this as a scope limitation, not a defect, and returned PASS.

### Run 2 — prime check with an injected defect (FAIL)
Task deliberately asked for `is_prime(1) == True` (mathematically wrong).
The agent implemented it faithfully and wrote tests that lock in the same
bug as expected behavior. TEST_COVERAGE flagged that the test suite
codifies a known defect as a guarantee — a correctness issue, not a
nitpick. The aggregator returned FAIL.

### Takeaway
The verifier calibrates by severity rather than defaulting to PASS: a
scope gap passed, a defect baked into the test contract failed. This is
the asymmetric verification + Default-FAIL pattern from the research
corpus, confirmed empirically on two contrasting runs rather than just
described.

### Side observation
On both runs, 3 of 4 reviewers (security, performance, style) returned "no
issues" on straightforward tasks. Full-cost review on every lens for every
task is likely wasted spend — candidate for model tiering (haiku for the
low-yield lenses) or gating fan-out on task complexity.

## Phase 2: Reviewer & aggregator calibration probes

Three probes designed to test whether the diamond pattern actually catches
what it's supposed to catch, not just to confirm the earlier PASS/FAIL demo.

### Probe 1 — Security: path traversal (miss)
Task: "read file contents, allow caller to pass any path for flexibility."
Result: no path validation in the generated code. SECURITY reviewer said
"no meaningful concern since the caller fully controls the input" —
backwards reasoning for exactly this vulnerability class ("caller controls
input" is the vulnerability, not a mitigation). Pattern-matched on
"no eval/subprocess" and stopped, without reasoning about trust boundaries.

### Probe 2 — Performance: O(n²) dedupe (caught)
Hand-planted a naive O(n²) `remove_duplicates` (linear `in` check per
item) directly into solution.py, then ran diamond review in isolation
(bypassing code-gen, to test the reviewer alone). PERFORMANCE reviewer
caught it without a size hint — reasoned abstractly about asymptotic
blowup ("100k items ≈ 10 billion comparisons") from code shape alone, and
proposed the correct set-based fix.

### Probe 3 — Aggregator bar (the real finding)
Despite Probe 2's genuine catch, the aggregator still returned PASS,
reclassifying the O(n²) finding as "an optimization opportunity, not a
defect" — for a function whose entire purpose is deduplication. Taken
together with Probe 1 (a real security-relevant design flaw waved through
at the reviewer level) and the earlier prime-number test (a correctness
bug correctly failed), a pattern emerges: the aggregator's current bar for
"blocking" is closer to "did it produce a wrong answer" than "is this
production-worthy." The Default-FAIL calibration confirmed earlier
appears to trigger reliably on outright correctness bugs, but not
consistently on security or performance severity.

### Honest summary
Not cherry-picked: one clean miss (security), one clean catch that still
got waved through (performance), one clean catch that correctly failed
(correctness). The diamond pattern's weak point isn't the reviewers alone
— PERFORMANCE reasoned well without hints — it's the aggregation step,
which needs an explicit severity rubric per category rather than a single
undifferentiated PASS/FAIL judgment call.

### Implication for design (deferred, not fixed here)
The aggregator prompt should probably require a verdict per category
(e.g. security: block/warn/pass, performance: block/warn/pass) rather than
one holistic PASS/FAIL, so a real finding in one lens can't be silently
absorbed into an overall PASS. Candidate for a follow-up phase.

### Side note on tiering (unchanged)
Reviewer signal quality varies by lens, not just by task difficulty:
PERFORMANCE reasoned correctly with no cues; SECURITY missed an
architecturally real vulnerability. This argues against blind model
downgrades for "low-yield" lenses — the fix demonstrated here was a
better probe/prompt, not a bigger model.

## Phase 2: Fixing the aggregator — one wrong diagnosis, one right one

### Attempt 1 — per-category isolation (didn't work)
Hypothesis: the aggregator "smoothed over" a single real finding because
one call saw all 4 reviews at once. Fix: give each review its own isolated
BLOCK/OK call (seeing only that review), and compute the final PASS/FAIL in
Python — FAIL if any category blocks — so the model can't blend.

Re-ran the O(n²) dedupe probe: still PASS. The isolated performance judge
saw the finding alone, with no clean reports beside it, and still said OK:
"O(n²) behavior only matters at scale... not a demonstrated regression
against current usage." Hypothesis falsified — blending was never the
mechanism.

### The actual mechanism
The judge had no severity bar, so it invented one: "is there a demonstrated
failure in current usage?" Neither a conditional vulnerability nor an
asymptotic complexity defect can ever satisfy that, isolated or not.

The security probe made it sharper. This time the SECURITY reviewer *did*
catch the path traversal (CWE-22, described accurately) — and the judge
overrode it anyway, because the reviewer had honestly hedged "if `filename`
ever originates from an untrusted source." The judge read that "if" as
"unproven." **The system was punishing epistemic honesty**: a more
overconfident, less accurate review would have scored higher. That's a
Goodhart-shaped incentive baked into the verification step itself.

### Attempt 2 — explicit severity rubric (worked)
Added a rubric to the judge prompt naming what must block even when hedged
(security flaw with no trust boundary; complexity defect in a function whose
purpose is that operation; correctness bug, including tests encoding wrong
behavior), plus an explicit clause: do not treat "if..." / "at scale..." as
evidence an issue is unproven.

| Probe                      | Before rubric | After rubric          |
|----------------------------|---------------|-----------------------|
| Security (path traversal)  | PASS          | FAIL (security: BLOCK)|
| Performance (O(n²) dedupe) | PASS          | FAIL (performance: BLOCK)|

Both judges cited the anti-hedge clause by name in their reasoning.

### Caveats (not resolved)
- **Partly circular**: the rubric names these two defect classes by
  description, so this shows it catches *known* cases, not that it
  generalizes to unanticipated ones. Untested on a novel defect type.
- **Findings now double-count across lenses**: on the dedupe probe, STYLE
  also returned BLOCK because the style reviewer mentioned the O(n²) issue
  in passing. Correct per the rubric, but category verdicts are no longer
  cleanly scoped to their own concern.
- **The judge still never sees the code** — only review text. If a reviewer
  misses a defect entirely, no rubric can recover it downstream.
- **Shared-file fragility**: one probe run was invalidated when a parallel
  run overwrote `solution.py`/`test_solution.py` mid-review. Probes now
  assert on the expected function name first, but the single-working-file
  design makes concurrent runs unsafe.
## Phase 2: Closing finding — the architectural ceiling of asymmetric verification

### Question
Does the aggregator's per-category rubric fix also protect against a
reviewer that simply misses a defect and reports "no issues found"?

### Test
Deterministic, not dependent on reviewer nondeterminism: hand-wrote a fake
"No issues found" review for each category and paired it with the known-
vulnerable `read_file_contents` (no path validation). Ran `aggregate_verdict`
directly against this fabricated review set.

### Result
PASS. Every category returned OK. The security judge's own reasoning:
"The review states no vulnerability exists and gives a specific, verifiable
rationale... it's a clean bill of health, not a disguised finding." The
judge called the rationale "verifiable" while holding neither the code nor
the tests — it can only assess whether a review sounds well-reasoned, not
whether it's true. A confident lie and a correct all-clear are
indistinguishable from where the judge sits.

### Why this is different from the earlier rubric fix
The rubric fix (Probes 1–2) addressed a calibration bug: real findings were
being reported but waved through as non-blocking. This is an architectural
limit: a finding that is never reported cannot be recovered downstream by
any prompt change to the judge, because the judge has no independent
access to the artifact being judged — only to what the reviewer chose to
say about it.

### The tradeoff, now demonstrated rather than argued
Giving the judge the code would close this hole, but at a cost: the judge
would stop being an independent check on the *reviews* and become a fifth
*reviewer*, looking at the code directly. That's a different system —
one more opinion in the fan-out — not a better-tuned version of the
current asymmetric-verification design, which depends on the judge staying
blind to everything except what it's asked to verify.

### Standing limitation
The diamond pattern as built is only as reliable as the honesty and
completeness of its 4 reviewers. The aggregator can raise the bar for
findings that surface, but has no mechanism to catch a finding that never
surfaces. This is recorded as a known, unresolved architectural boundary,
not a bug to fix in this phase.
