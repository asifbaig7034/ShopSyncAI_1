# UC-1 local password-recovery contract

Both endpoints are available only from the local machine. They accept JSON
and never return the recovery code or an access token.

| Method and path | Request body | Result |
| --- | --- | --- |
| `POST /api/account/password-reset/request` | `{ "email": "owner@example.com" }` | `200` with the same confirmation for known and unknown accounts. `503` if mail delivery is not configured, regardless of account existence. |
| `POST /api/account/password-reset/confirm` | `{ "email": "owner@example.com", "code": "...", "password": "at-least-12-characters" }` | `200` if the code was valid and unused; `400` for an invalid/expired code. After success, log in again. |

The code is emailed through server-configured SMTP, expires after 15 minutes,
and can be used once. Repeated requests for the same account are limited to
one per 60 seconds. Previous owner sessions stop working after successful
recovery. In the local prototype Mailpit receives the email; its inbox is
available at `http://127.0.0.1:8025` when the `mailpit` Compose service runs.

## UC-1 WhatsApp read-access check (Business Owner only)

| Method and path | Request body | Result |
| --- | --- | --- |
| `GET /api/integrations/whatsapp` | None; owner bearer session required | Current status and metadata, no access token. |
| `POST /api/integrations/whatsapp/connect` | `{ "waba_id": "123...", "phone_number_id": "456...", "access_token": "META_TOKEN" }` | Verifies the exact number through Meta, then saves an encrypted token and returns public status. |
| `POST /api/integrations/whatsapp/verify` | None; owner bearer session required | Rechecks Meta using the stored encrypted token. |
| `DELETE /api/integrations/whatsapp` | None; owner bearer session required | Removes the local connection only; does not unregister the Meta phone number. |

Public status has `status` (`not_connected`, `meta_read_verified`, or
`needs_attention`) and nullable `waba_id`, `phone_number_id`,
`display_phone_number`, `verified_name`, `verified_at`. No response includes
the access token or its ciphertext. A `meta_read_verified` status proves only
that Meta allowed this token to list that phone number at the time shown;
message sending and webhook verification are UC-3 work.
