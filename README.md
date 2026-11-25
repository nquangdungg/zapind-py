# Zapind (Python)

**Developer-friendly, async-first, type-safe contextual Zalo SDK for Python (`>= 3.10`).**

Zapind provides a clean domain-oriented async API (`account`, `users`, `contacts`, `groups`, `conversations`, `messages`, `media`, `settings`, `events`), contextual models (`user.send()`, `group.send()`, `conversation.send()`, `message.reply()`), automatic `ThreadType` resolution, built-in session stores, caching, request deduplication, rate limiting, and a typed `ZapindError` hierarchy.

## Installation

```bash
pip install zapind
```

For AES-256-GCM encrypted session storage (`EncryptedFileSessionStore`):

```bash
pip install "zapind[crypto]"
```

> **Runtime Note:** The default `NodeBridgeTransport` delegates live Zalo Web protocol operations to a local Node.js (`>= 20`) subprocess running the `zapind` npm package via stdio JSON-RPC. You can also supply any custom `TransportAdapter` (such as `MockTransport` for offline testing).

## Quick Start

```python
import asyncio
from zapind import Zapind, Reaction

async def main() -> None:
    async with Zapind(session="./session.json") as client:
        await client.login(
            on_qr_code=lambda qr: print("Scan QR:", qr.image_data_url),
            on_scanned=lambda info: print("Scanned by:", info.display_name),
        )

        me = await client.account.get_me()
        print("Logged in as:", me.id, me.display_name)

        @client.on("message")
        async def handle_message(message):
            if message.text == "!ping":
                await message.reply("pong!")
                await message.react(Reaction.HEART)

        await client.start_listening(retry_on_close=True)
        await asyncio.sleep(60)

asyncio.run(main())
```

## Features

- **Async-first (`asyncio`)** with `async with Zapind(...) as client:` context manager support.
- **9 Domain Services:** `account`, `users`, `contacts`, `groups`, `conversations`, `messages`, `media`, `settings`, `events`.
- **Contextual Models:** `User`, `Profile`, `Contact`, `Group`, `Conversation`, `Message`.
- **Automatic `ThreadType` Resolution:** Uses contextual models and a cached `ThreadTypeRegistry`.
- **Session Stores:** `MemorySessionStore`, `FileSessionStore` (atomic writes + `0o600` on POSIX), and `EncryptedFileSessionStore` (AES-256-GCM).
- **Resilience & Observability:** `TTLCache`, `InFlightDeduplicator`, `RateLimiter`, controlled retries, request timeouts, and `RedactingLogger`.
- **Full Type Hints:** Ships with `py.typed` (PEP 561).

## License

GNU Lesser General Public License v3.0 (`LGPL-3.0-only`).
