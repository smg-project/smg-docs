import type { DocsNavConfig, DocsNavSection } from '$lib/config/docs-nav';

export const referenceNav: DocsNavSection[] = [
	{
		label: 'API',
		items: [
			{ label: 'OpenAI Compatible', href: '/reference/api/openai' },
			{ label: 'Responses API', href: '/reference/api/responses' },
			{ label: 'Anthropic Messages API', href: '/reference/api/messages' },
			{ label: 'Admin API', href: '/reference/api/admin' },
			{ label: 'Gateway Extensions', href: '/reference/api/extensions' }
		]
	},
	{
		label: 'Guides',
		items: [
			{ label: 'Configuration', href: '/reference/configuration' },
			{ label: 'Priority Scheduler', href: '/reference/priority-scheduler' },
			{ label: 'Metrics', href: '/reference/metrics' },
			{ label: 'Internal MCP Servers', href: '/reference/mcp-internal-servers' }
		]
	}
];

export const referenceDocsNav: DocsNavConfig = {
	indexHref: '/reference',
	indexLabel: 'Reference',
	ariaLabel: 'Reference',
	sections: referenceNav
};
