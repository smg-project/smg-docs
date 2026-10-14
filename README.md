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

- **Main docs pages** live in `src/lib/content/<section>/**/*.md` (`getting-started`, `concepts`, `reference`, `contributing`). mkdocs-material syntax — `=== "Tab"` blocks, `!!! note` admonitions, `??? question` collapsibles, card grids — is supported by the pipeline in `src/lib/markdown/`.
- **Navigation** is data in `src/lib/config/*-nav.ts`.
- **Home and section headline copy** is seeded into D1 (`content_blocks` table) by the migrations in `drizzle/`, with static fallbacks in `src/lib/content/defaults.ts`.
- Every docs page links back to its source file in this repository via the **Edit** button.

## Documentation versions

The header's **Version** menu switches between SMG releases and `main`, following
the [Kueue documentation](https://kueue.sigs.k8s.io/docs/) pattern. All versions
are included in one Cloudflare deployment:

- `/smg/v1.13.0/getting-started` serves the documentation prepared for SMG
  **v1.13.0**, snapshotted from the current main docs. It is the default version.
  Its content, navigation, and diagrams are stored in `src/lib/versions/v1.13.0/`
  and `static/versions/v1.13.0/images/`.
- `/smg/v1.12.0/getting-started` serves the existing documentation captured for
  SMG **v1.12.0**. Its Markdown, navigation, URL aliases, and diagrams are stored
  independently in `src/lib/versions/v1.12.0/` and `static/versions/v1.12.0/images/`.
- `/smg/main/getting-started` serves the working content in `src/lib/content/` for
  SMG's latest **main branch**, with a development notice. It currently matches
  the v1.13.0 snapshot; subsequent main-branch documentation updates go here.
- Existing unversioned documentation and search URLs redirect to the latest
  documented release, currently v1.13.0.
- All documentation versions share the same version-neutral homepage at `/smg`.
  Its content and navigation are shared, with no version selector or release
  banner. Documentation links on the homepage use the unversioned entry URLs;
  the site logo returns here from every docs version.

Switching versions keeps the page, query, and anchor when that page exists in the
target version. Otherwise it opens that section's overview. Internal links and
Edit/source buttons use the selected version. Unknown versions and missing pages
return 404 rather than serving another version's content.

To verify content separation locally, open
`/smg/v1.13.0/reference/configuration#streaming-keepalives`. The **Streaming
Keepalives** section and `--sse-keepalive-secs` flag are present in v1.13.0 and
main, but absent from v1.12.0. This documents
[SMG #3241](https://github.com/smg-project/smg/pull/3241), merged after v1.12.0.
Switch versions on that page to compare the content and table of contents.

Release snapshots are outside the nightly updater's allowed `src/lib/content/`
tree. The existing updater still uses stable-release evidence: it can maintain
released behavior in main docs, but documenting unreleased changes requires
reviewing SMG main and editing the working content. Versioning does not itself
audit or import upstream changes.

To publish a future release after reviewing the working docs against its SMG tag:

```sh
pnpm docs:snapshot vX.Y.Z
pnpm exec prettier --write src/lib/config/docs-releases.json src/lib/versions/vX.Y.Z/manifest.json
pnpm test:docs
pnpm check
pnpm build
```

The snapshot command copies Markdown, navigation, URL aliases, and images, then
registers the release as the default in `src/lib/config/docs-releases.json`. It
refuses to overwrite an existing snapshot. Commit all generated files together.
Keep snapshots fixed except for intentional, version-specific corrections; the
site's rendering and styling remain shared across versions.

`pnpm test:docs` checks every page, navigation, version isolation, Markdown links,
and diagrams. With `pnpm dev` running, `pnpm test:docs:http` also checks the
rendered server responses, legacy redirects, and 404s. Pass a different origin
(e.g. `pnpm test:docs:http http://127.0.0.1:4173`) to test a production preview.

## Deployment

Every push to `main` runs `.github/workflows/deploy.yml`: build, apply D1 migrations to `smg-db`, and deploy to the `smg` Cloudflare Pages project. The production URL is [lightseek.org/smg](https://lightseek.org/smg); `smg-anw.pages.dev` is the underlying Pages origin, and its root redirects into `/smg`.

The header badge reads stars, forks, and the latest release from the GitHub API at request time. Set a `GITHUB_TOKEN` secret on the Pages project (`wrangler pages secret put GITHUB_TOKEN --project-name smg`; any token that can read public repositories works) so those calls are authenticated: unauthenticated calls share GitHub's 60-requests-per-hour limit per egress IP with every other Worker on Cloudflare, and the badge hides its counts whenever GitHub cannot be reached. Results are cached per edge location for 15 minutes and served stale for up to a day while refreshes fail. Locally, `pnpm preview` reads the token from `.dev.vars`.

CI (`.github/workflows/ci.yml`) runs lint, type checks, and a production build on every pull request. Branches follow `<type>/<description>` naming, PR titles follow Conventional Commits, and commits are DCO-signed.

## Design

The identity and site were designed by [Studio Noiich](https://studio-noiich.com) through a two-round concept process (May–June 2026), positioned deliberately at the interaction of tech and the humanities & arts.

The visual anchor is **Piet Mondrian**: his work distills complex reality into a perfect geometric equilibrium, with the grid standing for universal order and structural clarity. That maps directly onto what SMG does with inference traffic — orchestration as geometric equilibrium, efficiency as structural clarity — and the grid discipline runs through the whole site.

- **Symbol** — a gateway arch set into a Mondrian-like grid, progressively reduced until only the rounded arch remains: the mark that anchors the home hero and the favicon.
- **Color** — solid tech black and warm paper off-whites around the signature **radial orange glow**. Orange was chosen for its warmth — the humanities-and-arts half of the pairing, balancing the cool precision of the tech side — and the glow renders live as the home hero's shader background.
- **Typography** — [Changa](https://fonts.google.com/specimen/Changa) for the wordmark and display, [Inter](https://rsms.me/inter/) for text.

## Nightly documentation maintenance

The [nightly documentation workflow](scripts/doc-sync/README.md) audits SMG code
changes against the current docs on `smg-org-runner-cpu`. It opens separate draft
PRs for individual concerns, with a changed-line limit, duplicate prevention, and
scope, type-check, and build validation before publication.
