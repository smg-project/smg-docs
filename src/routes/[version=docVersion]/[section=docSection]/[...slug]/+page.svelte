<script lang="ts">
	import DocsLayout from '$lib/components/DocsLayout.svelte';
	import DocMarkdown from '$lib/components/DocMarkdown.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<DocsLayout nav={data.nav}>
	<DocMarkdown
		content={data.content}
		sourcePath={data.sourcePath}
		contentSourcePath={data.contentSourcePath}
		version={data.docsVersion}
	/>
</DocsLayout>
