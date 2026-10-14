<script lang="ts">
	import { defaultDocsVersion } from '$lib/config/docs-versions';
	import { renderDocMarkdown } from '$lib/markdown/doc';

	let {
		content,
		sourcePath,
		contentSourcePath,
		version = defaultDocsVersion
	}: {
		content: string;
		sourcePath?: string;
		contentSourcePath?: string;
		version?: string;
	} = $props();

	const html = $derived(renderDocMarkdown(content, sourcePath, version));

	const editUrl = $derived(
		contentSourcePath
			? `https://github.com/smg-project/smg-docs/edit/main/${contentSourcePath}`
			: undefined
	);

	const rawUrl = $derived(
		contentSourcePath
			? `https://raw.githubusercontent.com/smg-project/smg-docs/main/${contentSourcePath}`
			: undefined
	);

	let articleEl = $state<HTMLElement | null>(null);

	$effect(() => {
		const article = articleEl;
		if (!article) return;
		const copy = async (event: MouseEvent) => {
			const button = (event.target as Element).closest<HTMLButtonElement>('.doc-code-copy');
			if (!button) return;
			const code = button.parentElement?.querySelector('code')?.textContent ?? '';
			try {
				await navigator.clipboard.writeText(code);
				button.textContent = 'Copied';
				setTimeout(() => {
					button.textContent = 'Copy';
				}, 1500);
			} catch {
				button.textContent = 'Failed';
			}
		};
		article.addEventListener('click', copy);
		return () => article.removeEventListener('click', copy);
	});
</script>

<article class="doc-article" bind:this={articleEl}>
	{#if editUrl && rawUrl}
		<div class="doc-toolbar">
			<a
				class="doc-toolbar-btn"
				href={editUrl}
				rel="noopener noreferrer"
				target="_blank"
				title="Edit this page"
				aria-label="Edit this page on GitHub"
			>
				<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true">
					<path
						d="M10 20H6V4h7v5h5v3.1l2-2V8l-6-6H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h4zm10.2-7c.1 0 .3.1.4.2l1.3 1.3c.2.2.2.6 0 .8l-1 1-2.1-2.1 1-1c.1-.1.2-.2.4-.2m0 3.9L14.1 23H12v-2.1l6.1-6.1z"
					/>
				</svg>
			</a>
			<a
				class="doc-toolbar-btn"
				href={rawUrl}
				rel="noopener noreferrer"
				target="_blank"
				title="View source of this page"
				aria-label="View markdown source on GitHub"
			>
				<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true">
					<path
						d="M17 18c.56 0 1 .44 1 1s-.44 1-1 1-1-.44-1-1 .44-1 1-1m0-3c-2.73 0-5.06 1.66-6 4 .94 2.34 3.27 4 6 4s5.06-1.66 6-4c-.94-2.34-3.27-4-6-4m0 6.5a2.5 2.5 0 0 1-2.5-2.5 2.5 2.5 0 0 1 2.5-2.5 2.5 2.5 0 0 1 2.5 2.5 2.5 2.5 0 0 1-2.5 2.5M9.27 20H6V4h7v5h5v4.07c.7.08 1.36.25 2 .49V8l-6-6H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h4.5a8.2 8.2 0 0 1-1.23-2"
					/>
				</svg>
			</a>
		</div>
	{/if}

	<!-- eslint-disable-next-line svelte/no-at-html-tags -- rendered markdown comes from this repo's own content tree -->
	{@html html}
</article>
