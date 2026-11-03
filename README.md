# Librus Messages for Home Assistant

A small, unofficial integration for the new Librus Synergia messages application
at [wiadomosci.librus.pl](https://wiadomosci.librus.pl/nowy/inbox).

Get preview notifications using your own Home Assistant automations, then open
Librus to read the message. Polling **does not mark messages read**: this
integration only requests the inbox list and unread count. It never requests
full message content, attachments, or sends messages.

Requires **Home Assistant 2026.10.4 or newer**. Tested against 2026.10.4; future
HA and Librus changes may require integration updates. This project is not
affiliated with Librus or Home Assistant.

## Install through HACS

1. Open **HACS → ⋮ → Custom repositories**.
2. Add `https://github.com/tomegz/ha-librus-messages`, type **Integration**.
3. Find **Librus Messages** in HACS and download it.
4. Restart Home Assistant through **Settings → System → Restart Home Assistant**.
5. Open **Settings → Devices & services → Add integration → Librus Messages**.
6. Enter an account label and your **Synergia login and password**. Use the login
   which opens the new messages application, not an unrelated Librus portal account.

HACS installs the files; adding the integration creates the account and entities.
No SSH, YAML credentials, manually copied cookies, or external password-manager
connection is needed. Add the integration again for a separate account.

The first successful refresh establishes a baseline. It fills the sensors but
does **not** announce historical messages. The new-message event may therefore
show `unknown` until a genuinely new message arrives.

If you also installed an older Librus integration, disable its account entry if
you no longer need it. This integration uses its own domain and does not change
the older integration's configuration.

## Entities and options

With the default account label `Librus`, example entity IDs are:

| Entity | Meaning |
| --- | --- |
| `sensor.librus_unread_messages` | Unread count from Librus. |
| `sensor.librus_latest_message` | Newest message currently in the inbox: sent timestamp, with `message_id`, `sender`, `subject`, `preview`, `sent_at`, `url`, and `read` attributes. |
| `event.librus_new_message` | One occurrence per newly discovered message, including messages already read elsewhere. Event type: `new_message`. Payload uses the same attributes as the latest-message sensor. |
| `button.librus_refresh` | Request an immediate refresh through the same coordinator as scheduled polling. |
| `sensor.librus_last_successful_sync` | Last successful refresh, retained through errors and restarts. |

Your account label, HA language and existing entities can affect entity IDs.
Check the integration's device page before copying an automation.

- Default polling: **5 minutes**. Change it through the integration's options
  from **1 to 60 minutes**.
- Use **Reconfigure** to update a password. Reauthentication also appears in the
  UI when credentials stop working. Updating the same account preserves tracking.
- Latest means newest **still in the inbox**. Archiving it reveals the next
  newest message; an empty inbox gives an unknown latest timestamp.
- Read status updates on the next poll. Previews are the short snippets supplied
  by Librus; they are not full message bodies.

## Example phone notification automation

In **Settings → Automations & scenes**, create a new empty automation, open its
menu and choose **Edit in YAML**. Paste the example below, then replace the event
entity and `notify.mobile_app_your_phone` with your own values.

```yaml
alias: Librus - new message
description: Send one preview notification per new Librus message
triggers:
  - trigger: state
    entity_id: event.librus_new_message
conditions:
  - condition: template
    value_template: >-
      {{ trigger.from_state is not none
         and trigger.to_state is not none
         and trigger.from_state.state != 'unavailable'
         and not trigger.from_state.attributes.get('restored', false)
         and trigger.to_state.state not in ['unknown', 'unavailable']
         and trigger.from_state.state != trigger.to_state.state
         and trigger.to_state.attributes.get('event_type') == 'new_message' }}
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: "{{ trigger.to_state.attributes.subject }}"
      message: >-
        {{ trigger.to_state.attributes.sender }}:
        {{ trigger.to_state.attributes.preview }}
      data:
        tag: "{{ trigger.entity_id }}_{{ trigger.to_state.attributes.message_id }}"
        url: "{{ trigger.to_state.attributes.url }}"
        clickAction: "{{ trigger.to_state.attributes.url }}"
        actions:
          - action: URI
            title: Open in Librus
            uri: "{{ trigger.to_state.attributes.url }}"
mode: queued
max: 100
```

`url` supports an iOS notification tap and `clickAction` supports Android. The
optional action opens the same message link. Your browser may ask you to log in.
The message becomes read when **you open it in Librus**.

The state condition ignores entity restoration and unchanged event timestamps.
`queued` handles bursts; the example queues at most 100 automation runs. The tag
lets supported phone apps replace a repeated notification for the same message.
Recipients, quiet hours, notification delivery and queue limits belong to your
automation. Running the automation manually without a trigger will not supply
the message attributes used by these templates.

## Reliability and privacy

- All inbox pages are fetched. A failed/inconsistent scan does not advance
  tracking or publish a partial batch. Large scans have a finite safety limit
  of 1,000 pages and a 180-second overall deadline; hitting either fails safely.
- Messages discovered after downtime are announced if still in the inbox.
  Messages deleted or archived before any successful poll cannot be detected.
- Seen IDs are kept for the lifetime of the account entry, independent of read
  status. Batches are announced oldest first. Deleting the account entry deletes
  its tracking; adding it again creates a fresh silent baseline.
- Events are published before saving seen IDs. An abrupt crash in between may
  replay an event. There is no exactly-once or phone-delivery guarantee.
- Failed polls make message sensors unavailable; the last-successful-sync value
  stays unchanged. Rate limits are respected even for manual refresh requests.
- Missing or corrupt tracking is rebuilt automatically with a visible warning
  under **Settings → System → Repairs**. Messages in that new baseline are not
  announced. Normal network/authentication/storage I/O failures never reset it.
- Credentials are stored in HA's config entry under `.storage/core.config_entries`.
  Seen IDs and sync time are stored under `.storage/librus_messages.<entry_id>`.
  These are local files, not an encrypted secret vault. Protect HA access and backups.
- Sessions are isolated per account and cookies/tokens stay in memory. Credentials
  and token-bearing URLs are not logged or exposed through entities.
- Sender, subject, preview and other message payload attributes are excluded from
  Recorder history. They remain available to live automations. HA event restoration,
  automation traces and notifications may still retain payloads.

Only the new `wiadomosci.librus.pl` inbox is supported. Interactive MFA/CAPTCHA,
legacy message pages, archives, other school modules, attachment downloads and
message sending are outside v1. Librus provides no compatibility guarantee for
these private web endpoints.

## Development

Use Python 3.14:

```sh
python3.14 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest -q --cov=custom_components.librus_messages
```

Tests use synthetic data and mocked network responses, plus HA's real config
flow, entity and storage machinery. See [SPEC.md](SPEC.md) for the agreed behavior.

The GitHub Actions configuration is provided in
[`.github/validate.yml.example`](.github/validate.yml.example). Move it to
`.github/workflows/validate.yml` to enable tests, lint, hassfest and HACS validation.
Publishing an active workflow requires GitHub credentials with workflow permission.

When reporting issues, include the integration/HA versions and safe error
category. Do not attach credentials, session cookies, login URLs, message bodies,
or unredacted account exports.
