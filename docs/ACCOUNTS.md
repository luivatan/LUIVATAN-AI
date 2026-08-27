# Apex AI accounts (phases 21–30)

`apex_auth.py` provides the framework-neutral account foundation for registration, login, logout, password reset, email verification, profiles, protected sessions, and role metadata.

## Security decisions

- Passwords use salted PBKDF2-HMAC-SHA256 with 310,000 rounds; plaintext passwords are never stored.
- Session, verification, and reset tokens are cryptographically random and only their SHA-256 hashes are persisted.
- Tokens expire and are single-use. Password reset revokes all active sessions.
- `current_user()` rotates sessions, limiting replay exposure. The web adapter must replace the stored cookie/token with the returned value.
- Reset requests deliberately return the same outward behavior for known and unknown emails to reduce account enumeration.
- Protected routes should call `current_user()` and reject missing, expired, or invalid sessions. The `role` field is available for permission middleware; role administration must be server-side only.
- `accounts.sqlite3` is local development storage and should be mounted outside the repository in deployment. Email delivery is deliberately an adapter concern; verification/reset tokens are returned to the caller and must be sent through a trusted mail service without logging them.

## Integration contract

```python
service = AuthService(os.environ.get("ACCOUNT_DATABASE", "accounts.sqlite3"))
token = service.register(email, password, display_name)  # send verification link
token = service.login(email, password)                  # set secure HttpOnly cookie
profile = service.current_user(token)                   # protected route
service.logout(token)
```

The future HTTP adapter must use Secure, HttpOnly, SameSite cookies, CSRF protection for state-changing browser requests, rate limiting on login/reset endpoints, generic authentication errors, and email verification before sensitive workspace actions.
