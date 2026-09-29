# Naga client UI: design system and art direction

Status: **design proposal; a visual prototype exists in [../web/](../web/README.md)** (sample data only, no backend). The product is provisionally named **REVENSA** ("Turn conversations into revenue."). Nothing of the real application is implemented yet.
This document fixes the art direction, the design system and the screen architecture of the first
client-facing interface (Naga), and the rules that keep it reusable for the future Multi-Client Admin.
It is the "Web Admin frontend" of step 6 in [web-admin-product-design.md](web-admin-product-design.md)
section 9, and it obeys the boundaries set there and in [architecture.md](architecture.md).

Codename of the design language: **Nocturne** ("a night control room where light is information").
The codename is only a package prefix and can be renamed freely.

Language of this document: English, like the rest of `docs/`. UI copy examples are illustrative.

Contents

- **Part A. Context**: A1 what the repository tells us, A2 stack decision, A3 hard rules for the frontend
- **Part B. The design system** (numbered 1 to 28 as in the brief): 1 philosophy, 2 color, 3 gradient,
  4 typography, 5 spacing, 6 radius, 7 glass layers, 8 shadows, 9 background, 10 icons, 11 buttons,
  12 inputs, 13 cards, 14 KPI, 15 status, 16 tables, 17 timeline, 18 conversation, 19 AI activity,
  20 navigation, 21 modals and drawers, 22 toasts, 23 empty, 24 loading, 25 error, 26 responsive,
  27 accessibility, 28 motion
- **Part C. Architecture and screens**: C1 component architecture, C2 screen hierarchy, C3 UX
  patterns, C4 screen compositions, C5 what the UI can show today, C6 Multi-Client Admin reuse, C7
  build plan, C8 open decisions

---

# Part A. Context

## A1. What the repository tells us

Inspected: the whole tree, `pyproject.toml`, `src/sales_app/api/app.py`, `clients/naga/client.yaml`, and
the design documents ([web-admin-product-design.md](web-admin-product-design.md),
[client-configuration-design.md](client-configuration-design.md), [brain-settings.md](brain-settings.md),
[brain-commerce.md](brain-commerce.md), [core-facts-and-payments.md](core-facts-and-payments.md),
[naga-brain.md](naga-brain.md), [sheet-schema.md](sheet-schema.md)).

| Finding | Consequence for the UI |
|---|---|
| **No frontend exists.** No `package.json`, no JS/TS, no templates. Python 3.12, FastAPI, uv workspace, hatch wheel with an explicit `packages` list. | A new top-level `web/` directory can be added without touching Python packaging or the architecture tests (they only read `*.py`). |
| **No Admin API exists.** `sales_app/api/app.py` serves only `/health`, `/health/clients` and `/webhooks/{provider}`. The Admin API (`/admin/v1`) is a later step, in the same process. | The frontend is built against a **typed contract** (OpenAPI, mocked at first). It never reads the Sheet, the config store or secrets ([web-admin-product-design.md](web-admin-product-design.md) section 2). |
| **Order of work is fixed**: Configuration, then CRM and approvals, then Dashboard. Naga's immediate need is to **configure its catalog and commercial settings**. | Build order of screens follows section C7, not the order of the brief: Setup, Offers, Playbook, Settings first; Command Center, Prospects, Conversations, Performance as their APIs arrive. All screens are *designed* here. |
| **Naga has no business data.** `brain.settings: {}`; the agent cannot be enabled until settings validate. `sales_brain_kit.check_readiness` returns a list of what is missing. | The **first-run experience is a guided setup**, not an empty dashboard. The readiness list becomes the *Launch Runway* (section 19.7). |
| **Brain settings are one typed schema** with `json_schema()`, "unknown keys rejected everywhere", validation in two passes (structure, then references), each listing every problem with a path such as `brain.settings.catalog.products[x].pricing`. | Forms map server error paths to fields. The UI is curated (guided screens), and uses the JSON Schema for validation, limits and labels, not as a raw form dump. |
| **Configuration is versioned** (`ConfigStore`: publish, history, diff, rollback; a stored version is never rewritten). | Editing is **Draft, Review, Publish**, with History and Rollback (section C3, pattern 4). |
| **Four categories of setting**: infrastructure (operator), client configuration, Brain configuration, Core invariants. Clients can tighten guardrails, never loosen. Secrets are write-only or "set / not set". | The client console shows only categories 2 and 3. Guardrail caps are drawn as **locked zones** (section 19.2). The WhatsApp agent number is read-only with health. Operator wiring never appears. |
| **Modes**: `dry_run`, `approval`, `autonomous`; the requested mode is clamped by a deployment ceiling (`mode_clamped` is already in `/health/clients`). `autonomous` needs operator approval. | The **Autonomy Dial** (section 19.2) shows requested versus effective mode. |
| **Payment rule**: the agent never confirms a payment. `claimed`, `proof_received` are unverified; `paid` exists only after a person with `payments.confirm` confirms. | The **Payment Seal** (section 18.4) makes the three states visibly different; the confirm control exists only for permitted people. |
| **Exact values (prices, payment details, links) never pass through a model**; deterministic code inserts them verbatim. | A "Verbatim" input and badge pattern (section 12.5) tells the user these values are kept character for character. |
| **Facts have status and source** (`confirmed`, `discovered`, `hypothesis`; prospect, conversation, client data, human, system, brain inference), with evidence. | **Certainty Rings** (section 15.5): one visual language for how sure the agent is, used everywhere a fact appears. |
| **One KPI computation** in `sales_core` (future); CEO report and Dashboard share it. Appointments, conversions, objections, feedback and non-conversion reasons are **not modelled yet**. | The frontend **never computes a KPI**. KPI components have a first-class "Not tracked yet" state (section 14.6). |
| **Stages are client-defined opaque labels** (they may carry a generic milestone). Core lifecycle is `new, active, paused, opted_out, closed`. | Stage colors come from the milestone, else an ordered neutral scale; nothing hard-codes Naga's stages. |
| `Africa/Tunis` timezone; agent languages are client data. | The UI is **RTL-ready and i18n-ready by construction** (logical CSS properties, per-message `lang`, Arabic font fallbacks), even if the first UI locale is not Arabic. Which UI locales ship is a product decision (C8). |

## A2. Stack decision

There is no frontend stack to inherit, so this is a proposal, made before any implementation as asked.

| Choice | Decision | Why |
|---|---|---|
| App type | **Single-page app**, static build | Fully behind authentication, no SEO, no server rendering need. Served by a CDN or by `sales_app` as static files on the same origin as `/admin/v1` (no CORS). |
| Framework | **React 19 + TypeScript (strict)** | Largest ecosystem of accessible headless primitives; strong typing against the generated API client. |
| Build | **Vite** | Fast, no Node server to operate, static output. |
| Styling | **Tailwind CSS v4, CSS-first**, with the tokens of this document as CSS custom properties | Tokens live in plain CSS variables, so they can be themed per tenant and shared with non-Tailwind surfaces. Tailwind only supplies utility ergonomics. |
| Primitives | **Radix UI** (headless) | Accessibility (focus management, ARIA, keyboard) is solved once; all visuals are ours. |
| Motion | **Motion** (`motion/react`) for layout and spring animation, CSS for ambient loops | Shared-layout transitions are needed for the Opportunity River; ambient effects stay in CSS (cheaper, pausable). |
| Data | **TanStack Query** over a generated OpenAPI client | Caching, polling and mutations with optimistic updates; live updates by SSE later, polling fallback. |
| Routing | **React Router** | Route-per-screen, drawers as nested routes so links are shareable. |
| Charts | **Hand-built SVG** for the signature visuals, `d3-scale` and `d3-shape` for maths only | The signature visuals (Ribbon, Thread, Dial, Pulse) are the product identity. A chart library would impose its look. |
| Icons | **Lucide** plus a small custom set | Consistent 1.5px stroke; custom glyphs for agent concepts (section 10). |
| Fonts | **Self-hosted** Geist, Geist Mono, Instrument Serif, IBM Plex Sans Arabic, Noto Naskh Arabic | No third-party font request (privacy, CSP, speed). All are open licences. |
| Living documentation | **Storybook** with the a11y addon | This document is the spec; Storybook is the executable reference. |
| Tests | **Vitest**, **Playwright**, **axe-core** in CI, visual regression on Storybook | Accessibility and contrast are release gates, not reviews (section 27). |
| Browser baseline | Last two versions of Chrome, Edge, Firefox, Safari (16.4+), iOS Safari, Android Chrome | Needs `backdrop-filter`, container queries, `color-mix()`, `@property`. Fallbacks for each are specified. |

Rejected: **Next.js** (an SSR server and a second deployment for no benefit here); **server templates
with HTMX** (cannot carry the motion, the shared layout transitions and the component reuse the
Multi-Client Admin will need); **a component kit such as MUI or Ant** (would fight the glass language
and produce the generic-SaaS look we are avoiding).

Repository layout (new, self-contained, no change to Python code):

```
web/
  package.json                pnpm workspace root
  packages/
    tokens/                   tokens.css, tokens.ts, contrast test    (no React)
    ui/                       primitives and patterns                 (no data fetching)
    ui-agent/                 domain components: Pulse, Dial, Thread, River, Seal...  (props only)
    api-client/               generated from the /admin/v1 OpenAPI schema
    i18n/                     message catalogs, locale and direction helpers
  apps/
    console/                  the client console (Naga now, any client later)
    (operator/)               the future Multi-Client Admin, same packages
  .storybook/
```

Add `web/node_modules/`, `web/**/dist/` to `.gitignore` when the directory is created.

## A3. Hard rules for the frontend

Derived from the architecture documents. They are review criteria.

1. **Only `/admin/v1`.** The frontend imports nothing from `sales_core`, never touches the Sheet, the
   config store, the database or secrets.
2. **No KPI arithmetic in the browser.** Deltas, rates, funnels and comparison periods come from the API
   (one KPI computation). The UI formats and draws.
3. **No commercial rule in the browser.** Whether a prospect is "hot", which step comes next, which
   payment method applies: the API says, the UI renders. (Presentation of API values is fine; deriving
   business meaning is not.)
4. **No secret ever in the UI.** Credentials are shown as "set / not set" with a replace action.
5. **No client name in the design system.** "Naga" is data (`display_name`, logo, accent), never a string
   or color in `tokens`, `ui` or `ui-agent`.
6. **Client and operator surfaces stay separate.** Operator-only settings (wiring, secrets, mode ceiling,
   guardrail caps) are not in the client console, hidden or not. They live in the operator app.
7. **Permission-aware.** Roles (operator, owner, editor, viewer) and permissions such as
   `payments.confirm` come from the API; the UI reflects them, and never trusts itself to enforce them.

---

# Part B. The design system

## 1. Design philosophy

### 1.1 The idea

> **Light is information.** The interface is a quiet, deep night room. Nothing glows without a
> reason. When the agent thinks, the room fills with violet light. When it needs you, an amber edge
> appears. When something is won, emerald settles in. When nothing is happening, the room is calm.

Most SaaS dashboards show *data about a system*. Naga's interface shows **a colleague at work**: it
has a state, an intention, a voice, a level of certainty, and a boundary of autonomy you control. Every
design decision serves one of these five ideas:

| # | Idea | Expressed as |
|---|---|---|
| 1 | **Presence**: the agent is alive and you can see it | the *Pulse* (a living orb), the *Day Thread* with a "Now" line, ghosted future intentions |
| 2 | **Voice**: the agent explains itself in plain language | agent narration in a serif italic, distinct from the interface's sans-serif |
| 3 | **Certainty**: how sure is the agent, and about what | *Certainty Rings* on every fact; unverified payment looks unverified |
| 4 | **Autonomy**: you decide how much it may do alone | the *Autonomy Dial*, guardrails drawn as locked zones |
| 5 | **Direction**: always know what to do next | the *Needs You* tray first, one primary action per view |

### 1.2 Principles

1. **Calm by default, alive on purpose.** Ambient motion is slow and tiny; anything that moves fast
   means something.
2. **One hero per screen.** One dominant element (a Pulse, a numeral, a decision), everything else
   recedes. Hierarchy by size, light and space, not by borders.
3. **Space is the luxury.** Generous padding, few elements, big numerals, short sentences.
4. **Sentences over widgets.** Prefer "Amira asked about price; a draft is ready" to three badges.
5. **Color carries meaning, never decoration.** Five intents (section 2.4). If a color has no meaning
   here, it is not used.
6. **Depth shows importance.** The more a surface matters right now, the higher and lighter it sits.
7. **Trust is visible.** Exact values are marked verbatim; unverified things look unverified; the
   person who acted is named; guardrails are visible.
8. **Simple for a non-technical owner.** No jargon (`dry_run`, `handoff`, `stage`, `fact`) in the client's
   language; the mapping is in section 15.6.
9. **Elegant, not gaming.** No saturated neon, no hard glows, no glitch, no scan-lines, no
   particle storms. Halos are low-alpha and diffuse; gradients are analogous and desaturated.
10. **Credible.** It must look like a product a CEO trusts with customers and money.

### 1.3 The signature elements

Kept few on purpose. Each is specified in the section indicated.

| Name | What | Section |
|---|---|---|
| **Pulse** | living orb for the agent's state | 19.1 |
| **Autonomy Dial** | requested vs effective autonomy with locked guardrail zone | 19.2 |
| **Day Thread** | vertical timeline: past solid, "Now", future as ghosts | 17 |
| **Needs You tray** | decisions as keyboard-first cards | 19.5 |
| **Opportunity River** | prospects as beads flowing through stage banks | 13.4 |
| **Certainty Rings** | solid, partial, dotted ring = confirmed, learned, guessed | 15.5 |
| **Payment Seal** | claimed, proof, confirmed-by-a-person, ending in an engraved seal | 18.4 |
| **Story KPIs and Funnel Ribbon** | numerals with a sentence, one ribbon narrowing to outcomes | 14 |
| **Agent Voice** | serif italic reserved for what the agent says or narrates | 4.3 |
| **Verbatim lock** | exact values marked as kept character for character | 12.5 |
| **Launch Runway** | readiness as gates the agent must pass before takeoff | 19.7 |
| **Mood Aurora** | ambient background that shifts subtly with the agent's state | 9.2 |
| **Fleet Constellation** (later) | each client as a Pulse in the operator's grid | C6 |

---

## 2. Color system

### 2.1 Approach

Three layers of tokens: **primitives** (raw scales, never used directly in components), **semantic**
(what a color *means*), **component** (per component overrides). Components consume semantic tokens
only. This is what makes a light theme, a tenant accent and the operator variant possible later.

Colors are authored in sRGB hex here for readability. Implement halos with `rgb(var(--tint-x) / a)`
channel triplets (section 2.5) so alpha can vary.

### 2.2 Primitive scales

**Abyss** (backgrounds): near-black navy with a violet undertone, so the dark reads as depth rather
than as a flat gray.

| Token | Hex | Use |
|---|---|---|
| `abyss-0` | `#070914` | page base |
| `abyss-1` | `#0A0D1B` | recessed regions |
| `abyss-2` | `#0E1224` | modal / solid fallback surface |
| `abyss-3` | `#141A30` | solid fallback, elevated |
| `abyss-4` | `#1C2440` | solid fallback, highlighted |

**Aether** (violet, the agent's intelligence):

| Token | Hex |
|---|---|
| `aether-200` | `#DDD6FF` |
| `aether-300` | `#C3B7FF` |
| `aether-400` | `#A996FF` |
| `aether-500` | `#8470F2` |
| `aether-600` | `#6C57DB` |
| `aether-700` | `#5443B8` |
| `aether-800` | `#3A2F85` |
| `aether-900` | `#241C55` |

**Ice** (cyan, your actions and your data):

| Token | Hex |
|---|---|
| `ice-200` | `#C6EBF8` |
| `ice-300` | `#9EDDF3` |
| `ice-400` | `#6FCBEB` |
| `ice-500` | `#5CC3E6` |
| `ice-600` | `#3BA6CC` |
| `ice-700` | `#2A83A6` |
| `ice-900` | `#0E3345` |

**Emerald** (results):

| Token | Hex |
|---|---|
| `emerald-300` | `#8DEBC2` |
| `emerald-400` | `#5FE0A8` |
| `emerald-500` | `#34C98E` |
| `emerald-600` | `#22A672` |
| `emerald-900` | `#0C3B2B` |

**Amber** (needs a human):

| Token | Hex |
|---|---|
| `amber-300` | `#F7D58B` |
| `amber-400` | `#F2BE5C` |
| `amber-500` | `#E8A63A` |
| `amber-600` | `#C4861F` |
| `amber-900` | `#45300C` |

**Rose** (error, danger, "asked to stop"). Added beyond the brief: an interface that sends messages to
real people needs an unambiguous danger color that is not amber. It is muted, never a saturated red.

| Token | Hex |
|---|---|
| `rose-300` | `#F9B4C2` |
| `rose-400` | `#F58AA0` |
| `rose-500` | `#EC6584` |
| `rose-600` | `#C94766` |
| `rose-900` | `#451523` |

**Champagne** (added; the one warm accent). Used **only** for the moment of a confirmed conversion or
confirmed payment (the Seal, a celebration sheen). It makes a win feel earned without adding a sixth
alert color.

| Token | Hex |
|---|---|
| `champagne-300` | `#F1E6D3` |
| `champagne-400` | `#E9DAC1` |
| `champagne-500` | `#D4BE98` |

**Text and lines** (cool off-white and cool gray, never pure white):

| Token | Value | Use |
|---|---|---|
| `text-1` | `#EEF1F8` | primary text, numerals |
| `text-2` | `#B7BFD3` | secondary text |
| `text-3` | `#98A2BC` | tertiary text, captions (still passes AA everywhere) |
| `text-4` | `#66718F` | **disabled and decorative only**, never information |
| `line-1` | `rgba(255,255,255,.07)` | hairline between regions |
| `line-2` | `rgba(255,255,255,.12)` | glass border, dividers |
| `line-control` | `rgba(255,255,255,.38)` | borders of inputs and controls (3:1 non-text contrast) |

### 2.3 Verified contrast

Computed (WCAG relative luminance) against the surfaces they sit on. "Worst case" is glass level 2
over the brightest point of the aurora. **The build must re-run this check** (section C7, the `tokens`
package contrast test); these figures document the intent.

| Foreground | On page | On glass 1 | On glass 2 | Worst case | Requirement |
|---|---|---|---|---|---|
| `text-1` | 17.6 | 16.2 | 15.3 | 11.8 | AAA |
| `text-2` | 10.8 | 10.0 | 9.4 | 7.2 | AAA |
| `text-3` | 7.8 | 7.2 | 6.8 | 5.2 | AA |
| `aether-400` text | 8.0 | 7.4 | 7.0 | 5.4 | AA |
| `ice-400` text | 10.8 | 10.0 | 9.4 | 7.3 | AAA |
| `emerald-400` text | 12.0 | 11.1 | 10.5 | 8.1 | AAA |
| `amber-400` text | 11.6 | 10.7 | 10.1 | 7.8 | AAA |
| `rose-400` text | 8.5 | 7.9 | 7.5 | 5.7 | AA |
| `text-4` (disabled only) | 3.7 | 3.4 | 3.2 | 2.5 | exempt (disabled) |

| Component pairing | Ratio |
|---|---|
| Primary button: `#04121A` on `ice-500` | 9.4 |
| Agent button: `#FFFFFF` on `aether-600` | 5.2 (note: `aether-500` gives only 3.8 and must not carry white text) |
| Success button: `#031A12` on `emerald-500` | 8.5 |
| Danger button: `#1E0710` on `rose-500` | 6.2 |
| Focus ring `ice-300` on page | 13.3 |
| Control border `line-control` on an input well | 3.5 (non-text minimum is 3) |

### 2.4 Semantic meaning: the five intents

The most important color rule of the product. Each color answers one question.

| Intent | Color | Question it answers | Examples |
|---|---|---|---|
| **Agent** | Aether (violet) | "What is the intelligence doing or suggesting?" | Pulse, agent-authored messages, agent narration, "learned" notes, agent-suggested actions |
| **Action / Data** | Ice (cyan) | "What can I do, and where am I?" | primary buttons, links, focus, selection, neutral data series, your own actions |
| **Success** | Emerald | "What worked?" | reply received, appointment set, payment confirmed, converted, healthy connection |
| **Attention** | Amber | "What needs a human?" | approval waiting, handoff, unverified payment claim, warnings, limits nearing |
| **Danger** | Rose | "What is wrong or irreversible?" | errors, failed sends, destructive actions, connection down, asked to stop |

Rules:

1. **Meaning is exclusive.** Violet is never a button color, cyan is never "the AI", amber is never
   decoration. A designer choosing a color asks the question first.
2. **Never color alone.** Every colored state also has an icon and a text label (section 27).
3. **Neutral first.** At rest, most of the interface is neutral glass and off-white text. Color appears
   where meaning appears. A screen where everything is colored has no hierarchy.
4. **Champagne is reserved** for confirmed payment and confirmed conversion, maximum one champagne
   element visible per screen.
5. **Chart series order**: ice, aether, emerald, amber, then desaturated variants. Positive is not
   automatically emerald: a metric where "less is better" (for example time to reply) uses the
   direction supplied by the API (`good | bad | neutral`), not a sign.

### 2.5 Semantic tokens

```css
:root {
  /* surfaces are defined in section 7 */
  --bg:            #070914;
  --text-1:        #EEF1F8;
  --text-2:        #B7BFD3;
  --text-3:        #98A2BC;
  --text-disabled: #66718F;

  --line-1:        rgb(255 255 255 / .07);
  --line-2:        rgb(255 255 255 / .12);
  --line-control:  rgb(255 255 255 / .38);

  /* intents: solid, text, and channel triplets for alpha use */
  --agent:         #8470F2;   --agent-text:   #A996FF;  --agent-fill: #6C57DB;
  --action:        #5CC3E6;   --action-text:  #6FCBEB;  --action-hi:  #9EDDF3;
  --success:       #34C98E;   --success-text: #5FE0A8;
  --attention:     #E8A63A;   --attention-text:#F2BE5C;
  --danger:        #EC6584;   --danger-text:  #F58AA0;
  --seal:          #E9DAC1;

  --tint-agent:     132 112 242;
  --tint-action:     92 195 230;
  --tint-success:    52 201 142;
  --tint-attention: 232 166  58;
  --tint-danger:    236 101 132;
  --tint-seal:      233 218 193;
  --tint:           var(--tint-action);   /* the current context tint; components override it */

  --focus-ring: #9EDDF3;
}
```

A light theme ("Daylight") is **not part of v1**. The semantic layer is the reason it stays possible:
only the semantic values above change. Decision recorded in C8.

---

## 3. Gradient system

Gradients are analogous (neighbouring hues), low in chroma, and mostly transparent. They add depth and
direction, never a rainbow.

### 3.1 The set

| Name | Definition | Use |
|---|---|---|
| **Aurora** | three radial washes over `abyss-0`: violet `rgb(var(--tint-agent) / .16)` at 12% 0%, ice `rgb(var(--tint-action) / .09)` at 88% 4%, deep indigo `rgb(58 47 133 / .30)` at 50% 110% | the page background (section 9) |
| **Aether line** | `linear-gradient(135deg, var(--aether-400), var(--ice-400))` | the Pulse ring, the thin accent of agent elements, the Agent button border. **Only as a 1 to 2px line or a small area**, never as a large fill |
| **Ice fill** | `linear-gradient(180deg, var(--ice-400), var(--ice-500))` | the primary button |
| **Sheen** | `linear-gradient(135deg, rgb(255 255 255 / .22), rgb(255 255 255 / .04) 40%, rgb(255 255 255 / .10))` | glass borders (the "light catching an edge" effect, section 7.3) |
| **Top light** | `linear-gradient(180deg, rgb(255 255 255 / .06), rgb(255 255 255 / 0) 60%)` | inside glass surfaces, simulating a light source above |
| **Halo** | `radial-gradient(60% 60% at 50% 0%, rgb(var(--tint) / .16), transparent 70%)` | top edge glow of highlighted glass |
| **Data fade** | `linear-gradient(180deg, rgb(var(--tint) / .28), rgb(var(--tint) / 0))` | area under a sparkline |
| **Ribbon** | ice, then aether, then emerald across the funnel, each at 0.85 alpha with `mix-blend-mode: screen` overlaps | the Funnel Ribbon (14.4) |
| **Seal** | `conic-gradient(from 210deg, var(--champagne-300), var(--champagne-500), var(--emerald-400), var(--champagne-300))` | the Payment Seal ring only |

### 3.2 Contextual gradients ("mood")

The shell sets `data-mood` on the root from the agent's state. Two tokens, `--mood-a` and `--mood-b`,
tint the corner washes of the Aurora and the Halo of the hero element. The change is a 1.2 s
cross-fade and stays under 20% of the aurora's opacity, so it is felt rather than seen.

| Mood | When | `--mood-a` / `--mood-b` |
|---|---|---|
| `calm` (default) | idle, healthy | violet 16% / ice 9% |
| `working` | agent processing or writing | violet 22% / ice 12% |
| `attention` | at least one item needs a human | violet 12% / **amber 14%** (bottom-right wash) |
| `win` | a payment was confirmed or a conversion was recorded, for 6 s | violet 14% / **emerald 16%**, with one Seal sheen |
| `trouble` | a connection is failing | violet 10% / **rose 12%** |
| `paused` | agent paused or not enabled | desaturated (chroma x 0.4) |

### 3.3 Rules

1. No gradient text, except the single hero numeral of a screen, and only `text-1` to `ice-200`, subtle.
2. No gradient behind body text. Text sits on glass or the base, always at verified contrast.
3. Two gradient hues at most in any one component.
4. Gradient banding is fought with the grain layer (section 9.1).
5. Animated gradients use `@property` registered custom properties, never re-painting large areas per frame.

---

## 4. Typography

### 4.1 Families

| Role | Family | Fallback | Why |
|---|---|---|---|
| **Interface** | **Geist** (variable, 300 to 700) | `system-ui, sans-serif` | Precise, modern, excellent numerals, open licence |
| **Agent Voice** | **Instrument Serif** (regular and italic) | `Georgia, serif` | A refined display serif. It makes the agent audibly different from the interface |
| **Exact values, ids, code** | **Geist Mono** | `ui-monospace, monospace` | Verbatim values, references |
| **Arabic UI** | **IBM Plex Sans Arabic** | `Geist`, system Arabic | Matches Geist's proportions |
| **Arabic Agent Voice** | **Noto Naskh Arabic** | serif Arabic | Instrument Serif has no Arabic; Naskh is the closest register |

Fonts are subset by script and loaded with `font-display: swap` (metric-adjusted fallbacks to avoid
layout shift). Only Arabic-script users download the Arabic files.

### 4.2 Scale

Minimum size for any information is **14 px** (13 px only for `label` in dense, non-essential
captions). Body is 15 px to stay comfortable without looking cramped. Sizes are fluid where marked.

| Token | Size / line | Weight | Tracking | Use |
|---|---|---|---|---|
| `numeral-xl` | clamp(44, 5vw, 64) / 1.0 | 300 | -0.025em | the one hero figure of a screen |
| `numeral-l` | 40 / 44 | 300 | -0.02em | KPI values |
| `numeral-m` | 28 / 32 | 400 | -0.015em | secondary figures |
| `h1` | clamp(26, 3vw, 32) / 1.25 | 600 | -0.02em | screen title |
| `h2` | 22 / 30 | 600 | -0.015em | section title |
| `h3` | 18 / 26 | 600 | -0.01em | card title |
| `body-l` | 17 / 28 | 400 | 0 | introductions, empty states |
| `body` | 15 / 24 | 400 | 0 | default |
| `body-sm` | 14 / 20 | 400 | 0 | dense secondary text |
| `label` | 13 / 16 | 500 | +0.01em | field labels, chips, table headers |
| `overline` | 13 / 16 | 600 | +0.08em, uppercase | group headings (used sparingly, never for information) |
| `mono` | 14 / 22 | 400 | 0 | exact values |
| `voice-l` | 28 / 34 | 400 italic serif | -0.01em | the agent's headline sentence |
| `voice-m` | 20 / 30 | 400 italic serif | 0 | narration in the Thread and Command Center |
| `voice-s` | 17 / 26 | 400 italic serif | 0 | narration inside cards (never below 17, serif needs size) |

Numerals always use `font-variant-numeric: tabular-nums lining-nums` so columns and tickers do not
jitter. Large numerals at weight 300 are a deliberate contrast to the 600 titles: light, expensive, calm.

### 4.3 The Agent Voice rule

> **Revision after visual review (prototype):** the serif italic proved too condensed to read. The Agent Voice is now
> **Plus Jakarta Sans**, upright, 18 to 20 px with a 1.6 line height (32 px, 1.4 for the hero sentence), open word
> spacing, a soft violet rule at the leading edge, and no quotation marks. The rule below is unchanged: only what the
> agent authored or narrates uses this style. Wherever this document says "serif italic", read "Agent Voice".

> **Serif italic is used only for words the agent authored or narrates.** Nothing else, ever.

This is the single most recognisable typographic device of the product. It tells the reader, at a
glance and without a label, "this sentence comes from the agent", the same way the violet Pulse tells
"this is the agent". Examples: the headline of the Command Center ("Good morning. I'm working through
14 prospects today; 2 need you."), the narration of Thread events, the "Why?" explanation on a
decision, the reason line on a Needs You card. Human-typed text and system text are always Geist.
Agent-authored message bubbles in a conversation keep Geist (they are the actual WhatsApp text,
verbatim); only the agent's *commentary about* the conversation is serif.

### 4.4 Rules

1. One serif sentence per card at most; it is a voice, not a texture.
2. Line length for reading text: 60 to 72 characters (`max-width: 68ch`).
3. Sentence case everywhere. No ALL CAPS except `overline`.
4. Text is never smaller than its token; user zoom to 200% must not break layouts (section 27).
5. Numbers are formatted with `Intl.NumberFormat` in the *interface* locale; currencies use the
   currency code and locale rules of the value, never a hand-built string.
6. Truncation uses ellipsis with the full text in a tooltip and accessible name; never truncate an amount.

---

## 5. Spacing

### 5.1 Scale

Base unit **4 px**. Tokens are named by multiples.

| Token | px | Typical use |
|---|---|---|
| `space-1` | 4 | icon to text gap, tight chip padding |
| `space-2` | 8 | related controls |
| `space-3` | 12 | input padding, list item gap |
| `space-4` | 16 | card padding (compact), mobile gutter |
| `space-5` | 20 | default card padding |
| `space-6` | 24 | card padding (comfortable), gap between cards |
| `space-8` | 32 | section inner spacing |
| `space-10` | 40 | between page sections |
| `space-14` | 56 | hero breathing |
| `space-18` | 72 | major screen separation |
| `space-24` | 96 | first-run and empty-state vertical rhythm |

### 5.2 Layout

| Rule | Value |
|---|---|
| Page gutter | 16 (mobile), 24 (tablet), 32 (laptop), 48 (wide) |
| Content max width | 1240 px centered; the Command Center and River use the full width up to 1680 |
| Grid | 12 columns, 24 px gutters (16 on tablet, single column on mobile) |
| Gap between glass surfaces of the same level | **at least 24 px** (the "breathing rule"; glass needs room around it to read as glass) |
| Card padding | 20 (dense), 24 (default), 32 (hero) |
| Vertical rhythm | sections 40 px apart, a section title 16 px above its content |
| Touch target | 44 x 44 px minimum on touch devices, 32 px minimum on pointer devices |

### 5.3 Density

Two densities, chosen per surface, never mixed inside one card:

- **Comfortable** (default): the client console.
- **Compact**: lists with many rows (Ledger view, audit), row 44 px instead of 56 px. Compact never
  goes below the minimum text sizes.

---

## 6. Border radius

| Token | px | Use |
|---|---|---|
| `radius-xs` | 6 | tags, chips, small badges, focus outlines on tiny elements |
| `radius-sm` | 10 | small controls, table cells with fill, code blocks |
| `radius-md` | 14 | buttons, inputs, selects, list items |
| `radius-lg` | 20 | cards, popovers, menus |
| `radius-xl` | 28 | large panels, drawers (leading corners) |
| `radius-2xl` | 36 | modals, the hero panel of a screen |
| `radius-full` | 9999 | pills, avatars, beads, the Pulse |

**Concentric rule**: an inner element's radius is the outer radius minus the padding between them
(minimum 6). A `radius-lg` card with 8 px inner padding holds a `radius-sm`-ish child (12 to 10). This is
what makes nested glass look machined rather than assembled.

Radii are large by design (the brief asks for softness and elegance); no square corners anywhere except
table hairlines.

---

## 7. Glass layers

### 7.1 The stack

Six levels. Each is a token set (`--glass-N-*`) applied through one class or component, never re-typed.
Glass is **relative to what is behind it**: the more a surface matters, the lighter, blurrier and higher
it sits.

| Level | Name | Role | Fill (top to bottom) | Blur | Border | Shadow |
|---|---|---|---|---|---|---|
| **L-1** | **Well** | inputs, inset areas, code, the tray under items (sits *into* the surface) | `rgb(0 0 0 / .30)` | 0 | `line-control` (inputs) or `line-1` | inner: `inset 0 1px 2px rgb(2 3 10 / .5)` |
| **L0** | **Background** | the page | `abyss-0` + Aurora + grain + vignette | n/a | n/a | n/a |
| **L1** | **Glass Surface** | standard cards, panels, the nav rail | `white .055` to `white .02` | 20 px, saturate 140% | 1 px Sheen at `.07`-ish | `--shadow-1` |
| **L2** | **Elevated Glass** | popovers, menus, sticky bars, the KPI hero, hovered cards | `white .085` to `white .03` | 28 px, saturate 160% | 1 px Sheen at `.12` | `--shadow-2` |
| **L3** | **Highlighted Glass** | the one thing that matters now: an item needing you, the agent's hero, a selected card | L2 fill + tint halo (`--tint` .14 at the top edge) | 28 px | Sheen tinted with `--tint` at `.35` | `--shadow-3` (adds a tinted halo) |
| **L4** | **Modal Glass** | modals, drawers, command palette | `rgb(14 18 36 / .72)` (more opaque on purpose) + Top light | 40 px, saturate 150% | 1 px Sheen at `.16` | `--shadow-4` + scrim |

Modal glass is the *most opaque* glass, not the least: the things you must read carefully (a payment
confirmation, a form) get the most reliable contrast. Depth is communicated by blur and shadow, not by
transparency.

### 7.2 Anatomy of a glass surface

Every glass surface is the same five ingredients, in order. If one is missing, it reads as a cheap
translucent rectangle.

1. **Fill**: a two-stop vertical gradient of white at low alpha (the Top light is baked in).
2. **Backdrop**: `backdrop-filter: blur(N) saturate(S)`. Saturation is raised so the blurred aurora
   behind stays colorful instead of going gray.
3. **Sheen border**: a 1 px gradient border, brighter on the top-left edge, fading to nearly nothing on
   the bottom-right, like light catching the edge of a glass pane.
4. **Inner highlight**: `inset 0 1px 0 rgb(255 255 255 / .07)`, a hairline on the top edge.
5. **Shadow**: diffuse, dark, low-alpha, larger for higher levels. Never a colored drop shadow except
   L3's tinted halo.

### 7.3 Reference CSS

```css
:root {
  --glass-1-fill: linear-gradient(180deg, rgb(255 255 255 / .055), rgb(255 255 255 / .02));
  --glass-2-fill: linear-gradient(180deg, rgb(255 255 255 / .085), rgb(255 255 255 / .03));
  --glass-4-fill: linear-gradient(180deg, rgb(255 255 255 / .05), rgb(255 255 255 / 0) 40%),
                  rgb(14 18 36 / .72);
  /* solid fallbacks: reduced transparency, no backdrop-filter support, forced colors */
  --glass-1-solid: #0F1324;
  --glass-2-solid: #141A30;
  --glass-4-solid: #0E1224;
}

.glass {                       /* base for L1, L2, L3, L4; the level sets the variables */
  position: relative;
  background: var(--glass-fill);
  -webkit-backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-sat));
          backdrop-filter: blur(var(--glass-blur)) saturate(var(--glass-sat));
  border-radius: var(--glass-radius);
  box-shadow: inset 0 1px 0 rgb(255 255 255 / .07), var(--glass-shadow);
  isolation: isolate;
}
/* Sheen border: a gradient painted in the border box only, using a mask */
.glass::before {
  content: ""; position: absolute; inset: 0; border-radius: inherit; padding: 1px;
  background: linear-gradient(135deg,
      rgb(var(--glass-edge, 255 255 255) / var(--glass-edge-a, .22)),
      rgb(255 255 255 / .04) 40%,
      rgb(255 255 255 / .10));
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor; mask-composite: exclude;
  pointer-events: none;
}
/* Level 3 adds the tinted halo at the top edge */
.glass[data-level="3"]::after {
  content: ""; position: absolute; inset: 0; border-radius: inherit; pointer-events: none;
  background: radial-gradient(60% 60% at 50% 0%, rgb(var(--tint) / .16), transparent 70%);
}
@supports not (backdrop-filter: blur(1px)) { .glass { background: var(--glass-solid); } }
@media (prefers-reduced-transparency: reduce) { .glass { backdrop-filter: none; background: var(--glass-solid); } }
@media (forced-colors: active) { .glass { border: 1px solid CanvasText; background: Canvas; } }
```

### 7.4 Glassmorphism rules

1. **Glass needs something behind it.** A glass card on a flat color is just a gray card. The Aurora is
   what the glass refracts; never remove it "for performance" without also switching the glass to solid.
2. **Never glass on glass on glass.** Maximum two overlapping blurred layers in any viewport region
   (for example: page, L1 panel, L2 popover). A modal over a panel over a card is solved by the scrim
   plus the L4 opacity, and the layers underneath lose their `backdrop-filter` while the modal is open.
3. **Text never sits on raw blur.** Any text has a fill of at least L1 behind it, and contrast is
   checked against the *worst case* row of the table in section 2.3.
4. **Blur budget.** At most **six** `backdrop-filter` elements visible at once; list rows and table rows
   never use backdrop-filter (they use a flat fill of the L1 tone). Long lists are one glass panel
   containing flat rows, not N glass cards.
5. **Reduce on small screens.** Blur radius is halved below 600 px, and mobile bottom bars stay L2.
6. **Borders stay hairline.** 1 px, never 2, except focus rings and the Pulse ring.
7. **Tint only on L3.** Tinted glass (violet, amber, emerald) is reserved for surfaces that carry that
   meaning *right now*. A screen has one or two L3 surfaces, not ten.
8. **No opaque white, no pure black.** Highlights are white at low alpha; shadows are navy-black.
9. **Provide the fallbacks** (`@supports`, `prefers-reduced-transparency`, `forced-colors`), tested.

---

## 8. Shadows

Shadows are dark, cool and diffuse. Light in this interface comes from above; the top inner highlight
plus a soft lower shadow is what lifts a surface. Colored shadow exists only as the L3 halo.

```css
:root {
  --shadow-1: 0 1px 2px rgb(2 3 10 / .40), 0 6px 20px -10px rgb(2 3 10 / .55);
  --shadow-2: 0 2px 4px rgb(2 3 10 / .35), 0 18px 40px -16px rgb(2 3 10 / .70);
  --shadow-3: 0 2px 4px rgb(2 3 10 / .35), 0 22px 48px -18px rgb(2 3 10 / .70),
              0 0 60px -14px rgb(var(--tint) / .35);          /* the tinted halo */
  --shadow-4: 0 40px 120px -24px rgb(2 3 10 / .85), 0 0 0 1px rgb(255 255 255 / .06);
  --shadow-inset: inset 0 1px 2px rgb(2 3 10 / .50);           /* wells */
  --shadow-focus: 0 0 0 2px var(--bg), 0 0 0 4px var(--focus-ring);   /* double ring */
}
```

| Level | Token | Use |
|---|---|---|
| Resting | `shadow-1` | L1 cards, rail |
| Raised | `shadow-2` | L2, hover of interactive cards, sticky bars |
| Highlighted | `shadow-3` | L3 (tint set per component) |
| Overlay | `shadow-4` | modals, drawers, palette |
| Sunken | `shadow-inset` | inputs, wells |

Hover on an interactive card moves it from `shadow-1` to `shadow-2` and lifts it 2 px over 200 ms; it
does not change its color. Halos never exceed alpha .35 and never animate faster than 1.2 s.

---

## 9. Background effects

The background is the material the glass refracts, so it is a designed layer, not a wallpaper.

### 9.1 Stack (bottom to top, all `position: fixed`, behind the app)

1. **Base**: `abyss-0`.
2. **Aurora**: three large, heavily blurred blobs (see 3.1), each a `radial-gradient`; slow drift by
   `transform: translate3d` over **90 to 140 s** with alternating direction, amplitude under 6% of the
   viewport. Not a blur filter on a moving element (expensive); the gradients are already soft.
3. **Horizon**: one faint line of light near the top of the shell,
   `linear-gradient(90deg, transparent, rgb(var(--tint-agent) / .35), rgb(var(--tint-action) / .25), transparent)`,
   1 px high, 64 px below the top, blurred 6 px. A quiet dawn line that anchors the composition and hints
   at "Now".
4. **Field** (Command Center only): a dot grid, `radial-gradient(rgb(255 255 255 / .05) 1px, transparent 1.2px)`
   at 28 px pitch, masked to fade at the edges. It gives the Pulse a "space" to live in.
5. **Grain**: an SVG `feTurbulence` noise tile as a data URI, ~3% alpha, `mix-blend-mode: overlay`.
   Removes gradient banding and adds tactile richness. Static (never animated).
6. **Vignette**: `radial-gradient(120% 90% at 50% 40%, transparent 55%, rgb(2 3 10 / .55))`.

### 9.2 Mood Aurora

`data-mood` (section 3.2) re-tints the washes. Implementation is two registered custom properties
(`@property --mood-a`, `--mood-b`) transitioning over 1200 ms, so the background is one paint, not two
cross-faded layers. The mood is set from the agent's aggregate state by the shell. The **only** mood
that may be perceived as a notification is `attention` (a faint amber warmth in the lower right), and
it is always accompanied by an explicit count and label elsewhere.

### 9.3 Rules

1. The background is fixed and never scrolls with content.
2. Total ambient movement is one drift animation; it pauses when the tab is hidden and under
   `prefers-reduced-motion`.
3. The background never carries meaning by itself (no information only visible in the aurora).
4. Background layers are one compositing layer set (`will-change: transform` on the aurora only,
   removed when reduced motion is on).
5. In the operator app (C6) the aurora is re-tinted cool steel so a person managing many clients can
   tell which console they are in without reading.
6. Print and forced-colors: the whole stack is removed; the page is `Canvas`.

---

## 10. Icons

| Property | Value |
|---|---|
| Base set | **Lucide**, tree-shaken |
| Stroke | 1.5 px at 20 px size, round caps and joins; scaled proportionally at 16 and 24 |
| Sizes | 16 (inline text), 20 (default, buttons, nav), 24 (nav rail, empty states), 32 (glyph tiles), 48 (feature) |
| Color | inherits `currentColor`; intent colors only where the meaning applies (section 2.4) |
| Alignment | optically centered on the text x-height with `1em` boxes; never scaled by transform |
| Accessibility | decorative icons `aria-hidden`; an icon that carries meaning has an accessible name or is paired with visible text |
| RTL | directional icons (arrows, send, chevrons) mirror via a `data-mirror` class; symbols (check, plus) do not |

**Glyph tile**: an icon inside a 32 or 40 px `radius-sm` square with an `rgb(var(--tint) / .12)` fill and
a `rgb(var(--tint) / .25)` hairline. Used for Thread events, settings sections and empty states. It is the
standard way to give an icon presence without decoration.

**Custom set** (drawn in the Lucide grid and style; do not substitute generic icons for these):

| Icon | Meaning |
|---|---|
| `agent-spark` | the agent (a four-point spark inside a ring); used where the Pulse is too large |
| `handoff` | a conversation passing to a person |
| `verbatim-lock` | exact value kept character for character (a small lock inside quotation marks) |
| `seal` | payment confirmed by a person |
| `certainty-confirmed`, `certainty-learned`, `certainty-guess` | the three Certainty Rings as icons (solid, partial, dotted) |
| `guardrail` | a limit set by the platform |
| `rehearsal` | (future) simulated conversation |
| `river` | the stage view of prospects |
| `runway` | launch readiness |

No emoji in the interface, ever, including in the agent's narration (a message the agent sends to a
prospect follows the client's own communication settings, which are the client's business).

---

## 11. Buttons

### 11.1 Variants

One **primary** action per view. Everything else is quieter.

| Variant | Look | Use |
|---|---|---|
| **Primary** | Ice fill (`ice-400` to `ice-500`), text `#04121A` (9.4:1), Top light sheen, `shadow-1`, hover brightens to `ice-400` flat and lifts 1 px | the one main action: Publish, Approve, Save offer |
| **Agent** | L1 glass, 1 px **Aether line** border, text `aether-200`, a small `agent-spark` icon | actions that ask the agent to *do or suggest* something: "Ask the agent to draft", "Rehearse" |
| **Secondary** | L1 glass, `line-2` border, `text-1` | supporting actions |
| **Ghost** | no fill, `text-2`, fill `rgb(255 255 255 / .06)` on hover | tertiary, toolbars, "Cancel" |
| **Success** | Emerald fill, text `#031A12` (8.5:1) | **Confirm payment** only (with the Seal), and equally positive irreversible confirmations |
| **Danger** | transparent with `rose-400` text and `rose-500 / .5` border; fills `rose-500` with text `#1E0710` (6.2:1) on hover/pressed | destructive: Deactivate, Discard, Pause agent |
| **Link** | `ice-400` text, underline on hover and focus | inline navigation |
| **Icon** | 40 px square ghost, `aria-label` mandatory | toolbars |

### 11.2 Sizes

| Size | Height | Padding x | Text | Radius | Note |
|---|---|---|---|---|---|
| `sm` | 32 | 12 | `label` 14 | `radius-md` | pointer devices only; becomes 44 on touch |
| `md` (default) | 40 | 16 | `body` 15 / 500 | `radius-md` | becomes 44 on touch |
| `lg` | 48 | 20 | `body-l` 17 / 500 | `radius-md` | hero and first-run actions |

### 11.3 States

| State | Treatment |
|---|---|
| Rest | as above |
| Hover | brighten one step, lift 1 px, `shadow-2` (pointer devices only, never the only feedback) |
| Focus-visible | **double ring** `--shadow-focus` (2 px page-color gap, 2 px `ice-300` ring), always visible on every surface |
| Active | translate 0, scale .985, 90 ms |
| Loading | label stays, replaced by a 16 px spinner at the leading edge; width does not change; `aria-busy`; the button stays disabled to repeated clicks |
| Disabled | `text-disabled`, no fill change, no shadow; **a disabled action always exposes a reason** (tooltip and `aria-describedby`), for example "Only the owner can confirm payments" |
| Success flash | on completion the label morphs to a check for 1.2 s (emotional closure for Publish) |

### 11.4 Rules

1. Sentence case, verb first: "Publish changes", not "Submit".
2. Never two primary buttons in one view region. A destructive action is never primary-styled.
3. A button that triggers an irreversible or externally visible action (Publish, Approve a message,
   Confirm payment) states its consequence in the label or the line under it: "Approve and send".
4. A dialog's primary action is at the trailing edge, the safe action at the leading edge; RTL mirrors.
5. Icon-only buttons need `aria-label` and a tooltip on hover and focus.

---

## 12. Inputs

### 12.1 Shared anatomy

Label above (never placeholder-as-label), optional helper below, error replaces helper. Field height 44 (40
compact). The control is an **L-1 Well**: dark inset fill, `line-control` border (3.5:1), `radius-md`.

| State | Treatment |
|---|---|
| Rest | Well fill, `line-control` border |
| Hover | border to `rgb(255 255 255 / .5)` |
| Focus | border `ice-300`, outer `--shadow-focus`, a 150 ms light "fill-up" on the border (a bright arc from the start edge) |
| Filled | unchanged; value in `text-1` |
| Read-only | no border, `text-2`, a lock icon if it is platform-owned ("set by your platform administrator") |
| Disabled | `text-disabled`, fill at 60% |
| Invalid | `rose-400` border and icon, message in `rose-400` text, `aria-invalid` and `aria-describedby` linking the message |
| Valid (after test) | a small emerald check, only after an explicit verification such as a connection test |

### 12.2 Inventory

| Control | Notes |
|---|---|
| **Text, textarea** | autosize textarea; character counter appears at 80% of a limit and turns amber at 95%; the limit comes from the schema |
| **Select / combobox** | Radix Select or Combobox; option rows 44 px; searchable when more than 8 options |
| **Multi-select chips** | for languages, markets, countries; chips are removable buttons; "add" opens a combobox |
| **Segmented control** | 2 to 4 mutually exclusive options; sliding glass thumb (200 ms); used for View toggles and the Autonomy Dial on mobile |
| **Switch** | 44 x 26; on = `ice-500`; label to the leading side; a switch applies immediately *only* in views that are not part of a Draft (settings drafts wait for Publish) |
| **Checkbox, radio** | 22 px, `radius-xs`, Ice check; group in a `fieldset` with `legend` |
| **Slider** | ends labelled with numbers; a **guardrail zone** may be drawn on the track (19.3) |
| **Number, money** | money = amount plus a currency select (ISO 4217, from the schema's list); tabular numerals; no currency conversion anywhere |
| **Phone** | E.164 validated and normalised, country selector, shown formatted; the CEO report number must differ from the agent number |
| **URL** | HTTPS enforced by the schema; a "Test link" affordance where relevant |
| **Time, weekday picker** | time in 24 h or the locale's format, always displayed with the client timezone label; weekdays as seven toggle chips |
| **Tag / phrase list** | for opt-out keywords, trigger phrases, discovery questions; Enter adds, Backspace removes, drag or arrow buttons to reorder |
| **Rich guidance field** | plain text with a visible "This is what the agent will follow" caption; no markdown editor |
| **File** | drop zone (only where a feature needs it); type and size shown before upload |

### 12.3 Validation

1. **On blur, not on keystroke**, except counters. Never show an error on an untouched field.
2. **Server is the authority.** The schema-derived client checks make errors instant, but the API's
   validation (two passes: structure, then references) is displayed exactly, each problem at its field
   via its path (`brain.settings.catalog.products[x].pricing`). Errors that cannot be placed at a field are
   listed in the publish review (pattern C3.4) with a link that scrolls to the section.
3. Error copy says what to do: "Add a price, or choose 'On request'", not "Invalid value".
4. Never clear what the user typed on error.

### 12.4 Connection test pattern

For the Google Sheet (and any external connection the client owns): the field, a **Test connection**
button (Agent variant is *not* used; it is a normal secondary), then a three-step result that reveals
progressively: "Reachable", "Tabs found", "Columns match", each turning emerald with a check, or rose
with the exact reason and how to fix it. The platform service account's e-mail is shown with a copy
button, since the client must share the Sheet with it.

### 12.5 Verbatim input

For **exact values**: prices, payment instructions (bank details, wallet numbers), payment and booking
links, exact template text.

- Font: Geist Mono, `mono` token, so ambiguous characters are distinguishable.
- A `verbatim-lock` badge in the label row and this caption, always visible: **"Shown to prospects
  exactly as written, never rewritten by AI."**
- No auto-trim on paste display; leading and trailing whitespace is made visible with a subtle dot.
- On save, a **preview** shows the value as a prospect will receive it (in a bubble), so the client
  sees the exact final text.
- The same badge appears on values inside conversation bubbles that were inserted verbatim (18.2).

This turns a backend security property ("the model never sees or rewrites exact values") into a visible
promise, which is exactly the kind of trust a business handing over its payment details needs.

---

## 13. Cards

### 13.1 Base cards

| Card | Glass level | Purpose | Notes |
|---|---|---|---|
| **Panel** | L1 | groups related content; never clickable itself | title `h3`, optional subtitle `text-2`, padding 24 |
| **Interactive card** | L1, L2 on hover | a whole-card link or button (an offer, a prospect) | one focusable element for the card; hover lifts 2 px and moves to `shadow-2`; the click target is the entire card, secondary actions are separate buttons above it in z-order |
| **Hero card** | L2 (L3 when it carries the state) | the single dominant element of a screen: the Pulse, the day's narrative, the current decision | padding 32, `radius-2xl`; one per screen |
| **Decision card** | L3 tinted amber | something needs a person (Needs You) | see 19.5 |
| **Insight card** | L1 with an Aether line on its leading edge | an observation authored by the agent | one serif `voice-s` sentence, an optional data link, a "Why?" affordance |
| **Setup gate card** | L1, tint by gate state | one step of the Launch Runway | see 19.7 |

Anatomy rules: title first, meaning in the first 8 words; **at most one primary action** per card; metadata
in `text-3`; no more than three levels of information (title, value, caption).

### 13.2 Insight card

```
┌──────────────────────────────────────────────┐
▎  “Three prospects asked about installments    │   voice-s, Aether line at the leading edge
▎   this week. I have no answer to give them.”  │
▎                                                │
▎  Add a payment option  ›            Why?      │   ghost link, "Why?" opens the reasoning popover
└──────────────────────────────────────────────┘
```

Insights are **suggestions to improve the configuration**, generated by the agent side and delivered by
the API. They never appear as notifications, only on Command Center and in the relevant editor.

### 13.3 Prospect card and dossier

A prospect is never presented as a table row first. The **Prospect card** is compact and human:

```
┌────────────────────────────────────────┐
│  (AB)  Amira B.          ◔ Learned ×3  │   avatar bead with state ring, name, certainty summary
│        Tunis · Offer: Foundation       │   only facts that are settled, from the API
│  “Asked how the payment works.”        │   voice-s: the agent's one-line read of the last exchange
│  ● Waiting for you        2 h ago      │   status + last interaction
│  Next: I'll follow up tomorrow 10:00   │   next action, in words
└────────────────────────────────────────┘
```

The **Dossier** (drawer, 21.2) opens from any card and is the one place to see everything about a prospect:

1. Header: name, phone (formatted), status, stage, the Payment Seal if any, and actions
   (Open conversation, Take over, Pause, Close).
2. **Certainty panel**: what the agent knows, as fact chips with rings and provenance (15.5), grouped
   as *Confirmed*, *Learned*, *Guesses*. A guess is visibly a guess.
3. **Path**: the prospect's position in the sales path (workflow steps) as a horizontal step track;
   current step highlighted, done steps solid, upcoming ghosted.
4. **Thread**: the prospect's own Day Thread (17), history and next actions.
5. **Interest**: products the prospect is interested in, with level (confirmed, probable, possible).

The avatar bead is initials in a `radius-full` glass disc; its **ring** shows attention state
(none / violet working / amber waiting / emerald converted / muted rose asked-to-stop). Photos are never
fetched from third parties.

### 13.4 Opportunity River (prospects view)

The alternative to a Kanban and to a table. Prospects are **beads** that sit in the **banks** of a river
whose stages run left to right.

```
 Unsorted        Discovery         Recommended        Path: payment       Won
    │  ○ ○           │  ○ ○ ○           │  ○ ○              │  ○                │  ○ ○
    │  ○             │  ○ ○             │  ○                │                   │
 ╌╌╌╌╌╌╌╌╌╌╌╌╌╌ soft luminous current line runs through, slightly brighter toward Won ╌╌╌╌╌╌
    5                  9                  4                  2                   3
```

- The **banks are the client's stages**, in the order of `stages` from its settings; nothing is
  hard-coded. A bank's tint comes from the stage's milestone if it has one, else from an ordered
  neutral-to-ice scale. A leading "Unsorted" bank holds prospects with no stage.
- A **bead** is 40 px, an initials disc with the state ring (13.3). **All beads are the same size**:
  size never encodes an invented score. Position inside a bank is by recency (API order), not by
  a computed heat.
- A bead needing a person shows a small amber count-less dot and sits at the bank's top.
- **Hover or focus** shows a Prospect card preview (L2 popover, 200 ms delay). **Click** opens the Dossier.
- **Moving between banks** is a `layout` animation (the bead glides along the current, 450 ms spring).
  Dragging a bead to change stage is **not** offered in v1 (the agent and staff set stages; a manual
  override is a labelled action in the Dossier). This keeps the audit trail honest.
- More than 12 beads in a bank: the rest collapse to a "+N" bead that opens that bank's list.
- **The current line** is a 2 px Aether line, blurred 8 px, at 40% alpha, with a slow 14 s travelling
  highlight (paused offscreen and under reduced motion).
- **Accessibility**: the river is a visual layer over real semantics. Each bank is a `region`
  containing a list; beads are `button`s in that list; arrow keys move within and between banks; the
  visible order equals the DOM order. Screen readers get "Discovery, 9 prospects".
- **Ledger view** (16) is the toggle for people who want rows, sorting and search; the choice persists
  per user. River is the default.

### 13.5 Offer card

An offer (product) as a **poster-like card**, not a table row: glyph tile with the offer's initial or
icon, name, one-line summary, status chip (Active, Coming soon, Inactive), price in mono (verbatim badge
when set), the markets it sells in as small chips, and a **completeness ring** (an arc around the glyph
showing how many of the required fields are filled, from `check_readiness`). Clicking opens the
Offer editor. Deactivating is possible, deleting is not offered (products are deactivated, never
deleted, in the schema). The last card is always a dashed "Add an offer" card.

---

## 14. KPI components

KPIs are shown as **stories**, not as gauges in a grid.

### 14.1 Story KPI

```
  Replies                           ← label, text-2
  42            ▲ 12%  vs last week ← numeral-l (300), delta chip, comparison in text-3
  ╱╲╱╲___╱╲▁▂▃▅▆                    ← sparkline, ice line + data fade; last week as a ghost line
  “Most replies come in the first hour.”   ← optional caption, voice-s, supplied by the API
```

| Element | Spec |
|---|---|
| Label | sentence case noun ("Replies", "Appointments booked"), `label` or `body-sm`, `text-2` |
| Value | `numeral-l`, tabular, weight 300; abbreviated only above 9,999 (`12.4k`), with the full value in the accessible name |
| Delta chip | icon (arrow) plus signed percent plus the comparison period in words. Color by the API's direction: `good` emerald, `bad` rose, `neutral` `text-2`. **Never derived in the browser** |
| Sparkline | 1.5 px line, `--tint` at 100%, data fade under it, previous period as a 40% ghost line; no axes; hover shows a crosshair with value and date; keyboard focus steps through points |
| Caption | optional agent-voice sentence; the API decides if there is one |
| Card | L1; the first KPI of a row may be L2 |
| Live update | value morphs with a 300 ms numeric tween (no flash); a brand-new highest value in the period adds a single 800 ms sheen, once |

Alternative hero form for the top KPI of Command Center: `numeral-xl` inside the hero card, with the
narrative sentence beside it. Only one hero KPI per screen.

### 14.2 The KPI set

Exactly those of [web-admin-product-design.md](web-admin-product-design.md) section 3.3; grouped so the
screen tells a story from left to right and top to bottom.

| Group | KPIs | Intent color |
|---|---|---|
| **Reach** | prospects treated, new prospects, messages sent | ice |
| **Engagement** | replies, conversations, response rate, follow-ups | aether |
| **Outcomes** | appointments, conversions, conversion rate | emerald |
| **Learning** | objections, feedback, reasons for non-conversion | ice (categorical, no ranking color) |
| **Human** | human interventions, handoffs | amber |

Hero row on Command Center: **four** KPIs at most (prospects treated, replies, appointments,
conversions). Everything else lives on Performance. More than four numbers on a first view is the
enterprise-dashboard trap.

### 14.3 Comparison and periods

The period selector is a **segmented control**: Today, 7 days, 30 days, Custom (a calendar popover in
the client timezone). The comparison is always "previous equal period" and is **returned by the API**;
the UI never subtracts. The selected period is in the URL (`?period=7d`), so a link reproduces the view.

### 14.4 Funnel Ribbon

One continuous ribbon instead of a stack of bars. It runs left to right and narrows through the stages of
the commercial story.

```
 Contacted ━━━━━━━━━━━━━━━━┓
                           ┗━━━ Replied ━━━━━━┓
                                              ┗━━ Qualified ━━━┓
                                                               ┗━ Appointment ┅┅┅ Converted ┅┅
   214                        96 (45%)           41 (43%)            18 (44%)   not tracked yet
```

- Built as one SVG path with cubic Bezier transitions between segment heights; segments overlap with
  `mix-blend-mode: screen` so joins glow softly. Color runs ice (reach), aether (engagement),
  emerald (outcome) using the Ribbon gradient.
- Segment widths encode **counts supplied by the API**, and the percentages under them are supplied
  too. The stage-to-stage rates are not computed here.
- Hover isolates a segment (others fade to 35%) and shows the count and the definition of the step.
  Click filters Prospects by that step.
- Steps not yet trackable (appointments, conversions today) are drawn as **dashed ghost segments** with
  "Not tracked yet" (14.6). The ribbon is honest about what exists.
- Screen readers get the same content as an ordered list of "Replied: 96, 45% of contacted".
- The ribbon draws in once on first view (a 900 ms left-to-right mask reveal), then is static.

### 14.5 Rate ring

For a single percentage (response rate, conversion rate): a 96 px ring, 8 px stroke, `--tint`, with the
value in `numeral-m` at the center and a tick at the previous period's position. The tick position comes
from the API.

### 14.6 States of a KPI

| State | Presentation |
|---|---|
| Loading | skeleton (24.1), numeral placeholder at final size (no layout shift) |
| Value | as above |
| **Not tracked yet** | the label, then in `text-3` "Not tracked yet", a ghost sparkline (dashed flat line) and a link "How this will be tracked" that opens a short explanation. Never a zero, never a dash that reads as no data. Applies today to appointments, conversions, objections, feedback, non-conversion reasons |
| No data in period | the numeral `0` for counts that can legitimately be zero; "No activity yet" caption |
| Error | inline retry, rose icon, rest of the screen intact |
| Partial / stale | a small clock icon and "Updated 12 min ago" when the API reports staleness (Sheet quota) |

---

## 15. Status indicators

### 15.1 Rules

1. **Every status is dot or icon + word.** Color reinforces, never alone.
2. A status indicator is a **chip** (`radius-full`, 28 px high, `label`, tint fill `.12`, tint hairline
   `.25`, leading glyph) or, in dense lists, a bare dot plus text.
3. **Animation only for "in progress"** states (sending, working). Static everywhere else.
4. The client's language is used, never the internal enum (15.6).

### 15.2 Prospect status (Core lifecycle)

| Core value | Label | Tint | Glyph | Meaning shown to the client |
|---|---|---|---|---|
| `new` | New | ice | dot | not contacted yet |
| `active` | In conversation | aether | agent-spark | the agent is working with them |
| `paused` | Waiting for you | amber | hand | the agent stopped; a person must act (handoff or a pause) |
| `opted_out` | Asked to stop | rose (muted) | slash-circle | never contacted again; not reversible from the UI |
| `closed` | Closed | neutral | check-circle-dim | finished; the outcome is the client's stage or milestone |

Stage chips (client-defined labels) sit beside the status, are neutral by default and take a milestone
tint when the stage declares one.

### 15.3 Action status

| Core value | Label | Tint | Notes |
|---|---|---|---|
| `planned` | Planned | ice outline | future, ghosted in the Thread |
| `proposed` | Draft ready | amber | needs approval (approval mode) |
| `approved` | Approved | ice | queued to send |
| `rejected` | Declined | neutral | you declined it |
| `sending` | Sending | aether, animated three dots | transient |
| `sent` | Sent | ice check | **not** emerald: sending is not an outcome |
| `completed` | Done | neutral | resolved without sending (schedule, handoff, close) |
| `failed` | Failed | rose, with **Retry** where the API allows | |
| `cancelled` | Cancelled | neutral | |

Emerald is reserved for **outcomes**: a reply received, an appointment set, a payment confirmed, a
conversion.

### 15.4 Payment progress

Shown only through the Payment Seal (18.4): `none` (nothing shown), `claimed` "Says they paid",
`proof_received` "Sent a proof", `paid` "Payment confirmed by {name}". The first two are **amber and
hollow**; only `paid` is emerald and solid with the champagne seal. This deliberately makes an
unverified claim look unfinished.

### 15.5 Certainty Rings

The visual language for **how sure the agent is**. Used on every fact, interest and learned note.

| Fact status | Label | Ring | Text |
|---|---|---|---|
| `confirmed` | Confirmed | full solid ring, filled center dot | `text-1` |
| `discovered` | Learned | ring drawn as a 270 degree arc | `text-1` |
| `hypothesis` | Guess | dotted ring, 60% opacity | `text-2`, italic |

- The ring is 14 px in chips, 20 px in lists, drawn in the intent color of the *source* class:
  prospect or conversation = ice, human or client data = emerald, system = neutral, brain inference = aether.
- **Source glyph** after the value: prospect (speech bubble), conversation (quote), client data
  (table), human (person), system (gear), brain inference (spark).
- Hover or focus opens **Evidence**: the source, the message excerpt verbatim (from `excerpt`), the
  time, and "Open message". A fact without evidence says "From your data".
- A **guess is never shown as a heading or in a summary sentence**. It appears only inside the
  "Guesses" group and with the dotted ring, so a hypothesis can never be mistaken for a fact (the Core
  forbids acting on it as one).
- Legend: a permanent, tiny "Confirmed / Learned / Guess" key in the Certainty panel header.

### 15.6 Vocabulary for a non-technical owner

The interface says what the business means. The mapping is fixed and lives in the i18n catalog.

| Internal | The client sees |
|---|---|
| `dry_run` | **Observe only**: the agent prepares messages and logs them; nothing is sent |
| `approval` | **Ask me first**: the agent proposes, you approve before anything is sent |
| `autonomous` | **Autonomous**: the agent sends within your rules |
| handoff | **Needs a person** / "passed to you" |
| brain settings | **Playbook** (strategy) and **Offers** (catalog) |
| stage | the client's own step label |
| workflow | **Sales path** |
| fact | **What the agent knows** |
| opt-out | **Asked to stop** |
| `enabled` | **Live** / **Not live yet** |
| guardrail cap | **Limit set by your platform administrator** |

### 15.7 Connection health and mode chip

- **Connection chip**: Sheet, WhatsApp, Agent. States: `Healthy` (emerald dot, last success time),
  `Degraded` (amber), `Down` (rose, with the last error summarised in plain words, never a stack trace),
  `Not connected` (neutral). Backed by the per-client health snapshot.
- **Mode chip** (in the Context Bar): the effective mode; if it differs from the requested mode, an
  amber "clamped" marker with a tooltip "You asked for Autonomous; your platform allows Ask me first".

---

## 16. Tables

Tables are the **last resort**, used where comparison by rows is the task: the Ledger view of
prospects, the list of products in the compact view, the version History, and audit lists.

| Property | Spec |
|---|---|
| Container | one L1 panel holding flat rows (no per-row backdrop-filter) |
| Header | sticky, L2 flat fill, `label` in `text-2`, sort indicator, 44 px |
| Rows | 56 px comfortable, 44 px compact; **hairline dividers** `line-1`, **no zebra**, no vertical lines |
| Hover / selection | hover `white .04`; selected `rgb(var(--tint-action) / .10)` with a 2 px Ice leading bar |
| First column | the entity (name with avatar bead, or offer with glyph); links to the drawer; sticky on horizontal scroll |
| Status | chips (15), never raw text |
| Numbers | right-aligned (left in RTL), tabular |
| Dates | relative for recent ("2 h ago") with the absolute time in a tooltip and `<time datetime>`; the client timezone is named where absolute times show |
| Actions | one primary row action (open); the rest in a row menu; bulk actions appear in a floating L2 bar after selection |
| Sorting, filtering, paging | **server-side** (the Sheet is read in full tabs; the API pages). Filters are chips above the table; active filters are removable and in the URL |
| Search | one field, debounced; searches name, phone, the client's key fields |
| Empty | the empty state pattern (23), never an empty grid |
| Long lists | virtualised above 200 rows |
| Mobile | rows become **cards** (the Prospect card); columns are prioritised: name, status, next action |
| Semantics | real `<table>` with `scope`, sortable headers as buttons with `aria-sort`; keyboard row navigation |
| Density toggle | comfortable and compact, remembered per user |

No dense spreadsheets, no inline cell editing, no column chooser in v1. If a task needs a spreadsheet,
the client already has one: the Google Sheet.

---

## 17. Timeline: the Day Thread

The Day Thread is the agent's **journal and plan in one line**: what it did, what it is doing, and what
it intends to do, with "Now" in the middle.

```
        09:02   ◉ Wrote to Amira B.                 ← past: solid line, solid node
                  “I introduced Foundation and asked what she hopes to achieve.”
        09:14   ◉ Amira replied
        09:14   ◈ Learned: goal = “start online”     ← fact with Certainty Ring
        09:15   ◉ Answered her question about payment
        ━━━━━━━━ ◉ NOW  ·  10:32 (Tunis time) ━━━━━━━━   ← the Now marker: a small Pulse and a full-width hairline
        11:00   ◌ Follow up with Karim S.           ← future: dashed line, ghost node, 55% opacity
        11:30   ◌ Follow up with Leila M.
        14:00   ◌ Callback with Youssef (you)        ← a human-owned future item is amber-outlined
```

### 17.1 Anatomy

- **Spine**: a 2 px vertical line at the node column. Past: Aether-to-ice gradient at 40% alpha. Future:
  the same line dashed (4 px dash, 6 px gap) at 25%.
- **Nodes**: 28 px glyph tiles (section 10). Solid tile with tint for events that happened; **ghost** tile
  (outline only, 55% opacity, `Planned` label) for intentions.
- **Node kinds and tint**: message sent by agent (aether), message received (ice), message sent by a
  person (ice with avatar), fact learned (aether with a Certainty Ring), decision (aether), handoff
  (amber), payment claim or proof (amber hollow), payment confirmed (emerald + champagne), scheduled
  (ghost), failure (rose).
- **Narration**: each node has a single serif `voice-s` sentence (agent voice) and, below in `text-3`,
  the factual line (recipient, time). Narration is supplied by the API; the UI never composes it.
- **Grouping**: consecutive events for one prospect collapse into one group ("Amira B., 4 events") with
  a chevron; groups stay expanded for events that need a person.
- **Now marker**: a 12 px Pulse (mini) on the spine plus a full-width hairline with the current time and
  the timezone name. The scroll position anchors here on load; a "Jump to now" chip appears when it is
  off screen.
- **Filters**: chips (All, Messages, Decisions, Needs you); never more than four.
- **Live**: new events slide in above the Now marker (past) with a 300 ms fade and a 6 px rise; the list
  never jumps while the user is reading (a "New activity" pill appears if they scrolled away).
- **Boundaries**: days are separated in the **client's timezone**, with a sticky day label.

### 17.2 Other timelines

| Timeline | Form |
|---|---|
| Prospect history | the Day Thread filtered to one prospect, in the Dossier |
| **Follow-up cadence editor** (Playbook) | a **horizontal** timeline of touch points from first contact, each node a "after N days" step; the **minimum gap** drawn as a locked guardrail zone before the first node and between nodes; adding beyond the maximum touches is disabled with the reason |
| Version History (Settings) | a vertical list of versions with author, time, a short auto summary of changes and Diff / Roll back |
| Payment | the Payment Seal (18.4), a three-node micro-timeline |

---

## 18. Conversation components

### 18.1 Layout

Three columns on wide screens, collapsing progressively:

```
┌ Conversations ┐ ┌────────── Thread ──────────┐ ┌ Dossier / Agent's notes ┐
│ list, search  │ │ bubbles, drafts, composer  │ │ what the agent knows    │
│ Needs you ▲   │ │                            │ │ path, payment, actions  │
└───────────────┘ └────────────────────────────┘ └─────────────────────────┘
   320 px               flexible, max 720               360 px
```

Below 1280 px the right column becomes the Dossier drawer; below 1024 px the list and thread are
separate routes (list, then thread with a back button).

### 18.2 Bubbles

Three authors, always distinguishable without color alone (position, avatar or glyph, and a label on
the first bubble of a run).

| Author | Alignment | Style |
|---|---|---|
| **Prospect** | leading | L1 glass, `line-2` border, `text-1` |
| **Agent** | trailing | fill `rgb(var(--tint-agent) / .14)`, hairline `rgb(var(--tint-agent) / .30)`, a small `agent-spark` at the run's first bubble |
| **A person on your team** | trailing | fill `rgb(var(--tint-action) / .14)`, hairline ice, avatar bead with the person's initials and name |
| **System event** (payment claimed, stage changed, handoff) | centered | a quiet inline chip, `label`, no bubble |

- Text is Geist 15/24 (the actual WhatsApp text; **never serif**). Each bubble sets `lang` and
  `dir="auto"`, because a conversation may mix scripts.
- Max width 78% (mobile 88%); radius `radius-lg` with the tail-side corner at `radius-xs`.
- Timestamps in `text-3`, shown on hover and focus and at the end of each run; the full time in a tooltip.
- **Verbatim spans**: a value that the system inserted exactly as configured (price, payment
  details, link) is underlined with a dotted `ice-300` line; hover shows the `verbatim-lock` and "Inserted
  exactly as you configured it." The API marks these spans.
- Media (images, documents the prospect sent, for example a payment proof) render as a thumbnail card
  with type and size; opening is explicit. Anything that may be a receipt carries the amber hollow
  "Possible proof" marker, never "Proof of payment".
- The composer is absent while the agent owns the conversation and appears when a person takes over
  (18.6).

### 18.3 Agent's notes rail

Beside the thread (or under a bubble on narrow screens), aligned to the message they relate to and
tied to it by a fine hairline:

- **Learned**: fact chips with Certainty Rings and the excerpt.
- **Decided**: one serif line of what the agent chose and why in business terms ("Answered first, then
  asked one question").
- **Moved**: stage or step changes.
- **Scheduled**: the next action.

On mobile: a single line under the bubble, "Learned 2 things ›", that expands inline. Notes come from the
audit trail; **the audit read API does not exist yet** (C5), so v1 shows what the facts and actions
already carry.

### 18.4 The Payment Seal

The business rule is that **the agent never confirms a payment**. The component makes that visible.

```
  ◌╌╌╌╌╌╌╌◌╌╌╌╌╌╌╌●══ (seal)
  Says they   Sent a      Confirmed by
  paid        proof       Sami K. · 14:20
  amber,      amber,      emerald + champagne engraved ring
  hollow      hollow      (only a person can set this)
```

| Part | Spec |
|---|---|
| Nodes | 20 px circles on a 2 px track. `claimed` and `proof_received`: hollow, amber, dashed ring. `paid`: solid emerald center, an outer **Seal ring** (the conic Seal gradient, 3 px) with a fine engraved pattern (an SVG guilloche at 6% alpha) |
| Copy | while unverified, a permanent line: **"Not verified yet. Only a person at {client} can confirm a payment."** |
| Each unverified node | opens the evidence: the prospect's message (excerpt) or the proof media |
| Confirm control | **only rendered for a user holding `payments.confirm`**. Others see "Waiting for someone with permission to confirm", with the names of roles that can |
| Confirm dialog | modal glass. Shows the prospect, the claim and proof evidence, optional fields (amount, currency, reference), the confirming person auto-filled (read-only), and a required checkbox "I have checked this payment in our own bank or payment account." Primary **Success** button "Confirm payment". States plainly: "This is recorded with your name and cannot be edited." |
| On success | the ring closes clockwise (600 ms), the seal "stamps" (scale 1.08 to 1 with a soft champagne sheen sweeping once), the `win` mood for 6 s, a toast; the Needs You item disappears |
| Never | never a "Paid" badge from a claim, an opened link or a proof; never a confirm button for the agent or a system; no confetti |

### 18.5 Drafts (approval mode)

A proposed message appears **in the thread** as a ghost bubble: dashed Aether border, "Draft · waiting
for you" tag, the text with verbatim spans marked. Beneath it:

`[ Approve and send ]` (Primary) · `Edit` (Secondary) · `Decline` (Ghost). Keyboard: `A`, `E`, `D` while the
draft is focused. Editing is in place; an edited draft is labelled "Edited by you" once sent. Approving a
draft whose wording changed since it was proposed asks for a re-read. Sending shows the `sending` state,
then the draft settles into a normal bubble (shared layout transition).

### 18.6 Handoff and taking over

A **handoff banner** sits at the top of the thread when the agent stopped for a person: L3 amber,
the reason in agent voice ("Amira asked to speak with someone about a discount."), and two actions: **I'll
take it** (a person takes over; the composer appears, ice-tinted, the agent shows "Paused on this
conversation") and **Give back to the agent** (available after taking over). Whether the person is
required, optional or not involved at a workflow step comes from the client's configuration, so the
banner's copy distinguishes "needs you" from "you may want to look".

---

## 19. AI activity components

### 19.1 The Pulse

A living orb that **is** the agent. It appears in four sizes: Mini (24, in the Context Bar and the Now
marker), Nav (56), Hero (160, Command Center) and Fleet (72, operator, later).

**Construction** (layers, back to front, all GPU-composited, pure CSS plus one SVG):

1. **Halo**: a blurred radial gradient, 2x the orb's size, `rgb(var(--tint) / .28)`.
2. **Core**: a sphere, `radial-gradient(circle at 34% 28%, aether-300, aether-500 46%, aether-800 100%)`
   at 85% alpha, with an inset highlight for volume.
3. **Caustic**: a `conic-gradient` of aether and ice at 18% alpha, rotating slowly, blended with `screen`.
4. **Ring**: a 1.5 px ring using the Aether line gradient, rotating.
5. **Accessory** by state: satellites, arcs, a count badge, a dashed segment.

**States** (mapped from API `agent.state`, computed server-side from `enabled`, effective mode, pause,
schedule window, queue and health):

| State | Label shown | Motion | Tint |
|---|---|---|---|
| `idle` | "Ready" | slow breathing, 6 s, scale 1 to 1.03, halo .25 to .35 | aether |
| `working` | "Working on 3 conversations" | ring rotates 8 s; up to three tiny satellites orbit at different speeds | aether |
| `writing` | "Writing to Amira" | core shimmer, ring pulses in a 1.2 s rhythm | aether |
| `waiting` | "2 things need you" | breathing slows to 9 s, the ring shifts to **amber**, a count badge | aether + amber ring |
| `observing` (Observe only) | "Observing, nothing is sent" | the core is a **hollow outline**, dotted ring | aether, 70% |
| `off_hours` | "Resting until 09:00" | dimmed 55%, no rotation, a slow 12 s halo fade | aether, desaturated |
| `paused` | "Paused by you" | static, ring dashed | neutral |
| `degraded` | "A connection needs attention" | static, a **rose arc** segment on the ring (no flicker) | aether + rose |
| `not_live` | "Not live yet" | static, desaturated, hollow | neutral |

Rules:

1. **Always accompanied by a text label**, and in the hero by the agent's serif sentence. Motion
   without words is not a status.
2. **The Pulse is the only element allowed continuous motion above 2%** and it pauses offscreen, in a
   hidden tab and under reduced motion (where states are told by ring style and label, with a static
   transition).
3. Its rhythm never accelerates to alarm. Urgency is amber and count, not speed.
4. It is decorative for assistive tech (`aria-hidden`); the label is the accessible status, in a
   polite live region that announces **changes only** and at most every 10 s.
5. Only one Hero Pulse per screen.

### 19.2 The Autonomy Dial

How much the agent may do alone, as a physical, understandable control.

```
              Autonomous
           ╱‾‾‾‾‾‾‾‾‾‾╲
   Ask me  │      ●      │  [lock] locked zone
   first   ╲__________╱
        Observe only
```

- A 240 degree arc with **three detents**: Observe only, Ask me first, Autonomous. The knob is a glass
  disc with an Aether ring; snapping between detents is a 300 ms spring.
- Each detent has a one-sentence consequence beneath it that updates live:
  "The agent prepares messages but sends nothing." / "The agent proposes, you approve each message." /
  "The agent sends within your rules. You are told about anything unusual."
- **Requested versus effective**: the selected knob is solid; if the platform clamps it, a **ghost knob**
  marks where the client asked for, with "You asked for Autonomous. Your platform currently allows Ask me
  first." Data: requested mode, effective mode, `mode_clamped`.
- **The locked zone**: the part of the arc above the deployment ceiling is drawn with a diagonal hatch
  at 12% alpha and a `guardrail` lock. Focus and hover explain: "Set by your platform administrator."
  A **Request Autonomous** link starts the operator-approval flow.
- Changing the mode is a **Draft** change (published with Review), except **Pause**, which is
  immediate and reversible.
- On mobile and for assistive tech the dial is a **segmented control of three radios** with the same
  sentences; the arc is a visual enhancement over it.

### 19.3 Guardrail track and daily budget

Wherever a client sets a number **within a platform limit** (daily actions, minimum gap between contacts,
maximum touches, sending window), the control is a **slider with a guardrail zone**: the reachable range
is a bright ice track; the zone the platform forbids is hatched and locked. The client can tighten, never
loosen, and sees exactly where the wall is.

**Daily budget** (Command Center): an arc gauge, 96 px, "14 of 20 today". The 20 is the client's limit;
the platform cap, if higher, is a small locked tick. Colors: ice while normal, amber at 90%, and at 100% the
agent still answers replies (replies bypass the quota), which the caption says: "New conversations are
done for today. I'll still reply."

### 19.4 Activity ticker and queue

- **Ticker**: one line in the Context Bar or hero, an `aria-live="polite"` region, a sentence in serif
  that cross-fades every time something meaningful happens ("Replied to Amira about payment"), at most one
  change per 4 s; it is a heartbeat, not a log.
- **Queue**: three lanes, *Now* (in progress), *Next* (planned, ordered by the planner's priority) and
  *Later* (scheduled). Each item is a compact row with a glyph, the prospect, the action in words, and the
  planned time in the client timezone. It is the same data as the future part of the Day Thread, in a
  compact form for the Command Center.

### 19.5 The Needs You tray

The heart of an action-oriented interface. It is the first thing under the hero and its own screen.

Card types (all L3 amber unless stated):

| Type | Reason line (agent voice) | Primary action |
|---|---|---|
| **Draft to approve** | "A reply to Amira about the payment options." | Approve and send |
| **Conversation for you** (handoff) | "Karim asked for a discount I can't offer." | I'll take it |
| **Payment to confirm** | "Leila says she paid and sent a receipt." | Review and confirm (Seal dialog; requires `payments.confirm`) |
| **Failed action** (rose accent) | "I couldn't send to Youssef. The number looks invalid." | Retry, or Open |

Anatomy: avatar bead, prospect and reason (serif `voice-s`), the *evidence preview* (draft text, the
last messages, or the claim), how long it has waited, and the actions. Sort: by the API's priority
(urgent first), then oldest first.

**Keyboard-first**: `J`/`K` move, `Enter` opens, `A` approve, `E` edit, `D` decline, `T` take over.
Multi-select allows approving several drafts after a summary confirmation listing each message (never a
blind "approve all"). When the tray is empty, the **empty state** celebrates quietly (23): "Nothing needs
you. I'm on it." with the Pulse at rest.

### 19.6 "Why?" and narrated reasoning

Any decision or suggestion can be opened with **Why?**, an L2 popover with three parts: **What I
knew** (fact chips with Certainty Rings), **Which of your rules applied** (named in the client's terms,
linking to the Playbook section), and **What I did**. It shows **inputs and rules, never the model's
internal reasoning**, so it is stable, auditable and meaningful to a business owner. The audit read
capability that backs it does not exist yet (C5).

### 19.7 Launch Runway

Naga's first experience. The readiness list becomes a **runway**: an ordered set of gates the agent must
pass before it goes live, each a Setup gate card on a horizontal (desktop) or vertical (mobile) track
with a glowing line linking the gates as they complete.

| Gate | Passes when | Where it leads |
|---|---|---|
| **Connect your Sheet** | the connection test passed | Settings, Connections |
| **Add your first offer** | at least one active offer with its required fields | Offer editor |
| **Set how you get paid** | a payment method applies (or none is needed) | Offers, Payment |
| **Shape your voice** | languages, tone and message limits set | Playbook, Voice |
| **Define the sales path** | a workflow, handoff rule and follow-up cadence exist | Playbook |
| **Review and request activation** | no missing item remains | Review, then a request to the operator |

- Gates derive from the API's readiness list (C5); each unfinished item is shown as a sentence with a
  direct link to the field ("Add a price to Foundation, or choose 'On request'").
- **Going live is the operator's switch** (`enabled` is operator wiring). The last gate is therefore
  **Request activation**, and its result state is "Waiting for activation". Until then the Pulse is `not_live`.
- **Recommended first mode is Ask me first**; the Runway states it.
- When all gates pass, one calm ceremony: the line completes, the Pulse wakes from hollow to solid
  (900 ms), mood `win` once. Then the Runway collapses into a small "Setup complete" chip and Command
  Center takes over.
- The Runway remains reachable from Settings. Later edits that make an offer incomplete re-open only the
  affected gate.

### 19.8 Health constellation

Three nodes connected by fine lines: **Your Sheet**, **WhatsApp**, **The agent**. Each shows a status dot
and the time of the last success; a failing node draws a rose arc, and the link to it dims. It sits in
Settings and appears in the hero only when something is degraded. Purely a rendering of the client's
health snapshot.

---

## 20. Navigation

### 20.1 Structure

Two groups, so a non-technical user always knows whether they are *running* the agent or *shaping* it.

| Group | Item | Purpose |
|---|---|---|
| (pinned, temporary) | **Setup** | Launch Runway, present until activation, then a chip in Settings |
| **Run** | **Command** | the agent now, KPI story, Needs You preview (home) |
| | **Needs you** | decisions: approvals, handoffs, payments to confirm (with count) |
| | **Conversations** | the WhatsApp threads |
| | **Prospects** | River and Ledger |
| | **Performance** | KPIs and Funnel Ribbon over a period |
| **Shape** | **Offers** | products, prices, markets, payment |
| | **Playbook** | audience, positioning, discovery, objections, follow-up, handoff, sales path |
| | **Settings** | connections, schedule and limits, voice and languages, reports, team, versions |

Sections that lack data or API yet are **hidden, not greyed** in the shipped product (no dead ends);
this document designs all of them.

### 20.2 Desktop shell

- **Rail** (left): L1 glass, 264 px expanded, 72 px collapsed (icons with tooltips). Item height 48,
  `radius-md`, icon 24, label `body` 15. **Active**: a 3 px luminous ice bar on the leading edge,
  a `rgb(var(--tint-action) / .10)` fill, `text-1`. Hover: `white .05`. Badges (Needs you count) are
  amber pills. The Nav Pulse (56) sits at the top with the client name beneath it; it doubles as the
  link to Command.
- **Context bar** (top, 64 px, L2 when scrolled, transparent at the top): screen title, the **Mini Pulse**
  with the ticker, the **mode chip**, `⌘K` command palette trigger, notification bell (the Needs You count,
  not a separate inbox), and the user menu (profile, role, language, sign out).
- **Command palette** (`⌘K` / `Ctrl+K`, L4): navigation (screens, a prospect by name or phone, an offer by
  name), and safe actions (Pause agent, Resume agent, Publish changes, Open Needs you). **v1 is a
  deterministic command list with fuzzy search; no language model is involved.**
- **Breadcrumbs** only inside nested editors (Offers, Offer name, Pricing).
- **Unpublished changes bar**: a floating L2 pill at the bottom center whenever a Draft exists: "3
  unpublished changes · Review · Discard" (pattern C3.4). It sits above toasts.

### 20.3 Tablet and mobile

- **Tablet**: rail collapses to icons by default; drawers become full-height sheets.
- **Mobile**: **bottom tab bar** (L2, safe-area aware) with five tabs: **Command**, **Needs you** (badge),
  **Chats**, **Prospects**, **More** (a sheet with Performance, Offers, Playbook, Settings). The Context Bar
  reduces to the title, the Mini Pulse and the palette icon. The unpublished-changes pill sits above the
  tab bar.
- Gestures: swipe-back is native routing; no gesture is the only way to do anything.

### 20.4 Rules

1. **Two levels of navigation, maximum.** Rail item, then in-screen tabs. Never a third menu.
2. The current location is always visible in three places (rail highlight, title, document title).
3. Every screen has one primary action, in the same place (top trailing edge, or the sticky bottom bar on
   mobile).
4. Deep links: every drawer, filter and period is in the URL.
5. **Permission-aware**: an item a role cannot use is absent; an action within a screen that a role
   cannot use is **visible but disabled with the reason** (so the person understands who can).

---

## 21. Modals and drawers

### 21.1 Modal

| Property | Spec |
|---|---|
| Glass | **L4** (the most opaque glass), `radius-2xl`, `shadow-4` |
| Sizes | `sm` 440 (confirmations), `md` 560 (forms), `lg` 720 (review, diff) |
| Scrim | `rgb(3 4 12 / .60)`. While a modal is open, blurred surfaces underneath switch to their solid fallback (glass rule 7.4.2), so the blur budget holds |
| Header | `h2` title that states the consequence ("Confirm Leila's payment"), close button top trailing |
| Footer | leading: safe action (Cancel). Trailing: the primary action. Sticky when the body scrolls |
| Behaviour | focus trapped, initial focus on the first field or the safest control (never on a destructive confirm), `Esc` closes unless a form is dirty (then a small inline "Discard changes?" appears), focus returns to the trigger |
| Motion | enter: scale .96 to 1 plus fade, 220 ms `ease-out`. Exit 160 ms |
| Mobile | becomes a **bottom sheet** (21.4) |

Rules: no modal opens another modal; long forms are a page or a drawer, not a modal; a modal never carries
information the user cannot get elsewhere after closing it.

### 21.2 Drawer (side panel)

- Slides in from the **inline-end** edge (the right edge in LTR, the left in RTL), L4, `radius-xl` on the
  leading corners. Width 520; the **Dossier** uses 640.
- Two modes. **Modal drawer** (scrim, focus trap) below 1280 px and always for forms. **Inspector**
  (no scrim, no focus trap, the page stays interactive) at 1280 px and above, used by the Dossier so a
  person can move through the River or the Ledger while the Dossier follows their selection.
- A drawer is a **nested route**: the URL holds it (`/prospects/1234`), so it can be shared and the back
  button closes it.
- Motion: slide 320 ms `ease-out`, content fades in after 80 ms. Exit 200 ms.

### 21.3 Popover and tooltip

- **Popover**: L2, `radius-lg`, max 360 px wide, opens on click or Enter; used for Evidence, Why?,
  the calendar. Focus moves into it; `Esc` closes.
- **Tooltip**: L2, small, 400 ms delay on hover, immediate on focus; **never the only place** information
  lives (touch devices have no hover). Used for icon-button names and disabled reasons.

### 21.4 Bottom sheet (mobile)

Drag handle 36 x 4, snap points at 40%, 70% and 100%, L4, safe-area padding. Drag is an enhancement; a
close button and the `Esc`/back gesture always work.

### 21.5 Command palette

Centered L4 at 640 px (a full-height sheet on mobile), search field first, grouped results (Go to,
Actions, Prospects, Offers), keyboard only is fully sufficient. Recent items first.

---

## 22. Toasts and notifications

### 22.1 Toast

| Property | Spec |
|---|---|
| Glass | L2, `radius-lg`, 360 px wide (full width minus gutters on mobile), glyph tile plus title (`body` 15/500), optional one line, optional action, close button |
| Position | bottom center on desktop, **above the unpublished-changes bar**; top center on mobile so it never covers the tab bar |
| Stack | at most three visible; the oldest leaves first; identical toasts are merged with a counter |

| Type | Tint | Duration | Announcement | Use |
|---|---|---|---|---|
| Success | emerald | 4 s | `role="status"` | a result the user caused: "Published version 7", "Payment confirmed" |
| Info | ice | 4 s | `role="status"` | neutral confirmation: "Link copied" |
| Agent | aether | 6 s | `role="status"` | only what the user is waiting for: "Sent to Amira" after approving |
| Attention | amber | until dismissed | `role="status"` | something to resolve soon: "The daily limit is reached" |
| Error | rose | until dismissed | `role="alert"` | failed action, with **Retry** and, when useful, "Copy details" |
| Undo | neutral | 10 s | `role="status"` | reversible actions: "Draft declined · Undo" |

Rules:

1. **The agent's routine activity is never a toast.** It belongs in the ticker and the Thread. Toasts
   are for the user's own actions and for things that need resolving.
2. A toast never contains the only copy of important information; the state also changes on the screen.
3. Hover, focus and a screen-reader interaction **pause** the timer. Errors never auto-dismiss.
4. No toast for something already obvious on screen.

### 22.2 Notifications

There is **no notification inbox** in v1. The bell in the Context Bar shows the Needs You count and
opens that tray. Outbound notifications to the owner (the CEO report, later alerts) are WhatsApp or e-mail
features of the platform, configured under Settings, Reports.

---

## 23. Empty states

An empty state is a **guided moment**, never a blank grid. It uses a quiet composition: a glyph tile or
a resting Pulse over layered glass shapes with a soft aurora glow. No cartoon illustrations, no stock
art.

**Anatomy**: composition (96 to 160 px), `h3` title, `body-l` sentence in `text-2` (max 48 characters
wide per line), **one** primary action, optionally one secondary link.

| Kind | Example | Notes |
|---|---|---|
| **First run** | Offers: "Teach your agent what you sell." Action: "Add your first offer" | links back to the Launch Runway gate |
| **Nothing to do** (positive) | Needs you: "Nothing needs you. I'm on it." | Pulse at rest, mood calm; celebrated quietly |
| **No results** | "No prospects match these filters." Action: "Clear filters" | the filters stay visible above |
| **Waiting for data** | Prospects: "Prospects appear here as soon as they are in your Sheet." Action: "Check the Sheet connection" | |
| **Not tracked yet** | KPI (14.6) | honest, not apologetic |
| **No permission** | "Only owners can change limits." | shows who can |
| **Offline** | "You're offline. We'll reconnect automatically." | non-blocking banner, not a full state |

Copy rules: say what belongs here in the client's terms, why it is empty, and what to do next; never
"No data".

---

## 24. Loading states

### 24.1 Skeletons

Glass-shaped placeholders that **match the final layout and size** (no layout shift): a `white .05` fill
with a 1.6 s diagonal shimmer (`white .07` sweep). They appear only after **200 ms** (fast responses show
nothing) and remain at least 400 ms once shown to avoid flashing. Under reduced motion the shimmer is a
static fill.

### 24.2 Two kinds of "busy", kept distinct

| What is happening | Presentation |
|---|---|
| **The system is loading data** | skeletons or a 20 px spinner (an Aether-line arc, 800 ms rotation) |
| **The agent is working** | the Pulse, the ticker, the Thread's Now marker, `sending` chips |

The user should never confuse "the page is fetching" with "the agent is thinking".

### 24.3 Patterns

| Situation | Treatment |
|---|---|
| Route change | the shell stays; the content area shows skeletons; a 2 px Ice progress hairline at the top of the content after 400 ms |
| Refresh with data present | **stale-while-revalidate**: keep showing current data; a small "Updating" hairline on the panel; no skeleton |
| Mutation (save, approve) | **optimistic** where reversible (approve, decline, pause) with rollback and an error toast; **pessimistic** with a button spinner for irreversible or validated ones (publish, confirm payment) |
| Multi-step operation | a stepper with each step's label (connection test, publish validation), never an indeterminate spinner longer than 3 s |
| Long list | skeleton rows for the first page, infinite loader row afterwards |

---

## 25. Error states

### 25.1 Hierarchy

Errors are shown at the **smallest scope that contains them**; the rest of the interface stays usable.

| Scope | Presentation |
|---|---|
| Field | inline message under the field, rose icon and text, `aria-invalid` |
| Section / panel | a rose-hairline callout at the top of the panel with the list of problems, each linking to its field |
| Component data failure | the panel shows an error state (glyph tile, plain sentence, **Retry**); siblings keep working |
| Screen | a full-content error with the same structure, the shell intact |
| App | session expired: a modal with "Sign in again", changes preserved |

### 25.2 Situations

| Situation | Behaviour |
|---|---|
| **Validation on publish** | the review sheet lists **every** problem from both validation passes, grouped by section, each with a link; nothing is published; the draft is kept |
| **Newer version exists** (someone published while you were editing) | "Version 8 was published while you were editing." Actions: **Review the changes** (a three-way summary), **Reapply mine on top**, **Discard mine**. Never silently overwrite |
| **Send failed** | the action shows **Failed** (rose) in the Thread with the reason in plain words and Retry when the API allows |
| **Connection failing** (Sheet, WhatsApp, agent) | the Pulse goes `degraded`, mood `trouble`, the health constellation appears in the hero with the failing node and what to check |
| **Permission denied** | a lock glyph, "Only {roles} can do this", never a generic 403 |
| **Not found** | "This prospect isn't here anymore", with a way back |
| **Rate limit or quota** (Sheets) | "Data may be a few minutes behind." with the last-updated time; the UI keeps showing stale data |
| **Offline** | a banner; reads keep working from cache; writes are queued only when safe, otherwise disabled with the reason |

### 25.3 Copy

Say **what happened**, **whether anything was lost**, and **what to do**. No codes on screen; an error id
and "Copy details" for support. Blame nobody, including the system. Example: "I couldn't reach your Google
Sheet. Nothing was lost. Check that it is still shared with {service account e-mail}, then try again."

Rose is used for real errors and irreversible actions only. A screen with three rose elements has a
problem worth solving in the design.

---

## 26. Responsive behavior

### 26.1 Breakpoints

| Name | Width | Shell |
|---|---|---|
| `xs` | 0 to 599 | bottom tab bar, single column, sheets instead of modals |
| `sm` | 600 to 1023 | collapsed icon rail, up to 2 columns, full-height sheets |
| `md` | 1024 to 1279 | collapsed or expanded rail, 2 to 3 columns, Dossier as a modal drawer |
| `lg` | 1280 to 1679 | expanded rail, Dossier as an inspector, conversation three-column |
| `xl` | 1680 and up | content capped (1240 or 1680 for canvas screens), extra space stays as aurora |

Mobile-first CSS. Components respond to their **container** (`@container`), not only the viewport, so a
card inside a drawer adapts the same way as on a narrow screen.

### 26.2 Per-pattern behaviour

| Pattern | Small screens |
|---|---|
| Hero (Pulse, sentence) | Pulse 96, sentence below, actions full width |
| KPI row | horizontal scroll-snap of Story KPIs (each 240 px), first fully visible; or a 2 column grid for four |
| Funnel Ribbon | rotates to **vertical** (top to bottom) under 600 px |
| Opportunity River | banks become horizontal **snap lanes** with a lane switcher (segmented) or an accordion of banks; Ledger as cards |
| Day Thread | the spine moves to the leading edge; times sit inline; no side notes |
| Tables | cards (16) |
| Conversation | list and thread are separate routes; notes inline; the composer is sticky above the keyboard |
| Forms | single column, labels above, sticky action bar at the bottom |
| Autonomy Dial | segmented three-radio control |
| Nav | bottom tab bar, "More" sheet |
| Modals, drawers | bottom sheets |

### 26.3 Rules

1. **Touch first for targets** (44 px), pointer refinements second.
2. **No hover-only functionality**; hover effects live under `@media (hover: hover)`.
3. Glass is lighter on small screens: blur halved, fewer stacked layers, and **`data-perf="lite"`** (an
   automatic and a manual setting) removes the aurora drift, simplifies the Pulse and sets blur to 12
   when the device drops frames.
4. Safe-area insets respected (notch, home indicator).
5. Text scales with the user's setting; layouts tolerate 200% zoom and 320 CSS px reflow with no
   horizontal scroll, except deliberately scrollable regions (KPI snap row, River lanes, code).
6. Landscape phones use the `sm` shell with the tab bar hidden when the keyboard is open.

---

## 27. Accessibility

Target: **WCAG 2.2 AA** for the whole product, **AAA text contrast** for body and numerals where the
palette allows (it does, section 2.3). Accessibility is designed in the tokens and enforced in CI.

### 27.1 Requirements

| Area | Requirement |
|---|---|
| **Contrast** | text 4.5:1 (3:1 for large text), non-text UI 3:1, **measured against the worst-case glass over the brightest aurora**; a `tokens` unit test recomputes every documented pair and fails the build |
| **Color independence** | no state by color alone: icon, text, pattern (Certainty Rings differ by shape, the Payment Seal by fill and ring) |
| **Focus** | double ring on every focusable element, never removed, never clipped; focus order equals visual and DOM order; focus is moved deliberately into dialogs and returned after |
| **Keyboard** | every function is operable without a pointer; a documented shortcut map (Needs You `J K A E D T`, `⌘K`, `?` shows shortcuts); shortcuts are single-key only inside focused regions and never conflict with screen readers |
| **Target size** | 44 x 44 CSS px on touch, 32 minimum on pointer, with spacing |
| **No drag-only** | anything draggable has a button or menu alternative (reordering lists, the Dial) |
| **Motion** | `prefers-reduced-motion` respected (28.5); no flashing above 3 per second; nothing auto-plays more than 5 s without a pause, except the Pulse's ambient loop, which is decorative and disabled under reduced motion |
| **Transparency** | `prefers-reduced-transparency` swaps every glass level to its solid token; the design still reads because hierarchy relies on fill, border and shadow, not only blur |
| **Contrast preference** | `prefers-contrast: more` raises borders to `line-control`, glass fills to their solid tokens, text to `text-1` |
| **Forced colors** | Windows High Contrast: system colors, 1 px borders, no gradients on text, focus with `outline` |
| **Text** | 200% zoom and text-spacing overrides without loss; 320 px reflow; relative units |
| **Language and direction** | `lang` on the document, on each conversation bubble and on any content in another language; `dir` auto for user content; **RTL mirrored** by logical properties |
| **Forms** | visible labels, `aria-describedby` for helper and error, `autocomplete` tokens, errors announced and focus sent to the first invalid field on submit, no time limits |
| **Authentication** | (API side) no cognitive-test-only login |
| **Consistent help** | the "?" shortcut and help link in the same place on every screen |

### 27.2 Screen readers and live regions

- **One polite live region** for agent activity (the ticker and the Pulse label), **throttled** to a
  change every 10 s at most, announcing "Working on 3 conversations", never every message.
- **One assertive region** for errors that block work.
- Needs You updates announce the *count* ("3 items need you"), not each card.
- The Pulse, aurora, Ribbon geometry and beads' motion are decorative; **every one has a semantic text
  equivalent** (label, list, table).
- **Charts**: each has an accessible summary (`aria-describedby`), a **"View as table"** toggle, and
  keyboard-focusable data points.
- Status chips have text; icons are `aria-hidden` unless standalone.
- Landmarks: `banner`, `navigation` (rail), `main`, `complementary` (notes rail, inspector), and
  labelled regions for River banks.

### 27.3 Testing (release gates)

1. `axe-core` in Storybook and Playwright: zero serious or critical violations.
2. Keyboard-only Playwright scripts for the critical flows: approve a draft, take over a conversation,
   confirm a payment, publish changes, connect the Sheet.
3. Manual passes per release: NVDA + Firefox, VoiceOver + Safari, TalkBack on the mobile critical flows.
4. Visual tests in reduced motion, reduced transparency, forced colors, 200% zoom and RTL.
5. Component **Definition of Done** (C1) includes an accessibility checklist.

---

## 28. Motion and animation principles

### 28.1 Philosophy

> **Motion explains, then gets out of the way.** It shows where something came from, what changed, and
> what is alive. It is never decoration for its own sake.

Four purposes, each with its own character:

| Purpose | Character | Examples |
|---|---|---|
| **Alive** (ambient) | very slow, tiny, looping, pausable | Pulse breathing, aurora drift, the River's current |
| **Respond** (feedback) | fast, precise, never delaying the user | hover, press, focus, toggle, drag |
| **Reveal** (transition) | medium, spatial, continuous | modals, drawers, page changes, list changes, shared layout |
| **Celebrate** (win) | rare, one gesture, elegant | the Seal, the runway completing, a record high |

### 28.2 Tokens

```css
:root {
  --dur-instant: 90ms;   /* press, toggle */
  --dur-fast:   150ms;   /* hover, focus, chip */
  --dur-base:   220ms;   /* modal, popover, card lift */
  --dur-slow:   350ms;   /* drawer, layout, dial snap */
  --dur-slower: 500ms;   /* page, River glide, ribbon draw-in (900ms) */

  --ease-out:    cubic-bezier(.16, 1, .3, 1);      /* entering, most things */
  --ease-in:     cubic-bezier(.7, 0, .84, 0);      /* exiting */
  --ease-inout:  cubic-bezier(.65, 0, .35, 1);     /* moving between two places */
  --ease-linear: linear;                           /* ambient loops, progress only */
}
/* Spring (Motion library), used for layout and the Dial: { stiffness: 260, damping: 30, mass: 1 } */
```

Exits are about **70% of the enter duration**. Ambient loops range from 6 to 140 s.

### 28.3 Catalog

| Motion | Spec |
|---|---|
| Page change | crossfade 180 ms plus an 8 px rise on the incoming content; the shell never animates |
| Card hover | translateY(-2 px), shadow-1 to shadow-2, 200 ms `ease-out` |
| Button press | scale .985, 90 ms |
| Focus | ring appears in 100 ms, no motion of layout |
| Modal / drawer | see 21 |
| Toast | slides 12 px and fades in 220 ms; leaves in 150 ms |
| List insert / remove | height and opacity, 220 ms; siblings glide (layout animation), stagger 40 ms, **at most 8 items** |
| River bead moves | shared layout spring along the current, about 450 ms |
| Numeric change | tween the digits 300 ms; no flash |
| Sparkline / Ribbon | first view only: a 900 ms mask reveal from the leading edge; later data updates morph the path in 300 ms |
| Thread new event | fade and 6 px rise, 300 ms, above the Now marker |
| Dial snap | spring, about 300 ms |
| Skeleton | 1.6 s shimmer |
| Pulse | see 19.1; breathing 6 s, ring 8 s, halo fade 12 s |
| Aurora | 90 to 140 s drift; mood change 1.2 s |
| Payment Seal | ring closes 600 ms, stamp scale 1.08 to 1, one champagne sheen 800 ms |
| Runway complete | line completes, Pulse fills from hollow, 900 ms, mood `win` once |
| Send (approve) | the ghost draft settles into a bubble (shared layout, 300 ms) |

### 28.4 Rules

1. **One hero motion per screen.** If two things compete for attention, one of them is wrong.
2. **Animate cheap properties**: transform, opacity, and registered custom properties for gradients. Mask
   or clip only for the Ribbon reveal. Never animate width, height, top or box-shadow blur on a loop.
3. **Interruptible**: any animation can be cancelled by a new interaction and reverses from where it is.
4. **No animation on data refresh** other than value morphing. Polling never causes a visible flash.
5. **Stagger** small (40 ms), capped (8 items); never delay content the user came for.
6. **Pause when not seen**: offscreen (IntersectionObserver), hidden tab, or `data-perf="lite"`.
7. **Budget**: at most the aurora plus **two** ambient loops visible at once (for example the Pulse and
   the Thread's Now marker).
8. **No parallax, no scroll-jacking, no motion tied to scroll position** except sticky behaviour.
9. **Celebration is a single, quiet gesture** (sheen, seal, glow). No confetti, no bounce, no
   particles, no sound.
10. Performance target: 60 fps on a 2019 integrated-GPU laptop for the Pulse and the River; measured in
    CI on a throttled profile.

### 28.5 Reduced motion

Under `prefers-reduced-motion: reduce`:

- Ambient loops stop (aurora drift, Pulse breathing and rotation, River current, shimmer).
- Transforms and springs become **120 ms opacity fades**; the shared layout transitions become instant
  moves with a brief highlight.
- **States are still legible**: the Pulse's state is carried by ring style, tint and label; loading is a
  static skeleton and a text "Loading".
- Celebrations become a single static state change (the Seal appears solid, the runway line completes).
- Numeric tweens become direct value swaps.

---

# Part C. Architecture and screens

## C1. Component architecture

### C1.1 Tiers

Dependencies point **downward only**. A lower tier never imports a higher one.

```
apps/console      Tier 4  composition: routes, data hooks, permission gating, i18n catalogs, tenant
     │
packages/ui-agent Tier 3  domain components: they know the agent's world, not the API
     │
packages/ui       Tier 2  patterns   ·   Tier 1  primitives
     │
packages/tokens   Tier 0  CSS variables, TS constants, contrast test
```

| Tier | Package | Contents |
|---|---|---|
| **0 Tokens** | `tokens` | `tokens.css` (sections 2 to 9 and 28), `tokens.ts` (typed mirror), `contrast.test.ts`, the Tailwind theme mapping |
| **1 Primitives** | `ui` | `GlassSurface` (levels L-1 to L4), `Stack`, `Cluster`, `Grid`, `Text` (all type tokens), `Icon`, `GlyphTile`, `Button`, `IconButton`, `Link`, `Chip`, `Badge`, `Avatar`, `Spinner`, `Skeleton`, `VisuallyHidden`, `Divider` |
| **2 Patterns** | `ui` | `Field` (label, helper, error), `TextInput`, `Textarea`, `Select`, `Combobox`, `MultiChips`, `Switch`, `Checkbox`, `RadioGroup`, `Segmented`, `Slider`, `MoneyInput`, `PhoneInput`, `UrlInput`, `TimeInput`, `WeekdayPicker`, `PhraseList`, `Card`, `Panel`, `Tabs`, `Accordion`, `Table`, `Dialog`, `Drawer`, `Sheet`, `Popover`, `Tooltip`, `Toast`, `Toolbar`, `Breadcrumbs`, `EmptyState`, `ErrorState`, `FilterBar`, `CommandPalette`, `PageHeader`, `FormSection`, `StickyActionBar` |
| **3 Domain** | `ui-agent` | `Pulse`, `AutonomyDial`, `GuardrailSlider`, `DailyBudget`, `DayThread` (+ `ThreadGroup`, `ThreadNode`, `NowMarker`), `CadenceEditor`, `OpportunityRiver` (+ `Bank`, `Bead`), `ProspectCard`, `CertaintyChip`, `EvidencePopover`, `PathTrack`, `PaymentSeal`, `ConfirmPaymentDialog`, `StoryKpi`, `Sparkline`, `FunnelRibbon`, `RateRing`, `PeriodSegmented`, `ConversationThread`, `MessageBubble`, `DraftBubble`, `AgentNotes`, `HandoffBanner`, `NeedsYouCard`, `NeedsYouTray`, `InsightCard`, `WhyPopover`, `LaunchRunway` (+ `GateCard`), `ConnectionConstellation`, `VerbatimField`, `OfferCard`, `StatusChip` (prospect, action, payment, connection, mode), `Ticker` |
| **4 App** | `console` | screens, route tree (C2), API hooks (TanStack Query over `api-client`), **adapters** that turn API objects into `ui-agent` view-models, permission gating, tenant provider, i18n catalogs, feature flags |

### C1.2 Conventions

1. **Props in, events out.** Tier 1 to 3 components fetch nothing, know no API type and no route. They take
   **view-model props** defined in `ui-agent`; adapters in `console` translate. This is what lets the
   Multi-Client Admin reuse them unchanged.
2. **No strings inside tiers 1 to 3.** Visible text arrives through props or an `I18nProvider` context.
   Even the defaults (for example "Approve and send") are keys in `i18n`, not literals.
3. **No client name, ever** (A3.5). Tenant identity arrives as data (display name, logo URL, accent).
4. **Tokens only.** No raw hex, px radius, shadow or duration in a component; a lint rule (stylelint +
   ESLint) fails the build. Component tokens (`--button-bg`) alias semantic tokens.
5. **State through data attributes**: `data-state`, `data-tint`, `data-level`, `data-density`, so styling
   and tests share one vocabulary and Radix states map naturally.
6. **Variants by props, not by class strings**: `variant`, `size`, `tint`, `density`. `asChild` for
   polymorphism (Radix pattern).
7. **Accessibility props are required in the type** where the name cannot be inferred (for example
   `IconButton` requires `label`; `Pulse` requires `statusLabel`).
8. **Controlled and uncontrolled** both supported for inputs; forms use react-hook-form with resolvers
   built from the Brain schema (`json_schema()` from the API) plus server error paths.
9. **Logical CSS only** (`margin-inline-start`, `inset-inline-end`, `text-align: start`), so RTL costs
   nothing.
10. **One file per component**, with `Component.tsx`, `Component.css` (or Tailwind), `Component.stories.tsx`,
    `Component.test.tsx`, and `index.ts` exports.

### C1.3 Definition of Done (per component)

- [ ] Uses tokens only; no raw values (lint passes)
- [ ] All states specified in this document exist as stories (rest, hover, focus, active, disabled,
      loading, error, empty where relevant)
- [ ] Keyboard behaviour implemented and tested; focus ring visible on every surface
- [ ] axe clean in Storybook; contrast verified in the **worst-case** glass story
- [ ] Reduced motion, reduced transparency, forced colors and RTL stories
- [ ] Container-query responsive behaviour demonstrated at 320, 480, 720, 1024 px containers
- [ ] No fetching, no route, no client name, no literal string
- [ ] Visual regression baseline recorded
- [ ] Documented props and a usage note in Storybook (linking to the section of this document)

---

## C2. Screen hierarchy

```
/                         → redirects to /setup until activated, then /command
/setup                    Launch Runway
/command                  Command Center (home)
/needs-you                Needs You tray            (?type=draft|handoff|payment|failed)
/conversations            list + thread              /conversations/:prospectId
/prospects                River (default) or Ledger  ?view=river|ledger   /prospects/:id  (Dossier)
/performance              KPIs and Funnel Ribbon     ?period=7d
/offers                   tabs:  Offers | Markets | Payment
  /offers/:id             Offer editor
                           sections: Essentials · Audience and value · Pricing · Payment ·
                                     Sales path · Limits
/playbook                 chapters:
  /playbook/audience      who you sell to, positioning
  /playbook/voice         languages, tone, register, message limits
  /playbook/discovery     what to learn, questions, stop signals
  /playbook/objections    objections and answers
  /playbook/follow-up     cadence per situation (CadenceEditor)
  /playbook/handoff       when a person steps in, and the message the prospect gets
  /playbook/path          the sales path (workflow steps)
/settings
  /settings/connections   Google Sheet (test), WhatsApp (read-only, health)
  /settings/schedule      working days, hours, timezone, daily limit, the Autonomy Dial, opt-out words
  /settings/reports       CEO report: time, days, sections, language, recipient
  /settings/team          people and roles (when the API offers it)
  /settings/versions      history, diff, roll back
```

**Screen priority and readiness**

| Priority | Screens | Depends on |
|---|---|---|
| **P0** (first delivery, Naga configures itself) | Setup, Offers (with Markets, Payment), Playbook, Settings, the unpublished-changes bar and Review sheet | Admin API: Configuration |
| **P1** | Needs you, Prospects (River, Ledger, Dossier), Conversations, Payment Seal | Admin API: CRM and approvals, payment confirmation endpoint |
| **P2** | Command Center, Performance | KPI computation in `sales_core`, audit read |
| **P3** | Operator app (Multi-Client Admin) | tenant-scoped Admin API, roles |

The Command Center's *design* is central to the identity, but a Command Center without KPIs and audit
would be an empty room; so the shell ships a **reduced Command** at P1 (Pulse, agent sentence, Needs You,
queue) and gains the KPI story at P2.

---

## C3. UX patterns

1. **Needs You first.** The interface is ordered by what the person must decide. The bell, the rail
   badge, the hero sentence and the tray all say the same number.
2. **Simple by default, precise on demand.** Every editor shows **Essentials** first (the fields that
   produce a working result) and folds the rest under **Fine-tune** (an accordion). A client can run
   with defaults; the schema already has cautious ones.
3. **Explain, don't expose.** *Why?* shows what the agent knew, which of the client's rules applied and
   what it did (19.6). No model internals, no prompts, no scores.
4. **Draft, Review, Publish.** Every configuration edit (offers, playbook, settings) changes a **draft**,
   never the live agent.
   - The **unpublished-changes bar** appears at the first edit and follows the user.
   - **Review** (a lg modal) lists changes **in plain words per section** ("Foundation: price changed",
     "Follow-up: a third reminder was added"), then runs validation: the **two validation passes** show
     as one list of every problem with links to the fields. Publishing is disabled while any remain.
   - **Publish** creates a numbered version (a stored version is never rewritten), with an optional note.
     The toast offers **Roll back**. History lives in Settings, Versions, with Diff.
   - The screen tells the truth about timing: the agent applies the new version at its next cycle (the
     runtime-refresh design is still a proposal, so the API must report the applied version; the UI shows
     "Live: v7, Draft: v8").
   - Roles: viewers cannot edit; editors can publish, within the limits below; owners can change limits.
5. **Guardrails are visible.** A value bounded by the platform draws its limit (19.3). "Clients can
   tighten, never loosen" becomes a picture.
6. **Verbatim is a promise.** Exact values are marked, previewed as a prospect will see them, and marked
   again inside the conversation (12.5, 18.2).
7. **Name who did it.** Every action shows its author: the agent (Aether), a named colleague (Ice with
   initials), the system (neutral). Confirmations and edits carry a person's name.
8. **Undo over confirm.** Reversible actions happen immediately with **Undo**; confirmation dialogs are
   reserved for irreversible acts (confirm a payment, reinstate nothing that opted out, discard a
   long draft).
9. **Permission-aware.** Hidden when irrelevant to the role, **disabled with a reason** when the person
   might wonder why.
10. **Test before you trust.** Connections are tested with a step-by-step result before saving. (Future:
    **Rehearsal**, a sandbox conversation in Observe mode to try a Playbook before publishing.)
11. **Author with sentences, not syntax.** Rules are written as forms that read as sentences ("When a
    prospect asks about ___, then ___"), with pickers for the client's own products, markets and
    languages. No YAML, JSON, regex or ids visible. The schema stays underneath.
12. **Certainty is always visible.** Facts carry rings; guesses never read as facts (15.5).
13. **Freshness is honest.** Data shows when it was last updated; polling (about 10 s on live screens,
    slower elsewhere) never blanks or flashes the screen; a small "Live" dot when the connection is live.
14. **Progressive first run.** A new client sees the Launch Runway, not an empty dashboard; each screen's
    empty state is the next step of that runway.
15. **One primary action per view**, always in the same place.

---

## C4. Screen compositions

Wireframes are structural, not pixel-exact. `▓` marks the hero of the screen.

### C4.1 Command Center (desktop, 1440)

```
┌ rail ┐ ┌ Context bar: "Command"        ◉ Working…    [Ask me first]   ⌘K   ●2   (SA) ─┐
│ (◉)  │ │                                                                              │
│ Naga │ │   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  │
│      │ │   ▓   (  Pulse 160  )   “Good morning. I'm working through 14        ▓  │
│ Setup│ │   ▓                      prospects today. Two need you.”             ▓  │
│ ──── │ │   ▓                      Daily budget ◔ 14/20    Next: Karim at 11:00 ▓  │
│ Run  │ │   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  │
│ ▎Cmd │ │                                                                              │
│  Need│ │   Needs you (2)                                              See all ›      │
│  Chat│ │   ┌ Draft to approve · Amira ───────┐ ┌ Payment to confirm · Leila ──────┐   │
│  Pros│ │   │ “A reply about payment options” │ │ “Says she paid and sent proof”   │   │
│  Perf│ │   │ [Approve and send] Edit Decline │ │ [Review and confirm]             │   │
│ ──── │ │   └─────────────────────────────────┘ └──────────────────────────────────┘   │
│ Shape│ │                                                                              │
│  Offr│ │   This week                          7 days ▾                                 │
│  Play│ │   Prospects 214   Replies 96   Appointments —   Conversions —                │
│  Sett│ │   ╱╲╱╲▁▂▃▅   ╱╲╱╲▁▂▃▅   not tracked yet   not tracked yet                    │
│      │ │   ━━━━ Funnel Ribbon ━━━━━━━━━━┓                                             │
│      │ │                                                                              │
│      │ │   Today, on the Day Thread ─────────────────  Now ─────────  Next up        │
└──────┘ └──────────────────────────────────────────────────────────────────────────────┘
```

The hero is the only L3 surface besides the Needs You cards. The Aurora tints amber at the lower right
while cards wait.

### C4.2 Command Center (mobile, 390)

```
┌────────────────────────┐
│ Command      ◉    ⌘   │   title, Mini Pulse, palette
│                        │
│      ( Pulse 96 )      │
│  “I'm on 14 prospects. │
│   Two need you.”       │
│  ◔ 14/20 today         │
│                        │
│ Needs you (2)          │
│ ┌────────────────────┐ │
│ │ Draft · Amira      │ │
│ │ [Approve and send] │ │
│ └────────────────────┘ │
│ ┌────────────────────┐ │
│ │ Payment · Leila    │ │
│ └────────────────────┘ │
│ ‹ 214  │ 96  │ — ›     │   KPI snap row
│                        │
│ Today                  │
│ 09:02 ◉ Wrote to…      │
│ ── Now ─────────────── │
├────────────────────────┤
│ Cmd  Need  Chats Pros ⋯│   bottom tab bar
└────────────────────────┘
```

### C4.3 Needs You

```
Needs you                                   [All ▾ Drafts · Handoffs · Payments · Failed]
┌ ▎Draft to approve ────────────────────────────────────────────────────  waiting 12 min ┐
│  (AB) Amira B.  “A reply about the payment options.”                                   │
│  ┌ draft ───────────────────────────────────────────────────────────────────────────┐  │
│  │ …  [price ⓛ]  …  (verbatim values dotted-underlined)                             │  │
│  └──────────────────────────────────────────────────────────────────────────────────┘  │
│  [Approve and send]   Edit   Decline                    J K move · A E D · Enter open   │
└────────────────────────────────────────────────────────────────────────────────────────┘
┌ ▎Payment to confirm ───────────────────────────────────────────  waiting 1 h ┐
│  (LM) Leila M.  “Says she paid and sent a receipt.”  ◌╌╌◌╌╌○ Not verified yet    │
│  [Review and confirm]  (only for people who can confirm payments)                 │
└────────────────────────────────────────────────────────────────────────────────────┘
```

### C4.4 Prospects: River with the Dossier inspector

```
Prospects   [River | Ledger]   search…   filters: Needs you · Offer · Market          [+ Add]
┌ Unsorted ┐┌ Discovery ┐┌ Recommended ┐┌ Path: payment ┐┌ Won ┐     ┌ Dossier ──────────────┐
│ ○ ○      ││ ○ ○ ○     ││ ○ ○         ││ ◉ amber       ││ ○ ○ │     │ (AB) Amira B.         │
│ ○        ││ ○ ○       ││ ○           ││               ││     │     │ ● Waiting for you     │
│    5     ││    9      ││    4        ││     2         ││  3  │     │ What I know           │
└──── the current, lit toward Won ──────────────────────────────┘     │  ● Goal: start online │
                                                                       │  ◔ Country: Tunisia   │
                                                                       │  ◌ Budget (guess)     │
                                                                       │ Path ▸▸▸▹▹            │
                                                                       │ Thread…               │
                                                                       └───────────────────────┘
```

### C4.5 Conversation

```
┌ Conversations ┐ ┌─────── Amira B. ────────────────────────────┐ ┌ Agent's notes ───────┐
│ Needs you  ●2 │ │ ┌ Handed to you: “She asked for a discount.” │ │ Learned               │
│ Amira B.  ▲   │ │ └ [I'll take it]                              │ │  ● Goal: start online │
│ Karim S.      │ │            ┌ Hello Amira, … ┐  agent          │ │    “I want to start…” │
│ Leila M.      │ │ prospect ┌ What does it… ┐                    │ │ Decided               │
│               │ │            ┌ Foundation is [price] … ┐ agent  │ │  “Answered first, then│
│               │ │ ┌╌ Draft · waiting for you ╌╌╌╌╌╌╌┐          │ │   asked one question” │
│               │ │ │  Approve and send · Edit · Decline│          │ │ Payment               │
│               │ │ └╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┘          │ │  ◌╌╌◌╌╌○  unverified  │
└───────────────┘ └──────────────────────────────────────────────┘ └───────────────────────┘
```

### C4.6 Offer editor (Pricing section, Verbatim field)

```
Offers › Foundation                                   Draft · 2 unpublished changes   [Review]
┌ Sections ───────┐ ┌ Pricing ─────────────────────────────────────────────────────────────┐
│ Essentials  ✓   │ │ How do you price this offer?   ( Fixed ) On request   Free            │
│ Audience    ✓   │ │                                                                        │
│ ▎Pricing        │ │ Default price                                                          │
│ Payment     ◔   │ │ ┌ 1 500 ┐ ┌ TND ▾ ┐    [lock] verbatim: shown exactly as written.          │
│ Sales path  ○   │ │                                                                        │
│ Limits      ○   │ │ Different price for a market or country              [+ Add a price]   │
└─────────────────┘ │  Tunisia (market)      ┌ 1 200 ┐ TND     ⋯                              │
                    │                                                                        │
                    │ Preview  ┌ “Foundation is 1 200 TND.” ┐  as a prospect will read it     │
                    └────────────────────────────────────────────────────────────────────────┘
```

(Values are placeholders for layout only. No real Naga price exists in the repository.)

### C4.7 Playbook: Follow-up cadence

```
Playbook › Follow-up                                                        [Review changes]
When a prospect goes quiet after I've explained an offer…

  first message ──── 2 days ──── 5 days ──── 9 days ┄┄┄ (limit: 3 touches, set by your platform)
       ◉               ◉            ◉            ◉                          ▒▒▒ locked ▒▒▒
   “Reminder”     “Add value”   “Last check”     
  ▒▒ minimum gap: 1 hour, set by your platform ▒▒

  Stop following up when: [asked to stop] [not interested] [+ add]      Fine-tune ▾
```

### C4.8 Settings: Schedule and limits (with the Autonomy Dial)

```
Settings › Schedule and limits
 ┌ Working days ─────────────┐  ┌ Autonomy ───────────────────────┐
 │ M T W T F S S   Africa/Tunis│ │           ( Dial )              │
 │ Start 09:00   End 18:00   │  │ Ask me first                    │
 └───────────────────────────┘  │ “The agent proposes, you approve”│
 ┌ Daily limit ──────────────┐  │ [lock] Autonomous: set by platform   │
 │ ──────●──────▒▒▒▒▒▒▒ 20   │  │   [Request Autonomous]          │
 │ up to 40 (platform limit) │  └─────────────────────────────────┘
 └───────────────────────────┘
 Opt-out words   [stop] [+]
```

### C4.9 Launch Runway (first run)

```
Welcome, Naga. Let's get your agent ready.                          Ask me first is recommended
   ◉━━━━━━━◉━━━━━━━◌ ┄┄ ◌ ┄┄ ◌ ┄┄ ◌
 Sheet    Offer   Payment  Voice  Path   Go live
 ✓        ▎now     ○        ○      ○      ○
┌ Add your first offer ────────────────────────────────────────────────────────────────────┐
│ “Teach me what you sell. Start with a name and one sentence.”     [Add an offer]         │
│ Still missing: a price (or 'On request'), who it's for, one benefit.                     │
└──────────────────────────────────────────────────────────────────────────────────────────┘
 (Pulse, hollow, resting: Not live yet)
```

### C4.10 Review and publish

```
┌ Review changes · Draft based on v7 ─────────────────────────────────────────────── ✕ ┐
│  3 changes                                                                             │
│  Offers › Foundation      Price changed for Tunisia                       View diff ›  │
│  Playbook › Follow-up     A third reminder was added                       View diff ›  │
│  Settings                 Daily limit 15 → 20                              View diff ›  │
│                                                                                        │
│  Checks   ✓ Structure   ✓ References   ⚠ 1 problem                                     │
│   • Offers › Foundation › Pricing: add a price or choose "On request"     Fix ›        │
│                                                                                        │
│  Note (optional) ┌──────────────────────────────┐                                      │
│  Cancel                                                     [ Publish v8 ]  (disabled) │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## C5. What the UI can show today

Designing the whole product is one thing; not misleading the user is another. Each element is tied to
what the repository can already provide.

| Capability | Source in the repository today | Status | UI treatment |
|---|---|---|---|
| Agent state (Pulse) | `/health/clients`: mode, `mode_clamped`, config version, per-job success and failure | Partial | Admin API must return a computed `agent.state` (enabled, effective mode, pause, schedule, queue, health) |
| Prospects, status, stage, last interaction, next action | `SalesRepository` (Sheet) | Data exists | needs paged Admin API read |
| Conversation history | Messages tab through `SalesRepository` | Data exists | needs Admin API read |
| Actions and approvals (`proposed`, `approved`...) | Actions tab, statuses in the Core | Data exists | needs `ApprovalService` and endpoints |
| Facts with source and evidence | `Fact`, `FactEvidence`, Facts tab | Data exists | needs Admin API read |
| Payment progress, confirmation | `payment_progress`, `PaymentConfirmationService` | Core exists, no HTTP | needs an authenticated endpoint that builds `PaymentConfirmation` and enforces `payments.confirm` |
| Configuration read, write, versions, diff, rollback | `ConfigStore`, `config` CLI | Exists, no HTTP | Admin API `ConfigService` |
| Form definitions | `ConsultativeSalesSpec.json_schema()` | Exists | expose through the API with the settings version |
| Readiness | `check_readiness` returns **strings** | Exists | needs **structured items** (`path`, `code`, `gate`) so the Runway can map gates and write friendly sentences |
| Connection test (Sheet) | none | Missing | Admin API operation with the step results of 12.4 |
| KPIs | none (`sales_core.kpi` is future) | Missing | Command Center KPIs and Performance wait; KPI components ship with **Not tracked yet** and loading states |
| Appointments, conversions, objections, feedback, non-conversion reasons | not modelled | Missing | **Not tracked yet** state (14.6), ghost Ribbon segments |
| Audit read (Why?, Agent's notes, Thread narration) | `append_audit_events` only, no read | Missing | v1 uses facts and actions only; notes and Why? are reduced until the read capability exists |
| Narration sentences (Agent Voice) | none | Missing | **v1 decision: deterministic templates per event type, produced by the API in the interface language. No language model writes UI narration.** Insight cards beyond rule-based ones are later |
| Verbatim spans in messages | placeholders are expanded by deterministic code | Needs API marking | the API must return spans (offset, length, kind) for inserted exact values |
| Media and proof thumbnails | `conversation.media_handling` not implemented | Missing | "Possible proof" marker only when the API says so; otherwise a file-received chip |
| Live updates | none | Missing | polling in v1 (about 10 s); server-sent events later |
| Users, roles, sign-in | none (Admin API future) | Missing | the UI consumes the session and permissions from the API |
| Activation (`enabled`) | operator wiring in `client.yaml` | Operator-owned | the Runway's last gate is a **request**; the operator approves |

**Consolidated asks for the Admin API design step** (so the UI is not blocked later): typed OpenAPI with
generated client; paged read endpoints with server-side filters and sort; an `agent` summary object;
structured readiness; connection-test operation; verbatim spans; per-item `direction` for KPIs;
comparison period in every KPI response; staleness timestamps; `permissions` list on the session;
error responses carrying the validation **paths** of both passes; applied-configuration-version reporting.

---

## C6. Reuse for the Multi-Client Admin

The design system is built so the operator's admin is a **second app on the same packages**, not a
redesign.

### C6.1 What is shared, what is added

| Shared unchanged | Added for the operator |
|---|---|
| `tokens`, `ui`, `ui-agent`, `api-client`, `i18n` | a `TenantProvider` and a **client switcher** in the Context Bar |
| Pulse, StatusChip, CertaintyChip, tables, forms, drawers, toasts | **Fleet Constellation**: every client as a Mini Pulse in a grid with its state, Needs You count and mode, sortable |
| Offer, Playbook and Settings editors (same schema, same ConfigStore) | operator-only screens: **Wiring** (Brain package, providers, WhatsApp account ids), **Secrets** ("set / not set", replace, never revealed), **Guardrail caps** and **mode ceiling**, **Activation** (approve a client's request, the `enabled` switch), cross-client audit |
| Review and publish flow | a stricter variant: the target client is named in the Review header and the confirm button |

### C6.2 Making it impossible to confuse contexts

- The **Operator chrome** re-tints the Aurora to cool steel (no violet dominance), adds a persistent
  "Operator" marker, and always shows the **client chip** (name, logo) on the Context Bar. In a client
  editor, the chip is also in the page header. A person managing many clients must know which one they
  are changing without reading.
- **Density**: the operator app defaults to the compact Ledger for fleets; the client console keeps the
  airy default. Same components, `data-density` differs.
- Operator-only capabilities exist only in the operator app and its API routes (A3.6), not as hidden
  toggles in the client console.

### C6.3 Tenant theming, with limits

A tenant may supply a **logo**, a **display name** and, optionally, an **accent** that replaces the Ice
hue used for actions. The accent is validated by a generator that must keep **4.5:1** text contrast on the
primary fill and 3:1 for focus on every surface, else it is rejected. **The meaning colors are platform
constants**: Aether always means the agent, Emerald success, Amber attention, Rose danger. A client cannot
recolor meaning.

### C6.4 Reuse checklist (release gate for the client console)

- [ ] `grep` finds no client name in `tokens`, `ui`, `ui-agent`
- [ ] Every `ui-agent` story renders with **two different tenants** (name, logo, accent) and with RTL
- [ ] No component takes an API type; all take view-models
- [ ] No literal user-visible string in tiers 1 to 3
- [ ] Every operator-only concept is absent from `apps/console`
- [ ] Editors are generated from the schema version, not from a Naga-specific list of fields
- [ ] The contrast test passes for the default and a sample tenant accent

---

## C7. Build plan

| Phase | Deliverable | Needs | Exit criteria |
|---|---|---|---|
| **0. Foundation** | `web/` workspace; `tokens` (CSS, TS, **contrast test**); Tailwind mapping; Storybook; the primitives and glass levels; Aurora, grain, Pulse (states); fonts; CI with axe and visual regression | nothing (no API) | tokens documented equal to sections 2 to 9; worst-case contrast test green; glass fallbacks tested (reduced transparency, forced colors, no `backdrop-filter`); Pulse 60 fps on a throttled profile |
| **1. Configure** (P0) | shell, navigation, i18n, permission gating; **Launch Runway**, Offers (Offers, Markets, Payment), Playbook chapters, Settings, Draft/Review/Publish, Versions | Admin API Configuration (mocked with MSW from the real schema until it exists) | Naga can enter its whole catalog and commercial settings, publish, see structured errors, and roll back; keyboard-only run of "add an offer and publish"; NVDA and VoiceOver pass |
| **2. Operate** (P1) | Needs You, Prospects (River, Ledger, Dossier), Conversations, drafts, handoff, **Payment Seal**, reduced Command | Admin API CRM, approvals, payment confirmation endpoint | approve, take over and confirm-payment flows pass the keyboard and screen-reader scripts; River accessible as lists |
| **3. Understand** (P2) | full Command Center, Performance, Funnel Ribbon, Day Thread with narration, Why? | KPI computation, audit read | no KPI computed client-side (test); Not tracked yet states verified; chart table views |
| **4. Fleet** (P3) | operator app: switcher, Fleet Constellation, operator screens | tenant-scoped Admin API and roles | C6.4 checklist passes |

**Performance budgets**: initial JavaScript under 250 KB gzipped for the shell (routes lazy-loaded); LCP
under 2 s on a mid-range laptop; at most six `backdrop-filter` elements visible; `data-perf="lite"`
engages automatically when the frame rate drops below 40 fps for 3 s (and can be set manually).

**Testing the tokens** (Phase 0): a unit test recomputes WCAG contrast for every pair in section 2.3 with
the glass composited over the brightest aurora point, and fails the build below the stated threshold.

---

## C8. Open decisions

Recorded so they are decided on purpose, not by accident.

| # | Decision | Recommendation |
|---|---|---|
| 1 | **UI locales** for Naga (French, English, Arabic?). The system already supports RTL by construction | decide with Naga; ship one, keep all three unblocked |
| 2 | **Light theme ("Daylight")** | not in v1; the semantic token layer keeps it possible. Revisit after Phase 2 |
| 3 | **Brand**: is the platform name shown next to the client's? | tenant logo and name in the rail; a small platform wordmark in the footer of the rail |
| 4 | **Narration source** | deterministic templates in v1 (C5); revisit an assisted narration only with the audit read and a review process |
| 5 | **Who can activate** | operator only (`enabled` is wiring); the UI requests |
| 6 | **Manual stage override** in the Dossier | allowed as a labelled, audited action; no drag in the River |
| 7 | **Team and roles inside the client console** | operator-managed in v1; read-only list in Settings, Team |
| 8 | **Live updates** | polling first; SSE when the Admin API exists |
| 9 | **Font licences**: Geist, Instrument Serif, IBM Plex Sans Arabic, Noto Naskh Arabic are open licences | confirm at Phase 0 and record in the repository |
| 10 | **Rehearsal** (sandbox conversation) and **Agent-assisted authoring** (draft an offer from a page) | future, out of scope; both must keep exact values out of any model (the same rule as today) |
