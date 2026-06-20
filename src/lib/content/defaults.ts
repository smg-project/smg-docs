import type { ContentMap } from '$lib/server/content';

export const homeDefaults: ContentMap = {
	'home.hero.title': 'The High-performance inference gateway for production LLM deployments',
	'home.hero.subtitle':
		'Route, balance, and orchestrate traffic across your LLM fleet with enterprise-grade reliability.'
};

export const gettingStartedDefaults: ContentMap = {
	'getting-started.title': 'Getting Started',
	'getting-started.lead': 'Install Shepherd, configure your first route, and send a test request.',
	'getting-started.body':
		'Start with a minimal deployment, define upstream providers, then validate traffic through the gateway before rolling out to production workloads.'
};

export const conceptsDefaults: ContentMap = {
	'concepts.title': 'Concepts',
	'concepts.lead': 'Core ideas behind routing, policy enforcement, and gateway operations.',
	'concepts.body':
		'Learn how requests flow through the gateway, how policies are evaluated, and how observability data is collected across providers.'
};

export const referenceDefaults: ContentMap = {
	'reference.title': 'Reference',
	'reference.lead': 'Configuration options, API endpoints, and operational knobs.',
	'reference.body':
		'Use this section as the authoritative guide for gateway settings, environment variables, and integration contracts.'
};

export const contributingDefaults: ContentMap = {
	'contributing.title': 'Contributing',
	'contributing.lead': 'How to propose changes, report issues, and improve documentation.',
	'contributing.body':
		'Contributions are welcome. Open an issue to discuss larger changes, keep pull requests focused, and update docs alongside code.'
};
