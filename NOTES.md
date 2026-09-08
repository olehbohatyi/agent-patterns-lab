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