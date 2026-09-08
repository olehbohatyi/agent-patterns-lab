## agent_linear
```
λ olegbogaty: agent-phase0 (main) ∫ rm -f solution.py test_solution.py
 
python agent_linear.py "reverse a string"
Result: PASSED
           [ 50%]
test_solution.py::test_reverse_string_with_numbers PASSED                [ 60%]
test_solution.py::test_reverse_string_with_special_characters PASSED     [ 70%]
test_solution.py::test_reverse_string_mixed_case PASSED                  [ 80%]
test_solution.py::test_reverse_string_unicode PASSED                     [ 90%]
test_solution.py::test_reverse_string_long_string PASSED                 [100%]

============================== 10 passed in 0.04s ==============================

```
`palindrome check`
```
λ olegbogaty: agent-phase0 (main) ∫ rm -f solution.py test_solution.py
 
python agent_linear.py "palindrome check"
Result: FAILED
'is_palind...
FAILED test_solution.py::test_numeric_non_palindrome - NameError: name 'is_pa...
FAILED test_solution.py::test_alphanumeric_palindrome - NameError: name 'is_p...
FAILED test_solution.py::test_only_non_alphanumeric_characters - NameError: n...
FAILED test_solution.py::test_two_different_characters - NameError: name 'is_...
FAILED test_solution.py::test_palindrome_with_mixed_case_and_numbers - NameEr...
============================== 13 failed in 0.08s ==============================

```
`sum of digits (negative nums)`
```
λ olegbogaty: agent-phase0 (main) ∫ rm -f solution.py test_solution.py
 
python agent_linear.py "sum of digits (negative nums)"
Result: PASSED
           [ 16%]
test_solution.py::test_sum_of_digits_negative PASSED                     [ 33%]
test_solution.py::test_sum_of_digits_negative_single_digit PASSED        [ 50%]
test_solution.py::test_sum_of_digits_zero PASSED                         [ 66%]
test_solution.py::test_sum_of_digits_negative_with_zeros PASSED          [ 83%]
test_solution.py::test_sum_of_digits_large_negative PASSED               [100%]

============================== 6 passed in 0.02s ===============================
```
`second largest (duplicates)`
```
λ olegbogaty: agent-phase0 (main) ∫ rm -f solution.py test_solution.py
 
python agent_linear.py "second largest (duplicates)"
Result: PASSED
SED        [ 58%]
test_solution.py::test_unsorted_input_with_duplicates PASSED             [ 66%]
test_solution.py::test_floats_with_duplicates PASSED                     [ 75%]
test_solution.py::test_raises_when_all_values_identical PASSED           [ 83%]
test_solution.py::test_raises_when_single_element PASSED                 [ 91%]
test_solution.py::test_raises_when_empty_list PASSED                     [100%]

============================== 12 passed in 0.03s ==============================

```
`count vowels (case-insensitive)`
```
λ olegbogaty: agent-phase0 (main) ∫ rm -f sol
ution.py test_solution.py 
python agent_linear.py "count vowels (case-insensitive)"
Result: PASSED
           [ 64%]
test_solution.py::test_numbers_and_symbols_only PASSED                   [ 71%]
test_solution.py::test_single_vowel_character PASSED                     [ 78%]
test_solution.py::test_single_consonant_character PASSED                 [ 85%]
test_solution.py::test_whitespace_only PASSED                            [ 92%]
test_solution.py::test_y_is_not_counted_as_vowel PASSED                  [100%]

============================== 14 passed in 0.03s ==============================

```
======================================
## agent_loop
`reverse a string`
```


λ olegbogaty: agent-phase0 (main) ∫ rm -f sol
ution.py test_solution.py 
python agent.py "reverse a string"
=== Attempt 1: writing the first version ===

=== Attempt 1: tests PASSED ===
           [ 44%]
test_solution.py::test_reverse_string_with_spaces PASSED                 [ 55%]
test_solution.py::test_reverse_string_with_special_characters PASSED     [ 66%]
test_solution.py::test_reverse_string_with_numbers PASSED                [ 77%]
test_solution.py::test_reverse_unicode_string PASSED                     [ 88%]
test_solution.py::test_reverse_does_not_mutate_input_type PASSED         [100%]

============================== 9 passed in 0.02s ===============================


✅ The agent decided to stop on its own — tests are green.

```
`palindrome check`
```
λ olegbogaty: agent-phase0 (main) ∫ rm -f solution.py test_solution.py
 
python agent_loop.py "palindrome check"
=== Attempt 1: writing the first version ===

=== Attempt 1: tests PASSED ===
SSED       [ 58%]
test_solution.py::test_numeric_palindrome PASSED                         [ 66%]
test_solution.py::test_numeric_non_palindrome PASSED                     [ 75%]
test_solution.py::test_alphanumeric_mixed PASSED                         [ 83%]
test_solution.py::test_whitespace_only PASSED                            [ 91%]
test_solution.py::test_non_palindrome_with_punctuation PASSED            [100%]

============================== 12 passed in 0.03s ==============================


✅ The agent decided to stop on its own — tests are green.
```
`sum of digits (negative nums)`
```
λ olegbogaty: agent-phase0 (main) ∫ rm -f solution.py test_solution.py
 
python agent_loop.py "sum of digits (negative nums)"
=== Attempt 1: writing the first version ===

=== Attempt 1: tests PASSED ===
           [ 28%]
test_solution.py::test_sum_of_digits_zero PASSED                         [ 42%]
test_solution.py::test_sum_of_digits_negative PASSED                     [ 57%]
test_solution.py::test_sum_of_digits_negative_single_digit PASSED        [ 71%]
test_solution.py::test_sum_of_digits_negative_with_zeros PASSED          [ 85%]
test_solution.py::test_sum_of_digits_large_negative PASSED               [100%]

============================== 7 passed in 0.02s ===============================


✅ The agent decided to stop on its own — tests are green.
```
`second largest (duplicates)`
```
λ olegbogaty: agent-phase0 (main) ∫ rm -f solution.py test_solution.py
 
python agent_loop.py "second largest (duplicates)"
=== Attempt 1: writing the first version ===

=== Attempt 1: tests PASSED ===
       [ 58%]
test_solution.py::test_negative_numbers PASSED                           [ 66%]
test_solution.py::test_second_largest_with_multiple_duplicates_of_it PASSED [ 75%]
test_solution.py::test_unsorted_with_duplicates_scattered PASSED         [ 83%]
test_solution.py::test_largest_appears_first_then_duplicates_later PASSED [ 91%]
test_solution.py::test_floats_with_duplicates PASSED                     [100%]

============================== 12 passed in 0.03s ==============================


✅ The agent decided to stop on its own — tests are green.
```
`count vowels (case-insensitive)`
```
λ olegbogaty: agent-phase0 (main) ∫ rm -f sol
ution.py test_solution.py
python agent_loop.py "count vowels (case-insensitive)"
=== Attempt 1: writing the first version ===

=== Attempt 1: tests PASSED ===
           [ 64%]
test_solution.py::test_numbers_and_symbols_ignored PASSED                [ 71%]
test_solution.py::test_single_vowel_lowercase PASSED                     [ 78%]
test_solution.py::test_single_vowel_uppercase PASSED                     [ 85%]
test_solution.py::test_single_consonant PASSED                           [ 92%]
test_solution.py::test_y_is_not_a_vowel PASSED                           [100%]

============================== 14 passed in 0.05s ==============================


✅ The agent decided to stop on its own — tests are green.
```