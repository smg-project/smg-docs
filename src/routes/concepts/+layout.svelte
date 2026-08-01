<script lang="ts">
	import DocsLayout from '$lib/components/DocsLayout.svelte';
	import { conceptsDocsNav } from '$lib/config/concepts-nav';

	let { children }: { children: import('svelte').Snippet } = $props();
</script>

<DocsLayout nav={conceptsDocsNav}>
	{@render children()}
</DocsLayout>
