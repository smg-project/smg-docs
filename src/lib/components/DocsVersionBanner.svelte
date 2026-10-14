<script lang="ts">
	import { page } from '$app/stores';
	import {
		defaultDocsVersion,
		docsHref,
		docsPath,
		versionFromPath
	} from '$lib/config/docs-versions';
	import { versionSwitchPath } from '$lib/config/docs-manifests';
	const version = $derived(versionFromPath($page.url.pathname));
	const stablePath = $derived(versionSwitchPath($page.url.pathname, defaultDocsVersion));
</script>

<div class="docs-version-banner" class:development={version === 'main'}>
	{#if version === 'main'}
		<p>
			<strong>main</strong> — Development documentation for SMG’s latest main branch. Features may
			not be released yet.
			<a
				href={docsHref(stablePath) +
					(stablePath === docsPath($page.url.pathname) ? $page.url.search + $page.url.hash : '')}
				>View {defaultDocsVersion} docs</a
			>
		</p>
	{:else}
		<p>Documentation for <strong>SMG {version}</strong>.</p>
	{/if}
</div>

<style>
	.docs-version-banner {
		margin-bottom: 1.5rem;
		padding: 0.75rem 1rem;
		border: 1px solid rgba(0, 0, 0, 0.09);
		border-radius: 0.5rem;
		font-size: 0.8125rem;
		line-height: 1.6;
		background: rgba(0, 0, 0, 0.025);
	}
	.development {
		border-color: rgba(255, 95, 0, 0.25);
		background: rgba(255, 95, 0, 0.06);
	}
	p {
		margin: 0;
	}
	a {
		margin-left: 0.35rem;
		text-decoration: underline;
		text-underline-offset: 0.2em;
	}
</style>
