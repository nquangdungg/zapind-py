# Changelog

All notable changes to the `zapind` Python SDK are documented in this file.

## [1.0.1] - 2026-11-26

### Fixed
- **HTTP Error Status Classification (`ZAPIND-PY-001`)**: `normalize_error()` now inspects `status`, `statusCode`, `status_code`, nested `response.status`, and HTTP status codes embedded in error message strings (e.g. `"Request failed with status code 429"`, `401`, `403`, `404`, `5xx`), and preserves `rawCode`/`raw_code`. Ensures HTTP 429 maps to retryable `RateLimitError`, 401 to `SessionError`, 403 to `PermissionError`, 404 to `NotFoundError`, and 5xx to retryable `APIError`.
- **`NodeBridgeTransport` Stability & Protocol Completeness (`ZAPIND-PY-002`)**:
  - Typed bridge event deserialization: incoming JSON event frames (`reaction`, `typing`, `seen`, `undo`, `group.update`, `user.update`, `connected`, `disconnected`) are now mapped to dataclass event models instead of passing raw `dict` payloads that caused `AttributeError`.
  - Subprocess EOF / crash handling: pending RPC futures are immediately rejected with `NetworkError` and `"disconnected"` is emitted when the bridge process exits unexpectedly, preventing indefinite hangs.
  - Background stderr draining task prevents OS pipe buffer deadlocks when the bridge emits logs to stderr.
  - Serialized `proc.stdin` writes with an `asyncio.Lock` to prevent concurrent `StreamWriter.drain()` conflicts.
  - Full `SendMessagePayload` serialization over RPC: forwards `replyTo`, `mentions`, `styles`, `urgency`, `attachment`, and `attachments` (including base64-encoded `MediaBuffer`).
  - `upload_attachment()` now serializes `MediaBuffer` inputs instead of silently dropping them.
  - `get_unread_marks()` now maps and returns RPC results via `map_unread_marks()` instead of discarding them.
  - Cleaned up QR login handlers in `finally:` and realtime handlers on `stop_listening()` / `start_listening()` failure to prevent handler leaks.
- **Cloud Message History Pagination & Stop-Paging `-69` (`ZAPIND-PY-003`)**: `conversations.get_messages()` and `group.get_messages()` now support `cursor` and `is_old` pagination parameters, return `MessagePage.is_old`, and handle Zalo Cloud Message stop-paging code `-69` cleanly by returning an empty page with `has_more=False`.
- **Quote & Seen Event Mapping (`ZAPIND-PY-005`)**: Mapped `quote.fromD` / `quote.senderName` onto optional `MessageQuote.sender_name`, safely parsed string/bool group indicators in `map_message_kwargs()`, and extracted `realMsgId` / `msgId` in `map_seen_event()`.
- **DM `Conversation.get_messages()` Guard (`ZAPIND-PY-007`)**: Calling `await conversation.get_messages()` on a 1-on-1 `ThreadType.USER` conversation now raises a clear `ValidationError` pointing to `'old_messages'` and `conversations.request_old_messages(ThreadType.USER)`.
- **Async Lifecycle & Concurrency Fixes (`ZAPIND-PY-008`)**:
  - `EventBus.once()` now unregisters synchronously before invoking the handler so synchronous double-emits never fire twice.
  - `InFlightDeduplicator.run()` now shields every caller so cancelling one waiter does not abort concurrent waiters sharing the same request.
  - `Zapind.start_listening()` resets `_listening = False` if transport startup raises, unblocking retries.
  - QR login callbacks (`on_qr_code`, `on_scanned`, `on_expired`, `on_declined`) now safely support both sync and `async def` functions.
  - `FileSessionStore` and `EncryptedFileSessionStore` offload file I/O and `scrypt` key derivation to `asyncio.to_thread()` to avoid blocking the event loop.

### Added
- **WebSocket Historical Sync (`ZAPIND-PY-004`)**: Added `OldMessagesEvent`, `'old_messages'` realtime event handling, `conversations.request_old_messages(thread_type, last_msg_id=None)`, and `events.request_old_messages(...)`.
- **Restore-Only Login (`ZAPIND-PY-006`)**: Added `session_only: bool = False` option to `Zapind.login()` so servers can attempt restoring a saved session without blocking on QR fallback.

### Compatibility
- Fully backward-compatible with `1.0.0` (no breaking changes).

## [1.0.0] - 2026-11-26
- Initial stable release of the `zapind` Python SDK.
