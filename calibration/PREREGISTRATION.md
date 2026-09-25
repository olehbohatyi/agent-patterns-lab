Corpus: 27 frozen reviews (20 recovered-verbatim from the session transcript, 7 regenerated), labels in corpus.json, assigned before any judging.
Runs: 3x current LLM judge (judge_review_llm), 3x Jev judge (judge_review_jev, threshold 0.5) per review. Raw Jev P logged at full precision before thresholding.
Verdict per judge per case = majority of 3. Reported: per-case 3 verdicts and 3 P values.
Trigger for a dedicated NOTES.md entry (not a footnote): (a) Jev majority != label on ANY BLOCK-labeled non-contestable case (a missed defect), or
(b) Jev majority != LLM majority on MORE THAN 1 non-contestable case. Contestable cases (4) are reported separately and don't count toward triggers.
Claims allowed: per-case agreement counts and P values. No rates, no "Jev is as good as the LLM judge". n=23 non-contestable cases from ~4 distinct code/defect setups.
Threshold-vs-judgment read: for any Jev disagreement with label, inspect raw P: 0.3-0.5 for a BLOCK-label => threshold question; <0.1 => rubric-port question.
Recovered vs regenerated always reported separately.
