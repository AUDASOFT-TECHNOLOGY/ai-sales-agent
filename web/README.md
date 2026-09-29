# REVENSA web (visual prototype)

> **Turn conversations into revenue.**

A **visual prototype** of the REVENSA client console, built to judge the art direction ("Nocturne") before the real
application is developed. It implements the design system in
[../docs/naga-client-ui-design-system.md](../docs/naga-client-ui-design-system.md).

**Everything here is sample data.** No API, no Google Sheets, no WhatsApp, no OpenAI, no Python backend. The data lives in
[`src/mock/`](src/mock/) and is clearly isolated; the interface says so ("Sample data"). The Python backend and the Core
contracts are untouched.

## Run

```
cd web
npm install
npm run dev          # http://127.0.0.1:5173
```

The app opens on the **Command Center** (the demo client is already onboarded). **Setup** is the Launch Runway, the first-run onboarding: open it with *Prototype, Restart onboarding*. *Prototype* in the top bar opens the demo controls: force any
agent state, switch role (owner / viewer), simulate a failing connection, jump to the live demo, restart onboarding.

## Checks

```
npm run check:all    # typecheck, lint, WCAG contrast of the design tokens, production build
node scripts/audit.mjs   # (dev server running) axe accessibility, interactions, overflow at 5 widths, frame rate
node scripts/shots.mjs   # screenshots at desktop, laptop, tablet, mobile and RTL into .verify/
```

The audit and screenshot scripts drive the installed Google Chrome through `playwright-core` (no browser download).

## Layout

| Path | Contents |
|---|---|
| `src/styles/` | `tokens.css` (design tokens), `base.css` (type, Aurora background), `glass.css` (glass levels), component CSS, then `calm.css` (the simplification pass: space, one column, details on demand) |
| `src/ui/` | primitives and patterns: Glass, Button, Chip, Bead, controls, overlays, toasts, tabs |
| `src/agent/` | domain components: Pulse, Autonomy Dial, Certainty rings, Payment Seal, KPI, Funnel Ribbon, Day Thread, Opportunity River, conversation |
| `src/shell/` | app shell: rail, context bar, tab bar, command palette, publish bar |
| `src/screens/` | Runway, Command, Prospects, Conversations, Configure |
| `src/mock/` | **sample data and the in-memory state that simulates the API** |
| `src/prototype/` | demo-only controls |
| `src/i18n/` | messages (English, partial Arabic to prove RTL) |

## Differences from the plan in the design document

The design document proposes a pnpm workspace with Tailwind. For the prototype this is a single package with plain CSS
built on the same tokens, to keep it light. The tiers (`ui`, `agent`, `mock`) mirror the planned packages, so the split
is mechanical later.
