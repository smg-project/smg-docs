import { base } from '$app/paths';

import type { DocsNavConfig, DocsNavSection } from '$lib/config/docs-nav';

export const conceptsNav: DocsNavSection[] = [
	{
		label: 'Architecture',
		items: [
			{ label: 'Overview', href: `${base}/concepts/architecture/overview` },
			{ label: 'Service Discovery', href: `${base}/concepts/architecture/service-discovery` },
			{ label: 'gRPC Pipeline', href: `${base}/concepts/architecture/grpc-pipeline` },
			{ label: 'High Availability', href: `${base}/concepts/architecture/high-availability` }
		]
	},
	{
		label: 'Routing',
		items: [
			{ label: 'Load Balancing', href: `${base}/concepts/routing/load-balancing` },
			{ label: 'Cache-Aware Routing', href: `${base}/concepts/routing/cache-aware` },
			{ label: 'PD Disaggregation', href: `${base}/concepts/routing/pd-disaggregation` }
		]
	},
	{
		label: 'Performance',
		items: [{ label: 'Tokenizer Caching', href: `${base}/concepts/performance/tokenizer-caching` }]
	},
	{
		label: 'Extensibility',
		items: [
			{ label: 'WASM Plugins', href: `${base}/concepts/extensibility/wasm-plugins` },
			{ label: 'Model Context Protocol', href: `${base}/concepts/extensibility/mcp` }
		]
	},
	{
		label: 'Reliability',
		items: [
			{ label: 'Circuit Breakers', href: `${base}/concepts/reliability/circuit-breakers` },
			{ label: 'Rate Limiting', href: `${base}/concepts/reliability/rate-limiting` },
			{ label: 'Priority Scheduling', href: `${base}/concepts/reliability/priority-scheduling` },
			{ label: 'Retries', href: `${base}/concepts/reliability/retries` },
			{ label: 'Health Checks', href: `${base}/concepts/reliability/health-checks` },
			{ label: 'Graceful Shutdown', href: `${base}/concepts/reliability/graceful-shutdown` }
		]
	},
	{
		label: 'Data',
		items: [{ label: 'Chat History', href: `${base}/concepts/data/chat-history` }]
	},
	{
		label: 'Security',
		items: [{ label: 'Authentication', href: `${base}/concepts/security/authentication` }]
	}
];

export const conceptsDocsNav: DocsNavConfig = {
	indexHref: '/concepts',
	indexLabel: 'Concepts',
	ariaLabel: 'Concepts',
	sections: conceptsNav
};
