from solution import reverse_string


def test_reverse_string_basic():
    assert reverse_string("hello") == "olleh"


def test_reverse_string_empty():
    assert reverse_string("") == ""


def test_reverse_string_single_char():
    assert reverse_string("a") == "a"


def test_reverse_string_palindrome():
    assert reverse_string("racecar") == "racecar"


def test_reverse_string_with_spaces():
    assert reverse_string("hello world") == "dlrow olleh"


def test_reverse_string_with_punctuation():
    assert reverse_string("abc!def?") == "?fed!cba"


def test_reverse_string_numeric_chars():
    assert reverse_string("12345") == "54321"


def test_reverse_string_unicode():
    assert reverse_string("héllo") == "olléh"
```

Write permission for `test_solution.py` was denied, so I printed the test code above instead. Let me know if you'd like me to try writing it to the file.