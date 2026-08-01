import { error } from '@sveltejs/kit';
import { extractDocToc, type DocTocItem } from '$lib/markdown/doc';

const modules = import.meta.glob('./**/*.md', {
	query: '?raw',
	import: 'default',
	eager: true
}) as Record<string, string>;

function moduleForPath(filePath: string): string | undefined {
	const suffix = filePath === 'index' ? '/index.md' : `/${filePath}.md`;
	return Object.keys(modules).find((key) => key.endsWith(suffix));
}

export type ReferencePage = {
	content: string;
	sourcePath: string;
	toc: DocTocItem[];
};

export function loadReferencePage(slug?: string): ReferencePage {
	const filePath = slug && slug.length > 0 ? slug : 'index';
	const key = moduleForPath(filePath);

	if (!key) {
		error(404, 'Page not found');
	}

	const raw = modules[key];

	return {
		content: raw,
		sourcePath: `reference/${filePath}.md`,
		toc: extractDocToc(raw)
	};
}
