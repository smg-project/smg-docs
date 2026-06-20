<script lang="ts">
	import { cubicOut } from 'svelte/easing';
	import { slide } from 'svelte/transition';
	import { scrollReveal } from '$lib/actions/scrollReveal';
	import PlusMark from '$lib/components/PlusMark.svelte';
	import SectionLabel from '$lib/components/SectionLabel.svelte';

	const panelMotion = { duration: 320, easing: cubicOut };

	const features = [
		{
			title: 'Full OpenAI Server Mode',
			body: 'With gRPC workers, SMG acts as a full OpenAI-compatible server—tokenization, chat templates, tool parsing, reasoning extraction, and MCP execution all happen at the gateway.'
		},
		{
			title: 'High Performance',
			body: 'Routing decisions run in microseconds. Prefix-aware load balancing, tokenizer caching, and token-level streaming keep SMG from becoming the bottleneck.'
		},
		{
			title: 'Enterprise Reliability',
			body: 'Circuit breakers, health-aware failover, rate limiting, and graceful degradation route around worker failures so applications stay available under load.'
		},
		{
			title: 'Full Observability',
			body: 'Built-in metrics, OpenTelemetry traces, and structured logs give you end-to-end visibility across models, tenants, and backend workers.'
		}
	] as const;

	let openIndex = $state<number | null>(null);

	function toggle(index: number) {
		openIndex = openIndex === index ? null : index;
	}
</script>

<section class="home-why" aria-labelledby="home-why-heading">
	<div
		class="home-why-intro"
		use:scrollReveal={{
			children: '.home-why-label, .home-why-copy',
			y: 32,
			stagger: 0.12,
			duration: 0.85,
			start: 'top 88%'
		}}
	>
		<SectionLabel
			label="WHY SHEPHERD MODEL GATEWAY?"
			as="h2"
			id="home-why-heading"
			class="home-why-label"
			markSize={5}
		/>
		<p class="home-why-copy">
			SMG sits between your applications and LLM workers, providing a unified control and data plane
			for managing inference at scale. Whether you're running a single model or orchestrating
			hundreds of workers across multiple clusters, SMG gives you the tools to do it reliably.
		</p>
	</div>

	<div
		class="home-why-accordion"
		use:scrollReveal={{
			children: '.home-why-item',
			y: 28,
			stagger: 0.1,
			duration: 0.75,
			start: 'top 85%'
		}}
	>
		{#each features as feature, index}
			<div class="home-why-item" class:home-why-item--open={openIndex === index}>
				<button
					type="button"
					class="home-why-trigger"
					aria-expanded={openIndex === index}
					onclick={() => toggle(index)}
				>
					<span class="home-why-trigger-title">{feature.title}</span>
					<span class="home-why-toggle" aria-hidden="true">
						<PlusMark class="home-why-plus" />
					</span>
				</button>
				{#if openIndex === index}
					<div class="home-why-panel" transition:slide={panelMotion}>
						<p>{feature.body}</p>
					</div>
				{/if}
			</div>
		{/each}
	</div>
</section>
