<script lang="ts">
	import DocMarkdown from '$lib/components/DocMarkdown.svelte';

	let { data } = $props();
</script>

<DocMarkdown content={data.content} sourcePath={data.sourcePath} />
