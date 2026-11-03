"""Constants for Librus Messages."""

DOMAIN = "librus_messages"
CONF_INTERVAL = "poll_interval"
CONF_INITIALIZED = "tracking_initialized"
DEFAULT_INTERVAL = 5
EVENT_NEW_MESSAGE = "new_message"
STORAGE_VERSION = 1
PRIVATE_ATTRIBUTES = frozenset(
    {"message_id", "sender", "subject", "preview", "url", "sent_at", "read", "body"}
)
