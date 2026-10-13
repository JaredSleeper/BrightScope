import argparse
import asyncio
import json
import logging
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="brightscope")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    sub.add_parser("sync")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.cmd == "serve":
        uvicorn.run("brightscope.app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
    elif args.cmd == "sync":
        asyncio.run(_sync())


async def _sync() -> None:
    from . import db, sync

    await db.init_pool()
    try:
        print(json.dumps(await sync.run_sync("cli"), default=str))
    finally:
        await db.close_pool()


if __name__ == "__main__":
    main()
