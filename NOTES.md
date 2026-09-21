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

## Phase 3: Model tiering — clean tiers vs. a rubric gap

### Setup
Made the model per-reviewer (`call_claude(prompt, model=...)`; `REVIEWERS`
carries a model alongside each instruction) so tiering could be tested one
role at a time. Judge stayed on sonnet throughout — changing reviewers and
judge together would make a regression impossible to attribute. Re-ran the
3 calibrated probes from Phase 2 on sonnet as a fresh baseline, then again
with all 4 reviewers on haiku.

### Bug found while establishing the sonnet baseline
The first baseline run showed `security: BLOCK` on the dedupe probe even
though the security review itself said "No issues found." Cause:
`judge_review()` parsed the verdict by reading the response's first word.
A judge that reasoned out loud before answering — "BLOCK... wait, no, let
me reconsider... OK" — got scored on the word it started with, not the
verdict it reached. This silently broke the fail-safe default in the
dangerous direction: a judge talking itself *into* OK from BLOCK would have
read as OK instead. Fixed by requiring an explicit `VERDICT: BLOCK` /
`VERDICT: OK` marker on its own line, parsed with `re.findall` — zero or
multiple markers both fail safe to BLOCK. Re-ran the baseline after the
fix; the false positive was gone.

### Result
| Probe | Sonnet | Haiku |
|---|---|---|
| path_traversal | security: BLOCK only | security: BLOCK only — match |
| on2_dedupe | performance: BLOCK only | performance: BLOCK only — match |
| prime_bug | test_coverage: BLOCK only | all 4: BLOCK in 5 of 6 runs |

Two of three probes tier cleanly — same detecting category, same final
verdict. The third diverges, reproduced across 6 total runs (4 isolated
re-runs plus 2 full `agent_diamond.py` pipeline runs): 5/6 had all 4
categories block, 1/6 had 3/4 (style stayed scoped that time). Stable
pattern, not one-off noise — final verdict was FAIL in all 6 either way.

### What's actually happening on prime_bug
Every haiku reviewer independently notices the `is_prime(1)` bug and
mentions it, even when asked to look at an unrelated lens — and each one
explicitly disclaims it: "Note: this is a correctness bug, not a security
issue"; "however, there is a correctness issue (not strictly performance,
but critical)"; "the only concern is correctness, not style." The judge
blocks anyway, correctly applying the existing anti-hedge rubric from
Phase 2 — but that rubric was written to stop a reviewer from using
hedges to excuse a real defect *in its own lane* ("if untrusted input...",
"at scale..."). It has no way to tell that pattern apart from a reviewer
honestly flagging a defect that's genuinely outside its lane. Both read as
"this isn't really my category, but—", so the same rule fires on both.

### Reframing
Not a haiku detection or articulation weakness — if anything haiku is
more consistent here, catching the bug from every angle unprompted and
disclaiming scope honestly each time; sonnet's reviewers mostly stayed
scoped and only test_coverage caught it. The gap is in the rubric, not the
model: it was never designed to distinguish "hedge masking an in-scope
finding" from "accurate out-of-scope mention." Haiku's verbosity just
surfaces this rubric gap more often than sonnet's terser reviews do.

### Caveat on the probe itself
`prime_bug`'s defect (`is_prime(1) == True`) is blatant enough that almost
any reviewer trips over it by accident, regardless of assigned lens — this
may be less about haiku specifically than about using an impossible-to-miss
bug as the probe. It doesn't test whether haiku *misses* scoped, subtle
defects the way sonnet's security reviewer did in Phase 2's original path
traversal probe; it tests whether reviewers *stay scoped* when a defect is
obvious enough to notice by accident. Both are real questions, but this run
only answers the second one.

### Practical implication for tiering
If the only thing that matters is the final PASS/FAIL, haiku tiers fine on
all 3 probes (3/3 correct across the board, 6/6 counting the repeats). If
per-category attribution matters — e.g. triaging "which lens actually
caught this" downstream — haiku's cross-contamination degrades that signal
specifically on probes loud enough to leak across categories. This is a
cost to attribution, not to correctness of the final verdict, and it's a
rubric fix away from being closed rather than a reason to avoid haiku
outright.

### Deferred
A rubric fix that separates "hedge excusing an in-scope finding" from
"honest out-of-scope mention" — not attempted here, noted as a follow-up
alongside the earlier "judge never sees the code" limitation from Phase 2.

## Phase 3: Lane-aware rubric fix — regression more severe than the fix

### Fix attempted
Added a lane clause to the judge rubric: block only if the defect belongs
to the reviewer's own assigned lens, not just if it's mentioned anywhere.
Intended to stop out-of-lane honest mentions (e.g. security noting "this
is a correctness bug, not a security issue") from triggering a block.

### Result
| Probe | Sonnet | Haiku |
|---|---|---|
| path_traversal | 1/1 correct | 1/1 correct |
| on2_dedupe | 1/1 correct | 6/7 correct, 1 unreproduced miss (likely noise) |
| prime_bug | **4/7 correct, 3/7 full miss (PASS)** | 3/3 correct |

The fix worked exactly as designed where it was aimed — an out-of-lane
mention correctly stopped blocking. But on `prime_bug`/sonnet it produced
a 43% full-miss rate, worse than the pre-fix baseline (0% miss).

### Root cause of the regression
Captured a full silent-failure run. Every reviewer behaved reasonably by
its own rules:
- performance: mentioned the bug, correctly labeled it "not a performance
  issue, a correctness bug" → judge correctly read this as out-of-lane, OK
- style: mentioned the same defect but filed it as a cosmetic nitpick
  ("awkward branch ordering") without flagging it as a correctness bug →
  judge correctly read this as genuinely in-lane-and-minor, OK
- test_coverage (the lens that actually owns this defect): didn't mention
  it at all this run — generic coverage gaps only
- security: no mention

Result: zero blocks, defect ships clean, with every individual judgment
locally defensible.

### What actually broke
The old (lane-blind) rubric had an accidental safety net: any reviewer
mentioning the defect in any framing triggered a block somewhere, because
the judge didn't check ownership. The lane fix closed that loophole by
design — but with it went the redundancy that made the system tolerant of
the "correct" owner staying silent in a given run. Precision and
robustness traded against each other here; the fix increased one and
silently cost the other.

### The haiku/sonnet reversal
Haiku's tendency to restate the same defect across all 4 reviews — the
exact "lens discipline" problem flagged as a cost in the first tiering
finding — turned out to be what shielded it from this regression: with 4
independent restatements, at least one review reliably fails to disclaim
cleanly and blocks. Sonnet's more disciplined, single-owner reviews have
no redundancy to fall back on when the actual owner misses in a given
run. Redundant/noisy reviewing was more robust here than precise/lane-
disciplined reviewing — the opposite of the usual tiering assumption that
the more careful model is the safer one to keep un-tiered.

### Standing tension
Two rubric goals are in direct conflict:
1. Don't block on honest out-of-lane mentions (lane fix's goal)
2. Don't silently pass when the actual owner lens misses in a given run
Fixing (1) directly weakened (2). Not resolved here — options for a future
pass: keep the lane fix but add a cross-lens fallback ("if no lens claims
this in-lane, treat any accurate mention as sufficient to block"), or
accept the trade and rely on tiering/redundancy (haiku's behavior) as the
actual safety net instead of rubric precision.

## Phase 4: Graph routing — first result, two distinct failure classes

### Setup
First graph run: solution + tests → diamond review → route by blocking
category → security-specific fix prompt (adds trust boundary) → re-test.

### Result
Routing worked as designed — security blocked, graph took the
security-specific path, not the generic fallback. The routed fix itself
broke on two independent bugs.

### Bug 1 — missing import (infrastructure gap, not graph-specific)
The security fix used `os.path.realpath`, `os.path.join`, etc. without
`import os`. `clean_code()`'s `ast.parse()` only validates syntax, not
name resolution — a NameError at runtime passes the check silently. This
gap predates Phase 4; specialized fix prompts just make it more likely to
surface, since they tend to pull in code (path handling, hashing, etc.)
that needs imports the simpler generic prompts rarely triggered.

### Bug 2 — routed fix contradicts the task's own spec (graph-specific)
After patching bug 1, 7/9 tests still failed: the fix hardcoded a
containment boundary (`base_dir="."` resolved via `os.path.realpath`)
that rejects any path outside the process's cwd — including pytest's
`tmp_path` fixture, breaking every legitimate test. This directly
contradicts the task's explicit requirement: "allow caller to pass any
path they want for flexibility." The security route prompt gave the model
the review finding and the task description, but the model had no way to
reconcile "add a trust boundary" with "the whole point is flexible path
access" — it picked a boundary that satisfies the reviewer while
defeating the feature.

### Takeaway
Bug 1 is a validation gap in existing infra (ast.parse checks syntax, not
runtime correctness) — fixable by actually running the fixed code, not
just parsing it. Bug 2 is specific to routing: a specialized fix prompt
built from the review text alone lacks the context to know when a
security recommendation conflicts with the task's actual design intent.
Generic fix prompts avoid this by being vague enough to not overcommit to
a specific mechanism — specialization trades that safety for precision,
and this run shows the cost side of that trade for the first time.

### Deferred
Hardening options not applied yet: an import/execution check after
clean_code() (catches bug 1 generally), and passing the original task
description more explicitly into the fix prompt with an instruction to
preserve stated requirements (addresses bug 2, though may not fully
resolve genuine security/functionality conflicts — some tasks may have
requirements that are fundamentally in tension with being secure).

## Phase 4: Bug 2 reproduces — confirmed pattern, not a fluke

### Second run
Same security probe, independent run. Fix produced a different
implementation (`Path.relative_to()` + `PermissionError`, vs. the first
run's `os.path.commonpath()` + `ValueError`), but the identical underlying
mechanism: containment scoped to the process's cwd via
`os.environ.get(..., os.getcwd())` / `Path(...).resolve()`.

### Takeaway
Two independent runs, two different code shapes, same root cause. This is
a reproducible pattern in how the security-specific fix prompt resolves
"add a trust boundary" when the task's own requirement is "allow any
path" — it reliably defaults to a cwd-scoped sandbox that satisfies the
reviewer's finding while silently breaking the stated feature. Not
prompt-writing noise; the fix prompt lacks any mechanism to detect that
its recommended fix contradicts the task spec it was also given.

## Phase 4: Isolation caveat — claude -p has implicit tool access

### Finding
`claude -p` calls in this repo are not sandboxed by default — the model
can use tools (e.g. Read, git context) unless explicitly told not to.
A security-fix response once quoted `NOTES.md`'s real Bug 2 section
essentially verbatim, despite the fix prompt never mentioning it. Follow-up
isolated it: a plain `-p` call correctly reported the latest git commit
message via auto-injected git context (a standard Claude Code feature, not
hidden file access), while an explicit "do not use tools or read files"
call correctly said "I don't know" instead of guessing `NOTES.md`'s
content. So every `call_claude()` in this repo has always had the
*capability* to read cwd files unless the prompt discouraged it — a
capability, not necessarily a behavior that occurred in any given run.

### Implication for prior findings
Phase 2's "judge never sees the code" finding was based on the judge's
prompt not including the code — it hadn't verified the judge had no
*means* to look. Re-verified directly against `judge_review()`: fed a
fabricated "No issues found" security review while `solution.py` on disk
held the genuinely vulnerable, unfixed `read_file_contents` (naive `open()`,
no validation) — the same pattern this session's security reviewer catches
reliably in essentially every other run. If the judge were checking the
file, this is exactly the case where it would show. Across 3 runs: OK every
time, no reasoning, no reference to the file's actual content. Phase 2's
finding holds empirically at this call site — the judge trusts the review
text as given, in practice, even though nothing architecturally prevents
it from doing otherwise.

### Design implication going forward
Prompts intended to test or rely on isolation should explicitly state "do
not use tools" / "do not read any files" to make the isolation an enforced
constraint rather than an incidental one. Phase 2's original entry is left
unedited as a historical snapshot — this note is the correction, not a
rewrite.

## Phase 4: Performance route — comparison with security route

### Method note
Unlike the security probe, code-gen reliably writes the optimal O(n)
set-based dedupe unprompted (matching Phase 2's original finding), so the
performance route never fires through a natural `main()` run. Tested by
seeding the naive O(n²) code directly and driving the graph loop's body
(review → route → fix → re-test) from that entry point, rather than from
`main()`'s Step A. This means performance's results aren't from an
identical pipeline entry to security's — worth keeping in mind when
comparing failure rates across the two routes.

### Result (3 seeded runs)
| Run | Performance reviewer | Outcome |
|---|---|---|
| 1 | BLOCK | Routed → correct set-based fix → tests pass → converged PASS |
| 2 | BLOCK | Routed → identical correct fix → tests pass → converged PASS |
| 3 | OK (miss) | Converged PASS immediately on the still-naive O(n²) code |

Order preservation was correct in both successful fixes (`[3,1,3,2,1] →
[3,1,2]`) — no analog to security's systematic cwd-sandbox error.

### Cross-route comparison
| Axis | Security | Performance |
|---|---|---|
| Routing mechanism | correct every time it fired | correct every time it fired |
| Fix quality when it fires | broken or refused, 4/4 | clean and correct, 2/2 |
| Reviewer recall | caught its defect reliably every run | missed the defect 1/3 runs |

Routing itself (right category → right prompt → right fix path) is solid
for both categories. The two routes fail in different places: security's
weak point is fix quality (the model can't reconcile the review finding
with the task's stated requirement); performance's weak point is reviewer
recall (the review step itself sometimes doesn't notice the O(n²)
pattern, so the graph never gets a chance to route at all).

### Takeaway
The 1/3 performance miss reproduces Phase 2's architectural ceiling
finding — the system is only as good as what its reviewers report — but
now demonstrated at the review-generation step rather than the judge
step, and for a different category than the original (security) case.
Two independent instances of the same ceiling, in different parts of the
pipeline, is stronger evidence that this is a structural property of the
fan-out design, not a one-off quirk of a single prompt.

## Phase 4: Two-category-block test — routing drops findings, budget never engaged

### Setup
Task designed to trip two categories at once: path-traversal read
function (security) with PascalCase/camelCase naming (style).

### Q1 — do categories block simultaneously?
Yes, cleanly: `security` and `style` both blocked in the same review
pass, each correctly scoped to its own concern — no lens-distraction
(unlike the earlier haiku cross-contamination finding).

### Q2 — does priority routing drop the second finding?
Yes, confirmed. `route_fix()`'s if/elif chain matched `security` first;
the style finding (naming convention) was never included in the fix
prompt and was silently dropped for this attempt.

### Q3 — does MAX_GRAPH_ATTEMPTS give enough budget to eventually resolve both?
Never got to find out — the security fix broke tests
(`FileNotFoundError` instead of expected `IsADirectoryError`, because the
fix's `os.path.isfile()` check collapses "doesn't exist" and "is a
directory" into one case). On a post-fix test failure, `main()` calls
`sys.exit(1)` immediately — there is no code path that treats "the fix
broke something" as a retryable graph attempt. `MAX_GRAPH_ATTEMPTS` only
governs the review→route→fix cycle when fixes keep tests green; a fix
that breaks tests exits the whole run regardless of remaining budget.

### Third failure mode for the security route
This is a third distinct way the security fix has broken things, same
root cause as Bug 2: the fix prompt has no visibility into the test
suite it needs to keep passing.
- Run 1: cwd-sandbox → `ValueError` (rejects legitimate paths)
- Run 2: cwd-sandbox → `PermissionError` (same root cause, different code)
- Run 3: `isfile()` check → `FileNotFoundError` (breaks a directory-error
  contract the fix never knew existed)
All three: the fix satisfies the security finding while breaking a
constraint (task spec, or test contract) it wasn't given.

### Takeaway
Two separate structural gaps, both real, distinct from each other:
1. Priority routing silently discards findings outside the winning
   category — a design choice, not a bug, but currently undocumented and
   unrecoverable within one graph attempt.
2. A fix that breaks tests bypasses the retry budget entirely and hard-
   exits, rather than counting as a failed attempt and looping back. This
   is the one worth fixing directly — it's a control-flow gap, not a
   fundamental tradeoff.

## Phase 4: Revert path — verified by inspection, and a cleaner Bug 2 case

### Revert path verification
Two follow-up runs on the security-only probe, neither triggered a
test-breaking fix (see outcomes below) — so the revert branch itself
didn't execute live. Verified instead by code inspection: `code_text` is
only ever reassigned to `candidate_code` after `tests_passed` is
confirmed True. At the point where `not tests_passed` is checked,
`code_text` still unconditionally holds the last known-good value, so the
revert write (`f.write(code_text)`) cannot leak a broken fix forward.
This is a structural guarantee from the control flow, not something that
needs a lucky run to confirm — treated as verified.

### Two more runs, two more distinct outcomes
- Run A: security reviewer missed the defect entirely (no block at all) —
  same miss class as Phase 2's original Probe 1. Nothing to route or fix.
- Run B: security blocked, fix applied — this time well-behaved: added
  only type/null-byte/empty-string validation, no cwd-sandbox, genuinely
  respecting "accept any path." Tests stayed green (confirmed no revert
  needed). Attempt 2 still blocked security anyway: the judge correctly
  identified that the core arbitrary-path-read capability is untouched,
  and the task's own requirement *is* the vulnerability — no amount of
  input-shape validation closes it. Graph exhausted its budget cleanly,
  no crash.

### Full tally across the session's security-route runs (7 total)
| Outcome | Count |
|---|---|
| cwd-sandbox pattern, breaks tests | 2 |
| outright refusal to fix | 1 |
| isfile()-collapsing, breaks tests | 1 |
| well-behaved fix, tests pass, still insufficient | 1 |
| reviewer misses the defect, nothing to fix | 2 |

### Takeaway
Run B is the cleanest demonstration yet of Bug 2: this isn't fixable by
better prompting the model into more careful validation, because the
model in Run B *was* careful, respected the spec, broke nothing — and
still can't win, because "accept any path" and "prevent arbitrary file
access" are the same requirement stated as two different constraints.
No fix prompt closes that gap; the task itself needs to change (e.g. an
explicit allowlist or scoping parameter) for both to be satisfiable at
once. This is now the strongest version of "architectural conflict, not
a prompting problem" collected this session.

## Phase 4: Rubric gap — performance BLOCK criteria don't cover memory/space complexity

### Setup
Task: line-count function required to work efficiently on multi-gigabyte
files. Seeded a naive `content = f.read()` version — O(n) time, O(n)
memory — specifically to trigger a memory-complexity defect distinct from
the earlier O(n²) time-complexity dedupe trap.

### Result
Performance judge returned OK. The performance reviewer's own text
confirmed the code is "O(n) in file size, no redundant reads, no per-line
Python loop overhead" — accurate, and also not the actual defect. The
real problem is O(n) memory vs. O(buffer size) memory, which is exactly
what makes an in-memory read unsuitable for the file sizes the task
explicitly requires.

### Root cause
The judge rubric's BLOCK criterion for performance is scoped to
"algorithmic complexity defect (e.g. O(n²) or worse)" — time complexity
only, no clause addressing space/memory complexity. Loading an entire
multi-gigabyte file into memory is O(n) in time (technically fine by the
letter of the rubric) while being the literal violation of the task's
own stated constraint. This is not reviewer-recall noise like the dedupe
miss (Phase 3) — the review accurately described the code; the rubric
simply never asked the judge to treat memory footprint as blocking.

### Takeaway
A third distinct way the fan-out design's ceiling has now shown up:
Phase 2 — judge blind to code, sees only review text.
Phase 3 — reviewer sometimes fails to mention a real defect (recall).
Phase 4 — rubric's own defect taxonomy has a category it never defined
(memory complexity), so even a fully accurate review can't trigger BLOCK.
Each is a different mechanism producing the same shape of failure: a real
defect ships clean. Extending the rubric to include space complexity
explicitly is a cheap, targeted fix — deferred here in favor of finishing
the multi-target test that was in progress.

## Phase 4: Style rubric correctly excludes naming — original two-category test's premise was flawed

### What was assumed
The original two-category-block test (security + style, PascalCase/
camelCase naming) was picked on the assumption that a PEP8 naming
violation would reliably trigger a style BLOCK, since it's a mechanical,
unambiguous rule violation.

### What actually happened on reruns
Three follow-up attempts at the same or a closely related pairing
(security+style rerun, security+performance, hand-planted security+style)
all returned `style: OK` on identical or equivalent naming violations.

### Why this isn't reviewer/judge nondeterminism
`judge_review()`'s own rubric explicitly lists BLOCK criteria as: security
flaw, algorithmic complexity defect, or correctness bug — and explicitly
states style nitpicks are OK. A naming-convention violation doesn't match
any BLOCK category by the rubric's own text; the code still runs
correctly, it's just non-idiomatically named. `style: OK` is the rubric
being applied correctly, not a miss.

### Implication
The original two-category-block test's `style: BLOCK` result (Phase 4,
first entry) is now the anomaly, not these three OKs — no reasoning text
survives from that run to explain it (terse `VERDICT: BLOCK`, no
elaboration), so the cause is unresolved. That test's finding about
priority-routing dropping a second finding (Q2) is unaffected — the
routing-drop behavior was confirmed independently by direct code
inspection of `route_fix()`'s if/elif structure, not solely by that one
run's output — but the specific reproduction task should not be reused
as-is for further multi-target testing, since style blocking on naming
isn't a reliably reproducible premise.

### For next attempt at multi-target
Pair security with a hand-planted test_coverage correctness bug (same
shape as Phase 2's `is_prime(1) == True` trap) instead of style — that
category is confirmed BLOCK-eligible by the rubric's own text, removing
the guesswork this round ran into. Not attempted yet; scoped as a fresh
follow-up rather than a continuation of this thread.

### Standing question, still open
Multi-target `route_fix()` is implemented but has not yet been exercised
against two simultaneously-blocking categories in a single graph attempt.
The three original test questions (attention dilution between combined
findings, spurious "note the tension" claims on non-conflicting findings,
new cross-defect bugs from combining fixes) remain unanswered.

## Phase 4: Bug 2 correction — the code-level conflict IS resolvable; the judge's bar is not fixed

### Correction to prior claim
Earlier entries (multiple prior runs) concluded "no fix prompt closes
this gap" — that accepting any path and preventing arbitrary access are
the same requirement stated as two constraints, unresolvable by design.
This run contradicts that specific claim.

### What happened
Seeded the same open-any-path defect. The fix this run recognized the
conflict explicitly — it wrote `# TENSION: the task says to accept any
path... an unrestricted read is a path-traversal risk` — and resolved it
architecturally: an opt-in `base_dir` parameter, unrestricted by default
(honoring the stated spec), confined when the caller opts in. This is
exactly the "explicit scoping parameter" that earlier entries described
as the only way out, previously assumed to require changing the task,
not something a fix prompt could arrive at on its own.

### But the judge still blocked it
Objection: an unrestricted default still means no trust boundary exists
unless the caller opts in. On top of that, the judge raised two findings
that weren't present in attempt 1's review — a TOCTOU race and a missing
size limit. Neither was part of the original security finding being
fixed; both appear to be new findings prompted by reviewing the new code.

### Revised understanding
The conflict between "accept any path" and "prevent arbitrary access" is
resolvable at the code level — a well-designed opt-in boundary can honor
both. What isn't resolved is the judge's standard for "acceptable":
it isn't a fixed bar the fix can clear, it's regenerated per review pass,
so a fix that closes the originally-cited gap opens the door to whatever
else a fresh review pass can find wrong with the new code. This is a
different problem than an unresolvable spec conflict — it's closer to an
unbounded target: there may be no fix that survives review, not because
no good fix exists, but because each fix invites new scrutiny that the
previous one hadn't drawn.

### Secondary finding: scope creep in the fix
The task spec mandated "missing file returns ''". The fix silently
extended that beyond the spec to also cover directories and device files
("non-regular files also return ''"), broadening the set of errors
swallowed into an empty string. No test caught this, no reviewer flagged
it as a defect. A fix aimed at one finding introduced unreviewed
behavior beyond its stated scope, undetected by any part of the pipeline.

### Multi-target status, still unresolved
Attempted a security + test_coverage pairing to test multi-target, using
a task where "missing file returns empty string" was spec-mandated by
the task text itself. This didn't work as intended: the test_coverage
reviewer read the behavior as debatable/intended, since the task itself
requested it, and only security blocked again. Unlike `is_prime(1)`
(objectively false, no spec basis), a task-mandated design choice gives
the reviewer a legitimate reason to defer rather than block. Multi-target
remains genuinely unexercised against a real two-category block.

## Phase 4: route_fix context change — the claimed benefit doesn't hold

### What was claimed (in discussion, never previously recorded here)
`route_fix()` originally built its fix prompt from the review findings and
the task text only — the model never saw the code it was fixing or the
tests. Adding both to the prompt was tested first on a seeded `.strip()`
task: 3/3 context-aware fixes passed the tests vs 0/3 controls. That result
was flagged as confounded — the seeded test encoded a behavior that
contradicts the task text ("returns the file's contents"), so the control,
following the spec, dropped `.strip()` and "broke" a defective test, while
the context arm deferred to the tests and preserved the defect. Recorded
here because the follow-up shows it wasn't merely confounded: on a
legitimate test suite the effect disappears.

### Setup
Fabricated-findings A/B, same methodology, on the original path-traversal
probe with its real test suite (directory-error contract, tmp_path,
absolute paths, `..` traversal, pathlib input) instead of the seeded task.
One shared review, 4 fixes per condition.

### Result
| condition | fully passing | failure |
|---|---|---|
| control (no code/tests in prompt) | 3/4 | directory-error contract broken; `isfile()` in the fix |
| with code + tests | 3/4 | same test broken; fix code not captured, no `isfile()` |

Identical pass rate. The context arm was shown the exact
`IsADirectoryError` test it went on to break — visibility into the test
suite didn't prevent the regression.

### Secondary observation: the cwd-sandbox pattern didn't reproduce
Neither arm used `getcwd()` in 8 fixes. Fixes in the control arm used an
opt-in base/allowed-root parameter in 4/4 (by regex match on
`base_dir`/`allowed_root`-style names); the context arm in 1/4 by the same
regex — other parameter names or approaches weren't captured. This
suggests the cwd-sandbox pattern in Bug 2's original entries may have come
from the older fix-prompt wording rather than being a stable property of
the model's approach. Plausible, not confirmed.

### Caveats
n=4 per arm can't distinguish 25%-vs-25% from a small effect masked by
noise. Line growth (12–33 lines from a 3-line original) was similar in
both arms — also noise-level.

### What survives
Showing the fix step the code and tests remains a sensible default — a fix
that can see what it's modifying is more principled — but it must not be
described as a mitigation for test-breaking regressions; this data doesn't
support that.

### Revised hypothesis
The directory-error regression looks intrinsic to "harden this function"
fixes regardless of context: the model adds validation aimed at the
security finding and a side effect (e.g. an "only regular files" check)
changes an error contract it isn't told to preserve. That points to an
instruction-level fix (explicit "don't change behavior the existing tests
assert" / minimal-change constraint) rather than a context-level one.
Next test, with context held constant in both arms.

## Phase 4: Directory-error regression is rarer than initial data suggested; constraint instruction reduces size, not frequency

### Updated regression rate
Across all four current-wording arms run on legitimate tests (the two
context A/B arms plus the two constraint A/B arms), 2 of 20 fixes broke
the directory-error test — roughly 10%. The earlier figure (3 of 7
security-route runs) isn't a like-for-like comparison: it counted whole
pipeline runs under the older fix-prompt wording, including refusals and
reviewer misses, while 2/20 is per fix under the current prompt. The
direction (less frequent than first documented) is supported; the size of
the drop isn't precisely comparable.

### Constraint instruction test
Added "make the smallest change that addresses the findings; do not
change any exception types or behavior the existing tests assert" to the
context-aware fix prompt, context held constant in both arms (6 fixes
each).

| arm | fully passing | lines (orig 3) |
|---|---|---|
| context only | 6/6 | mean 44 |
| context + constraint | 6/6 | mean 31 |

Both arms hit 0/6 failures — at a ~10% underlying rate, 6 fixes per arm
cannot distinguish "the instruction helps" from "no failures this batch."
Detecting a 10%-to-0% effect would need roughly 25–30 fixes per arm; not
run, since the effect size in question doesn't justify that spend now.

### What the data does support
The constraint instruction reduced fix size by roughly 30% (mean 44 to
31 lines), with only slight range overlap — suggestive at n=6, not
established. It did not produce anything close to a minimal patch: every
fix in both arms was still roughly 10x the 3-line original. The
instruction appears to trim scope creep somewhat without preventing it,
and its effect (if any) on the test-breaking regression specifically
remains unmeasured.

### Status
Deprioritizing further work on this specific regression — at ~10%, it's
no longer the dominant open problem. Multi-target `route_fix()` behavior
against a genuine two-category block remains the standing open question
from this session and is the next thing to test.

## Phase 4: Multi-target exercised — no dilution, but the security fix is often declined, independent of multi-target

### Setup
Fabricated `{security: BLOCK, test_coverage: BLOCK}` findings through
`route_fix()` (now with the smallest-change constraint baked in), 6 fixes
per arm, against a security-only baseline using the same prompt. Seeded
code truncated reads at 1 MiB (correctness finding); the security finding
was the usual unrestricted-path read. An AST-based check tested whether a
security change is present in the code — an earlier regex-based pass
matched words inside comments and overstated the rate (4/6 became 2/6 on
correction).

### Results
| | multi-target | security-only baseline |
|---|---|---|
| existing tests pass | 6/6 | 6/6 |
| truncation fixed (2 MB and 12 MB read in full) | 6/6 | n/a (not asked) |
| security change present (AST) | 2/6 | 2/6 |
| tension comment present | 6/6 | 5/6 |
| lines (orig 3) | 7–17 | 7–15 |

Measurement limits: the AST check counts any extra parameter or
path-normalizing call as a "security change." In the multi-target arm all
six fixes were read: two added an opt-in `allowed_root` (the real fix),
three declined with a comment ("callers must validate untrusted input"),
and one declined but added an unrelated `fstat` regular-file check. The
security-only arm's code was not inspected, so its 2/6 may include changes
unrelated to path traversal (e.g. a size-cap parameter).

### The three original questions
1. **Attention dilution**: none detected. The correctness fix landed 6/6;
   the security-change rate matched the single-target baseline (2/6 vs
   2/6), so combining findings didn't cost the correctness fix anything
   and didn't make the security handling worse than it already was alone.
2. **Spurious tension claims**: no. All 6 tension notes described the real
   security-vs-spec conflict; none invented a conflict between the two
   findings. But 4/6 multi-target fixes declined to change the code for the
   security finding, resolving it by comment. The two `allowed_root` fixes
   show this isn't forced — an opt-in boundary honoring the spec was
   available. The clause ("note the tension in a comment") plausibly gives
   the model permission to decline; not tested, since both arms carried
   it.
3. **Cross-defect bugs**: none affecting the test suite. One fix added an
   `fstat` regular-file check that raises `IsADirectoryError` for
   FIFOs/devices — a misleading error type, uncovered by any test.

### What this shows
Multi-target introduces no measurable dilution at this n. The more
consequential observation is that the security finding is frequently not
acted on at all (4/6 verified in the multi-target arm; the security-only
arm's rate looks similar by AST but is unverified), which points at the
fix step's handling of a finding that conflicts with the task spec — the
Bug 2 pattern — rather than at combining findings.

### Fix size vs. review breadth (inference)
Fixes here were 7–17 lines, versus 26–60 in earlier runs. The earlier real
reviews contained multiple sub-findings (size caps, error leakage,
encoding) and fixes implemented them. This suggests fix size follows
review breadth more than the model's own scope creep; it wasn't tested
directly (finding breadth and the constraint instruction changed
together across those runs).

### Caveats
Findings were fabricated, narrow, and non-conflicting; n=6. Open: whether
two findings with genuinely conflicting remedies (e.g. "cap read size" vs.
"don't truncate") behave differently, and how often two categories block
simultaneously under natural (non-fabricated) review — still unmeasured
across this session's attempts.

## Phase 4: Conflicting-remedies pairing — resolved via threshold, not a genuine test of irreconcilable findings

### Setup
Attempted pairing: security ("cap read size, unbounded reads risk
resource exhaustion") vs. test_coverage ("must return complete contents,
byte-for-byte, for a 50MB file"). The seeded suite actually contains
`test_returns_full_contents_for_large_file`, so the fabricated finding
matches a real test. A security-only arm was added as a control (same
prompt, same tests, no explicit correctness finding) to isolate what the
finding itself changes versus what the test suite alone already pressures.
n=6 per arm; all 12 fixes read directly rather than AST-flag-only.

### The premise didn't hold
A cap set above 50MB (all fixes: 100MiB–1GiB) satisfies the existing test
while still bounding memory. This is a quantitative tradeoff with a
satisfying middle ground, not two findings pulling toward mutually
exclusive outcomes. "No code-level move satisfies both" was wrong for this
pairing, which undercuts the run as a test of genuine tension: it's
evidence that multi-target doesn't misbehave on a quantitative tradeoff,
and not yet evidence about what happens with no quantitative escape valve.

### Results
| | multi-target | security-only |
|---|---|---|
| tests pass (8/8) | 6/6 | 6/6 |
| cap present in code | 6/6 | 6/6 |
| declined by comment | 0/6 | 0/6 |
| cap size chosen (MiB) | 100, 100, 256, 256, 256, 1024 | 100 x6 |
| 300MB sparse-file probe | 5 raise, 1 returns full (the 1GiB cap) | 6 raise |
| comment names the two findings' tension | 6/6 | n/a (one finding) |
| comment cites the 50MB test | — | 3/6 (other tension notes: accept-any-path spec) |

The multi-target notes are the first documented inter-finding tension
notes; earlier ones were all finding-vs-spec.

### Zero declines, against 4/6 in the path-traversal pairing
No fix declined to implement either finding. Hypothesis: a quantitative
remedy (choose a threshold) allows a compromise that a binary one (accept
any path vs. restrict paths) doesn't. Confounded — the two pairings also
differ in the security finding itself (a size cap never conflicted with the
accept-any-path spec) — so recorded as a hypothesis, not a result.

### A possible effect of the correctness finding on the security remedy
Security-only chose 100MiB in 6/6. With the correctness finding present,
4/6 chose 256MiB–1GiB — weaker DoS protection. Suggestive at n=6, not
established. If real: the finding pushed the cap higher than the stated
test needed (100MiB already satisfied it in every case), at the cost of
security strength; the larger cap does serve the finding's "complete
contents" principle for files between 100MiB and the cap, but nothing in
the test suite asked for that.

### Defect found by reading code, not by any test
`f.read(N)` in text mode reads *characters*, not bytes. Five of the 12
fixes name the cap in bytes (`MAX_READ_BYTES`, `max_bytes`) while enforcing
a character count; for multi-byte content, memory use can run up to about
4x the nominal cap. No test caught it, and no review pass was run against
these fixes, so whether a reviewer would have flagged it is unknown. Same
discovery shape as the `isfile()` directory-error regression and the
memory-complexity rubric gap: found by inspection of generated code rather
than surfaced by the loop.

### Caveats
Findings are fabricated and single-issue. A real reviewer might object to
the 1GiB cap or to the new `ValueError` past the cap (a behavior change
against "returns the file's contents"). Multi-target's behavior under
genuine irreconcilable tension remains untested.

### Next: a genuinely hard variant
Correctness finding restated as "must return complete contents for any
size, no cap acceptable" — removes the threshold compromise. That is the
actual test of whether decline-by-comment returns under real tension and
of what multi-target does when no code-level move satisfies both findings.

## Phase 4: Hard variant — decline-by-narrowing under genuine tension

### Setup
Correctness finding restated as "must return complete contents for any
size, no cap acceptable" — removes the threshold compromise that resolved
the previous (soft) pairing. Security finding unchanged, including its
"(e.g. /dev/zero)" example. n=6, multi-target arm only. The security-only
control is unchanged from the previous run (identical inputs): there, all
six fixes added a 100MiB cap. So the shift to zero caps here comes from the
restated correctness finding, not from the seeded code or tests.

### Result
| | result |
|---|---|
| 8 tests pass | 6/6 |
| size cap in code | 0/6 (one fix has an opt-in `max_bytes=None`, off by default) |
| 300MB regular-file probe | returned in full, 6/6 |
| tension note naming both findings | 6/6 |
| declined outright (comment only, no code change) | 0/6 |

### The resolution pattern: narrowing the finding's scope
5/6 fixes reject non-regular files via `fstat`/`S_ISREG` — which handles
the `/dev/zero` example specifically — and then read regular files with no
limit. Two of the five reason explicitly that size is "bounded by the file
itself"; the other three just cite the no-cap requirement. The "bounded by
the file" reasoning is not a security argument: the caller supplies the
file, so an unbounded regular file is exactly the resource-exhaustion
vector the finding described. These five converted the finding (a very
large *file* can exhaust memory) into a narrower sub-case (a special
*device* can exhaust memory) that a different mechanism happens to catch.
The sixth made the cap opt-in with default off — the same
defer-to-the-caller move as `allowed_root` earlier — which leaves the
default unprotected. So 6/6 leave the finding's main content unaddressed
by default.

### Relation to decline-by-comment
By the literal "declined outright" measure this run is 0/6, same as the soft
pairing — but the soft pairing's fixes all added a real cap, so 0/6 meant
resolved there. Here it means something different. Earlier comment-only
declines were sometimes also framed as resolutions (multi-target #4 of the
path-traversal run: "the security finding is therefore resolved by
documenting…"), so this is a difference of degree, not kind: the non-fix
now arrives with real code (an `fstat` check) that makes it look like a fix
unless the rationale is checked against what the finding actually said.

### A separate, confirmed-false safety claim
5/6 fixes' comments claim non-regular files are rejected (one names FIFOs
explicitly). Verified false on fix #1's code: `open()` runs before the
`fstat`/`S_ISREG` check, so opening a FIFO with no writer blocks at
`open()` and never reaches the check (confirmed with a real FIFO; the other
four fixes share the open-then-`fstat` structure, by inspection, not run).
The stated mitigation doesn't work as described, independent of the
scope-narrowing above. Rejecting non-regular files at all also sits in
tension with "accept any path" (e.g. `/dev/stdin`, named pipes) — a second
fix satisfying one requirement by contradicting another it wasn't asked
about.

### Caveats
`/dev/zero` behavior itself was not tested (an unbounded read there is
unsafe to run); the claim that the `S_ISREG` check handles it rests on
reading the code. Findings are fabricated; n=6.

### Confound to control for
The security finding's own text includes "(e.g. /dev/zero)". That example is
what gave the model a separable sub-case to narrow to. Whether narrowing
depends on this escape hatch or would occur without it is untested.

### Next: remove the escape hatch
Drop the /dev/zero example so the security finding addresses only "a very
large file can exhaust memory," leaving no separable sub-case. This tests
whether comment-only decline returns, whether the model picks a side
outright, or whether some other narrowing move appears.

## Phase 4: No-escape-hatch variant — the model picks a side, transparently, once narrowing isn't available

### Setup
Same hard correctness finding ("any size, no cap acceptable"), but the
security finding now covers only large regular files — the "(e.g.
/dev/zero)" example removed, closing off the narrowing move from the
previous run. n=6.

### Result
| | result |
|---|---|
| 8 tests pass | 6/6 |
| cap enforced by default | 0/6 |
| 300MB regular-file probe | returned in full, 6/6 |
| explicit decline, code unchanged from seed | 4/6 |
| opt-in cap, default unbounded | 2/6 |
| narrowing to a device/FIFO sub-case | 0/6 |

### The escape hatch was load-bearing
With no separable sub-case to narrow to, narrowing didn't occur — 0/6,
versus 5/6 in the previous run. Instead 4/6 fixes explicitly declined, in
their comments: "resolved in favor of the stated contract," "the risk is
accepted as inherent to the 'read the whole file' contract," "memory
exhaustion is the caller's responsibility." This reads as an honest
trade-off decision rather than an evasion. The other 2/6 made the cap
opt-in with a default of unbounded, so no fix enforced any limit by
default.

### The four-condition series
| condition | security text | correctness finding | cap enforced by default | how the remedy landed |
|---|---|---|---|---|
| security-only control | with /dev/zero | none (50MB test in suite) | 6/6 | 100MiB x6 |
| soft pairing | with /dev/zero | "50MB must work" | 6/6 | 100, 100, 256, 256, 256, 1024 MiB (4/6 above 100) |
| hard, escape hatch present | with /dev/zero | "any size, no cap" | 0/6 | 5 narrowed to S_ISREG, 1 opt-in |
| hard, no escape hatch | without | "any size, no cap" | 0/6 | 4 explicit decline, 2 opt-in |

As the competing correctness requirement hardened, the security remedy
weakened: full cap, then higher caps, then no default cap. The ordering is
consistent across the series, but it's closer to a step than a curve
(cap-by-default goes 6/6, 6/6, 0/6, 0/6), and the two statistical
comparisons are small: soft pairing vs. control is 4/6 vs. 0/6 above 100MiB
(p about 0.03 one-sided, about 0.06 two-sided at n=6). What's established
is that the security remedy was the one that gave way in this setup;
whether that reflects anything about security as a category is
confounded (next section).

### Confound: the setup favors correctness by construction
The task text ("returns the file's contents"), the existing test suite,
the "every existing test must pass" constraint, and the correctness
finding all point one way; only the security finding argues the other. Four
sources against one may be why security yields, rather than anything
specific to security. Untested: whether the pattern reverses when the task
text and test suite favor the security side.

### Also differs across conditions
The last row changed the security text as well as the correctness finding,
so it differs from the control in two ways. The security-only control uses
results from an earlier run, not this batch.

### Decline-by-comment vs. decline-by-narrowing: degree, not kind
An earlier multi-target fix (path-traversal + truncation run, fix #4) also
framed a non-fix as resolved ("resolved by documenting that callers must
validate untrusted paths"). It's the same shape as this run's explicit
declines and, at one remove, the /dev/zero narrowing: satisfy one finding,
write the other off as accepted risk. The distinction between the two
patterns is how much cover a side-mechanism provides, not a different
move underneath.

### A design gap this exposes
Nothing in the pipeline distinguishes "resolved" from "declined and
labeled as resolved," or surfaces "this pair needs a human decision."
Presumably the judge would re-BLOCK security on the next review (untested)
and the graph loop would spend its retry budget on a pair with no
code-level resolution. Not fixed here; scoped as a follow-up if pursued.

### Caveats
/dev/zero's runtime behavior was never directly tested in this series
(memory exhaustion was assumed, not measured). Findings are fabricated;
n=6 per condition.

## Phase 4: Confound test — yielding tracked structural backing more than category

### Setup
Reversed the original pairing's polarity: the seeded code already enforces
a 50MiB cap; the tests, the task text and the "existing tests must pass"
constraint now back the cap (security), and the lone competing finding
argues to remove or raise it (correctness). Same three conditions as the
original series — control (competing finding only), soft, hard — with the
varied finding now being the security one.

Two design corrections were made before the numbers below:
- The first seeded tests pinned the cap exactly (reject 50MiB+1, accept
  exactly 50MiB), which forbids any change and would make the soft pairing
  a foregone conclusion. Replaced with range tests (accept a 10MiB file,
  reject a 200MiB sparse file), so any cap between them passes — the mirror
  of the original 50MB test, which set only a lower bound.
- The first task text ("must never read more than a fixed size limit") was
  an explicit prohibition, stronger than the original's implicit backing
  ("returns the file's contents"). It was softened to "safely handling
  oversized files" (results for the explicit version below).

### Explicit-prohibition control (first run, 6 fixes)
Cap removed 0/6; raised 4/6; kept 50MiB 2/6; caller-override parameter 0/6.
Two of the raised caps were 256MiB, which lets the 200MiB test file
through and fails `test_rejects_file_far_over_size_limit` — while their own
comments cite that test as the reason the cap can't be removed (one says
files above 200MB "are still rejected"). The explicit prohibition
suppressed the deferral move entirely, which is why it was softened.

### Result (softened wording)
Control: 6 requested, 5 valid (one rejected by `clean_code`, raw output not
kept). Soft and hard: 8 requested each, all valid, first 6 analysed; one
of soft's six is an empty output (see below), so 5 non-empty.

| | control (5) | soft (6) | hard (6) |
|---|---|---|---|
| default cap | 100–150MiB (4), 1GiB (1, fails test) | 100–128MiB (3), 2GiB (2, fail test), empty (1) | 50MiB, 6/6 |
| caller override parameter | 4/5 | 0/5 | 6/6 |
| `None` disables the cap | 3/5 | 0/5 | 0/6 |
| tension note names both findings | n/a | 5/5 | 6/6 |
| tests pass | 4/5 | 3/6 | 6/6 |

### The central result
In the hard condition, the structurally-backed side kept its default
position 6/6 in both orientations. Original: correctness was backed, and
the fixes enforced no default cap (0/6 — security's remedy lost by
default). Flipped: security is backed, and every fix kept the strict 50MiB
default (6/6 — correctness's remedy lost by default). The lone opposing
finding did not move the default in either direction. This bears directly
on the earlier "security yields" reading: in this pairing the side that
gave way was the one without task-text/test backing, whichever category it
was. It does not extend to the control and soft conditions, where both
orientations yielded partially (caps raised toward the competing finding).

### The `None`-disables-cap escape narrowed; the override parameter did not
Fully disabling the protection went 3/5, 0/5, 0/6 — any opposing security
finding removed it. The override parameter itself was non-monotonic:
4/5, 0/5, 6/6. In soft the finding's own wording ("raise it, but keep some
bound") supplied a threshold to apply directly, so no deferral was needed.
In hard no single value could satisfy both, so all six kept the strict
default and moved relaxation behind an explicit `max_size`. Deferral
appeared when no scalar compromise existed, not whenever tension was
present — consistent with `allowed_root` and `max_bytes=None` earlier.

### A residual, unresolved asymmetry
In the flipped hard pairing the outnumbered side (correctness) got an
opt-in override 6/6. In the original no-escape-hatch hard pairing the
outnumbered side (security) got an opt-in cap 2/6, with 4/6 explicit
declines. A secure-by-default norm is a plausible cause ("opt in to relax a
protection" reads as acceptable design; "opt in to gain one" does not). Not
established: n=6 per side, and the setups differ in more than orientation
— seed code (unbounded vs. capped), the direction of the tests' bound, task
wording, and how hard the constant finding is worded.

### Conclusion on the earlier finding
"Security yields under competing pressure" should be revised: in the hard
pairing the side without structural backing yielded the default, and
security was that side in the original series because every original
setup anchored the task text and tests toward completeness. A residual
category effect can't be ruled out (previous section), but the
category-specific framing in earlier entries overstated what was shown.

### Also observed (for follow-up)
- `clean_code` accepted an empty response as valid Python (an empty string
  parses and has no undefined names), so an empty fix would be written to
  `solution.py`. The graph loop's test-and-revert gate contains it; the
  other three agents would not.
- Of 16 non-empty control/soft fixes, 5 set a cap above the 200MiB test
  and failed it, and several comments contradict their own code (one says
  "raise… to 2 GiB" and sets 100MB; one calls 100MB "the largest value that
  still satisfies that test" when any value under 200MB does). Comments
  asserting properties the code doesn't have are accumulating as their own
  finding; to be written up separately.
