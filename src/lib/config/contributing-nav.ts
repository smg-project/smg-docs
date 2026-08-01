import { base } from '$app/paths';

import type { DocsNavConfig, DocsNavSection } from '$lib/config/docs-nav';

export const contributingNav: DocsNavSection[] = [
	{
		label: 'Guides',
		items: [
			{ label: 'Development Setup', href: `${base}/contributing/development` },
			{ label: 'Code Style', href: `${base}/contributing/code-style` }
		]
	}
];

export const contributingDocsNav: DocsNavConfig = {
	indexHref: `${base}/contributing`,
	indexLabel: 'Contributing',
	ariaLabel: 'Contributing',
	sections: contributingNav
};
