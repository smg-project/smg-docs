import type { DocsNavConfig, DocsNavItem, DocsNavSection } from '$lib/config/docs-nav';

export type GettingStartedNavItem = DocsNavItem;
export type GettingStartedNavSection = DocsNavSection;

export const gettingStartedNav: GettingStartedNavSection[] = [
	{
		label: 'Core Setup',
		items: [
			{ label: 'Multiple Workers', href: '/getting-started/multiple-workers' },
			{ label: 'gRPC Workers', href: '/getting-started/grpc-workers' },
			{ label: 'PD Disaggregation', href: '/getting-started/pd-disaggregation' },
			{ label: 'Service Discovery', href: '/getting-started/service-discovery' }
		]
	},
	{
		label: 'Operations',
		items: [
			{ label: 'Monitoring', href: '/getting-started/monitoring' },
			{ label: 'Logging', href: '/getting-started/logging' },
			{ label: 'TLS', href: '/getting-started/tls' },
			{ label: 'Control Plane Auth', href: '/getting-started/control-plane-auth' },
			{ label: 'Control Plane Operations', href: '/getting-started/control-plane-operations' }
		]
	},
	{
		label: 'Reliability and Data',
		items: [
			{ label: 'Reliability Controls', href: '/getting-started/reliability-controls' },
			{ label: 'Data Connections', href: '/getting-started/data-connections' },
			{
				label: 'Tokenization and Parsing APIs',
				href: '/getting-started/tokenization-and-parsing-apis'
			}
		]
	},
	{
		label: 'Advanced Features',
		items: [
			{ label: 'Load Balancing', href: '/getting-started/load-balancing' },
			{
				label: 'KV Events Cache-Aware Routing',
				href: '/getting-started/kv-events-cache-aware-routing'
			},
			{ label: 'Tokenizer Caching', href: '/getting-started/tokenizer-caching' },
			{ label: 'MCP in Responses API', href: '/getting-started/mcp-in-responses-api' },
			{ label: 'External Providers', href: '/getting-started/external-providers' }
		]
	}
];

export const gettingStartedIndexHref = '/getting-started';

export const gettingStartedDocsNav: DocsNavConfig = {
	indexHref: gettingStartedIndexHref,
	indexLabel: 'Getting Started',
	ariaLabel: 'Getting Started',
	sections: gettingStartedNav
};
