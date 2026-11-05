"""Seed fictional product/order examples in an explicitly configured MongoDB."""

from backend.app.core.config import get_settings
from backend.app.infrastructure.db.mongo import get_database
from backend.app.infrastructure.db.repositories import seed_demo


def main() -> None:
    settings = get_settings()
    if not settings.demo_mode:
        raise SystemExit("Seeding is disabled when DEMO_MODE=false.")
    if not settings.mongodb_uri:
        print("No MONGODB_URI: the server seeds its disposable in-memory database on startup.")
        return
    ids = seed_demo(get_database())
    print("Seeded fictional demo data:", ids)


if __name__ == "__main__":
    main()
