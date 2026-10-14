import { base } from '$app/paths';
import releases from './docs-releases.json';

export const docsSections = ['getting-started', 'concepts', 'reference', 'contributing'] as const;
export type DocsSection = (typeof docsSections)[number];
export const defaultDocsVersion = releases[0];
export const docsVersions = [
	{ id: 'main', label: 'main', description: 'Development' },
	...releases.map((id, index) => ({
		id,
		label: id,
		description: index === 0 ? 'Latest release' : 'Release'
	}))
];

export function isDocsVersion(value: string): boolean {
	return docsVersions.some(({ id }) => id === value);
}

export function isDocsSection(value: string): value is DocsSection {
	return docsSections.some((section) => section === value);
}

/** A site-relative path without the deployment base or documentation version. */
export function docsPath(pathname: string): string {
	const local =
		pathname === base
			? ''
			: pathname.startsWith(`${base}/`)
				? pathname.slice(base.length)
				: pathname;
	const parts = local.replace(/^\/+|\/+$/g, '').split('/');
	if (isDocsVersion(parts[0])) parts.shift();
	return parts.join('/');
}

export function docsHref(path = 'getting-started', version = defaultDocsVersion): string {
	return `${base}/${version}/${docsPath(path)}`;
}

export function versionFromPath(pathname: string): string {
	const local = pathname.startsWith(`${base}/`) ? pathname.slice(base.length + 1) : '';
	const version = local.split('/')[0];
	return isDocsVersion(version) ? version : defaultDocsVersion;
}
