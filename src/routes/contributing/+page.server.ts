import { loadContributingPage } from '$lib/content/contributing/load';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async () => {
	return loadContributingPage();
};
