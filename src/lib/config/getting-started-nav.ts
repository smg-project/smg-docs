import { base } from '$app/paths';

import type { DocsNavConfig, DocsNavItem, DocsNavSection } from '$lib/config/docs-nav';

export type GettingStartedNavItem = DocsNavItem;
export type GettingStartedNavSection = DocsNavSection;

export const gettingStartedNav: GettingStartedNavSection[] = [
	{
		label: 'Core Setup',
		items: [
			{ label: 'Multiple Workers', href: `${base}/getting-started/multiple-workers` },
			{ label: 'gRPC Workers', href: `${base}/getting-started/grpc-workers` },
			{ label: 'PD Disaggregation', href: `${base}/getting-started/pd-disaggregation` },
			{ label: 'Service Discovery', href: `${base}/getting-started/service-discovery` }
		]
	},
	{
		label: 'Operations',
		items: [
			{ label: 'Monitoring', href: `${base}/getting-started/monitoring` },
			{ label: 'Logging', href: `${base}/getting-started/logging` },
			{ label: 'TLS', href: `${base}/getting-started/tls` },
			{ label: 'Control Plane Auth', href: `${base}/getting-started/control-plane-auth` },
			{
				label: 'Control Plane Operations',
				href: `${base}/getting-started/control-plane-operations`
			}
		]
	},
	{
		label: 'Reliability and Data',
		items: [
			{ label: 'Reliability Controls', href: `${base}/getting-started/reliability-controls` },
			{ label: 'Data Connections', href: `${base}/getting-started/data-connections` },
			{
				label: 'Tokenization and Parsing APIs',
				href: `${base}/getting-started/tokenization-and-parsing-apis`
			}
		]
	},
	{
		label: 'Advanced Features',
		items: [
			{ label: 'Load Balancing', href: `${base}/getting-started/load-balancing` },
			{
				label: 'KV Events Cache-Aware Routing',
				href: `${base}/getting-started/kv-events-cache-aware-routing`
			},
			{ label: 'Tokenizer Caching', href: `${base}/getting-started/tokenizer-caching` },
			{ label: 'MCP in Responses API', href: `${base}/getting-started/mcp-in-responses-api` },
			{ label: 'External Providers', href: `${base}/getting-started/external-providers` }
		]
	}
];

export const gettingStartedIndexHref = `${base}/getting-started`;

export const gettingStartedDocsNav: DocsNavConfig = {
	indexHref: gettingStartedIndexHref,
	indexLabel: 'Getting Started',
	ariaLabel: 'Getting Started',
	sections: gettingStartedNav
};
