import { error } from '@sveltejs/kit';
import { getDocsManifest, getDocsNav } from '$lib/config/docs-manifests';
import { isDocsSection } from '$lib/config/docs-versions';
import { extractDocToc } from '$lib/markdown/doc';

const modules = import.meta.glob(
	['/src/lib/content/**/*.md', '/src/lib/versions/*/content/**/*.md'],
	{
		query: '?raw',
		import: 'default',
		eager: true
	}
) as Record<string, string>;

/** Load content, navigation, and headings from exactly one version; missing routes or sources throw 404. */
export function loadDocPage(version: string, section: string, slug = '') {
	if (!isDocsSection(section)) error(404, 'Documentation section not found');
	const manifest = getDocsManifest(version);
	const path = `${section}${slug ? `/${slug}` : ''}`;
	const sourcePath = manifest?.pages[path];
	if (!sourcePath) error(404, `Page not found in ${version} documentation`);
	const contentSourcePath =
		version === 'main'
			? `src/lib/content/${sourcePath}`
			: `src/lib/versions/${version}/content/${sourcePath}`;
	const content = modules[`/${contentSourcePath}`];
	if (content === undefined) error(404, 'Documentation source not found');
	return {
		content,
		sourcePath,
		contentSourcePath,
		docsVersion: version,
		nav: getDocsNav(section, version),
		toc: extractDocToc(content)
	};
}
