<script lang="ts">
	import DocsLayout from '$lib/components/DocsLayout.svelte';
	import { gettingStartedDocsNav } from '$lib/config/getting-started-nav';

	let { children }: { children: import('svelte').Snippet } = $props();
</script>

<DocsLayout nav={gettingStartedDocsNav}>
	{@render children()}
</DocsLayout>
