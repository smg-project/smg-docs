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

Brand identity and site design by [Studio NOIICH](https://studio-noiich.com). Typography: [Changa](https://fonts.google.com/specimen/Changa) and [Inter](https://rsms.me/inter/).
