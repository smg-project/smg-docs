<script lang="ts">
	import { scrollReveal } from '$lib/actions/scrollReveal';
	import HomeMetricValue from '$lib/components/HomeMetricValue.svelte';
	import SectionLabel from '$lib/components/SectionLabel.svelte';

	const metrics = [
		{
			title: 'TIFT REDUCTION',
			prefix: '',
			value: 70,
			suffix: '%',
			description: 'Reduction in Inference Latency'
		},
		{
			title: 'ROUTING LATENCY',
			prefix: '<',
			value: 1,
			suffix: 'ms',
			description: 'Routing Overhead'
		},
		{
			title: 'METRICS',
			prefix: '',
			value: 40,
			suffix: '+',
			description: 'Metrics Available'
		},
		{
			title: 'OPEN AI COMPATIBLE',
			prefix: '',
			value: 100,
			suffix: '%',
			description: 'Open AI Compatible'
		}
	] as const;
</script>

<section
	class="home-metrics"
	aria-label="Product metrics"
	use:scrollReveal={{ children: '.home-metrics-card', y: 48, stagger: 0.12, duration: 0.85 }}
>
	<div class="home-metrics-grid">
		{#each metrics as metric, index}
			<article class="home-metrics-card">
				<div class="home-metrics-card-head">
					<SectionLabel label={metric.title} as="h2" class="home-metrics-card-title" />
				</div>
				<div class="home-metrics-card-body">
					<p class="home-metrics-card-value">
						<HomeMetricValue
							prefix={metric.prefix}
							value={metric.value}
							suffix={metric.suffix}
							duration={1500 + index * 120}
						/>
					</p>
					<p class="home-metrics-card-desc">{metric.description}</p>
				</div>
			</article>
		{/each}
	</div>
</section>
