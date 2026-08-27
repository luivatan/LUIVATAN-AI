from apex_security import SecurityError, admin_required, security_headers
import pytest

def test_headers_and_admin_guard():
    assert security_headers()["X-Frame-Options"] == "DENY"
    assert admin_required({'role':'admin'})
    with pytest.raises(SecurityError): admin_required({'role':'user'})
