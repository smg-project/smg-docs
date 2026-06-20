<script lang="ts">
	import '../app.css';
	import { onNavigate } from '$app/navigation';
	import { page } from '$app/stores';
	import { headerNavItems, siteName } from '$lib/config/nav';
	import SiteFooter from '$lib/components/SiteFooter.svelte';
	import SiteHeader from '$lib/components/SiteHeader.svelte';
	import { heroShaderActive, heroShaderEnergy } from '$lib/stores/hero-shader';
	import type { LayoutData } from './$types';

	onNavigate((navigation) => {
		if (typeof document === 'undefined') return;
		if (!document.startViewTransition) return;
		if (navigation.willUnload) return;
		if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

		const from = navigation.from?.url.pathname ?? '';
		const to = navigation.to?.url.pathname ?? '';
		// Home hero is full-viewport with negative margin — morphing main bounds looks like orange sliding up.
		if (from === '/' || to === '/') return;

		return new Promise<void>((resolve) => {
			document.startViewTransition(async () => {
				resolve();
				await navigation.complete;
			});
		});
	});

	$effect(() => {
		if ($page.url.pathname !== '/') {
			heroShaderActive.set(false);
			heroShaderEnergy.set(0);
		}
	});

	const pageTitle = $derived.by(() => {
		const match = headerNavItems.find(
			(item) =>
				$page.url.pathname === item.href || $page.url.pathname.startsWith(`${item.href}/`)
		);
		if ($page.url.pathname === '/search') return `Search · ${siteName}`;
		if (match) return `${match.label} · ${siteName}`;
		return siteName;
	});

	let { data, children }: { data: LayoutData; children: import('svelte').Snippet } = $props();

	const isHome = $derived($page.url.pathname === '/');
</script>

<svelte:head>
	<title>{pageTitle}</title>
	<meta name="description" content="Shepherd Model Gateway — high-performance inference gateway for production LLM deployments." />
	<link rel="icon" href="/favicon.svg" type="image/svg+xml" />
	<link rel="icon" href="/favicon.png" type="image/png" sizes="192x192" />
	<link rel="apple-touch-icon" href="/favicon.png" />
</svelte:head>

<div
	class="site-shell"
	class:site-shell--home={isHome}
	style:--hero-shader-energy={isHome ? $heroShaderEnergy : 0}
>
	<SiteHeader github={data.github} />
	<main class="site-main" class:site-main--docs={!isHome}>
		{@render children()}
	</main>
	<SiteFooter />
</div>
