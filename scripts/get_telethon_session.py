"""One-time helper to generate TELETHON_SESSION_STRING.

Run this once on Railway (web shell) or local VPS.
Enter your credentials → SMS code → copy the printed SESSION_STRING to env vars.

Usage:
    python scripts/get_telethon_session.py

Then:
    export TELETHON_SESSION_STRING="<printed-string>"
    # or paste into Railway dashboard → env vars
"""
import asyncio
import sys
from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    print("\n=== Telethon Session String Generator ===\n")

    api_id = input("Enter TELETHON_API_ID: ").strip()
    api_hash = input("Enter TELETHON_API_HASH: ").strip()
    phone = input("Enter phone (e.g. +1234567890): ").strip()

    if not all([api_id, api_hash, phone]):
        print("Missing credentials.")
        sys.exit(1)

    try:
        api_id = int(api_id)
    except ValueError:
        print("Invalid API_ID (must be numeric)")
        sys.exit(1)

    # Create client with empty StringSession
    # This generates a new session tied to these api_id/api_hash
    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        print(f"\nRequesting code for {phone}...")
        await client.start(phone=phone, code_callback=_code_callback)

        # Get the session string
        session_string = client.session.save()

        print("\n" + "=" * 60)
        print("✓ Session created successfully!")
        print("=" * 60)
        print("\nCopy this and set as TELETHON_SESSION_STRING:\n")
        print(session_string)
        print("\n" + "=" * 60)
        print("\nOn Railway:")
        print("  1. Dashboard → Service → Variables")
        print("  2. Add: TELETHON_SESSION_STRING = <paste-above>")
        print("  3. Redeploy (or just restart)")
        print("\nOn local VPS:")
        print("  export TELETHON_SESSION_STRING='<paste-above>'")
        print("  python -m main")
        print("\n")


async def _code_callback(attempt: int) -> str:
    """Called by Telethon when SMS code is needed."""
    if attempt > 0:
        print(f"Invalid code. Attempt {attempt}. Try again.")
    code = input("Enter SMS code: ").strip()
    return code


if __name__ == "__main__":
    asyncio.run(main())
