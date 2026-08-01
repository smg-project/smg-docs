import { getGitHubRepoStats } from '$lib/server/github';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = async ({ fetch, platform }) => {
	const github = await getGitHubRepoStats(fetch, platform?.env?.GITHUB_TOKEN);

	return { github };
};
