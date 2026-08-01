import { loadConceptsPage } from '$lib/content/concepts/load';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params }) => {
	return loadConceptsPage(params.slug);
};
