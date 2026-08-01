export type GitHubRepoStats = {
	fullName: string;
	url: string;
	version: string | null;
	stars: number;
	forks: number;
};

const REPO = 'smg-project/smg';
const DEFAULT_STATS: GitHubRepoStats = {
	fullName: REPO,
	url: `https://github.com/${REPO}`,
	version: null,
	stars: 0,
	forks: 0
};

const CACHE_TTL_MS = 5 * 60 * 1000;
let memoryCache: { expiresAt: number; stats: GitHubRepoStats } | null = null;

function githubHeaders(token?: string): HeadersInit {
	const headers: Record<string, string> = {
		Accept: 'application/vnd.github+json',
		'User-Agent': 'smg-site',
		'X-GitHub-Api-Version': '2022-11-28'
	};
	if (token) headers.Authorization = `Bearer ${token}`;
	return headers;
}

async function fetchJson<T>(url: string, fetchFn: typeof fetch, token?: string): Promise<T | null> {
	try {
		const response = await fetchFn(url, { headers: githubHeaders(token) });
		if (!response.ok) return null;
		return (await response.json()) as T;
	} catch {
		return null;
	}
}

export async function getGitHubRepoStats(
	fetchFn: typeof fetch,
	token?: string
): Promise<GitHubRepoStats> {
	if (memoryCache && memoryCache.expiresAt > Date.now()) {
		return memoryCache.stats;
	}

	const [repo, release] = await Promise.all([
		fetchJson<{
			full_name: string;
			html_url: string;
			stargazers_count: number;
			forks_count: number;
		}>(`https://api.github.com/repos/${REPO}`, fetchFn, token),
		fetchJson<{ tag_name: string }>(
			`https://api.github.com/repos/${REPO}/releases/latest`,
			fetchFn,
			token
		)
	]);

	if (!repo) {
		return memoryCache?.stats ?? DEFAULT_STATS;
	}

	const stats: GitHubRepoStats = {
		fullName: repo.full_name,
		url: repo.html_url,
		version: release?.tag_name ?? null,
		stars: repo.stargazers_count,
		forks: repo.forks_count
	};

	memoryCache = { stats, expiresAt: Date.now() + CACHE_TTL_MS };
	return stats;
}
