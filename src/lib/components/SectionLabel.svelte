<script lang="ts">
	import ShiftMark from '$lib/components/ShiftMark.svelte';

	let {
		label,
		as = 'div',
		id,
		markSize = 4,
		class: className = ''
	}: {
		label: string;
		as?: keyof HTMLElementTagNameMap;
		id?: string;
		markSize?: number;
		class?: string;
	} = $props();
</script>

<svelte:element this={as} class="section-label {className}" {id}>
	<ShiftMark size={markSize} />
	<span class="section-label-text">{label}</span>
</svelte:element>
