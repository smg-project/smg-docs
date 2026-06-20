<script lang="ts">
	import { browser } from '$app/environment';
	import { page } from '$app/stores';
	import { slide } from 'svelte/transition';
	import { cubicOut } from 'svelte/easing';
	import type { DocsNavConfig } from '$lib/config/docs-nav';

	let { nav }: { nav: DocsNavConfig } = $props();

	function isActive(href: string, pathname: string) {
		if (href === nav.indexHref) {
			return pathname === href || pathname === `${href}/`;
		}
		return pathname === href || pathname.startsWith(`${href}/`);
	}

	function sectionHasActive(index: number, pathname: string) {
		return nav.sections[index]?.items.some((item) => isActive(item.href, pathname)) ?? false;
	}

	// Sections are open by default; track the ones the user has collapsed.
	let closedSections = $state<Record<number, boolean>>({});

	const isOpen = (index: number) => !closedSections[index];

	// Re-open the section that contains the current page when navigating.
	$effect(() => {
		const pathname = $page.url.pathname;
		nav.sections.forEach((_, index) => {
			if (sectionHasActive(index, pathname)) closedSections[index] = false;
		});
	});

	// On mobile the sidebar sits above the article, so start it compact:
	// collapse every section except the one for the current page.
	let didInitMobile = false;
	$effect(() => {
		if (didInitMobile || !browser) return;
		didInitMobile = true;
		if (!window.matchMedia('(max-width: 900px)').matches) return;
		const pathname = $page.url.pathname;
		nav.sections.forEach((_, index) => {
			if (!sectionHasActive(index, pathname)) closedSections[index] = true;
		});
	});

	function toggle(index: number) {
		closedSections[index] = !closedSections[index];
	}
</script>

<nav class="docs-sidebar" aria-label={nav.ariaLabel}>
	<ul class="docs-sidebar-sections">
		<li class="docs-sidebar-section">
			<a
				class="docs-sidebar-link docs-sidebar-link--index"
				class:active={isActive(nav.indexHref, $page.url.pathname)}
				href={nav.indexHref}
			>
				{nav.indexLabel}
			</a>
		</li>

		{#each nav.sections as section, index}
			<li class="docs-sidebar-section" class:docs-sidebar-section--open={isOpen(index)}>
				<button
					type="button"
					class="docs-sidebar-heading"
					aria-expanded={isOpen(index)}
					onclick={() => toggle(index)}
				>
					<span class="docs-sidebar-heading-label">{section.label}</span>
				</button>

				{#if isOpen(index)}
					<ul class="docs-sidebar-links" transition:slide={{ duration: 220, easing: cubicOut }}>
						{#each section.items as item}
							<li>
								<a
									class="docs-sidebar-link"
									class:active={isActive(item.href, $page.url.pathname)}
									href={item.href}
								>
									{item.label}
								</a>
							</li>
						{/each}
					</ul>
				{/if}
			</li>
		{/each}
	</ul>
</nav>
