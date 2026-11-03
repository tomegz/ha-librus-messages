# Librus Messages — v1 specification

Status: implemented for v0.1.0. Updated 2026-11-03.

Repository: `tomegz/ha-librus-messages`

Home Assistant domain: `librus_messages`

## Purpose and supported scope

Provide a small Home Assistant integration for the Librus Synergia messages
application at `wiadomosci.librus.pl`. Users receive preview notifications through
their own automations and open messages in Librus to read them.

Target the current stable Home Assistant release at implementation time. There
is no requirement to support older releases. Support installation and updates
through HACS as a public custom repository, with UI configuration and no SSH.

V1 excludes full-content fetching, attachments, sending messages, a custom inbox
UI, and separate acknowledgement tracking. It does not implement the older
Synergia messages interface or other school modules.

## Authentication and configuration

- Configure Librus username and password through the integration setup UI.
- Authenticate automatically and obtain or renew the messages-app session
  without manually copied browser cookies.
- Keep each configured account's session and tracking state independent.
- Store credentials in the HA config entry's `data`, user options in `options`,
  and processing state with `homeassistant.helpers.storage.Store`.
- Keep session cookies and authentication handoff tokens in memory only.
- HA's local configuration and Store files are not an encrypted credential vault.
- Credentials, cookies and token-bearing URLs must never appear in logs, entity
  attributes, diagnostics, fixtures, documentation examples or the repository.

## Fetching

- Poll the inbox list and unread-count endpoints at a configurable interval.
- Fetch metadata and previews only. Never call the message-detail endpoint or
  mark messages read automatically.
- Scheduled polling and the refresh button use the same fetching, authentication,
  pagination and detection logic.
- Fetch the complete inbox, including pagination. Do not assume a single page or
  stop merely because one previously seen message was encountered.
- Do not advance tracking or publish new-message events from an incomplete or
  failed refresh. Detect pagination inconsistencies rather than treating an
  incomplete result as a successful scan.

The feasibility spike verified automatic login, messages-session handoff,
paginated inbox retrieval, preview decoding and recovery after losing the
messages-app cookie. A separate authorized test confirmed that fetching full
message content marks an unread message read. The detail response can still
contain `readDate: null`; a subsequent inbox listing reveals the change.

## Entities per account

Entity IDs below are examples; HA may add account-specific suffixes.

| Example entity | State and purpose |
| --- | --- |
| `sensor.librus_unread_messages` | Current unread count reported by Librus. |
| `sensor.librus_latest_message` | Timestamp of the newest message currently in the inbox; attributes include message ID, sender, subject, preview, URL and read status. |
| `event.librus_new_message` | One occurrence per newly discovered message, carrying ID, sender, subject, sent timestamp, preview and URL. The event entity state is the occurrence time; the message's sent time is payload data. |
| `button.librus_refresh` | Request a refresh using the normal synchronization logic. |
| `sensor.librus_last_successful_sync` | Diagnostic timestamp of the last successful refresh, retained through failures and restarts. |

The latest-message entity represents the current inbox, not a retained snapshot
of the last message ever received. If the newest message is archived or deleted,
the entity changes to the next newest message still in the inbox. An empty inbox
gives an unknown latest-message timestamp with no message payload attributes;
the unread sensor can legitimately report zero after a successful fetch.

## Tracking and event semantics

- Persist seen message IDs independently per account using Store.
- Establish a baseline on the first successful setup without announcing
  historical messages.
- Restore tracking through restarts and reloads. Announce messages received
  during downtime if they remain available in the inbox and have not been seen.
- Detect newly discovered IDs independently of read status. A message read in
  Librus before the next poll still qualifies as new to the integration.
- Handle multiple new messages within one poll with separate event occurrences.
- Read-status changes and changes to an already-seen message do not announce it
  again. Removing the newest message may change the latest-message sensor, but
  must not announce the next older, already-seen message again.
- Publish events before durably saving the corresponding seen IDs. Accept the
  possibility of duplicate occurrences after an abrupt crash between those
  operations. Normal polling and clean restarts remain deduplicated.
- Do not claim exactly-once processing or guaranteed notification delivery.
- Persisted IDs track processing, not delivery to a phone or reading by a user.

## Failures and recovery

- Preserve tracking on failed fetches.
- Message sensors become unavailable on failed refreshes rather than reporting
  zero or presenting stale metadata as a successful refresh.
- The last-successful-sync sensor retains its previous successful timestamp.
- Refresh expired sessions automatically; genuinely rejected credentials should
  use HA's UI reauthentication flow rather than requiring file edits.
- Network failures and service errors must not erase the baseline or be treated
  automatically as incorrect credentials.
- Corrupt or unexpectedly missing tracking for a previously initialized account
  triggers automatic rebaselining with a visible HA warning, as detailed below.

## Notifications and history

Notification delivery belongs to user automations. Provide an example using
subject, sender, preview and URL, with a notification tap opening the message in
Librus. The example must handle several occurrences in one polling cycle and
avoid interpreting entity restoration or availability changes as new messages.

Recipients, quiet hours, wording and an optional Open in Librus action are user
configuration. Opening the link may require a Librus login in that browser.

Keep message payload attributes available to live entities and automations, but
exclude them from Recorder history. Count and timestamp history can remain.
This is a Recorder policy, not a guarantee that message metadata never reaches
disk: restored event state, automation traces and notifications can retain data.

## Implementation defaults for v1

These defaults fill in routine implementation details within the agreed scope:

- Poll every five minutes by default; expose a one-to-sixty-minute option.
- Serialize refreshes per account and coalesce overlapping requests.
- Announce a batch in chronological order, oldest first.
- Keep seen IDs for the lifetime of the config entry, including IDs of messages
  subsequently removed from the inbox; do not retain whole message bodies.
- Remove integration-owned tracking when the account entry is deleted.
- Keep tracking when the password is updated for the same account.
- Do not publish catch-up events during initial HA startup before automations
  and the event entity are ready to receive them.
- Use synthetic data in public fixtures and tests.

## Recovery after loss of established tracking

If an existing, previously initialized account's tracking is corrupt or
unexpectedly missing, automatically establish a fresh baseline from the next
complete, successful inbox refresh. Save that baseline without publishing events
for its messages, then resume normal detection on subsequent refreshes.

Show a visible HA warning explaining that tracking was reset and messages already
in the inbox at that reset were not announced. Recovery requires no user action,
SSH, or custom repair flow. The unread-count and latest-message sensors continue
to reflect successful fetches.

Network failures, failed polls, expired sessions, rejected credentials, storage
permission errors and temporary storage I/O failures must not trigger a reset.
An unsupported stored schema version is not corruption and must not be silently
discarded. Preserve existing tracking and report or retry the actual failure.

Librus read status cannot reconstruct which events were previously published.
First-time setup still uses the normal silent baseline without a recovery warning.

## References

- [HA config entries](https://contributors.home-assistant.io/core/integrations/config-entries/)
- [HA event entities](https://contributors.home-assistant.io/core/entities/event/)
- [HA Recorder attribute exclusions](https://contributors.home-assistant.io/core/entities/state-attributes/#keep-attributes-out-of-the-history)
- [HA Store implementation, 2026.10.4](https://github.com/home-assistant/core/blob/2026.10.4/homeassistant/helpers/storage.py)
