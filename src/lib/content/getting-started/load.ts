import { error } from '@sveltejs/kit';
import { extractDocToc, type DocTocItem } from '$lib/markdown/doc';
import { resolveFileName } from './slugs';

const modules = import.meta.glob('./*.md', {
	query: '?raw',
	import: 'default',
	eager: true
}) as Record<string, string>;

function moduleForFile(fileName: string): string | undefined {
	const suffix = `/${fileName}.md`;
	return Object.keys(modules).find((key) => key.endsWith(suffix));
}

export type GettingStartedPage = {
	content: string;
	sourcePath: string;
	toc: DocTocItem[];
};

export function loadGettingStartedPage(slug?: string): GettingStartedPage {
	const fileName = slug ? resolveFileName(slug) : 'index';
	const key = moduleForFile(fileName);

	if (!key) {
		error(404, 'Page not found');
	}

	const raw = modules[key];

	return {
		content: raw,
		sourcePath: `getting-started/${fileName}.md`,
		toc: extractDocToc(raw)
	};
}

export function isValidGettingStartedSlug(slug: string): boolean {
	return Boolean(moduleForFile(resolveFileName(slug)));
}
