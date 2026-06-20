<script lang="ts">
	import { page } from '$app/stores';
	import { slide, fade } from 'svelte/transition';
	import { cubicOut } from 'svelte/easing';
	import { headerNavItems } from '$lib/config/nav';
	import Logo from '$lib/components/Logo.svelte';
	import Symbol from '$lib/components/Symbol.svelte';
	import GitHubBadge from '$lib/components/GitHubBadge.svelte';
	import type { GitHubRepoStats } from '$lib/server/github';

	let { github }: { github: GitHubRepoStats } = $props();

	let headerEl = $state<HTMLElement | null>(null);
	let scrolled = $state(false);
	let onDark = $state(false);
	let menuOpen = $state(false);
	let hidden = $state(false);

	const isHome = $derived($page.url.pathname === '/');
	const solid = $derived(!isHome || scrolled);

	function isActive(href: string, pathname: string) {
		return pathname === href || pathname.startsWith(`${href}/`);
	}

	function closeMenu() {
		menuOpen = false;
	}

	// Close the mobile menu whenever the route changes.
	$effect(() => {
		void $page.url.pathname;
		menuOpen = false;
	});

	// Close on Escape while the menu is open.
	$effect(() => {
		if (!menuOpen) return;
		const onKey = (event: KeyboardEvent) => {
			if (event.key === 'Escape') menuOpen = false;
		};
		window.addEventListener('keydown', onKey);
		return () => window.removeEventListener('keydown', onKey);
	});

	$effect(() => {
		let lastY = window.scrollY;
		let ticking = false;

		const update = () => {
			const y = window.scrollY;
			scrolled = y > 16;

			const delta = y - lastY;
			// Hide on downward scroll past the header, reveal on upward scroll.
			if (!menuOpen && Math.abs(delta) > 6) {
				if (delta > 0 && y > 96) hidden = true;
				else if (delta < 0) hidden = false;
			}
			lastY = y;
			ticking = false;
		};

		const onScroll = () => {
			if (!ticking) {
				ticking = true;
				requestAnimationFrame(update);
			}
		};

		update();
		window.addEventListener('scroll', onScroll, { passive: true });
		return () => window.removeEventListener('scroll', onScroll);
	});

	// Keep the header visible whenever the mobile menu is open.
	$effect(() => {
		if (menuOpen) hidden = false;
	});

	$effect(() => {
		if (!isHome || !headerEl) {
			onDark = false;
			return;
		}

		const darkSections = () =>
			[...document.querySelectorAll('.home-choose-path, .site-footer')] as HTMLElement[];

		const updateOnDark = () => {
			const headerRect = headerEl!.getBoundingClientRect();
			onDark = darkSections().some((section) => {
				const sectionRect = section.getBoundingClientRect();
				return headerRect.bottom > sectionRect.top && headerRect.top < sectionRect.bottom;
			});
		};

		updateOnDark();
		window.addEventListener('scroll', updateOnDark, { passive: true });
		window.addEventListener('resize', updateOnDark);

		return () => {
			window.removeEventListener('scroll', updateOnDark);
			window.removeEventListener('resize', updateOnDark);
			onDark = false;
		};
	});

	$effect(() => {
		if (!headerEl) return;

		const setHeight = () => {
			document.documentElement.style.setProperty(
				'--site-header-height',
				`${headerEl!.offsetHeight}px`
			);
		};

		setHeight();
		const observer = new ResizeObserver(setHeight);
		observer.observe(headerEl);
		return () => observer.disconnect();
	});
</script>

<header
	class="site-header"
	class:site-header--solid={solid}
	class:site-header--on-dark={onDark}
	class:site-header--menu-open={menuOpen}
	class:site-header--hidden={hidden}
	bind:this={headerEl}
>
	<div class="site-header-inner">
		<a class="site-brand" href="/" onclick={closeMenu}>
			<Logo />
		</a>

		<div class="site-header-right">
			<nav class="site-nav-pill" aria-label="Primary">
				<a
					class="site-nav-symbol"
					href={github.url}
					aria-label="Go to GitHub repository"
					target="_blank"
					rel="noopener noreferrer"
				>
					<Symbol size={18} />
				</a>
				<ul class="site-nav-links">
					{#each headerNavItems as item}
						<li>
							<a href={item.href} class:active={isActive(item.href, $page.url.pathname)}>
								{item.label}
							</a>
						</li>
					{/each}
					<li>
						<a href="/search" class:active={$page.url.pathname === '/search'}>Search</a>
					</li>
				</ul>
			</nav>
			<GitHubBadge {github} />
		</div>

		<button
			type="button"
			class="site-menu-toggle"
			aria-label={menuOpen ? 'Close menu' : 'Open menu'}
			aria-expanded={menuOpen}
			aria-controls="site-mobile-menu"
			onclick={() => (menuOpen = !menuOpen)}
		>
			<span class="site-menu-toggle-icon" class:is-open={menuOpen} aria-hidden="true">
				<span></span>
				<span></span>
				<span></span>
			</span>
		</button>
	</div>

	{#if menuOpen}
		<button
			type="button"
			class="site-menu-backdrop"
			aria-label="Close menu"
			tabindex="-1"
			onclick={closeMenu}
			transition:fade={{ duration: 180 }}
		></button>

		<div
			id="site-mobile-menu"
			class="site-mobile-menu"
			transition:slide={{ duration: 220, easing: cubicOut }}
		>
			<nav class="site-mobile-nav" aria-label="Mobile">
				<ul class="site-mobile-links">
					{#each headerNavItems as item}
						<li>
							<a
								href={item.href}
								class:active={isActive(item.href, $page.url.pathname)}
								onclick={closeMenu}
							>
								{item.label}
							</a>
						</li>
					{/each}
					<li>
						<a
							href="/search"
							class:active={$page.url.pathname === '/search'}
							onclick={closeMenu}
						>
							Search
						</a>
					</li>
				</ul>
			</nav>

			<a
				class="site-mobile-repo"
				href={github.url}
				target="_blank"
				rel="noopener noreferrer"
				onclick={closeMenu}
			>
				<Symbol size={18} />
				<span class="site-mobile-repo-name">{github.fullName}</span>
				<span class="site-mobile-repo-stats">&#9733; {github.stars}</span>
			</a>
		</div>
	{/if}
</header>
