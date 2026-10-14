import { gettingStartedDocsNav } from './getting-started-nav';
import { conceptsDocsNav } from './concepts-nav';
import { referenceDocsNav } from './reference-nav';
import { contributingDocsNav } from './contributing-nav';
import { fileToSlug } from '$lib/content/getting-started/slugs';
import { docsPath, docsHref, type DocsSection } from './docs-versions';
import type { DocsNavConfig } from './docs-nav';

export type DocsManifest = {
	navigation: Record<DocsSection, DocsNavConfig>;
	pages: Record<string, string>;
};

const mainFiles = import.meta.glob('/src/lib/content/**/*.md');
const snapshots = import.meta.glob('/src/lib/versions/*/manifest.json', {
	eager: true,
	import: 'default'
}) as Record<string, DocsManifest>;

function normalizeNav(nav: DocsNavConfig): DocsNavConfig {
	return {
		...nav,
		indexHref: docsPath(nav.indexHref),
		sections: nav.sections.map((section) => ({
			...section,
			items: section.items.map((item) => ({ ...item, href: docsPath(item.href) }))
		}))
	};
}

export const mainManifest: DocsManifest = {
	navigation: {
		'getting-started': normalizeNav(gettingStartedDocsNav),
		concepts: normalizeNav(conceptsDocsNav),
		reference: normalizeNav(referenceDocsNav),
		contributing: normalizeNav(contributingDocsNav)
	},
	pages: Object.fromEntries(
		Object.keys(mainFiles).map((key) => {
			const file = key.replace('/src/lib/content/', '');
			let route = file.replace(/\.md$/, '').replace(/\/index$/, '');
			if (route.startsWith('getting-started/')) {
				const name = route.slice('getting-started/'.length);
				route = `getting-started/${fileToSlug[name] ?? name}`;
			}
			return [route, file];
		})
	)
};

export function getDocsManifest(version: string): DocsManifest | undefined {
	return version === 'main'
		? mainManifest
		: snapshots[`/src/lib/versions/${version}/manifest.json`];
}

export function getDocsNav(section: DocsSection, version: string): DocsNavConfig {
	const nav = getDocsManifest(version)!.navigation[section];
	return {
		...nav,
		indexHref: docsHref(nav.indexHref, version),
		sections: nav.sections.map((group) => ({
			...group,
			items: group.items.map((item) => ({ ...item, href: docsHref(item.href, version) }))
		}))
	};
}

/** Keep the page when available, otherwise offer the corresponding section. */
export function versionSwitchPath(pathname: string, version: string): string {
	const path = docsPath(pathname);
	const manifest = getDocsManifest(version);
	if (path === 'search' || manifest?.pages[path]) return path;
	const section = path.split('/')[0];
	return manifest?.pages[section] ? section : 'getting-started';
}
