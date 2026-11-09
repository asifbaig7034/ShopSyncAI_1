# Validation results

Validated on 9 November 2026.

| Check | Result |
| --- | --- |
| Backend functional suite | 26 unittest tests passed using in-memory storage and mocked external providers. |
| English, Urdu and Roman Urdu smoke checks | Passed for stored product facts and reply language labels. |
| Owner takeover and suppression | Passed, including takeover during an in-flight classification request. |
| Customer and owner retry identifiers | Passed duplicate protection and conflicting-message rejection checks. |
| Webhook verification and business routing | Passed with signed local test payloads and fake providers. |
| Delivery failure handling | Passed known-failure retry, ambiguous-result and interrupted-attempt checks. |
| Frontend source | JSX/API source parsed and transformed successfully with Babel; package metadata parsed successfully. |
| Frontend Vite production build | Not run successfully: dependency installation returned HTTP 403 from the npm registry in this environment. |
| Browser rendering | Not verified: the available Chromium process was blocked by the environment's socket restriction. |
| MongoDB and Docker runtime | Not executed: MongoDB/Docker were unavailable and the PyMongo dependency could not be downloaded here. |
| Real OpenAI and WhatsApp | Not tested; no live credentials or external messages were used. |
| Performance and intent accuracy | Not benchmarked; do not claim the 10-second, 50-conversation or 85% targets from these checks. |

The backend checks used Python 3.12, FastAPI 0.141.1, HTTPX 0.28.1 and Pydantic 2.13.4 from the available runtime. Its configured SOCKS proxy was removed from the environment for offline tests because that runtime lacked the optional `socksio` dependency. The project's requirements include `httpx[socks]` for environments using a SOCKS proxy.

Run the suite and frontend build again in your local development environment after installing project dependencies. Complete the MongoDB and live-provider checks before presenting those integrations as working.
