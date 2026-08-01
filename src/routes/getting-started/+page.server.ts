import { loadGettingStartedPage } from '$lib/content/getting-started/load';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async () => {
	return loadGettingStartedPage();
};
