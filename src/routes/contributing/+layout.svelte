<script lang="ts">
	import DocsLayout from '$lib/components/DocsLayout.svelte';
	import { contributingDocsNav } from '$lib/config/contributing-nav';

	let { children }: { children: import('svelte').Snippet } = $props();
</script>

<DocsLayout nav={contributingDocsNav}>
	{@render children()}
</DocsLayout>
