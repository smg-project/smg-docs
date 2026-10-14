<script lang="ts">
	import { page } from '$app/stores';
	import { docsHref, docsPath, docsVersions, versionFromPath } from '$lib/config/docs-versions';
	import { versionSwitchPath } from '$lib/config/docs-manifests';

	let open = $state(false);
	let element = $state<HTMLDetailsElement>();
	const current = $derived(versionFromPath($page.url.pathname));

	function destination(version: string) {
		const path = versionSwitchPath($page.url.pathname, version);
		const samePage = path === docsPath($page.url.pathname);
		return docsHref(path, version) + (samePage ? $page.url.search + $page.url.hash : '');
	}

	$effect(() => {
		void $page.url.pathname;
		open = false;
	});
</script>

<svelte:window
	onclick={(event) => {
		if (element && !element.contains(event.target as Node)) open = false;
	}}
	onkeydown={(event) => {
		if (event.key === 'Escape' && open) {
			open = false;
			element?.querySelector('summary')?.focus();
		}
	}}
/>

<details class="docs-version-switcher" bind:open bind:this={element}>
	<summary aria-label="Documentation version: {current}">
		<span>Version: <strong>{current}</strong></span>
		<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg>
	</summary>
	<nav class="docs-version-options" aria-label="Documentation versions">
		{#each docsVersions as version (version.id)}
			<a
				href={destination(version.id)}
				aria-current={current === version.id ? 'true' : undefined}
				onclick={() => (open = false)}
			>
				<span>{version.label}</span>
				<small>{version.description}</small>
			</a>
		{/each}
	</nav>
</details>

<style>
	.docs-version-switcher {
		position: relative;
		font-size: 0.8125rem;
	}
	summary {
		display: flex;
		align-items: center;
		gap: 0.65rem;
		min-height: 2.5rem;
		height: 100%;
		padding: 0 0.85rem;
		border: 1px solid var(--nav-pill-border);
		border-radius: 0.625rem;
		background: var(--nav-pill-bg);
		cursor: pointer;
		list-style: none;
		white-space: nowrap;
	}
	summary::-webkit-details-marker {
		display: none;
	}
	summary:focus-visible,
	a:focus-visible {
		outline: 2px solid currentColor;
		outline-offset: 3px;
	}
	svg {
		width: 1rem;
		height: 1rem;
		fill: none;
		stroke: currentColor;
		stroke-width: 1.5;
	}
	.docs-version-options {
		position: absolute;
		right: 0;
		top: calc(100% + 0.5rem);
		z-index: 110;
		min-width: 14rem;
		padding: 0.4rem;
		border: 1px solid rgba(0, 0, 0, 0.12);
		border-radius: 0.625rem;
		background: var(--smg-surface);
		color: var(--text-on-surface);
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.1);
	}
	a {
		display: flex;
		justify-content: space-between;
		gap: 1rem;
		padding: 0.7rem;
		border-radius: 0.3rem;
		color: inherit;
		text-decoration: none;
	}
	a:hover,
	a[aria-current] {
		background: rgba(0, 0, 0, 0.05);
	}
	a[aria-current] {
		font-weight: 600;
	}
	small {
		opacity: 0.65;
		font-weight: 400;
	}
	@media (max-width: 900px) {
		.docs-version-options {
			left: 0;
			right: auto;
		}
	}
</style>
