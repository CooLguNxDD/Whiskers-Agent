from api.admin_routes import _safe_internal_redirect

def test_safe_internal_redirect():
    # Valid cases
    assert _safe_internal_redirect("/") == "/"
    assert _safe_internal_redirect("/home") == "/home"
    assert _safe_internal_redirect("/path?a=1") == "/path?a=1"
    assert _safe_internal_redirect("/path/to/page") == "/path/to/page"
    assert _safe_internal_redirect("/path#fragment") == "/path#fragment"

    # Malicious / bypass cases
    assert _safe_internal_redirect("http://attacker.com") == "/"
    assert _safe_internal_redirect("https://attacker.com") == "/"
    assert _safe_internal_redirect("//attacker.com") == "/"
    assert _safe_internal_redirect(" //attacker.com") == "/"
    assert _safe_internal_redirect("\t//attacker.com") == "/"
    assert _safe_internal_redirect("/\\attacker.com") == "/"
    assert _safe_internal_redirect("/\\/attacker.com") == "/"
    assert _safe_internal_redirect("\\\\attacker.com") == "/"
    assert _safe_internal_redirect("javascript:alert(1)") == "/"
    assert _safe_internal_redirect(" javascript:alert(1)") == "/"
    assert _safe_internal_redirect("http:attacker.com") == "/"
    assert _safe_internal_redirect("/%5Cattacker.com") == "/"
    assert _safe_internal_redirect("/%2F%2Fattacker.com") == "/"
    assert _safe_internal_redirect(None) == "/"
    assert _safe_internal_redirect("") == "/"

if __name__ == "__main__":
    test_safe_internal_redirect()
    print("All tests passed!")
