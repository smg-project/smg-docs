<script lang="ts">
	import { base } from '$app/paths';
	import { scrollReveal } from '$lib/actions/scrollReveal';
	import ChoosePathArrow from '$lib/components/ChoosePathArrow.svelte';
	import SectionLabel from '$lib/components/SectionLabel.svelte';

	const paths = [
		{
			title: 'New to SMG',
			description: 'Start here to understand what SMG does and get it running in minutes.',
			href: `${base}/getting-started`,
			cta: 'Getting Started'
		},
		{
			title: 'Learn the Concepts',
			description: "Understand SMG's architecture, routing strategies, and reliability features.",
			href: `${base}/concepts`,
			cta: 'Read Concepts'
		},
		{
			title: 'Ops Setup',
			description: 'Continue onboarding with monitoring, logging, and TLS guides.',
			href: `${base}/getting-started`,
			cta: 'View Getting Started Guides'
		},
		{
			title: 'API Reference',
			description: 'Complete reference for the OpenAI-compatible API and SMG extensions.',
			href: `${base}/reference`,
			cta: 'View Reference'
		}
	] as const;
</script>

<section class="home-choose-path" aria-labelledby="home-choose-path-heading">
	<div class="home-choose-path-inner">
		<div
			class="home-choose-path-header"
			use:scrollReveal={{
				children: '.home-choose-path-label, .home-choose-path-intro',
				y: 36,
				stagger: 0.12,
				duration: 0.9,
				start: 'top 88%'
			}}
		>
			<SectionLabel
				label="CHOOSE YOUR PATH"
				as="h2"
				id="home-choose-path-heading"
				class="home-choose-path-label"
				markSize={5}
			/>
			<p class="home-choose-path-intro">
				SMG sits between your applications and LLM workers, providing a unified control and data
				plane for managing inference at scale. Whether you're running a single model or
				orchestrating hundreds of workers across multiple clusters, SMG gives you the tools to do it
				reliably.
			</p>
		</div>

		<div
			class="home-choose-path-grid"
			use:scrollReveal={{
				children: '.home-choose-path-card',
				y: 48,
				stagger: 0.11,
				duration: 0.85,
				start: 'top 85%'
			}}
		>
			{#each paths as path (path)}
				<article class="home-choose-path-card">
					<p class="home-choose-path-card-copy">{path.description}</p>
					<a class="home-choose-path-cta" href={path.href}>
						<span class="home-choose-path-cta-label">{path.title}</span>
						<span class="home-choose-path-cta-icon" aria-hidden="true">
							<ChoosePathArrow />
						</span>
					</a>
				</article>
			{/each}
		</div>
	</div>
</section>
