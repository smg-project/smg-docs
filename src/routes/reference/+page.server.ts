import { loadReferencePage } from '$lib/content/reference/load';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async () => {
	return loadReferencePage();
};
