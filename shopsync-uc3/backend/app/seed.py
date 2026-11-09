"""Explicitly seed example products into the configured Mongo database."""
import argparse
import asyncio

from .catalogue import DEMO_PRODUCTS
from .config import Settings
from .repository import MongoStore


async def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-demo-data", action="store_true", help="Confirm this is a sample-data database, never a live business catalogue")
    args = parser.parse_args()
    if not args.confirm_demo_data:
        parser.error("Sample data requires --confirm-demo-data")
    settings = Settings.from_env()
    if settings.storage != "mongo":
        parser.error("Set STORAGE=mongo before seeding persistent example products")
    store = MongoStore(settings.business_id, settings.mongo_uri, settings.mongo_database)
    await store.initialize()
    try:
        await store.seed(DEMO_PRODUCTS)
        print("Sample products inserted if absent. Existing products were not overwritten.")
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(run())
