import { base } from '$app/paths';
import { fileToSlug } from '$lib/content/getting-started/slugs';

function dirname(filePath: string): string {
	const index = filePath.lastIndexOf('/');
	return index === -1 ? '' : filePath.slice(0, index);
}

function resolveRelativePath(fromFile: string, href: string): string {
	const stack = dirname(fromFile) ? dirname(fromFile).split('/') : [];

	for (const part of href.split('/')) {
		if (part === '..') stack.pop();
		else if (part === '.' || part === '') continue;
		else stack.push(part);
	}

	let resolved = stack.join('/').replace(/\.md$/, '');
	if (resolved.endsWith('/index')) {
		resolved = resolved.slice(0, -'/index'.length);
	}

	return resolved;
}

function toUrlPath(resolved: string): string {
	if (!resolved || resolved === 'index') return '/';

	const parts = resolved.split('/');

	if (parts[0] === 'getting-started') {
		if (parts.length === 1) return '/getting-started';
		const slug = fileToSlug[parts[1]] ?? parts[1];
		return parts.length === 2 ? `/getting-started/${slug}` : `/${resolved}`;
	}

	if (parts[0] === 'concepts') {
		const rest = parts.slice(1).join('/');
		return rest ? `/concepts/${rest}` : '/concepts';
	}

	if (parts[0] === 'reference') {
		const rest = parts.slice(1).join('/');
		return rest ? `/reference/${rest}` : '/reference';
	}

	if (parts[0] === 'contributing') {
		const rest = parts.slice(1).join('/');
		return rest ? `/contributing/${rest}` : '/contributing';
	}

	return `/${resolved}`;
}

export function rewriteDocLinks(text: string, sourcePath?: string): string {
	const fromFile = sourcePath ?? 'getting-started/index.md';

	return text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, label: string, href: string) => {
		if (href.startsWith('https://lightseek.org/smg/')) {
			const path = href.replace('https://lightseek.org/smg/', '').replace(/\/$/, '');
			return `[${label}](${base}/${path})`;
		}

		const hashIndex = href.indexOf('#');
		const fragment = hashIndex >= 0 ? href.slice(hashIndex) : '';
		const path = hashIndex >= 0 ? href.slice(0, hashIndex) : href;

		if (!path.endsWith('.md')) return match;

		const normalized = path.replace(/^\.\//, '');

		if (normalized === 'index.md') {
			const section = dirname(fromFile).split('/')[0];
			if (section === 'getting-started') return `[${label}](${base}/getting-started${fragment})`;
			if (section === 'concepts') return `[${label}](${base}/concepts${fragment})`;
			if (section === 'reference') return `[${label}](${base}/reference${fragment})`;
			if (section === 'contributing') return `[${label}](${base}/contributing${fragment})`;
		}

		return `[${label}](${base}${toUrlPath(resolveRelativePath(fromFile, normalized))}${fragment})`;
	});
}

export function rewriteAssetPaths(text: string): string {
	return text
		.replace(/!\[([^\]]*)\]\((?:\.\.\/)+assets\/images\/([^)]+)\)/g, `![$1](${base}/images/$2)`)
		.replace(/!\[([^\]]*)\]\(\.\/assets\/images\/([^)]+)\)/g, `![$1](${base}/images/$2)`);
}
