import { base } from '$app/paths';
import { defaultDocsVersion, docsHref, docsPath, isDocsSection } from '$lib/config/docs-versions';
import { getDocsManifest } from '$lib/config/docs-manifests';

/** Resolve a Markdown link relative to its source file without a deployment prefix. */
function resolveRelativePath(fromFile: string, href: string): string {
	const stack = fromFile.split('/').slice(0, -1);
	for (const part of href.split('/')) {
		if (part === '..') stack.pop();
		else if (part !== '.' && part !== '') stack.push(part);
	}
	return stack.join('/');
}

/** Scope internal documentation links to the selected manifest, preserving queries, anchors, and external URLs. */
export function rewriteDocLinks(
	text: string,
	sourcePath = 'getting-started/index.md',
	version = defaultDocsVersion
): string {
	const manifest = getDocsManifest(version);
	return text.replace(/(?<!!)\[([^\]]+)\]\(([^)]+)\)/g, (match, label: string, href: string) => {
		const suffixIndex = href.search(/[?#]/);
		const suffix = suffixIndex >= 0 ? href.slice(suffixIndex) : '';
		let path = suffixIndex >= 0 ? href.slice(0, suffixIndex) : href;
		if (!path) return match;
		if (path.startsWith('https://lightseek.org/smg/')) {
			path = `${base}/${path.slice('https://lightseek.org/smg/'.length)}`;
		} else if (/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(path)) {
			return match;
		}
		if (path.startsWith('/')) {
			const route = docsPath(path);
			if (isDocsSection(route.split('/')[0]) || route === 'search') {
				return `[${label}](${docsHref(route, version)}${suffix})`;
			}
			return match;
		}
		if (!path.endsWith('.md')) return match;
		const file = resolveRelativePath(sourcePath, path);
		const route =
			Object.entries(manifest?.pages ?? {}).find(([, source]) => source === file)?.[0] ??
			file.replace(/\.md$/, '').replace(/\/index$/, '');
		return `[${label}](${docsHref(route, version)}${suffix})`;
	});
}

/** Point documentation images at live main assets or the selected release snapshot. */
export function rewriteAssetPaths(text: string, version = defaultDocsVersion): string {
	const images = version === 'main' ? `${base}/images` : `${base}/versions/${version}/images`;
	return text
		.replace(/!\[([^\]]*)\]\((?:\.\.\/)+assets\/images\/([^)]+)\)/g, `![$1](${images}/$2)`)
		.replace(/!\[([^\]]*)\]\(\.\/assets\/images\/([^)]+)\)/g, `![$1](${images}/$2)`)
		.replaceAll(`](${base}/images/`, `](${images}/`)
		.replaceAll(`src="${base}/images/`, `src="${images}/`);
}
