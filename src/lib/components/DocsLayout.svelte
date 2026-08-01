<script lang="ts">
	import { page } from '$app/stores';
	import DocsSidebar from '$lib/components/DocsSidebar.svelte';
	import DocsToc from '$lib/components/DocsToc.svelte';
	import type { DocsNavConfig } from '$lib/config/docs-nav';
	import type { DocTocItem } from '$lib/markdown/doc';

	let {
		nav,
		children
	}: {
		nav: DocsNavConfig;
		children: import('svelte').Snippet;
	} = $props();

	const toc = $derived(($page.data.toc as DocTocItem[] | undefined) ?? []);
</script>

<div class="docs-layout" class:docs-layout--with-toc={toc.length > 0}>
	<DocsSidebar {nav} />
	<div class="docs-layout-content">
		{@render children()}
	</div>
	<DocsToc items={toc} />
</div>
