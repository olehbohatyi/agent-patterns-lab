# Findings: fan-out review and fix loops around `claude -p`

Research phase closed. This document is thematic and states each finding in
its final, corrected form. [NOTES.md](NOTES.md) is the chronological lab
notebook: it holds the evidence, the wrong turns and every correction, so
any claim here can be traced back to it (section titles are given as
pointers).

## 1. What this project is

Four small scripts that drive the `claude` CLI (`claude -p`) to write a Python
function and its tests for a task, then check the result:

| script | what it does |
|---|---|
| [agent_linear.py](agent_linear.py) | one attempt: generate, test, report |
| [agent_loop.py](agent_loop.py) | on test failure, feed pytest output back for a fix (up to 3 attempts) |
| [agent_diamond.py](agent_diamond.py) | once tests pass, 4 reviewers (security, performance, style, test coverage) run in parallel; each review is judged in isolation (BLOCK/OK against a severity rubric); Python computes the verdict |
| [agent_graph.py](agent_graph.py) | routes blocked categories to a fix prompt (one prompt covering every blocked category), re-reviews, and reverts a fix that breaks tests |

The question the work followed: **where do fan-out review-and-fix
architectures fail, and are those failures visible to the loop itself?**
Most experiments used probes with known defects, often hand-planted (code
generation tends to avoid naive traps on its own), and fed *fabricated
findings* straight into the fix prompt to separate the fix step's behavior
from reviewer variance.

## 2. How to read the confidence

Samples are small: typically 4–8 fixes per condition and 1–7 runs per probe.
No number below is a general rate. Everything was measured through the local `claude -p` backend; the
Anthropic API backend added afterwards is a different system (see "Backend dependence" in section 4).
Model aliases (`sonnet`, `haiku`) track the latest model and aren't pinned, and the resolved model wasn't
recorded during the research phase; today they resolve to `claude-sonnet-5` and
`claude-haiku-4-5-20251001`.
Tags:

- **Measured**: counted across repeated runs, small n.
- **Inspected**: verified by reading or executing specific generated code.
- **Single observation**: seen once.
- **Hypothesis**: proposed, not isolated.

## 3. Findings

### 3.1 The verification layer has structural blind spots

**V1. The judge trusts review text it can't check.** The judge sees only the
review, never the code. A fabricated "no issues found" review passed on
genuinely vulnerable code (a deterministic probe, then 3 direct checks: OK
every time). Giving the judge the code would close this, but would turn it
into a fifth reviewer rather than an independent check. *Measured, small n.*
(NOTES: "Closing finding — the architectural ceiling of asymmetric
verification".)

**V2. Isolation is a prompt convention, not a construction.** `claude -p`
can read files in the working directory unless told not to (checked
directly). The judge didn't do so in 3 checks, so the isolation held in
practice. The earlier "each call is a blank slate" claim was wrong. *Inspected +
measured.* (NOTES: "Isolation caveat".)

**V3. Reviewers miss defects intermittently.** The performance reviewer
missed a seeded O(n²) defect in 1 of 3 runs; the security reviewer missed a
path-traversal defect in the original probe and in 2 of 7 later security-route
graph runs. These are incidents, not a rate. Downstream, nothing recovers a defect that is
never reported. *Measured, small n.*

**V4. The judge's rubric had three separate gaps, each producing a defect
that ships clean.**
- *Hedge dismissal.* With no severity bar the judge invented one ("is there
  a demonstrated failure in current usage?") and punished reviewers for
  honest hedging ("if untrusted input…"). An explicit rubric fixed this on
  both probes, though the rubric names those defect classes, so the fix is
  partly circular. Per-category isolation alone did **not** fix it; that
  hypothesis was falsified.
- *Lane check.* A clause blocking only defects in the reviewer's own lane
  stopped honest out-of-lane mentions from over-blocking, but removed the
  accidental redundancy that caught a defect when its owning lens stayed
  silent: `prime_bug` on sonnet went to 3 full misses in 7 runs, against no
  misses in the few pre-fix runs. Unresolved trade-off.
- *Memory complexity.* The performance criterion covered only time
  complexity, so a seeded read-the-whole-file defect for a multi-GB task
  passed although the review described it accurately (1 seeded run).

*Measured / single observation.* (NOTES: "Fixing the aggregator", "Lane-aware
rubric fix", "Rubric gap".)

**V5. A first-word verdict parse silently inverted self-corrections.** A
judge reasoning aloud ("BLOCK… wait, no… OK") was scored on its first word.
Fixed with an explicit `VERDICT:` marker; ambiguous or missing verdicts
default to BLOCK. *Inspected.*

**V6. Cheaper reviewers tier cleanly on scoped defects, with a twist.**
Sonnet and haiku reviewers matched exactly on 2 of 3 probes. On the third
(an obvious bug), haiku restated the defect in every lens (5 of 6 runs), which
hurt per-category attribution but turned out to make it *more* robust than
sonnet once the lane check was added. The probe's defect was blatant, so it
tests lens discipline more than detection. *Measured, small n.*
(NOTES: "Model tiering".)

### 3.2 The fix step

**X1. Routing works; fix quality depends on the defect.** The right category
reached the right prompt every time it fired. Performance fixes were clean
(2 of 2). Security fixes failed in different ways early on (a cwd-scoped
sandbox twice, an `isfile()` check that collapsed the directory-error
contract, one refusal). Under the later prompt, 2 of 20 fixes broke that
contract (about 10%; not like-for-like with the earlier figure, which counted
whole pipeline runs). *Measured, small n.*

**X2. Showing the fix step the code and tests did not reduce test breakage.**
A first result (3/3 vs 0/3) was confounded by a defective seeded test; on a
legitimate suite it was 3/4 vs 3/4. The change is still a sensible default,
but not a demonstrated mitigation. A "smallest change" instruction shortened
fixes by about 30% (mean 44 to 31 lines, n=6) without approaching a minimal
patch, and fix size seems to follow the breadth of the review (7–17 lines
for narrow fabricated findings vs 26–60 for real multi-issue reviews; an
inference, not isolated). *Measured, small n.*

**X3. Multi-target fixing showed no dilution on a clean pairing.** With a
security and a correctness finding together (n=6), the correctness fix landed
6/6 and the security-change rate matched the single-finding arm (2/6 vs 2/6,
though that arm's code wasn't read). Findings were fabricated, narrow and
non-conflicting. Natural two-category blocks were never reproduced reliably.
*Measured, small n.* (NOTES: "Multi-target exercised".)

**X4. Control-flow and validation gaps found and closed.**
- A fix that broke tests hard-exited the graph instead of counting as a failed
  attempt. It now reverts and continues (inspected, observed live once).
- `clean_code` accepted code that parsed but referenced undefined names (a
  missing `import os` failed only at runtime, inside a function body), and
  accepted empty output. Both are now rejected in all four scripts.
- The test-generation prompt never showed `solution.py`, so tests guessed the
  function name and failed on import; the retry loop couldn't recover
  because it only rewrites `solution.py`. Fixed by including the code.
  *Inspected.*

**X5. Escalation gap closed — with mechanical history, not a stuck classifier.**
A design that had the fix step self-declare what it addressed, cross-checked
against the diff, was rejected: judging whether a diff "addresses" a finding
needs the same semantic call the false-claim pattern (C1) already showed is
unreliable — it would move that problem up a layer, not remove it. Built
instead on two zero-trust, mechanical signals tracked per category per
attempt: did `solution.py`'s text change since this category last blocked,
and does it still block. Ambiguous cases (code changed, still blocks — which
covers both an insufficient fix and a real fix that shifted the review
surface, as in K1) are left unclassified and printed as a transcript for a
human, not resolved automatically. Validated on 3 synthetic histories and 1
live exhausted run, where a security fix's added validation opened a new,
real `test_coverage` finding on attempt 2 — previously invisible behind a
bare exit — correctly reported as "changed, unresolved." *Inspected +
single live observation.* (NOTES: "Escalation gap addressed".)

### 3.3 How the fix step handles conflicting requirements

**K1. Security finding vs task spec ("accept any path").** In the
multi-target arm, 4 of 6 fixes declined to change the code and resolved the
finding by comment; 2 of 6 added an opt-in `allowed_root`. A fix with an
opt-in boundary honored the spec, but the judge still blocked it on the next
review and raised objections the first review hadn't (a TOCTOU race, a size
limit), so the standard for "acceptable" moves with each review pass.
*Measured (n=6) + single observation.* (NOTES: "Bug 2 correction".)

**K2. A threshold escape changes the behavior.** When a value satisfies both
findings (a size cap above the tested file size), all 12 fixes used it and
none declined. When no value can (must return any size), fixes either
narrowed the finding to a separable sub-case when one existed (5 of 6, via a
non-regular-file check) or, with none available, picked a side explicitly
(4 of 6 declined in a comment, 2 of 6 added an opt-in cap). Confounded with
the findings differing in kind. *Measured, small n; hypothesis for the
mechanism.*

**K3. Yielding tracked structural backing more than category.** The original
series suggested "security yields under competing pressure." Reversing the
setup (the tests, task text and constraint now backed security) showed that in
the hard condition the backed side kept its default 6/6 in both orientations.
A residual asymmetry remains (the outnumbered side got an opt-in override 6/6
when it was correctness vs 2/6 when it was security) and isn't disentangled
from other setup differences. The earlier category-specific claim was
overstated. *Measured, small n.* (NOTES: "Confound test".)

**K4. Experiment design changes the answer.** An explicit prohibition in the
task text suppressed the defer-to-caller move (0/6 overrides vs 4/5 with softer
wording), and tests that pinned a cap exactly would have decided the outcome
in advance. Both were caught and redesigned before the numbers were used.

### 3.4 Comments that assert what the code doesn't do

**C1.** The fix model writes comments claiming a constraint is met or a
mitigation works, and some are false. Instances found while reading fixes
for other purposes: claims of test compliance contradicted by the tests
(raising a cap to 256 MiB while citing the 200 MB rejection test as the
constraint, which a 256 MiB cap fails), a mitigation
that fails at runtime (a comment says FIFOs are rejected; `open()` blocks
before the check, confirmed with a real FIFO on one fix and by inspection on
the others), comments contradicting their own code ("to 2 GiB" above a
100 MB constant), a byte-named cap that counts characters, and "resolved"
written over unaddressed findings. Only claims about the existing test suite
are caught mechanically (by the post-fix test run); nothing checks the rest.
This is a set of incidents, not a rate, and whether a reviewer would catch
them is untested. *Inspected.* (NOTES: "Comments that assert properties the
code doesn't have".)

## 4. Known gaps, not addressed

- **Judge blindness** (V1) is architectural; the memory-complexity rubric gap
  and the lane-check trade-off (V4) are unresolved.
- **Isolation** is a convention (V2); nothing enforces "do not use tools."
- **Backend dependence.** `claude -p` runs in the repository, can read files there and loads project
  context; a bare API call sees only the prompt and carries no CLI system prompt. Whether any finding
  depended on those differences (context-related ones such as X2, the isolation finding V2, and the
  decline patterns are the obvious candidates) is unknown: nothing has been measured on the API backend.
- **The Jev judge is calibrated only lightly.** A live comparison on 27 frozen reviews (20 recovered
  verbatim, 7 regenerated, 4 of the 27 contestable) found it matched the rubric-derived labels 27/27
  against 26/27 for the LLM judge. Both matched all 7 regenerated cases; the one disagreement is on a
  recovered coverage-gap review, judged three times. The corpus had no near-boundary cases, so the
  0.5 threshold is untested, and it comes from three defect setups; see NOTES.md "Jev judge, stage 1
  calibration". Both judges share the reviewer-miss blind spot.
- **Reviewer coverage of false claims** (C1) and of the extra behavior fixes
  introduce is untested, because the fix experiments ran without a second
  review pass.
- **Natural multi-category blocks** were not reproduced reliably, so multi-target
  has only been exercised on fabricated findings.
- **Shared working files.** All four scripts write `solution.py` and
  `test_solution.py`, so concurrent runs clobber each other.

## 5. Claims that were revised

| claim | where | what changed it |
|---|---|---|
| The loop's 5/5 vs linear's 4/5 shows self-correction improves reliability | Phase 1 | Raw logs: the loop passed on attempt 1 in all 5 runs. The linear failure was a missing test import; a one-run difference from test-generation variance |
| The aggregator "smoothed over" findings; per-category isolation will fix it | Phase 2 | Isolated judges still passed the O(n²) defect; the missing severity bar was the cause |
| Each `claude -p` call is a blank slate with no filesystem access | Phase 2 | Verified directly: it can read files in the working directory |
| A naming violation should trigger a style BLOCK | Phase 4 | The rubric excludes style nitpicks; the one earlier BLOCK was the anomaly, cause unresolved |
| No fix prompt closes the security-vs-spec conflict | Phase 4 | A fix produced an opt-in `base_dir`; the judge blocked it anyway |
| Code and tests in the fix prompt reduce test breakage (3/3 vs 0/3) | Phase 4 | A defective seeded test confounded it; 3/4 vs 3/4 on a legitimate suite |
| Security-change rate 4/6 in the multi-target arm | Phase 4 | The regex matched words inside comments; AST check gave 2/6 |
| Regression rate fell from about 40% to about 10% | Phase 4 | Different denominators (pipeline runs vs individual fixes) |
| Security yields under competing pressure | Phase 4 | The confound test: structural backing drove the hard condition; a residual category effect can't be ruled out |

## 6. Methods worth reusing

- **Fabricate findings** to test the fix step apart from reviewer variance;
  **hand-plant defects** where code generation avoids the trap.
- **Read the generated code.** A regex flag overstated a rate by 2x; reading
  found the false FIFO claim, the character-vs-byte cap and the empty-output
  gap, none of which any test or reviewer surfaced.
- **Check the design before running.** Exact-pin tests, an explicit prohibition
  and a "style blocks on naming" premise would each have decided a result in
  advance.
- **Size samples honestly.** Show the arithmetic (a 10% event needs roughly
  25–30 fixes per arm) and stop rather than chase a rate the sample can't
  measure.
- **Correct forward.** Keep the original entry and add a dated correction;
  the trail is what makes a revised claim credible.
