import type { DocsNavConfig, DocsNavSection } from '$lib/config/docs-nav';

export const conceptsNav: DocsNavSection[] = [
	{
		label: 'Architecture',
		items: [
			{ label: 'Overview', href: '/concepts/architecture/overview' },
			{ label: 'Service Discovery', href: '/concepts/architecture/service-discovery' },
			{ label: 'gRPC Pipeline', href: '/concepts/architecture/grpc-pipeline' },
			{ label: 'High Availability', href: '/concepts/architecture/high-availability' }
		]
	},
	{
		label: 'Routing',
		items: [
			{ label: 'Load Balancing', href: '/concepts/routing/load-balancing' },
			{ label: 'Cache-Aware Routing', href: '/concepts/routing/cache-aware' },
			{ label: 'PD Disaggregation', href: '/concepts/routing/pd-disaggregation' }
		]
	},
	{
		label: 'Performance',
		items: [{ label: 'Tokenizer Caching', href: '/concepts/performance/tokenizer-caching' }]
	},
	{
		label: 'Extensibility',
		items: [
			{ label: 'WASM Plugins', href: '/concepts/extensibility/wasm-plugins' },
			{ label: 'Model Context Protocol', href: '/concepts/extensibility/mcp' }
		]
	},
	{
		label: 'Reliability',
		items: [
			{ label: 'Circuit Breakers', href: '/concepts/reliability/circuit-breakers' },
			{ label: 'Rate Limiting', href: '/concepts/reliability/rate-limiting' },
			{ label: 'Priority Scheduling', href: '/concepts/reliability/priority-scheduling' },
			{ label: 'Retries', href: '/concepts/reliability/retries' },
			{ label: 'Health Checks', href: '/concepts/reliability/health-checks' },
			{ label: 'Graceful Shutdown', href: '/concepts/reliability/graceful-shutdown' }
		]
	},
	{
		label: 'Data',
		items: [{ label: 'Chat History', href: '/concepts/data/chat-history' }]
	},
	{
		label: 'Security',
		items: [{ label: 'Authentication', href: '/concepts/security/authentication' }]
	}
];

export const conceptsDocsNav: DocsNavConfig = {
	indexHref: '/concepts',
	indexLabel: 'Concepts',
	ariaLabel: 'Concepts',
	sections: conceptsNav
};
