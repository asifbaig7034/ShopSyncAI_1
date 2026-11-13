"""Check database connectivity and required transaction support without a UI."""


def main():
    try:
        from app.db import close_connection, get_database, initialize
    except ImportError:
        print("Install dependencies first: python -m pip install --upgrade -r requirements.txt")
        return 2
    try:
        initialize()
        database = get_database()
        print(f"MongoDB connected: {database.name}; transaction check passed.")
        for name in ("products", "customers", "orders"):
            print(f"{name}: {database[name].count_documents({})}")
        return 0
    except Exception as error:
        # Never print raw connection exceptions, which can contain connection data.
        print(f"MongoDB check failed ({type(error).__name__}). Check .env, server/network access and replica-set setup; see README.md.")
        return 1
    finally:
        close_connection()


if __name__ == "__main__":
    raise SystemExit(main())
