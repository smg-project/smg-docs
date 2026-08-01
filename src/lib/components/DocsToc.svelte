<script lang="ts">
	import type { DocTocItem } from '$lib/markdown/doc';

	let { items }: { items: DocTocItem[] } = $props();
</script>

{#if items.length > 0}
	<nav class="docs-toc" aria-label="Table of contents">
		<p class="docs-toc-heading">Table of contents</p>
		<ul class="docs-toc-list">
			{#each items as item (item.id + item.text)}
				<li class="docs-toc-item" class:docs-toc-item--h3={item.depth === 3}>
					<a class="docs-toc-link" href={`#${item.id}`}>{item.text}</a>
				</li>
			{/each}
		</ul>
	</nav>
{/if}
