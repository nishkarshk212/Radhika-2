import asyncio
from pyrogram import Client


async def generate():
    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║   Pyrogram Session String Generator              ║")
    print("║   Get API credentials: https://my.telegram.org   ║")
    print("╚══════════════════════════════════════════════════╝")
    print()

    api_id   = int(input("  API_ID   : ").strip())
    api_hash = input("  API_HASH : ").strip()

    print()
    print("  Starting Telegram client — you will receive an OTP...")
    print()

    async with Client(name="generate_session", api_id=api_id, api_hash=api_hash, in_memory=True) as app:
        session = await app.export_session_string()

        print()
        print("╔══════════════════════════════════════════════════╗")
        print("║  ✅  SESSION STRING GENERATED                    ║")
        print("╚══════════════════════════════════════════════════╝")
        print()
        print(session)
        print()
        print("  ↑ Copy the string above and paste it as SESSION= in your .env")
        print()

        # Also save to a file for convenience
        with open("generated_session.txt", "w") as f:
            f.write(f"SESSION={session}\n")
        print("  📄 Also saved to: generated_session.txt")
        print()


asyncio.run(generate())
