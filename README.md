# SMG Documentation

Documentation site for [SMG — Shepherd Model Gateway](https://github.com/smg-project/smg), the high-performance inference gateway for production LLM deployments.

**Live at [lightseek.org/smg](https://lightseek.org/smg)**

## Stack

- [SvelteKit](https://svelte.dev/docs/kit) (Svelte 5) on Cloudflare Pages, served under the `/smg` base path
- Cloudflare D1 + [Drizzle](https://orm.drizzle.team) for editable content blocks (home and section copy)
- A custom markdown pipeline that renders mkdocs-material-flavored content — tabbed blocks, admonitions, collapsibles, card grids, and a generated table of contents — via `marked` and `highlight.js`

## Development

Requires Node 22 (`pnpm node:use` installs and activates it via fnm) and pnpm 10.

```sh
pnpm install
pnpm dev                # vite dev server
pnpm db:migrate:local   # apply D1 migrations to the local database
pnpm check              # wrangler types --check + svelte-check
pnpm lint               # prettier + eslint
pnpm build              # production build
pnpm preview            # serve the built site with wrangler pages dev
```

## Editing content

- **Docs pages** live in `src/lib/content/<section>/**/*.md` (`getting-started`, `concepts`, `reference`, `contributing`). mkdocs-material syntax — `=== "Tab"` blocks, `!!! note` admonitions, `??? question` collapsibles, card grids — is supported by the pipeline in `src/lib/markdown/`.
- **Navigation** is data in `src/lib/config/*-nav.ts`.
- **Home and section headline copy** is seeded into D1 (`content_blocks` table) by the migrations in `drizzle/`, with static fallbacks in `src/lib/content/defaults.ts`.
- Every docs page links back to its source file in this repository via the **Edit** button.

## Deployment

Every push to `main` runs `.github/workflows/deploy.yml`: build, apply D1 migrations to `smg-db`, and deploy to the `smg` Cloudflare Pages project. The production URL is [lightseek.org/smg](https://lightseek.org/smg); `smg-anw.pages.dev` is the underlying Pages origin, and its root redirects into `/smg`.

CI (`.github/workflows/ci.yml`) runs lint, type checks, and a production build on every pull request. Branches follow `<type>/<description>` naming, PR titles follow Conventional Commits, and commits are DCO-signed.

## Design

The identity and site were designed by [Studio Noiich](https://studio-noiich.com) through a two-round concept process (May–June 2026), positioned deliberately at the interaction of tech and the humanities & arts.

The visual anchor is **Piet Mondrian**: his work distills complex reality into a perfect geometric equilibrium, with the grid standing for universal order and structural clarity. That maps directly onto what SMG does with inference traffic — orchestration as geometric equilibrium, efficiency as structural clarity — and the grid discipline runs through the whole site.

- **Symbol** — a gateway arch set into a Mondrian-like grid, progressively reduced until only the rounded arch remains: the mark that anchors the home hero and the favicon.
- **Color** — solid tech black and warm paper off-whites around the signature **radial orange glow**. Orange was chosen for its warmth — the humanities-and-arts half of the pairing, balancing the cool precision of the tech side — and the glow renders live as the home hero's shader background.
- **Typography** — [Changa](https://fonts.google.com/specimen/Changa) for the wordmark and display, [Inter](https://rsms.me/inter/) for text.
