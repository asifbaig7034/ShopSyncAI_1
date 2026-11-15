"""Check MongoDB and print actionable diagnostics without credentials or raw URIs."""
import os
from pathlib import Path


def failure_hint(error, stage="connection"):
    """Inspect errors locally; never echo their raw text or connection details."""
    kind = type(error).__name__
    code = getattr(error, "code", None)
    detail = str(error).lower()
    if code == 18 or "authentication failed" in detail or "bad auth" in detail:
        return ("AUTH_FAILED", "Atlas database-user username/password verify karein. URI mein password ke special characters encode karein.")
    if code == 13 or "not authorized" in detail or "unauthorized" in detail:
        return ("PERMISSION_DENIED", "Database user ko MONGODB_DB par read/write aur indexes create karne ki permission dein.")
    if kind in ("InvalidURI", "InvalidName") or "invalid uri" in detail:
        return ("INVALID_CONFIG", ".env mein Atlas ki complete connection string aur valid MONGODB_DB check karein.")
    if "transactions require a replica set" in detail or "transaction numbers are only allowed" in detail:
        return ("TRANSACTIONS_REQUIRED", "Standalone MongoDB se connect hain. Atlas ya replica-set setup use karein.")
    if "set mongodb_db" in detail:
        return ("INVALID_DATABASE_NAME", ".env mein MONGODB_DB=shopsync_uc4 rakhein; admin/config/local use na karein.")
    if any(word in detail for word in ("certificate verify failed", "ssl handshake", "tls handshake", "ssl:")):
        return ("TLS_ERROR", "Python certificates, system date/time aur network TLS interception check karein. Certificate verification disable na karein.")
    if any(word in detail for word in ("dns", "querysrv", "nxdomain", "resolution lifetime", "name or service not known", "getaddrinfo", "_mongodb._tcp")):
        return ("DNS_ERROR", "Atlas cluster hostname aur internet/DNS resolution check karein; paused/deleted cluster bhi check karein.")
    if kind in ("ServerSelectionTimeoutError", "NetworkTimeout", "ConnectionFailure", "AutoReconnect") or "cannot connect to mongodb" in detail:
        return ("CONNECTION_TIMEOUT", "Atlas mein current IP allow karein; running cluster, internet, VPN/firewall aur port 27017 reachability check karein.")
    if kind in ("ConfigurationError", "ValueError"):
        return ("INVALID_CONFIG", "MongoDB URI options/format check karein. Credentials ko raw error ke saath share na karein.")
    return ("CHECK_FAILED", f"{stage} step failed. Exact connection details share kiye baghair is diagnostic code ko report karein.")


def main():
    shell_uri = "MONGODB_URI" in os.environ
    try:
        from app.db import close_connection, get_database, initialize
    except ImportError:
        print("DEPENDENCIES_MISSING: .\\.venv\\Scripts\\python.exe -m pip install --upgrade -r requirements.txt")
        return 2
    stage = "configuration"
    try:
        env_file = Path(__file__).resolve().parents[1] / ".env"
        print("CONFIG: .env file " + ("found" if env_file.is_file() else "not found"))
        uri = os.getenv("MONGODB_URI")
        if shell_uri:
            print("CONFIG: MONGODB_URI terminal variable is in use; it overrides .env.")
        if uri is None:
            print("CONFIG: MONGODB_URI is missing; the local replica-set default is in use.")
        elif not uri.strip() or any(token in uri.lower() for token in (
                "<db_password>", "<password>", "<username>", "apni_password_wali",
                "yahan_apni", "apna_cluster_host", "your_cluster_host")):
            print("CONFIG_PLACEHOLDER: .env mein placeholders ki jagah apni actual Atlas URI dein.")
            return 1
        else:
            print("CONFIG: " + ("Atlas/SRV connection selected." if uri.startswith("mongodb+srv://") else "Standard MongoDB connection selected."))
        database = get_database()
        # Probe before initialize() wraps driver errors in a generic RuntimeError.
        stage = "connection"
        database.client.admin.command("ping")
        stage = "transaction/index setup"
        initialize()
        print(f"MongoDB connected: {database.name}; transaction check passed.")
        for name in ("products", "customers", "orders"):
            print(f"{name}: {database[name].count_documents({})}")
        return 0
    except Exception as error:
        code, hint = failure_hint(error, stage)
        print(f"{code}: {hint}")
        return 1
    finally:
        close_connection()


if __name__ == "__main__":
    raise SystemExit(main())
