import pytest

from solution import is_prime


@pytest.mark.parametrize("n", [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 97, 101])
def test_is_prime_true_for_primes(n):
    assert is_prime(n) is True


@pytest.mark.parametrize("n", [0, 1, 4, 6, 8, 9, 10, 15, 21, 25, 100])
def test_is_prime_false_for_non_primes(n):
    assert is_prime(n) is False


@pytest.mark.parametrize("n", [-1, -2, -10, -97])
def test_is_prime_false_for_negative_numbers(n):
    assert is_prime(n) is False


def test_is_prime_two_is_prime():
    assert is_prime(2) is True


def test_is_prime_even_numbers_are_not_prime():
    assert is_prime(4) is False
    assert is_prime(1000) is False


def test_is_prime_large_prime():
    assert is_prime(7919) is True


def test_is_prime_large_non_prime():
    assert is_prime(7921) is False