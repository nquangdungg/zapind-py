# Zapind (Python)

**Developer-friendly, async-first, type-safe contextual Zalo SDK for Python (`>= 3.10`).**

Current PyPI release: **`v1.0.1`**.

Zapind provides a clean domain-oriented async API (`account`, `users`, `contacts`, `groups`, `conversations`, `messages`, `media`, `settings`, `events`), contextual models (`user.send()`, `group.send()`, `conversation.send()`, `message.reply()`), automatic `ThreadType` resolution, built-in session stores, caching, request deduplication, rate limiting, and a typed `ZapindError` hierarchy.

## Installation

```bash
pip install zapind
```

For AES-256-GCM encrypted session storage (`EncryptedFileSessionStore`):

```bash
pip install "zapind[crypto]"
```

> **Runtime Note:** The default `NodeBridgeTransport` delegates live Zalo Web protocol operations to a local Node.js (`>= 20`) subprocess running the `zapind` (`>= 1.0.1`) npm package via stdio JSON-RPC. You can also supply any custom `TransportAdapter` (such as `MockTransport` for offline testing).

## Quick Start

```python
import asyncio
from zapind import Zapind, Reaction, SessionError, ThreadType

async def main() -> None:
    async with Zapind(session="./session.json") as client:
        # Optional: restore saved session only without blocking on QR login
        try:
            await client.login(session_only=True)
        except SessionError:
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

        @client.on("old_messages")
        async def handle_old_messages(evt):
            print(f"Synced {len(evt.messages)} historical messages ({evt.thread_type})")

        await client.start_listening(retry_on_close=True)
        await client.conversations.request_old_messages(ThreadType.USER)

        # Cursor-based group cloud history pagination
        page = await client.conversations.get_messages("group-id", count=50)
        if page.has_more and page.next_cursor:
            older = await client.conversations.get_messages(
                "group-id", count=50, cursor=page.next_cursor, is_old=page.is_old
            )

        await asyncio.sleep(60)

asyncio.run(main())
```

## Features

- **Async-first (`asyncio`)** with `async with Zapind(...) as client:` context manager support and non-blocking `asyncio.to_thread()` file/crypto session persistence.
- **9 Domain Services:** `account`, `users`, `contacts`, `groups`, `conversations`, `messages`, `media`, `settings`, `events`.
- **Contextual Models:** `User`, `Profile`, `Contact`, `Group`, `Conversation`, `Message` (with `MessageQuote.sender_name` and group-only guard on `Conversation.get_messages()`).
- **History & Sync:** Cursor-paginated group history (`get_messages(..., cursor=..., is_old=...)` with clean `-69` stop-paging) and WebSocket `'old_messages'` sync (`request_old_messages()`).
- **Restore-Only Startup:** `await client.login(session_only=True)` raises `SessionError` immediately when no valid saved session exists.
- **Session Stores:** `MemorySessionStore`, `FileSessionStore` (atomic writes + `0o600` on POSIX), and `EncryptedFileSessionStore` (AES-256-GCM).
- **Resilience & Observability:** `TTLCache`, cancellation-safe `InFlightDeduplicator`, `RateLimiter`, controlled retries, request timeouts, HTTP status error classification, and `RedactingLogger`.
- **Full Type Hints:** Ships with `py.typed` (PEP 561).

## Documentation & Changelog

- Full bilingual docs: [Zapind SDK Documentation](https://github.com/nquangdungg/zapind-sdk)
- Release notes: [CHANGELOG.md](./CHANGELOG.md)

## License

GNU Lesser General Public License v3.0 (`LGPL-3.0-only`).
