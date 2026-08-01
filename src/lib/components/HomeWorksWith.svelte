<script lang="ts">
	import { assets } from '$app/paths';
	import { scrollReveal } from '$lib/actions/scrollReveal';
	import SectionLabel from '$lib/components/SectionLabel.svelte';

	const partners = [
		{
			name: 'vLLM',
			logo: `${assets}/images/logos/vllm.svg`,
			href: 'https://vllm.ai',
			kind: 'engine'
		},
		{
			name: 'TokenSpeed',
			logo: `${assets}/images/logos/tokenspeed.svg`,
			href: 'https://github.com/lightseekorg/tokenspeed',
			kind: 'engine'
		},
		{
			name: 'SGLang',
			logo: `${assets}/images/logos/sglang.svg`,
			href: 'https://github.com/sgl-project/sglang',
			kind: 'engine'
		},
		{
			name: 'TensorRT-LLM',
			logo: `${assets}/images/logos/tensorrt-llm.svg`,
			href: 'https://developer.nvidia.com/tensorrt-llm',
			kind: 'engine'
		},
		{
			name: 'OpenAI',
			logo: `${assets}/images/logos/openai.svg`,
			href: 'https://openai.com',
			kind: 'provider'
		},
		{
			name: 'Claude',
			logo: `${assets}/images/logos/claude.svg`,
			href: 'https://www.anthropic.com/claude',
			kind: 'provider'
		},
		{
			name: 'Gemini',
			logo: `${assets}/images/logos/gemini.svg`,
			href: 'https://ai.google.dev/gemini-api',
			kind: 'provider'
		}
	] as const;
</script>

<section
	class="home-works-with"
	aria-label="Compatible platforms"
	use:scrollReveal={{
		children: '.home-works-with-label, .home-works-with-item',
		y: 36,
		stagger: 0.08,
		duration: 0.8
	}}
>
	<SectionLabel label="WORKS WITH" as="h2" class="home-works-with-label" markSize={5} />

	<ul class="home-works-with-grid">
		{#each partners as partner (partner)}
			<li
				class="home-works-with-item"
				class:home-works-with-item--provider={partner.kind === 'provider'}
			>
				<a
					class="home-works-with-link"
					href={partner.href}
					target="_blank"
					rel="noopener noreferrer"
					aria-label="{partner.name} (opens in new tab)"
				>
					<span class="home-works-with-pill">
						<img
							class="home-works-with-logo"
							class:home-works-with-logo--wide={partner.name === 'vLLM'}
							class:home-works-with-logo--mark={partner.name === 'SGLang' ||
								partner.name === 'TensorRT-LLM' ||
								partner.name === 'TokenSpeed'}
							src={partner.logo}
							alt=""
							width="120"
							height="32"
							loading="lazy"
							decoding="async"
						/>
					</span>
					<span class="home-works-with-name">{partner.name}</span>
				</a>
			</li>
		{/each}
	</ul>
</section>
