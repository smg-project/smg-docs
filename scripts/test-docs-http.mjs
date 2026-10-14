import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

// Run against a development server or the production preview; inspect SSR before hydration.
const origin = process.argv[2] ?? 'http://127.0.0.1:5173';
const releases = JSON.parse(await readFile('src/lib/config/docs-releases.json', 'utf8'));
const latest = releases[0];
let pages = 0;
for (const version of [...releases, 'main']) {
	// The latest snapshot supplies the released route baseline for main.
	const snapshot = version === 'main' ? latest : version;
	const manifest = JSON.parse(await readFile(`src/lib/versions/${snapshot}/manifest.json`, 'utf8'));
	for (const route of Object.keys(manifest.pages)) {
		const response = await fetch(`${origin}/smg/${version}/${route}`);
		assert.equal(response.status, 200, `${version}/${route}`);
		const html = await response.text();
		assert.ok(html.includes(`Documentation version: ${version}`));
		assert.ok(
			html.includes(`href="/smg/${version}/concepts"`),
			'Header preserves the version during SSR'
		);
		assert.ok(html.includes(`· ${version} · Shepherd Model Gateway</title>`));
		const source = version === 'main' ? 'src/lib/content/' : `src/lib/versions/${version}/content/`;
		assert.ok(
			html.includes(
				`https://github.com/smg-project/smg-docs/edit/main/${source}${manifest.pages[route]}`
			)
		);
		assert.doesNotMatch(html, new RegExp(`/smg/${version}/smg/`));
		if (route === 'reference/configuration' && ['v1.12.0', 'v1.13.0', 'main'].includes(version)) {
			assert.equal(
				html.includes('id="streaming-keepalives"'),
				version !== 'v1.12.0',
				`${version} serves its own configuration content`
			);
		}
		pages++;
	}
	for (const path of [`/smg/${version}/reference/not-a-page`, `/smg/${version}/unknown`]) {
		assert.equal((await fetch(origin + path)).status, 404, path);
	}
	const index = await fetch(`${origin}/smg/${version}?from=home`, { redirect: 'manual' });
	assert.equal(index.status, 307);
	assert.equal(index.headers.get('location'), `/smg/${version}/getting-started?from=home`);
	assert.equal((await fetch(`${origin}/smg/${version}/search`)).status, 200);
}
for (const route of [
	'getting-started',
	'concepts/reliability/retries',
	'reference/api/openai',
	'contributing/development',
	'search'
]) {
	const response = await fetch(`${origin}/smg/${route}?from=legacy`, { redirect: 'manual' });
	assert.equal(response.status, 307);
	assert.equal(response.headers.get('location'), `/smg/${latest}/${route}?from=legacy`);
}
assert.equal((await fetch(`${origin}/smg/v0.99.0/getting-started`)).status, 404);
const home = await fetch(`${origin}/smg/`);
assert.equal(home.status, 200);
assert.doesNotMatch(await home.text(), /Documentation version:|Documentation for SMG/);
const image = await fetch(`${origin}/smg/versions/${latest}/images/architecture-detailed.svg`);
assert.equal(image.status, 200);
assert.match(image.headers.get('content-type'), /image\/svg\+xml/);
console.log(
	`HTTP checks passed: ${pages} rendered pages, versioned SSR headers and source links, redirects, 404s, search, and release assets.`
);
