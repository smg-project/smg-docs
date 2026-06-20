<script lang="ts">
	import { cubicOut } from 'svelte/easing';
	import { slide } from 'svelte/transition';
	import { scrollReveal } from '$lib/actions/scrollReveal';
	import HomeFlowMark from '$lib/components/HomeFlowMark.svelte';
	import HomeHowBranch from '$lib/components/HomeHowBranch.svelte';
	import HomeHowLink from '$lib/components/HomeHowLink.svelte';
	import SectionLabel from '$lib/components/SectionLabel.svelte';

	const gatewayFeatures = [
		'Rate Limiter',
		'OIDC Auth',
		'WebAssembly',
		'Metrics',
		'OpenTelemetry Support',
		'Multi-Tenant'
	] as const;

	const routerFeatures = [
		'Tokenization',
		'Chat History',
		'Function Calls',
		'Reasoning',
		'MCP Execution',
		'Load Balancing',
		'Circuit Breaker'
	] as const;

	const clients = {
		id: 'clients',
		title: 'Clients',
		variant: 'clients',
		lines: ['Applications', 'AI Agents', 'Chat UIs', 'API Services']
	} as const;

	const outputs = [
		{
			id: 'grpc',
			title: 'gRPC Workers',
			variant: 'grpc',
			lines: ['SGLang / vLLM / TRT-LLM', 'Raw Inference + PD', 'Max Performance']
		},
		{
			id: 'http',
			title: 'HTTP Workers',
			variant: 'http',
			lines: ['SGLang / vLLM / TRT-LLM', 'OpenAI-Compatible']
		},
		{
			id: 'external',
			title: 'External APIs',
			variant: 'external',
			lines: ['OpenAI / Claude / Gemini', 'Groq / Together / Bedrock']
		}
	] as const;

	const panelMotion = { duration: 320, easing: cubicOut };

	let openNodeId = $state<string | null>(null);

	function toggleNode(id: string) {
		openNodeId = openNodeId === id ? null : id;
	}
</script>

<section class="home-how" aria-labelledby="home-how-heading">
	<header
		class="home-how-header"
		use:scrollReveal={{ y: 28, duration: 0.75, start: 'top 90%' }}
	>
		<SectionLabel
			label="HOW IT WORKS"
			as="h2"
			id="home-how-heading"
			class="home-how-label"
			markSize={5}
		/>
	</header>

	<div
		class="home-how-diagram"
		use:scrollReveal={{ y: 56, duration: 1.05, delay: 0.08, start: 'top 88%' }}
	>
		<div
			class="home-how-output home-how-output--{clients.variant}"
			class:home-how-output--open={openNodeId === clients.id}
		>
			<button
				type="button"
				class="home-how-output-trigger"
				aria-expanded={openNodeId === clients.id}
				onclick={() => toggleNode(clients.id)}
			>
				<span class="home-how-output-title">{clients.title}</span>
				<HomeFlowMark />
			</button>
			{#if openNodeId === clients.id}
				<div class="home-how-output-body home-how-output-body--center" transition:slide={panelMotion}>
					{#each clients.lines as line}
						<p>{line}</p>
					{/each}
				</div>
			{/if}
		</div>

		<div class="home-how-connector home-how-connector--h" aria-hidden="true">
			<HomeHowLink class="home-how-link-svg" />
		</div>

		<div class="home-how-stack">
			<p class="home-how-stack-title">Gateway Layer</p>
			<div class="home-how-stack-body">
				<div class="home-how-stack-header">Gateway</div>
				<ul class="home-how-feature-list">
					{#each gatewayFeatures as feature}
						<li>{feature}</li>
					{/each}
				</ul>
			</div>
		</div>

		<div class="home-how-connector home-how-connector--h" aria-hidden="true">
			<HomeHowLink class="home-how-link-svg" />
		</div>

		<div class="home-how-stack">
			<p class="home-how-stack-title">Router Layer</p>
			<div class="home-how-stack-body">
				<div class="home-how-stack-header">Router</div>
				<ul class="home-how-feature-list">
					{#each routerFeatures as feature}
						<li>{feature}</li>
					{/each}
				</ul>
			</div>
		</div>

		<div class="home-how-rail">
			<div class="home-how-connector home-how-connector--branch" aria-hidden="true">
				<HomeHowBranch class="home-how-branch-svg" />
			</div>

			<div class="home-how-outputs">
				{#each outputs as output, index}
					<div
						class="home-how-output home-how-output--{output.variant} home-how-output--slot-{index + 1}"
						class:home-how-output--open={openNodeId === output.id}
					>
						<button
							type="button"
							class="home-how-output-trigger"
							aria-expanded={openNodeId === output.id}
							onclick={() => toggleNode(output.id)}
						>
							<span class="home-how-output-title">{output.title}</span>
							<HomeFlowMark />
						</button>
						{#if openNodeId === output.id}
							<div class="home-how-output-body" transition:slide={panelMotion}>
								{#each output.lines as line}
									<p>{line}</p>
								{/each}
							</div>
						{/if}
					</div>
				{/each}
			</div>
		</div>
	</div>

	<p
		class="home-how-tagline"
		use:scrollReveal={{ y: 24, duration: 0.7, start: 'top 92%' }}
	>
		One API<span class="home-how-tagline-sep" aria-hidden="true">•</span>Any Backend<span
			class="home-how-tagline-sep"
			aria-hidden="true">•</span
		>Enterprise Reliability
	</p>
</section>
