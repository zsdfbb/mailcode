from mailcode.relay.security import SecurityChecker

sc = SecurityChecker()


def test_dkim_spf_pass_strict():
    v, r = sc.verify_auth_results("dkim=pass; spf=pass", "strict")
    assert v and r == "OK", f"FAIL: {v} {r}"


def test_dkim_spf_pass_warn():
    v, r = sc.verify_auth_results("dkim=pass; spf=pass", "warn")
    assert v and r == "OK", f"FAIL: {v} {r}"


def test_dkim_fail_strict():
    v, r = sc.verify_auth_results("dkim=fail; spf=pass", "strict")
    assert not v, f"strict should reject dkim=fail: {r}"


def test_spf_fail_strict():
    v, r = sc.verify_auth_results("dkim=pass; spf=fail", "strict")
    assert not v, f"strict should reject spf=fail: {r}"


def test_dkim_softfail_strict():
    v, r = sc.verify_auth_results("dkim=softfail; spf=pass", "strict")
    assert not v, f"strict should reject dkim=softfail: {r}"


def test_spf_softfail_warn():
    v, r = sc.verify_auth_results("dkim=pass; spf=softfail", "warn")
    assert v, f"warn should accept spf=softfail: {r}"


def test_spf_softfail_strict():
    v, r = sc.verify_auth_results("dkim=pass; spf=softfail", "strict")
    assert not v, f"strict should reject spf=softfail: {r}"


def test_dkim_none_strict():
    v, r = sc.verify_auth_results("dkim=none; spf=pass", "strict")
    assert not v, f"strict should reject dkim=none: {r}"


def test_spf_neutral_strict():
    v, r = sc.verify_auth_results("dkim=pass; spf=neutral", "strict")
    assert not v, f"strict should reject spf=neutral: {r}"


def test_dkim_neutral_warn():
    v, r = sc.verify_auth_results("dkim=neutral; spf=pass", "warn")
    assert v, f"warn should accept dkim=neutral: {r}"


def test_empty_header_warn():
    v, r = sc.verify_auth_results("", "warn")
    assert v, f"warn should accept empty header: {r}"


def test_empty_header_strict():
    v, r = sc.verify_auth_results("", "strict")
    assert not v, f"strict should reject empty header: {r}"


def test_whitespace_header_warn():
    v, r = sc.verify_auth_results("  ", "warn")
    assert v, f"warn should accept whitespace-only header: {r}"


def test_policy_off():
    v, r = sc.verify_auth_results("dkim=fail; spf=fail", "off")
    assert v, f"off should always accept: {r}"


def test_temperror_warn():
    v, r = sc.verify_auth_results("dkim=temperror; spf=pass", "warn")
    assert v, f"temperror should pass even in warn: {r}"


def test_temperror_strict():
    v, r = sc.verify_auth_results("dkim=temperror; spf=pass", "strict")
    assert v, f"temperror should pass even in strict: {r}"


def test_permerror_strict():
    v, r = sc.verify_auth_results("dkim=permerror; spf=pass", "strict")
    assert v, f"permerror should pass even in strict: {r}"


def test_spf_temperror_warn():
    v, r = sc.verify_auth_results("dkim=pass; spf=temperror", "warn")
    assert v, f"spf=temperror should pass in warn: {r}"


def test_gmail_folded_header():
    header = (
        "mx.google.com;\n"
        "       dkim=pass header.i=@gmail.com header.s=20230601;\n"
        "       spf=pass (google.com: domain of user@gmail.com designates\n"
        "        1.2.3.4 as permitted sender) smtp.mailfrom=user@gmail.com;\n"
        "       dmarc=pass (p=NONE) header.from=gmail.com"
    )
    v, r = sc.verify_auth_results(header, "strict")
    assert v and r == "OK", f"Gmail header should pass: {v} {r}"


def test_outlook_header():
    header = (
        "spf=pass (sender IP is 1.2.3.4) smtp.mailfrom=outlook.com;\n"
        " dkim=pass (signature was verified) header.d=outlook.com;\n"
        " dmarc=pass action=none header.from=outlook.com"
    )
    v, r = sc.verify_auth_results(header, "strict")
    assert v and r == "OK", f"Outlook header should pass: {v} {r}"


def test_qqmail_header():
    header = "mx.qq.com; dkim=pass header.i=@qq.com; spf=pass"
    v, r = sc.verify_auth_results(header, "strict")
    assert v and r == "OK", f"QQMail header should pass: {v} {r}"


def test_dkim_missing_strict():
    """只有 spf 没有 dkim 字段"""
    v, r = sc.verify_auth_results("spf=pass", "strict")
    assert not v, f"missing dkim should fail in strict: {r}"


def test_dkim_missing_warn():
    v, r = sc.verify_auth_results("spf=pass", "warn")
    assert v, f"missing dkim should pass in warn: {r}"


def test_spf_missing_warn():
    v, r = sc.verify_auth_results("dkim=pass", "warn")
    assert v, f"missing spf should pass in warn: {r}"


def test_case_insensitive():
    v, r = sc.verify_auth_results("DKIM=PASS; SPF=PASS", "strict")
    assert v and r == "OK", f"case insensitive check: {v} {r}"
