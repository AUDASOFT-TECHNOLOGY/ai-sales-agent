# Read model and metrics (lot 2B)

How a client's prospects and conversations are listed at volume, independently of the agent, and the
figures the dashboards (lot 4) consume.

## 1. Where the data lives

| Data | Place |
| --- | --- |
| Products, circuits (lanes) | `config_versions` (documents) |
| Who a prospect is | the source (Sheets, CRM), or `op_local_prospects` for people Revansa created |
| Where Revansa stands with them | `op_prospect_states` |
| Messages, actions, facts | `op_messages`, `op_actions`, `op_facts` |
| **Read model of the prospects** | `op_prospect_index` (new, migration 0022) |

`op_prospect_index` is a **projection**, never the truth: one row per prospect the platform has seen,
with what a list filters, searches and sorts by (name, e-mail, phone, `search_text`, status, stage,
lane, source, channel, next action, last contact / inbound / activity, `needs_human`, conversation id).
It is refreshed by the repository (`CompositeRepository._index`, best effort) each time the Engine
reads the whole list and each time it saves prospects, and seeded by the migration from the states and
the local prospects. A source's prospect therefore appears in the read model after the first Engine
read; until then (empty read model) the API lists through the Engine, which fills it as a side effect.
The read model can be rebuilt at any time; nothing depends on it for correctness of sending.

## 2. Schema (migration `0022`, down_revision `0021`)

Additive only; backfilled from the stored documents (an unreadable document leaves NULL, never a guess).

- `op_messages`: `direction`, `channel`, `sent_at`; indexes `(client_id, prospect_id, sent_at)`, `(client_id, sent_at)`.
- `op_actions`: `prospect_id`, `action_type`, `due_at`; indexes `(client_id, prospect_id)`, `(client_id, status, due_at)`.
- `op_facts`: `fact_key`; index `(client_id, fact_key)`.
- `op_prospect_index`: PK `(client_id, prospect_id)`; **unique `(client_id, lane_id, external_id)`**
  (a person is held once per client, circuit and identifier in the source; a refresh the database
  refuses is skipped); indexes `(client_id, status)`, `(client_id, lane_id)`, `(client_id, channel)`,
  `(client_id, next_action_at)`, `(client_id, last_activity_at)`, `(client_id, display_name)`.
- Deduplication that already existed and stays: `op_prospect_states` PK `(client_id, prospect_id)`,
  `op_local_contacts` (one local prospect per phone / address), `op_actions` idempotency key,
  `op_messages` / `op_facts` ids.

Circuit / product / source / channel of a prospect: `lane_id`, `source_id`, `channel` columns of the
index. A conversation is reached through its prospect (`op_messages.prospect_id`). The **product** is
not stored on the prospect: it is derived from the lane's products in the client's settings
(`sales_app.metrics.fold_by_product`, `lanes_selling`).

## 3. Reading contract (`sales_app.read_model`)

Port `ProspectReadModel` (bound to one client, no method takes a client id), SQL adapter
`sales_app.persistence.read_model_rows.SqlProspectReadModel`, wired as `Application.read_models`.

- `list_prospects(ProspectListQuery) -> Page[ProspectItem]`
- `list_conversations(ProspectListQuery) -> Page[ConversationItem]` (prospects with at least one stored message; `message_count`, `last_message_at`, `last_message_direction`)
- `conversation(prospect_id, limit, offset, order) -> ConversationDetail | None` (messages page, `total_messages`, planned next action, failed actions as `ActionError`)
- `ProspectFilter`: `statuses`, `lane_ids` (resolved circuit; `()` = nobody), `channel`, `source_id`, `needs_human`, `has_error`, `active_since` / `active_until` (`[since, until)` on last activity; on the last message for conversations), `search`.
- Search: lower-cased substring on name, e-mail, phone, and phone digits; every word must be found; wildcards are literals (`sales_app.read_model.search`).
- Sorts: `name` (name, id) and `recent` (latest activity first, none last).
- Limits (documented, enforced with 422): page 1..100 (default 50); message page 1..500 (default 200). There is no other cap.
- Pure in-memory twin `list_in_memory` / `conversations_in_memory` gives the same answers for the Engine fallback.

## 4. HTTP (`/client/v1`, session client only, `Cache-Control: no-store`)

Backward compatible: every previous field is unchanged; new fields and parameters are additive.

- `GET /prospects?limit&offset&sort&q&status*&lane&product&channel&source&needs_human&has_error&active_since&active_until`
  New response fields per prospect: `source_id`, `last_activity_at`, `needs_human`, `status_reason`, `has_error` (`null` when listed through the Engine).
- `GET /conversations` same parameters (default `sort=recent`) -> `{total, limit, offset, conversations: [{prospect, message_count, last_message_at, last_message_direction}]}`.
- `GET /conversations/{prospect_id}?limit&offset&order` -> previous fields plus `total_messages`, `limit`, `offset`, `order`, `prospect`, `next_follow_up`, `errors`.
- With a database the lists are served without the Engine (agent not loaded is fine). Another client's prospect is a plain 404 (or an empty list). `has_error` through the Engine fallback is never true.
- `GET /summary` is unchanged (still Engine-based).

## 5. Metrics contract (`sales_app.metrics`)

`MetricsService(reader, lane_products).client_metrics(Period(since, until)) -> ClientMetrics` (`.to_json()`).
Reader: `sales_app.persistence.metrics_rows.SqlMetricsRows` (`Application.metrics_readers`). Pure helpers:
`labelled`, `fold_by_product`, `lanes_selling`, `percent`, `payment_counts`.

| Field | Meaning |
| --- | --- |
| `prospects_total`, `by_status`, `by_lane` (circuit), `by_product`, `by_channel`, `by_source` | snapshot of the read model; with a period, prospects whose last activity falls in it. Missing key = `unassigned`. A multi-product lane counts in each product (the sum may exceed the total); a whole-catalog lane counts under `ALL`. |
| `prospects_contacted` | distinct prospects with at least one outbound message in the period |
| `conversations` | distinct prospects with at least one message in the period |
| `messages_sent`, `replies_received`, `prospects_replied` | outbound / inbound messages, distinct prospects who wrote |
| `reply_rate_percent` | `prospects_replied / prospects_contacted`; may exceed 100 (people who wrote first); `NotTracked` when nobody was contacted |
| `needs_human` | snapshot: prospects paused, waiting for a person |
| `with_errors` | distinct prospects with a failed action due in the period |
| `messages_by_day`, `messages_by_channel` | chart series (UTC days) |
| `payments.declared` | prospects who said they paid / sent a receipt (facts by date) |
| `payments.confirmed` | prospects whose payment a person at the client confirmed (valid confirmation only) |
| `payments.awaiting_confirmation` | declared and not confirmed |
| `conversions_declared` | = `payments.declared`: prospects whose payment the prospect declared (never counted as confirmed) |
| `conversions_confirmed` | = `payments.confirmed`: prospects whose payment is confirmed (by a person at the client or a reliable mechanism) |
| `conversion_rate_declared_percent`, `conversion_rate_confirmed_percent` | conversions / `prospects_contacted` of the period (same population caveat as the reply rate: may exceed 100); `NotTracked` when nobody was contacted |

A figure not tracked is `{"tracked": false, "reason": "..."}`; an empty tracked figure is a real `0`.

## 6. Known limits

- Names and contacts of a source's prospects reach the read model only after the Engine has read the
  source (not at the migration: states hold no name by design). Staleness is bounded by the run cycle.
- The product filter needs the client's lanes; a client without lanes of its own (implicit lane) is not restricted by `product`.
- `external_id` uniqueness only applies when both a lane and an external id are known.
- Message counts / last direction are not known in the Engine fallback (`null`).
- Payment figures use the facts' `recorded_at` for the period; a confirmation outside the window does not pair with a claim inside it.
- Conversions are the payment counts (declared / confirmed) with rates over the prospects contacted. Payment facts carry no lane or channel in the metrics reader, so there is no conversion breakdown by product, circuit, channel or source, and the daily series is monthly (performance). A prospect confirmed without a recorded declaration counts as confirmed only. The HubSpot / Google Sheets write-back does not expose payment statuses today, so the conversion result is not synchronised there (document-only: add the status to the write-back mapping when the client asks for it).
