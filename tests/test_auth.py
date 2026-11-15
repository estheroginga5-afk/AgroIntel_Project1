"""Offline tests for sign-up / log-in."""
import pytest

from agrointel import auth

FAST = 1000          # PBKDF2 iterations: fast for tests only


@pytest.fixture()
def store(tmp_path):
    return auth.UserStore(tmp_path / "u.db", iterations=FAST)


def test_signup_then_login(store):
    u = store.signup("Essy", "Essy@Example.com", "Maize2026", "Maize2026")
    assert u.email == "essy@example.com"
    assert store.login("ESSY@example.com", "Maize2026").id == u.id


def test_password_is_not_stored_in_plain_text(store, tmp_path):
    store.signup("Essy", "e@x.co", "Maize2026", "Maize2026")
    assert b"Maize2026" not in (tmp_path / "u.db").read_bytes()


def test_wrong_password_and_unknown_user_give_same_message(store):
    store.signup("Essy", "e@x.co", "Maize2026", "Maize2026")
    with pytest.raises(auth.AuthError) as a:
        store.login("e@x.co", "wrong")
    with pytest.raises(auth.AuthError) as b:
        store.login("nobody@x.co", "wrong")
    assert str(a.value) == str(b.value)


def test_duplicate_email_rejected(store):
    store.signup("Essy", "e@x.co", "Maize2026", "Maize2026")
    with pytest.raises(auth.AuthError, match="already exists"):
        store.signup("Other", "E@x.co", "Maize2026", "Maize2026")


@pytest.mark.parametrize("name,email,pw,pw2", [
    ("E", "e@x.co", "Maize2026", "Maize2026"), ("Essy", "bad", "Maize2026", "Maize2026"),
    ("Essy", "e@x.co", "short1A", "short1A"), ("Essy", "e@x.co", "alllowercase1", "alllowercase1"),
    ("Essy", "e@x.co", "NoNumbersHere", "NoNumbersHere"), ("Essy", "e@x.co", "Maize2026", "Maize2027")])
def test_weak_or_invalid_signup_rejected(store, name, email, pw, pw2):
    with pytest.raises(auth.AuthError):
        store.signup(name, email, pw, pw2)


def test_lockout_after_repeated_failures(store):
    store.signup("Essy", "e@x.co", "Maize2026", "Maize2026")
    for _ in range(auth.MAX_FAILS):
        with pytest.raises(auth.AuthError):
            store.login("e@x.co", "bad")
    with pytest.raises(auth.AuthError, match="Too many"):
        store.login("e@x.co", "Maize2026")
