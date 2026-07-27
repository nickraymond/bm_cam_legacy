# Sofar Spotter Command API — Reference (digitized)

> **Provenance:** Transcribed 2026-07-27 from Nick's screen capture of the
> Sofar Ocean Notion page "Spotter Command API Reference Document"
> (`sofarocean.notion.site/...2e08ff959450803aadf6d50b600033ba`, captured
> 2026-07-27 09:50). Source PDF is an image-only capture; text below was
> transcribed visually.
>
> **⚠️ One section still MISSING:** the Notion toggle **"Example cURL
> Request"** was collapsed when the page was captured (auth header format
> + exact request example not in the source). The **"Responses"** section
> was supplied by Nick as a follow-up screenshot 2026-07-27 and is
> transcribed below. Auth: assume Sprint09's working `api/sensor-data`
> token auth; confirm at first Phase C send.

## Background

The Spotter Command API sends commands directly to a Spotter device over
cellular or satellite telemetry. Commands are queued (cloud-side mailbox)
and executed by the device when it next successfully transmits using the
selected telemetry path. The sender must specify which telemetry to use.

## Endpoint

```text
POST /user-rest/devices/:spotterId/command
```

(Host not shown on the page; Sprint09's proven REST base is
`api.sofarocean.com` — verify in Phase C.)

## Request body

```text
{
  telemetry: 'cellular' | 'cellular_with_fallback' | 'satellite',
  message?: string,              // printable ascii characters and newlines only
  clear_command_queue?: boolean, // default false
}
```

Fields:

- **telemetry** (required) — how the command is delivered:
  - `cellular` — send over cellular only
  - `satellite` — send over satellite only
  - `cellular_with_fallback` — attempt cellular first, fall back to
    satellite if needed
- **message** (optional) — the command string to be sent to the Spotter.
- **clear_command_queue** (optional) — if `true`, clears all pending
  commands for the selected telemetry before enqueuing the new command.

> ⚠️ You must provide **either** a non-empty `message` **or** set
> `clear_command_queue` to `true`.

## Command requirements & constraints

To successfully enqueue a command, all of the following must be true:

### Ownership & connectivity

- The Spotter must belong to the account associated with the API token.
- The Spotter must have an **active line** for the selected telemetry;
  `cellular_with_fallback` requires **both** cellular and satellite lines
  to be active.

### Message format

- Commands may contain **printable ASCII characters and newlines only**
  (letters, numbers, symbols, punctuation, spaces). Tabs (`\t`) are
  **not allowed**.
- Commands may be chained using newline characters (`\n`),
  e.g. `cfg vle 1\ncfg save\n`.
- **Maximum command length: 270 bytes.** The final newline counts toward
  the limit; if the message does not end with a newline, the server adds
  one before enforcing the length limit.
- Requests must contain either a non-empty `message` or
  `clear_command_queue: true`.

## Example cURL request

*(Collapsed toggle in the capture — content not available. Re-capture
needed.)*

## Responses

All responses include a JSON body with `status` and `message`.

### Successful responses

- **HTTP Status:** `202 Accepted`
- The command has been **successfully enqueued** (not necessarily
  executed yet).

```json
{
  "status": "success",
  "message": "successfully enqueued clearing cellular queue, sending message"
}
```

### Unsuccessful responses

- **HTTP Status:** `400 Bad Request`
- The response message explains why the request failed.

```json
{
  "status": "bad request",
  "message": "Invalid telemetry option: cell. Valid options are: cellular, c…"
}
```

*(Error example message truncated at the screenshot edge; it lists the
valid telemetry options.)*

## Costs & billing

Charges apply **only after a successful request**; failed requests are
not billed.

### Telemetry costs

| Commands sent using | Cost |
|---|---|
| `satellite` | 1 satellite credit per **50 bytes per command** |
| `cellular` | No satellite credits required |
| `cellular_with_fallback` | Cost depends on whether the command is ultimately delivered via satellite. If `clear_command_queue=true`, **1 additional satellite credit** is charged. |

### Minimum credit balance

- `satellite` or `cellular_with_fallback` requires a **minimum balance
  of 100 satellite credits**.
- No minimum balance is required for `cellular`.

## Rate limits

- **1 successful request per minute per Spotter.**
- Once a successful request is made, **all** subsequent requests
  (successful or not) are rejected until the cooldown expires.
- No daily limit beyond the per-minute cooldown.

## FAQ

### How are commands processed?

Commands are **not delivered immediately**. Instead:

1. A successful request places the command into a **mailbox**.
2. Each telemetry type (cellular and satellite) has its **own mailbox**.
3. When the Spotter successfully transmits using that telemetry, it
   checks the mailbox and executes queued commands **one at a time, in
   order**.

> **Important notes:** if a command triggers a reboot, the reboot happens
> immediately, and any commands behind it will not execute until the
> Spotter transmits again.

### How does the mailbox work?

| Command | Expiration | Limit |
|---|---|---|
| `satellite` | Expire after 5 days | Maximum of 50 commands per Spotter |
| `cellular` | Do not expire | No limit |

### What is the `clear_command_queue` flag?

A successful request with the flag set erases all pending commands in the
requested telemetry option's mailbox prior to enqueuing the new command.
When set to `true`:

- Clears all pending commands **before** adding the new one.
- Clears one mailbox (`cellular` or `satellite`), or **both mailboxes**
  if using `cellular_with_fallback`.
- Commands **cannot be selectively removed**.

When to use it (common scenarios): you no longer want queued commands to
run; you're unsure what's in the queue and want a clean state; you need
to prioritize a critical command.

## Troubleshooting

### Why isn't my Spotter receiving my command?

For Spotter to receive commands, it must first successfully transmit a
message using the **same telemetry as the command**. Make sure the
Spotter has sufficient battery and a clear view of the sky.

---

## Implications for BM camera Sprint10 (our notes, not Sofar's)

- The `message` string is a **Spotter console command line** (the doc's
  chaining example `cfg vle 1\ncfg save\n` is Spotter CLI syntax). Our
  bench-proven injection `bm pub bmcam/cmd {"id":N,"c":"roi","v":2} 1 1`
  is itself a console command, so the expected downlink path is: cloud
  mailbox → Spotter executes `bm pub ...` on transmit → BM bus → mote →
  Pi UART — identical to the Phase B path from the console. **Must be
  verified in Phase C** (first test: one `bm pub` ping via
  `telemetry: cellular`).
- Our compact-JSON payload has no tabs and is printable ASCII → format-
  legal. Worst-case command line is far under 270 bytes.
- `telemetry: cellular` costs no satellite credits and its mailbox never
  expires with no queue limit — right default for bench and field.
- Rate limit (1/min/Spotter) must be enforced client-side in the GUI
  send path; the in-flight lockout (DESIGN D10) already fits this.
- Delivery is gated on the Spotter **successfully transmitting on that
  telemetry**: commands land when our node wakes and transmits — matches
  the queue-while-off model in SPEC/D5. The camera's pre-capture listen
  window catches what the mailbox drains at wake.
- `clear_command_queue` is the recovery tool if the operator stuffs the
  mailbox; GUI should expose it as an explicit "flush queue" action, not
  a default (it cannot selectively remove, and with fallback telemetry
  it clears both mailboxes and bills a satellite credit).
