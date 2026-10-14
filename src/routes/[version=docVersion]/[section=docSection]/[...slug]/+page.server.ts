import { loadDocPage } from '$lib/server/docs';
import type { PageServerLoad } from './$types';

/** Load the requested page from the version and section validated by the route matchers. */
export const load: PageServerLoad = ({ params }) =>
	loadDocPage(params.version, params.section, params.slug);
