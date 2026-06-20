import type { DocsNavConfig, DocsNavSection } from '$lib/config/docs-nav';

export const contributingNav: DocsNavSection[] = [
	{
		label: 'Guides',
		items: [
			{ label: 'Development Setup', href: '/contributing/development' },
			{ label: 'Code Style', href: '/contributing/code-style' }
		]
	}
];

export const contributingDocsNav: DocsNavConfig = {
	indexHref: '/contributing',
	indexLabel: 'Contributing',
	ariaLabel: 'Contributing',
	sections: contributingNav
};
