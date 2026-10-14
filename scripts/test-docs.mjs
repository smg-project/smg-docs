import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createServer } from 'vite';

const server = await createServer({
	server: { middlewareMode: true, hmr: false, ws: false, watch: null },
	appType: 'custom'
});
try {
	const { docsVersions, defaultDocsVersion, docsHref, docsPath, isDocsVersion } =
		await server.ssrLoadModule('/src/lib/config/docs-versions.ts');
	const { getDocsManifest, getDocsNav, mainManifest, versionSwitchPath } =
		await server.ssrLoadModule('/src/lib/config/docs-manifests.ts');
	const { loadDocPage } = await server.ssrLoadModule('/src/lib/server/docs.ts');
	const { rewriteDocLinks, rewriteAssetPaths } = await server.ssrLoadModule(
		'/src/lib/markdown/links.ts'
	);
	const { renderDocMarkdown } = await server.ssrLoadModule('/src/lib/markdown/doc.ts');
	assert.ok(isDocsVersion(defaultDocsVersion));
	assert.notEqual(defaultDocsVersion, 'main');
	assert.equal(isDocsVersion('v0.99.0'), false);
	assert.equal(docsPath('/smg/v1.12.0/reference/api/openai/'), 'reference/api/openai');
	assert.equal(
		docsHref('/smg/v1.12.0/reference/api/openai', 'main'),
		'/smg/main/reference/api/openai'
	);
	assert.equal(versionSwitchPath('/smg/main/reference/missing', 'v1.12.0'), 'reference');
	assert.equal(versionSwitchPath('/smg', 'main'), 'getting-started');
	assert.equal(versionSwitchPath('/smg/main/search', 'v1.12.0'), 'search');
	assert.equal(
		versionSwitchPath('/smg/main/getting-started/mcp-in-responses-api', 'v1.12.0'),
		'getting-started/mcp-in-responses-api'
	);
	assert.throws(
		() => loadDocPage('v0.99.0', 'concepts'),
		(error) => error.status === 404
	);
	assert.throws(
		() => loadDocPage('main', 'concepts', 'missing/overview'),
		(error) => error.status === 404
	);
	assert.equal(loadDocPage('v1.12.0', 'concepts').sourcePath, 'concepts/index.md');

	// Actual release content must stay isolated when the same page changes later.
	for (const version of ['v1.12.0', 'v1.13.0', 'main']) {
		const config = loadDocPage(version, 'reference', 'configuration');
		assert.equal(
			config.content.includes('--sse-keepalive-secs'),
			version !== 'v1.12.0',
			`${version} has the expected streaming keepalive documentation`
		);
		assert.equal(
			versionSwitchPath('/smg/v1.13.0/reference/configuration', version),
			'reference/configuration'
		);
	}

	// A new main-only page and navigation change cannot leak into an existing release.
	const released = getDocsManifest('v1.12.0');
	const originalLabel = mainManifest.navigation.reference.indexLabel;
	mainManifest.pages['reference/main-only'] = 'reference/main-only.md';
	mainManifest.navigation.reference.indexLabel = 'Main reference';
	assert.equal(released.pages['reference/main-only'], undefined);
	assert.equal(released.navigation.reference.indexLabel, 'Reference');
	assert.equal(versionSwitchPath('/smg/main/reference/main-only', 'v1.12.0'), 'reference');
	delete mainManifest.pages['reference/main-only'];
	mainManifest.navigation.reference.indexLabel = originalLabel;

	let pages = 0;
	for (const { id: version } of docsVersions) {
		const manifest = getDocsManifest(version);
		assert.ok(manifest && Object.keys(manifest.pages).length > 0);
		for (const [route, file] of Object.entries(manifest.pages)) {
			const [section, ...segments] = route.split('/');
			const page = loadDocPage(version, section, segments.join('/'));
			const source =
				version === 'main'
					? `src/lib/content/${file}`
					: `src/lib/versions/${version}/content/${file}`;
			assert.equal(page.content, await readFile(source, 'utf8'));
			assert.equal(page.contentSourcePath, source);
			const html = renderDocMarkdown(page.content, file, version);
			assert.ok(html.includes('<h1'), `${version}/${route} has a heading`);
			assert.doesNotMatch(
				html,
				/href="\/smg\/(?:getting-started|concepts|reference|contributing)(?:\/|")/
			);
			if (version !== 'main') assert.doesNotMatch(html, /src="\/smg\/images\//);
			pages++;
		}
		for (const section of Object.keys(manifest.navigation)) {
			const nav = getDocsNav(section, version);
			for (const href of [
				nav.indexHref,
				...nav.sections.flatMap((group) => group.items.map((item) => item.href))
			]) {
				assert.ok(href.startsWith(`/smg/${version}/`));
				assert.ok(manifest.pages[docsPath(href)], `Navigation points to an existing page: ${href}`);
			}
		}
		assert.equal(
			rewriteDocLinks(
				'[MCP](../../getting-started/mcp.md?view=all#tools)',
				'reference/api/responses.md',
				version
			),
			`[MCP](/smg/${version}/getting-started/mcp-in-responses-api?view=all#tools)`
		);
		assert.equal(
			rewriteDocLinks('[Overview](index.md)', 'concepts/reliability/retries.md', version),
			`[Overview](/smg/${version}/concepts/reliability)`
		);
		assert.equal(
			rewriteDocLinks(
				'[Guide](https://lightseek.org/smg/reference/configuration/#workers)',
				undefined,
				version
			),
			`[Guide](/smg/${version}/reference/configuration#workers)`
		);
		assert.equal(
			rewriteDocLinks('[Docs](/smg/concepts)', undefined, version),
			`[Docs](/smg/${version}/concepts)`
		);
		const external =
			'[Source](https://github.com/smg-project/smg/blob/main/README.md) [Anchor](#test)';
		assert.equal(rewriteDocLinks(external, undefined, version), external);
		const images = version === 'main' ? '/smg/images' : `/smg/versions/${version}/images`;
		assert.equal(
			rewriteAssetPaths('![Diagram](../../assets/images/architecture-detailed.svg)', version),
			`![Diagram](${images}/architecture-detailed.svg)`
		);
		const blocks =
			'!!! note\n    [Guide](../getting-started/mcp.md)\n\n=== "Tab"\n\n    [Config](configuration.md)';
		const html = renderDocMarkdown(blocks, 'reference/index.md', version);
		assert.ok(html.includes(`href="/smg/${version}/getting-started/mcp-in-responses-api"`));
		assert.ok(html.includes(`href="/smg/${version}/reference/configuration"`));
	}
	console.log(
		`Documentation checks passed: ${pages} pages across ${docsVersions.length} versions, navigation, isolation, links, assets, and fallbacks.`
	);
} finally {
	await server.close();
}
