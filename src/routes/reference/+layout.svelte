<script lang="ts">
	import DocsLayout from '$lib/components/DocsLayout.svelte';
	import { referenceDocsNav } from '$lib/config/reference-nav';

	let { children }: { children: import('svelte').Snippet } = $props();
</script>

<DocsLayout nav={referenceDocsNav}>
	{@render children()}
</DocsLayout>
