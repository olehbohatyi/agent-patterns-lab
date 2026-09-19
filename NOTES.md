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
