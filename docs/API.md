# Apex AI API

Start the integration backend:

```bash
uvicorn api_server:app --reload
```

Available endpoints:

- `GET /api/health`
- `POST /api/auth/register`
- `POST /api/auth/verify?token=...`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/me`
- `POST /api/auth/password-reset/request`
- `POST /api/auth/password-reset`

The API uses an HttpOnly session cookie. Set `secure=True` behind HTTPS in production. Registration currently returns a verification token only for local development; production must deliver it through an email provider and never include it in the response.
