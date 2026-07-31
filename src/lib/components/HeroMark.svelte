<script lang="ts">
	let {
		class: className = '',
		width = 72,
		active = false
	}: {
		class?: string;
		width?: number;
		active?: boolean;
	} = $props();

	const height = Math.round((width * 108.05) / 47.18);
</script>

<svg
	class={className}
	{width}
	{height}
	viewBox="0 0 48 109"
	fill="none"
	xmlns="http://www.w3.org/2000/svg"
	aria-hidden="true"
>
	<defs>
		<filter
			id="smg-glow-idle"
			filterUnits="userSpaceOnUse"
			x="-18"
			y="-14"
			width="84"
			height="137"
			color-interpolation-filters="sRGB"
		>
			<feGaussianBlur in="SourceAlpha" stdDeviation="5" result="blur" />
			<feFlood flood-color="#ffffff" flood-opacity="0.5" result="color" />
			<feComposite in="color" in2="blur" operator="in" result="glow" />
			<feMerge>
				<feMergeNode in="glow" />
				<feMergeNode in="SourceGraphic" />
			</feMerge>
		</filter>

		<filter
			id="smg-glow-hover"
			filterUnits="userSpaceOnUse"
			x="-22"
			y="-18"
			width="92"
			height="145"
			color-interpolation-filters="sRGB"
		>
			<feGaussianBlur in="SourceAlpha" stdDeviation="7" result="blur" />
			<feFlood flood-color="#ffffff" flood-opacity="0.72" result="color" />
			<feComposite in="color" in2="blur" operator="in" result="glow" />
			<feMerge>
				<feMergeNode in="glow" />
				<feMergeNode in="SourceGraphic" />
			</feMerge>
		</filter>

		<filter
			id="smg-glow-active"
			filterUnits="userSpaceOnUse"
			x="-26"
			y="-22"
			width="100"
			height="153"
			color-interpolation-filters="sRGB"
		>
			<feGaussianBlur in="SourceAlpha" stdDeviation="6" result="blur-white">
				<animate attributeName="stdDeviation" values="5;8;5" dur="2.4s" repeatCount="indefinite" />
			</feGaussianBlur>
			<feFlood flood-color="#ffffff" flood-opacity="0.85" result="color-white" />
			<feComposite in="color-white" in2="blur-white" operator="in" result="glow-white" />
			<feGaussianBlur in="SourceAlpha" stdDeviation="14" result="blur-warm" />
			<feFlood flood-color="#ffc878" flood-opacity="0.45" result="color-warm" />
			<feComposite in="color-warm" in2="blur-warm" operator="in" result="glow-warm" />
			<feMerge>
				<feMergeNode in="glow-warm" />
				<feMergeNode in="glow-white" />
				<feMergeNode in="SourceGraphic" />
			</feMerge>
		</filter>

		<clipPath id="smg-hero-mark-clip">
			<rect width="47.18" height="108.05" fill="white" />
		</clipPath>
	</defs>

	<g class="mark-body" class:is-active={active}>
		<g clip-path="url(#smg-hero-mark-clip)" fill="currentColor">
			<path
				d="M47.18 25.06C47.18 24.28 47.14 23.48 47.08 22.69C46.57 16.83 44.21 11.53 40.28 7.35C36.73 3.58 32.26 1.15 27.34 0.320002C26.1 0.110002 24.84 0.0100021 23.6 0.0100021C22.36 0.0100021 21.09 0.120002 19.86 0.320002C14.94 1.15 10.47 3.58 6.92 7.35C2.97 11.52 0.62 16.83 0.1 22.68C0.03 23.47 0 24.27 0 25.05C0 25.23 0 25.42 0 25.6C0.03 27.23 0 30.41 0 30.44V104.3C0 106.36 1.58 108.04 3.52 108.04H43.66C45.6 108.04 47.18 106.36 47.18 104.3V30.44C47.18 30.44 47.14 27.23 47.18 25.6C47.18 25.42 47.18 25.23 47.18 25.05V25.06Z"
			/>
		</g>
	</g>
</svg>

<style>
	svg {
		display: block;
		overflow: visible;
		color: #ffffff;
	}

	.mark-body {
		filter: url(#smg-glow-idle);
		transition: color 0.5s ease;
	}

	.mark-body.is-active {
		color: var(--smg-orange, #b35a11);
		filter: url(#smg-glow-active);
	}

	:global(.home-hero-mark-btn:hover) .mark-body:not(.is-active) {
		filter: url(#smg-glow-hover);
	}

	@media (prefers-reduced-motion: reduce) {
		.mark-body.is-active {
			filter: url(#smg-glow-hover);
		}
	}
</style>
