import { base } from '$app/paths';

import type { DocsNavConfig, DocsNavSection } from '$lib/config/docs-nav';

export const referenceNav: DocsNavSection[] = [
	{
		label: 'API',
		items: [
			{ label: 'OpenAI-Compatible', href: `${base}/reference/api/openai` },
			{ label: 'Responses API', href: `${base}/reference/api/responses` },
			{ label: 'Anthropic Messages API', href: `${base}/reference/api/messages` },
			{ label: 'Admin API', href: `${base}/reference/api/admin` },
			{ label: 'Gateway Extensions', href: `${base}/reference/api/extensions` }
		]
	},
	{
		label: 'Guides',
		items: [
			{ label: 'Configuration', href: `${base}/reference/configuration` },
			{ label: 'Tenant Rate Limiting', href: `${base}/reference/tenant-rate-limiting` },
			{ label: 'Priority Scheduler', href: `${base}/reference/priority-scheduler` },
			{ label: 'Metrics', href: `${base}/reference/metrics` },
			{ label: 'Internal MCP Servers', href: `${base}/reference/mcp-internal-servers` }
		]
	}
];

export const referenceDocsNav: DocsNavConfig = {
	indexHref: `${base}/reference`,
	indexLabel: 'Reference',
	ariaLabel: 'Reference',
	sections: referenceNav
};
