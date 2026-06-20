<script lang="ts">
	let {
		title,
		lead,
		body
	}: {
		title: string;
		lead: string;
		body: string;
	} = $props();
</script>

<article>
	<header class="doc-hero">
		<h1>{title}</h1>
		<p>{lead}</p>
	</header>
	<section class="doc-section">
		<p>{body}</p>
	</section>
</article>
